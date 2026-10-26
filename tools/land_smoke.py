"""Live-prepare smoke for a landing: a real preparation per game with the code being landed, on the live state as it is.

The unit tests never prepare on a real project's state. ecc5573 changed the fsca table's format (c346302: two tables,
1 MiB) while every live state held the one-table 512 KiB file: its unit tests passed and every live prepare failed.
Run from the checkout being landed, after its unit tests:

usage: python tools/land_smoke.py [BASE] [--projects KEY,...] [--slot-wait S] [--timeout S] [--json OUT]
  BASE         what the landing changes from (default master): BASE's merge base -> the working tree
  --projects   tests/patterns.json project keys (default: GAMES, one per game); a key's workspace path comes from
               tests/patterns.local.json, as for tools/regress.py
  --slot-wait  the longest wait for a Ghidra slot (default 1800 s)
  --timeout    each probe's limit (tools/bounded.py, the whole process tree; default 1800 s)
1. Only when needed: tools/regress_targets classes the change; a target whose mode is prepare or gate (what makes a
   harness, or a file of no known class) is smoked. Readable, judge or none: nothing to run ("not needed", exit 0).
2. Per project of a smoked target: its smallest matched function (catalog bytes, then address) whose live harness
   holds 16+ valid dev vectors is prepared afresh by tools/oracle_probe.py (a private copy: nothing live is written,
   and the live state's files are read as they are), and its matched C judged on the fresh harness. A preparation
   error, a refusal, no valid dev vector or a verdict other than PASS fails the smoke.
3. Ghidra's cap: each probe starts only while fewer than --ghidra-max (2) Ghidra JVMs run. When no slot frees within
   --slot-wait the smoke FAILS ("no slot"): never skipped, since a skipped smoke is how ecc5573 got through.
Exit 0: not needed, or every project PASS. 1: a project failed. 3: no slot.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tools"))
import regress_targets as rt  # noqa: E402
from core import toolcfg  # noqa: E402

GAMES = ("tc", "ps2", "sp")  # Toy Commander, Power Stone 2, Spawn (main)
SMOKED = ("prepare", "gate")
PS = ("(Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | Where-Object { $_.CommandLine -match 'ghidra' }"
      " | Measure-Object).Count")


def needed(base, root=TOOL):
    """(targets to smoke, the plan {target: mode}) of the change BASE..working tree."""
    mb, files, old_text = rt.changed_files(base, root)
    targets = sorted(p.name for p in (root / "targets").iterdir() if (p / "target.json").exists())
    mode = rt.plan(rt.classify(files, root, old_text), targets)
    return sorted(t for t, m in mode.items() if m in SMOKED), mode


def pick(ws):
    """The workspace's smallest matched function (catalog bytes, then address) with 16+ valid dev vectors in its live
    harness; None when there is none."""
    ws = pathlib.Path(ws)
    ledger = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
    size = {f["addr"]: f.get("bytes") or 0 for f in
            json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]}
    for addr in sorted((a for a, r in ledger.items() if r.get("status") == "matched"),
                       key=lambda a: (size.get(a, 1 << 30), a)):
        try:
            summary = json.loads((ws / "harness" / addr / "oracle_summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (ws / "matched" / f"{addr}.c").exists() and (summary.get("dev_valid") or 0) >= 16:
            return addr
    return None


def ghidra_jvms():
    """How many Ghidra JVMs run on this machine (any session's)."""
    if os.name == "nt":
        out = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True, text=True,
                             timeout=60).stdout
    else:
        out = str(sum("ghidra" in line and "java" in line for line in
                      subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=60).stdout.splitlines()))
    return int(out.strip() or 0)


def wait_slot(limit, cap, count=ghidra_jvms, sleep=time.sleep, clock=time.monotonic, every=30):
    """True once fewer than cap Ghidra JVMs run; False when none freed within limit seconds."""
    t0 = clock()
    while True:
        if count() < cap:
            return True
        if clock() - t0 >= limit:
            return False
        sleep(min(every, max(1, limit - (clock() - t0))))


def probe(ws, key, addr, timeout, out):
    """tools/oracle_probe.py on one function (bounded), its matched C judged: the probe's result row."""
    cmd = [sys.executable, str(TOOL / "tools" / "bounded.py"), str(timeout), sys.executable,
           str(TOOL / "tools" / "oracle_probe.py"), "--project", str(ws), "--tag", f"smoke-{key}", "--jobs", "1",
           "--judge-matched", "--json", str(out), addr]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=TOOL,
                       timeout=timeout + 120)
    try:
        return json.loads(pathlib.Path(out).read_text(encoding="utf-8"))["results"][0]
    except (OSError, ValueError, LookupError):
        return {"addr": addr, "error": f"probe exited {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}"}


def verdict(r):
    """None when the probe's row passes, else why not."""
    if r.get("error"):
        return f"prepare error: {r['error']}"
    if r.get("unsupported"):
        return f"refused: {r['unsupported'][:200]}"
    if not r.get("dev_valid"):
        return f"no valid dev vector ({r.get('dev')})"
    if r.get("verdict") != "PASS":
        return f"matched C {r.get('verdict')} {r.get('failing', '')} {r.get('detail', '')}".strip()
    return None


def main(argv=None, run=probe, slot=wait_slot):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", nargs="?", default="master")
    ap.add_argument("--projects", default=",".join(GAMES))
    ap.add_argument("--slot-wait", type=float, default=1800)
    ap.add_argument("--ghidra-max", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    smoke, mode = needed(a.base)
    report = {"base": a.base, "mode": mode, "projects": {}}

    def done(rc, msg):
        print(msg, flush=True)
        report.update(rc=rc, result=msg)
        if a.json:
            pathlib.Path(a.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
        return rc
    if not smoke:
        return done(0, f"land smoke: not needed (no prepare-class change: {mode})")
    spaces = toolcfg.patterns(TOOL / "tests")["projects"]
    failed = []
    for key in a.projects.split(","):
        ws = spaces.get(key)
        if not ws:
            failed.append(key)
            report["projects"][key] = {"why": "no workspace path (tests/patterns.local.json)"}
            continue
        target = json.loads((pathlib.Path(ws) / "project.json").read_text(encoding="utf-8"))["target"]
        if target not in smoke:
            report["projects"][key] = {"why": f"target {target}: {mode.get(target)}, not smoked"}
            continue
        addr = pick(ws)
        if not addr:
            failed.append(key)
            report["projects"][key] = {"why": "no matched function with a 16+ dev vector harness"}
            continue
        if not slot(a.slot_wait, a.ghidra_max):
            report["projects"][key] = {"addr": addr, "why": "no slot"}
            return done(3, f"land smoke: FAIL, no slot: {a.ghidra_max}+ Ghidra JVMs for {a.slot_wait:.0f} s ({key})")
        t = time.monotonic()
        out = TOOL / "tests" / ".work" / f"smoke-{key}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = run(ws, key, addr, a.timeout, out)
        why = verdict(r)
        report["projects"][key] = {"addr": addr, "secs": round(time.monotonic() - t, 1), "why": why,
                                   "dev": r.get("dev"), "verdict": r.get("verdict")}
        print(f"land smoke {key} {addr}: {why or 'PASS'} ({time.monotonic() - t:.0f} s)", flush=True)
        if why:
            failed.append(key)
    if failed:
        return done(1, f"land smoke: FAIL ({', '.join(failed)})")
    return done(0, f"land smoke: PASS ({', '.join(k for k, v in report['projects'].items() if v.get('addr'))})")


if __name__ == "__main__":
    sys.exit(main())
