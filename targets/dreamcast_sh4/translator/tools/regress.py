"""Rerun DP's judge on every matched function of the Dreamcast games and compare each verdict with the saved one
(results/verdicts_<game>.json): a change of the translator must leave every PASS a PASS.

The judge runs as tools/dp_judge.py does (the game folders are only read), in parts, each a child process bounded
by --part-timeout (its whole process tree is killed past it). One call runs parts until --budget seconds are spent
and then stops; the next call goes on from the parts already done (out_regress/<game>/), so a long run goes in
bounded pieces. --fresh starts over.

With --classify, the functions that do not pass are judged twice more, to say why: with fmac rounded twice
(SH4_FMAC_TWO_ROUNDINGS: test vectors made before DP's single-rounding fmac) and DP's matched agent C (--baseline:
when that fails too, the vectors are stale). --save writes the verdicts (and the classes) as the new reference.

usage: python tools/regress.py [--games tc,ps2,spawn] [--budget 500] [--classify] [--save] [--fresh]
Exit status: 0 when every saved PASS still passes (and all parts are done), 1 when one does not, 2 when parts are
left for the next call.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parents[1]
DP = HERE.parents[2]  # the DecompPipeline checkout this translator is part of
sys.path.insert(0, str(DP))
from core import toolcfg  # noqa: E402
PROJECTS = pathlib.Path(toolcfg.load(DP).get("projects_dir") or DP.parent)  # config "projects_dir", as the scorecard
GAMES = {
    "tc": ("ToyCommander", str(PROJECTS / "DreamCast/ToyCommander/decomp"), 4),
    "ps2": ("PowerStone2", str(PROJECTS / "DreamCast/PowerStone2/decomp/main"), 8),
    "spawn": ("SpawnIntheDemonsHand", str(PROJECTS / "DreamCast/Spawn In the Demon's Hand/decomp/main"), 4),
}
CLASSES = {
    "fmac": "test vectors made with fmac rounded twice (before DP's single-rounding fmac): passes with two roundings",
    "stale": "DP's matched agent C fails these vectors too: the vectors are stale",
    "noharness": "matched, but the workspace has no harness for it",
    "novectors": "the harness has no test vectors (only meta.json)",
    "translator": "the translator's own failure",
}


def judge(ws, out, extra, timeout, env=None):
    """One dp_judge.py run as a child process, its process tree killed past timeout. Returns its exit code (None:
    killed)."""
    cmd = [sys.executable, str(HERE / "tools" / "dp_judge.py"), "--ws", ws, "--out", str(out)] + extra
    p = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.kill()
        p.wait()
        return None


def rows_of(paths):
    return {r["addr"]: r for x in paths for r in json.loads(pathlib.Path(x).read_text(encoding="utf-8"))}


def classify(name, ws, out, bad, timeout):
    """{addr: class} for the functions in bad (their rows)."""
    only = ",".join(sorted(bad))
    env = dict(os.environ, SH4_FMAC_TWO_ROUNDINGS="1")
    judge(ws, out / "fmac", ["--only", only], timeout, env)
    judge(ws, out / "agent", ["--only", only, "--baseline"], timeout)
    two = rows_of((out / "fmac").glob(f"results_translated_{name}.json"))
    agent = rows_of((out / "agent").glob(f"results_baseline_{name}.json"))
    cls = {}
    for a, r in bad.items():
        if r["verdict"] == "NOHARNESS":
            cls[a] = "noharness"
        elif r["verdict"] == "ERROR" and not (pathlib.Path(ws) / "harness" / a / "vectors.txt").exists():
            cls[a] = "novectors"
        elif two.get(a, {}).get("verdict") == "PASS":
            cls[a] = "fmac"
        elif agent.get(a, {}).get("verdict") not in (None, "PASS"):
            cls[a] = "stale"
        else:
            cls[a] = "translator"
    return cls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default="tc,ps2,spawn")
    ap.add_argument("--budget", type=float, default=500, help="seconds of judging in this call")
    ap.add_argument("--part-timeout", type=float, default=400)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--classify", action="store_true")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    t0, status = time.time(), 0
    for g in args.games.split(","):
        name, ws, parts = GAMES[g]
        out = HERE / "out_regress" / g
        if args.fresh and out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        for k in range(parts):
            res = out / f"results_translated_{name}_part{k}of{parts}.json"
            if res.exists():
                continue
            if time.time() - t0 > args.budget:
                break
            ts = time.time()
            rc = judge(ws, out, ["--part", f"{k}/{parts}", "--jobs", str(args.jobs)], args.part_timeout)
            print(f"{g} part {k}/{parts}: {'done' if res.exists() else f'no result (exit {rc})'} "
                  f"in {time.time() - ts:.0f}s", flush=True)
        done = [out / f"results_translated_{name}_part{k}of{parts}.json" for k in range(parts)]
        if not all(x.exists() for x in done):
            print(f"{g}: {sum(x.exists() for x in done)} of {parts} parts done; call again to go on")
            status = status or 2
            continue
        rows = rows_of(done)
        ref_path = HERE / "results" / f"verdicts_{g}.json"
        ref = json.loads(ref_path.read_text(encoding="utf-8")) if ref_path.exists() else None
        npass = sum(r["verdict"] == "PASS" for r in rows.values())
        print(f"{g}: {npass} of {len(rows)} pass")
        verdicts = {a: r["verdict"] for a, r in sorted(rows.items())}
        if ref is not None:
            lost = sorted(a for a, v in ref["verdicts"].items() if v == "PASS" and verdicts.get(a) != "PASS")
            won = sorted(a for a, v in verdicts.items() if v == "PASS" and ref["verdicts"].get(a) != "PASS")
            print(f"  against the reference ({ref['passes']} pass): {len(lost)} lost {lost[:20]}, "
                  f"{len(won)} gained {won[:20]}")
            if lost:
                status = 1
        classes = ref.get("classes", {}) if ref else {}
        if args.classify:
            bad = {a: r for a, r in rows.items() if r["verdict"] != "PASS"}
            classes = classify(name, ws, out, bad, args.part_timeout)
        if classes:
            from collections import Counter
            left = {a: c for a, c in classes.items() if verdicts.get(a) != "PASS"}
            print("  not passing, by cause:", dict(Counter(left.values())))
        if args.save:
            ref_path.parent.mkdir(exist_ok=True)
            ref_path.write_text(json.dumps({"game": name, "passes": npass, "functions": len(rows),
                                            "verdicts": verdicts,
                                            "classes": {a: classes[a] for a in sorted(classes)
                                                        if verdicts.get(a) != "PASS"},
                                            "class_meaning": CLASSES}, indent=1) + "\n", encoding="utf-8")
            print(f"  saved {ref_path.relative_to(HERE)}")
    print(f"{time.time() - t0:.0f}s")
    sys.exit(status)


if __name__ == "__main__":
    main()
