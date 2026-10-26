"""laptop2: the full re-proof's prepares. For each RUN (TREE:CFG, TREE rb = the rebased branch, land = its base), every
sampled function (tools/mutation_audit/sample.json) of every workspace, one workspace after another, each bounded.
usage: py -3.12 l2full.py JOBS RUN ...      e.g. l2full.py 3 rb:off land:off rb:on
A run whose probe JSON exists is skipped (resume). LOCALAPPDATA: this job's own broker folder. Before each workspace it
waits for RAM under 85% (5 readings in a row after a high one); at 95% it stops."""
import json
import os
import pathlib
import subprocess
import sys
import time

import psutil

W = pathlib.Path(os.environ["DP_PREWAVE_DIR"])  # the work folder (on laptop2: its dpwork\prewave)
PROOF = W / "proof"
WS = {"grandia": W / r"ws\grandia\ws-1pct8", "skies": W / r"ws\skies\decomp-gekko", "tc": W / r"ws\tc\decomp",
      "ps2": W / r"ws\ps2\decomp\main", "spawn-main": W / r"ws\spawn\decomp\main"}
ON = {"branch_coverage": True, "targeted_vectors": True, "mock_junk": True, "mock_writes": True,
      "call_snapshots": True, "exit_frame": True}
env = dict(os.environ, LOCALAPPDATA=str(W / "appdata"))
env.pop("DP_BROKER_PASS", None)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def waitlow():
    if psutil.virtual_memory().percent < 85:
        return
    ok = 0
    for _ in range(480):
        r = psutil.virtual_memory().percent
        if r >= 95:
            sys.exit(f"RAM {r}% >= 95: stop")
        ok = ok + 1 if r < 85 else 0
        if ok >= 5:
            return
        time.sleep(30)
    sys.exit("RAM never settled")


jobs = sys.argv[1]
for run in sys.argv[2:]:
    tree, cfg = run.split(":")
    repo = W / f"repo-{tree}"
    sample = json.loads((W / "repo-rb" / "tools" / "mutation_audit" / "sample.json").read_text(encoding="utf-8"))
    for key in WS:
        tag = f"{key}_{tree}_{cfg}"
        out = PROOF / f"probe_{tag}.json"
        if out.exists():
            log(tag, "done before, skipped")
            continue
        lst = PROOF / f"list_{key}.txt"
        lst.write_text("\n".join(sample[key]) + "\n", encoding="utf-8")
        waitlow()
        log("start", tag, f"RAM {psutil.virtual_memory().percent}%")
        t = time.time()
        with open(PROOF / f"log_{tag}.txt", "w", encoding="utf-8") as f:
            r = subprocess.run([sys.executable, r"tools\bounded.py", "7200", sys.executable,
                                str(PROOF / "probe_flags.py"), str(repo), tag, json.dumps(ON if cfg == "on" else {}), "--project", str(WS[key]),
                                "--tag", tag, "--jobs", jobs, "--json", str(out), "@" + str(lst)],
                               cwd=repo, env=env, stdout=f, stderr=subprocess.STDOUT)
        log("end", tag, "exit", r.returncode, f"{time.time() - t:.0f}s")
log("all done")
