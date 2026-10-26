"""Pattern regression suite: the gate for every checker change (core/check.py, core/lint.py, the target's runtime,
Ghidra scripts or input template). Judges every fixture (each workspace's regress/fixtures.json, merged by
core/toolcfg.patterns with tests/patterns.json), plus a seeded sample of matched
functions per project, with the CURRENT working-tree code, against harnesses prepared afresh into tests/.work/
(private copies of each project's Ghidra project and state; no live harness, packet, ledger or slot is written).

usage: python tools/regress.py [--projects KEY,KEY|all] [--only RX] [--sample N] [--seed S] [--jobs N]
                               [--catalog] [--reuse] [--timeout S] [--json OUT] [--set KEY=JSON ...]
                               [--parity] [--list FILE] [--targeted [BASE]] [--dry-run] [--cache DIR]
  --projects  keys from tests/patterns.json "projects" (default: the "default_projects" list there; all = every key);
              each key's workspace path comes from tests/patterns.local.json (git-ignored, this machine's paths)
  --only      regex on fixture id or pattern; with --only the sample defaults to 0
  --sample    matched functions re-judged per project from matched/ (default 15), seeded by --seed (default 1)
  --jobs      parallel Ghidra preparations per project (default: from the cores and free RAM, auto_jobs)
  --catalog   re-run the catalog fixups and TcCatalog on the private copies first (when those scripts changed)
  --reuse     reuse the harnesses in tests/.work whose oracle stamp (meta.json "oracle_stamp": a hash of the Ghidra
              scripts, harness generator, facts/config, counts, hints, catalog, binary) is current; re-prepare the
              rest (quick iteration on fixture C; NOT a gate run). The run reports how many were reused/prepared
  --set       a config key for this run only, over config.json/target.json/project.json (e.g. fpscr_model=true):
              trials of a staged switch before a project turns it on
  --parity    also judge each sampled (or listed) matched function's raw C with the readable runtime
              (readable_check.check_raw, the 32-bit build refine.py uses) on the same harness: item rd.ADDR,
              expected PASS like the raw item, so any disagreement between the two judges is unexpected; --parity
              reuses harnesses by oracle stamp as --reuse does
  --list      the matched functions to judge instead of a random sample (one project; a file of addresses, # comments):
              the full readable-parity run is --parity --list over every matched function, in bounded chunks
  --targeted  only what the change from BASE (default master: its merge base -> the working tree) can affect
              (tools/regress_targets.py classes each changed file by the gate's import graph): a project whose target
              the change does not reach is skipped; one it reaches always runs its catalog step (--catalog), and is
              judged in full with every harness prepared afresh (a change to the oracle, the gate or an unknown file),
              in full on cached harnesses (the raw judge or runtime), or only its readable items on cached harnesses
              (the readable judge or runtime). A change that reaches nothing the gate runs exits 0 at once;
              --dry-run prints that selection and stops (no Ghidra)
  --cache     the harness cache (default: "harness_cache" in tests/patterns.local.json, else tests/.work/cache):
              harnesses prepared by this run are kept there by oracle stamp and prepare code hash, and a --targeted
              run of a judge-only change reuses them; a gate run without --targeted fills it too when DIR is given
An item judged on no vector at all (0/0) reads ORACLE, never FAIL: an oracle problem, not a verdict. A group with
ORACLE or an out-of-time refusal is prepared once more, alone, after the parallel run (the oracle's deadline is
wall-clock, so load can cause it); the alone result is recorded, the first one kept as first_run.
Exit 0 only when every PASS-expected item passes, every FAIL-expected item fails, every REFUSED-expected item
(a fixture with "refused": a regex) is refused by the preparation with a matching reason, and nothing errors.
"""
import argparse
import ctypes
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from bounded import kill_tree  # noqa: E402
sys.path.insert(0, str(TOOL))
from core import toolcfg  # noqa: E402  (patterns.json + the git-ignored patterns.local.json paths)
import regress_targets  # noqa: E402

TESTS = TOOL / "tests"
WORK = TESTS / ".work"
GHIDRA_WORK = TESTS / "ghidra_work"  # Ghidra refuses project paths with a '.'-element, so not under .work


def free_ram():
    """Free physical memory in bytes (None when unknown)."""
    try:
        if os.name == "nt":
            class Mem(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "avail", "ptotal", "pavail", "vtotal", "vavail", "x")]
            m = Mem()
            m.len = ctypes.sizeof(Mem)
            return m.avail if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else None
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


GHIDRA_RAM = 3 << 30  # one headless Ghidra preparation (2 GB heap, analyzeHeadless's default) and its checks


