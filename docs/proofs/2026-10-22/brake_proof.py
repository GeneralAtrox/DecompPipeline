"""Proof 3 (wave RAM brake, 1a8bb09): one wave-style re-stamp chunk of PS2 in a private workspace, by the real tool
(tools/reprepare_stale.py --stamps --serve --slots 3 --max N --rounds 0), with and without --brake 85.

usage: python brake_proof.py build LIVE_WS PRIVATE_WS N   private copy; the N lowest matched addresses lose their
                                                          oracle stamp there (so the chunk re-prepares them)
       python brake_proof.py ram LOGFILE                  sample RAM every second until LOGFILE.stop exists
Nothing live is written: the copy holds project.json (absolute binary), oracle/, readable/names, matched/, the
master Ghidra project, the state files (no logs, marks, backups) and every harness/*/meta.json.
"""
import json
import pathlib
import shutil
import sys
import time

SKIP_DIRS = {"ghidra_scripts", "readable_snapshots", "stamp_refresh", "sig_override_backup", "usage_probe"}


def build(live, ws, n):
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "state").mkdir(parents=True)
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    p["binary"] = str((live / p["binary"]).resolve())
    p.pop("ghidra_dir", None)
    (ws / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    for d in ("oracle", "matched"):
        shutil.copytree(live / d, ws / d)
    shutil.copytree(live / "readable" / "names", ws / "readable" / "names")
    shutil.copytree(live / "ghidra", ws / "ghidra", ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
    for f in (live / "state").iterdir():
        if f.is_dir():
            if f.name not in SKIP_DIRS:
                shutil.copytree(f, ws / "state" / f.name)
        elif not (".before" in f.name or f.suffix in (".log", ".lock") or f.name.startswith(("ghidra_marks",
                  "prepare_timing", "events", "final_audit"))):
            shutil.copy2(f, ws / "state" / f.name)
    led = json.loads((live / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
    matched = sorted((x for x, r in led.items() if r.get("status") == "matched"), key=lambda x: int(x, 16))
    picked = []
    for d in (live / "harness").iterdir():
        if (d / "meta.json").exists():
            (ws / "harness" / d.name).mkdir(parents=True)
            shutil.copy2(d / "meta.json", ws / "harness" / d.name / "meta.json")
    for x in matched:
        m = ws / "harness" / x / "meta.json"
        if len(picked) < n and m.exists():
            meta = json.loads(m.read_text(encoding="utf-8"))
            meta.pop("oracle_stamp", None)
            m.write_text(json.dumps(meta, indent=1), encoding="utf-8")
            picked.append(x)
    (ws / "picked.txt").write_text("\n".join(picked) + "\n", encoding="utf-8")


def ram(logfile):
    import psutil
    logfile = pathlib.Path(logfile)
    stop = logfile.with_name(logfile.name + ".stop")
    with open(logfile, "a", encoding="utf-8") as f:
        while not stop.exists():
            f.write(f"{time.time():.0f} {psutil.virtual_memory().percent}\n")
            f.flush()
            time.sleep(1)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), int(sys.argv[4]))
    else:
        ram(sys.argv[2])
