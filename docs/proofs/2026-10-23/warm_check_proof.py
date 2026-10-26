"""Proof L (row 24): the warm agent check (core/warm_check) answers exactly as the cold one, and how long each takes.

For each function of a private workspace copy, three work.c variants: the file as it is (the matched raw C, or the
readable C), one that does not build (a stray statement), and one with its first hex constant plus one (most then
FAIL on the vectors). Each goes through the sandbox's own check.py (the real launcher text, core/agent or
core/readable_sandbox), run as the agent's Bash runs it (`python check.py` in the sandbox folder) five times:
cold, warm, cold, warm, cold. Warm runs inside warm_check.session, the spare ready before the check is sent (in a
session the agent's own turn gives it that time). Every run's exit code and output must be the same bytes: the agent
check prints the verdict and the first differences. The first run builds; the times are of the four after it.

usage: python warm_check_proof.py raw|readable WS OUT.json [HEADERS_SNAPSHOT]   (env N: N functions, evenly spaced)
DP_TREE names the DP tree (default: this file's tree). Nothing is written in WS but the check's own build folders.
"""
import json
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

TREE = pathlib.Path(os.environ.get("DP_TREE") or pathlib.Path(__file__).resolve().parents[3])


def variants(src):
    yield "as_is", src
    body = src.find("{")
    yield "no_build", src[:body + 1] + "\n    this does not build;\n" + src[body + 1:]
    m = re.compile(r"0x([0-9a-fA-F]+)").search(src, body)
    if m:
        yield "const_plus_1", src[:m.start()] + f"0x{int(m.group(1), 16) + 1:x}" + src[m.end():]


def run_check(box, env):
    t = time.perf_counter()
    p = subprocess.run([sys.executable, "check.py"], cwd=box, env=env, capture_output=True, timeout=900)
    return p.returncode, p.stdout + p.stderr, time.perf_counter() - t


def main(mode, ws, out, headers=None):
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(TREE))
    from core import agent, agent_cli, paths, readable_sandbox, warm_check  # noqa: F401
    if mode == "readable":
        from core import header_snapshot
        header_snapshot.use_dir(pathlib.Path(headers))
    addrs = (ws / "addrs.txt").read_text(encoding="utf-8").split() if (ws / "addrs.txt").exists() else \
        sorted(p.stem for p in (ws / "matched").glob("*.c"))
    n = int(os.environ.get("N") or 0)  # N: that many of them, evenly spaced
    if n and len(addrs) > n:
        addrs = addrs[::len(addrs) // n][:n]
    cfg = paths.config()
    env = agent_cli.agent_env(cfg)
    env.pop("DP_WARM_CHECK", None)
    res, times = {}, {"cold": [], "warm": []}
    first = True
    for a in addrs:
        src = (ws / ("readable/src" if mode == "readable" else "matched") / f"{a}.c").read_text(encoding="utf-8")
        for name, text in variants(src):
            box = pathlib.Path(tempfile.mkdtemp(prefix="wc_box_"))
            (box / "work.c").write_text(text, encoding="utf-8")
            launcher = (readable_sandbox.check_launcher(a, box / "work.c") if mode == "readable" else
                        agent.CHECK_LAUNCHER.format(tool=paths.TOOL / "decomp.py", project=paths.ROOT, addr=a,
                                                    work=box / "work.c"))
            (box / "check.py").write_text(launcher, encoding="utf-8")
            if first:  # one-time workspace state (the fsca table, the MSVC environment) made before any run counts
                run_check(box, env)
                first = False
            runs = []
            ses = warm_check.Session(env, box, paths.ROOT)  # what warm_check.session runs around a session
            try:
                for k, kind in enumerate(("cold", "warm", "cold", "warm", "cold")):
                    if kind == "warm":
                        ses.ready.wait(60)
                    rc, text_out, secs = run_check(box, {**env, **ses.env_vars()} if kind == "warm" else env)
                    runs.append((kind, rc, text_out, secs))
                    if k:
                        times[kind].append(secs)
                served = ses.served
            finally:
                ses.close()
            same = len({(rc, o) for _, rc, o, _ in runs}) == 1
            first_line = runs[0][2].decode("utf-8", "replace").strip().splitlines()[:1]
            res[f"{a} {name}"] = {"same": same, "warm_served": served, "rc": runs[0][1], "first_line": first_line[0] if first_line else "",
                                  "seconds": [round(x[3], 3) for x in runs]}
            print(a, name, "same" if same else "DIFFERENT", res[f"{a} {name}"]["first_line"][:70],
                  [round(x[3], 2) for x in runs], flush=True)
            shutil.rmtree(box, ignore_errors=True)
    summary = {"mode": mode, "checks": len(res), "all_same": all(r["same"] for r in res.values()),
               "all_warm_served": all(r["warm_served"] == 2 for r in res.values()),
               "verdict_lines": sorted({r["first_line"].split(" - ")[0] for r in res.values()}),
               "cold_median_s": round(statistics.median(times["cold"]), 3),
               "warm_median_s": round(statistics.median(times["warm"]), 3),
               "cold_runs": len(times["cold"]), "warm_runs": len(times["warm"])}
    pathlib.Path(out).write_text(json.dumps({"summary": summary, "checks": res}, indent=1) + "\n", encoding="utf-8",
                                 newline="\n")
    print(json.dumps(summary, indent=1))
    return 0 if summary["all_same"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], pathlib.Path(sys.argv[2]).resolve(), sys.argv[3], *sys.argv[4:]))