def auto_jobs():
    """Parallel preparations: a quarter of the cores, as many as free RAM holds, 1..8."""
    cores = os.cpu_count() or 4
    ram = free_ram()
    by_ram = ram // GHIDRA_RAM if ram else 3
    return max(1, min(8, cores // 4, by_ram))


def run_child(cmd, timeout, prefix):
    """Run a child, echoing its output with a prefix; kill its whole tree after `timeout` s. Returns rc or None."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                         bufsize=1)

    def pump():
        for line in p.stdout:
            print(f"{prefix} {line.rstrip()}", flush=True)
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        p.wait(timeout=60)
        rc = None
    t.join(timeout=10)
    return rc


def select(spec, a):
    keys = list(spec["projects"]) if a.projects == "all" else \
        (a.projects.split(",") if a.projects else spec.get("default_projects") or list(spec["projects"]))
    bad = [k for k in keys if k not in spec["projects"]]
    if bad:
        sys.exit(f"unknown project key(s) {bad}; known: {', '.join(spec['projects'])}")
    unset = [k for k in keys if not spec["projects"][k]]
    if unset:
        sys.exit(f"no workspace path for project key(s) {unset}: set them under \"projects\" in "
                 "tests/patterns.local.json (git-ignored)")
    rx = re.compile(a.only) if a.only else None
    plan = {}
    for k in keys:
        fx = [f for f in spec["fixtures"] if f["project"] == k and (not rx or rx.search(f["id"]) or rx.search(f["pattern"]))]
        if fx or a.sample or a.list:
            plan[k] = fx
    return plan


def readable_items(fixtures, parity):
    """Whether a readable-mode project has an item to judge: tools/regress_project.py keeps only the fixtures on a
    readable runtime and, with --parity, the sample's parity items. Without one its run judged 0 items after a full
    setup (R1: 28 of 29 projects, about 33 s each)."""
    return bool(parity) or any(f.get("runtime") in ("readable", "readable-src") for f in fixtures)


def ok(r):
    return r.get("got") == r.get("expect")


def table(rows):
    lines = [f"{'project':8} {'item':38} {'pattern':16} {'expect':6} {'got':6} {'failing':8} {'live':5} detail",
             "-" * 120]
    for k, r in rows:
        live = {True: "same", False: "diff", None: "-"}[r.get("same_as_live")]
        mark = "" if ok(r) else "  <-- UNEXPECTED"
        lines.append(f"{k:8} {r['id'][:38]:38} {r['pattern'][:16]:16} {r['expect']:6} {r.get('got', '?'):6} "
                     f"{r.get('failing', ''):8} {live:5} {r.get('detail', '')[:70]}{mark}")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects")
    ap.add_argument("--only")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int)
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--timeout", type=float, default=5400, help="per project, seconds (tree-killed after)")
    ap.add_argument("--json", help="also write all results to this file")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=JSON")
    ap.add_argument("--parity", action="store_true")
    ap.add_argument("--list")
    ap.add_argument("--targeted", nargs="?", const="master", metavar="BASE")
    ap.add_argument("--cache")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run and not a.targeted:
        sys.exit("--dry-run needs --targeted")
    listed = []
    if a.list:
        with open(a.list.lstrip("@"), encoding="utf-8-sig") as fh:
            listed = [w.lower() for ln in fh for w in ln.split("#")[0].split()]
        if len(set(listed)) < len(listed):  # each function once (batch 21: a merged CRLF list repeated 48)
            print(f"--list: {len(listed) - len(set(listed))} duplicate(s) dropped", flush=True)
            listed = list(dict.fromkeys(listed))
        if not a.projects or "," in a.projects or a.projects == "all":
            sys.exit("--list needs exactly one --projects key")
        a.sample = 0
    if a.sample is None:
        a.sample = 0 if a.only else 15
    spec = toolcfg.patterns(TESTS)
    for f in spec["fixtures"]:
        for side in ("pass", "fail"):
            if f.get(side) and not (TOOL / f[side]).exists():
                sys.exit(f"fixture {f['id']}: missing {f[side]}")
    plan = select(spec, a)
    modes, cache, skipped = {}, None, []
    if a.targeted:
        cache = pathlib.Path(a.cache or spec.get("harness_cache") or WORK / "cache")
        mb, files, old_text = regress_targets.changed_files(a.targeted)
        rows = regress_targets.classify(files, old_text=old_text)
        print(f"--targeted {a.targeted} (merge base {mb[:10]}): {len(files)} changed file(s)", flush=True)
        for f, kind, t in rows:
            print(f"  {kind:8} {t or '*':14} {f}", flush=True)
        by_target = regress_targets.plan(rows, sorted(p.name for p in (TOOL / "targets").iterdir()
                                                      if (p / "target.json").exists()))
        for k in list(plan):
            target = json.loads((pathlib.Path(spec["projects"][k]) / "project.json").read_text(
                encoding="utf-8")).get("target")
            modes[k] = by_target.get(target, "gate")  # a target this tree does not know: the full gate
            if modes[k] == "none":
                del plan[k]
            elif modes[k] == "readable" and not readable_items(plan[k], a.parity):
                del plan[k]  # nothing the readable judge judges: no setup, no Ghidra copy or catalog (~33 s each)
                skipped.append(k)
        print("projects: " + (", ".join(f"{k} {modes[k]}" for k in plan) or "none"), flush=True)
        if skipped:
            print(f"skipped, no readable item: {', '.join(skipped)}", flush=True)
        if not plan:
            print("the change reaches nothing the gate runs: no gate items (exit 0)")
            return 0
        if a.dry_run:
            return 0
    elif a.cache:
        cache = pathlib.Path(a.cache)
    over = {}
    for kv in a.set:
        k, _, v = kv.partition("=")
        try:
            over[k] = json.loads(v)
        except ValueError:
            over[k] = v
    if not plan and not a.targeted:
        sys.exit("nothing selected")
    t0 = time.time()
    if not a.jobs:
        a.jobs = auto_jobs()
        print(f"--jobs {a.jobs} (cores {os.cpu_count()}, free RAM {(free_ram() or 0) / 2 ** 30:.1f} GB)", flush=True)
    rows, secs, failed_projects, harn = [], {}, [], {"reused": 0, "prepared": 0, "retried_alone": 0, "cached": 0}
    hashes = {}
    for k, fixtures in plan.items():
        work = WORK / k
        work.mkdir(parents=True, exist_ok=True)
        pf, out = work / "plan.json", work / "result.json"
        mode = modes.get(k)
        pf.write_text(json.dumps({"key": k, "fixtures": fixtures, "sample": a.sample, "seed": a.seed, "config": over,
                                  "list": listed, "parity": a.parity, "reuse_cache": mode in ("judge", "readable"),
                                  "items": "readable" if mode == "readable" else "all"}, indent=1), encoding="utf-8")
        out.unlink(missing_ok=True)
        cmd = [sys.executable, str(TOOL / "tools" / "regress_project.py"), "--project", spec["projects"][k],
               "--work", str(work), "--ghidra-work", str(GHIDRA_WORK / k), "--plan", str(pf), "--out", str(out), "--jobs", str(a.jobs)]
        cmd += ["--reuse"] * a.reuse + ["--catalog"] * (a.catalog or bool(a.targeted))
        if cache:
            target = json.loads((pathlib.Path(spec["projects"][k]) / "project.json").read_text(
                encoding="utf-8")).get("target")
            if target not in hashes:
                hashes[target] = regress_targets.prepare_hash(target)
            cmd += ["--cache", str(cache / k), "--prepare-hash", hashes[target]]
        print(f"== {k}: {len(fixtures)} fixture(s), sample {a.sample}  ({spec['projects'][k]})"
              + (f"  config {over}" if over else "") + (f"  targeted: {mode}" if mode else ""), flush=True)
        t = time.time()
        rc = run_child(cmd, a.timeout, f"[{k}]")
        secs[k] = time.time() - t
        if rc != 0 or not out.exists():
            failed_projects.append(f"{k}: runner {'timed out' if rc is None else f'exited {rc}'}")
            continue
        res = json.loads(out.read_text(encoding="utf-8"))
        rows += [(k, r) for r in res["results"]]
        for n, v in (res.get("harnesses") or {}).items():
            harn[n] = harn.get(n, 0) + v
    print()
    print("\n".join(table(rows)))
    bad = [(k, r) for k, r in rows if not ok(r)]
    print(f"\n{len(rows)} item(s), {len(bad)} unexpected; " +
          ", ".join(f"{k} {s / 60:.1f} min" for k, s in secs.items()) + f"; total {(time.time() - t0) / 60:.1f} min")
    again = harn["retried_alone"]
    print(f"harnesses: {harn['reused']} reused (oracle stamp current; {harn['cached']} from the cache), "
          f"{harn['prepared']} prepared"
          + (f" ({again} of them a second time, alone, after oracle trouble under load)" if again else ""))
    oracle = [r["id"] for _, r in rows if r.get("got") == "ORACLE"]
    if oracle:
        print(f"ORACLE (0/0: no vector to judge by, an oracle problem, not a FAIL): {' '.join(oracle)}")
    for msg in failed_projects:
        print("PROJECT FAILED:", msg)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps([dict(r, project=k) for k, r in rows], indent=1), encoding="utf-8")
    if a.reuse:
        print("(--reuse: harnesses with a current oracle stamp were reused; this is not a gate run)")
    return 1 if bad or failed_projects else 0


if __name__ == "__main__":
    sys.exit(main())
