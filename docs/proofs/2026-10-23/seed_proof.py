"""Proof D (row 20): would seed() with -noanalysis write the same catalog as before? A private workspace gets a fresh
setup (import, analysis, fixups, catalog: the state before a first seed). It is then copied three times: two copies
are seeded as core/ghidra.seed does (a whole-program auto-analysis before TcSeed: is it repeatable?), one with
-noanalysis added (the change tried, not landed). catalog.json, code_ranges.txt and function_bodies.txt are hashed.

usage: python seed_proof.py setup WS SOURCE_WS     copy SOURCE_WS's project.json, state/ and oracle/ to WS, then
                                                    setup --force
       python seed_proof.py seed WS old|new        seed WS; WS/seed_MODE.json: seconds, TcSeed's count, hashes
DP_TREE names the DP checkout to run. The broker pass is read from the HOLD into this process's environment only
(never printed, logged or written).
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import time

OUTPUTS = ("catalog.json", "code_ranges.txt", "function_bodies.txt")


def env(ws):
    hold = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker" / "HOLD"
    if hold.exists():
        key = (json.loads(hold.read_text(encoding="utf-8") or "{}") or {}).get("pass")
        if key:
            os.environ["DP_BROKER_PASS"] = key
        del key
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(pathlib.Path(os.environ["DP_TREE"])))
    from core import ghidra, paths
    assert paths.ROOT == ws.resolve() and str(paths.GHIDRA_PROJECT).startswith(str(ws.resolve()))
    return ghidra, paths


def seed(ghidra, paths, cfg, mode):
    """core/ghidra.seed's run; "new" adds -noanalysis (no auto-analysis before TcSeed)."""
    ghidra.ghidra_server.stop_all()
    ghidra._headless(cfg, [
        "-process", cfg["binary_path"].name, *(["-noanalysis"] if mode == "new" else []),
        "-scriptPath", str(paths.sync_scripts()), "-postScript", "TcSeed.java",
        *ghidra.fixups(cfg), "-postScript", "TcCatalog.java", str(paths.CATALOG), str(paths.CODE_RANGES),
        str(paths.FUNCTION_BODIES),
    ], "ghidra_seed.log", timeout=ghidra.whole_timeout(cfg))


def main(cmd, ws, arg):
    if cmd == "setup":
        src = pathlib.Path(arg)
        shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True)
        shutil.copy2(src / "project.json", ws / "project.json")
        shutil.copytree(src / "state", ws / "state", ignore=shutil.ignore_patterns("*.log", "*.lock", "fsca"))
        shutil.copytree(src / "oracle", ws / "oracle")  # the workspace's own oracle scripts (TcInputs.java)
        ghidra, paths = env(ws)
        t = time.perf_counter()
        ghidra.setup(paths.config(), force=True)
        print(f"setup: {time.perf_counter() - t:.1f} s", flush=True)
        return
    ghidra, paths = env(ws)
    cfg = paths.config()
    t = time.perf_counter()
    seed(ghidra, paths, cfg, arg)
    secs = round(time.perf_counter() - t, 1)
    log = (paths.STATE / "ghidra_seed.log").read_text(encoding="utf-8")
    added = re.findall(r"TcSeed: (\d+) function\(s\) added in total", log)
    rec = {"mode": arg, "seconds": secs, "added": int(added[-1]) if added else None,
           "analysis_total": re.findall(r"Total Time\s+(\d+) secs", log),
           "sha256": {f: hashlib.sha256((paths.STATE / f).read_bytes()).hexdigest() for f in OUTPUTS}}
    (ws / f"seed_{arg}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], pathlib.Path(sys.argv[2]).resolve(), sys.argv[3])
