"""Laptop2's judging for the compiler proof: the four frozen copies under its own pinned 14.44 (config.local.json
vcvars), one judge run each, in order. Each run is bounded: past its limit its own process tree is killed.
    python run_l2.py TAG DP    (DP: laptop2's DP checkout)"""
import pathlib, subprocess, sys
here = pathlib.Path(__file__).resolve().parent
tag = sys.argv[1]
DP = sys.argv[2]
for game, dp in (("tc", DP), ("df2", DP), ("skies", DP), ("th", str(here / "dp-x360"))):
    log = open(here / f"{tag}_{game}.log", "w")
    p = subprocess.Popen([sys.executable, str(here / "judge.py"), "--dp", dp, "--mirror", str(here / f"m_{game}"),
                          "--out", str(here / f"{tag}_{game}_configured.json"), "--toolset", "configured"],
                         stdout=log, stderr=subprocess.STDOUT, cwd=here)
    try:
        p.wait(timeout=3600)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], timeout=60)
        print(game, "TIMED OUT", flush=True)
    print(game, "exit", p.returncode, flush=True)
