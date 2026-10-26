"""Regression check: translate and judge again, and show that every verdict and every byte of C is the same as the
recorded baseline (regress/<name>.json). Run after any translator change; --update records a new baseline (only
after the differences it prints are understood).

usage: python tools/regress.py --ws <workspace> [--all] [--jobs N] [--update] [--name skies]
"""
import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--name", default="skies")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()
    out = HERE / "out"
    cmd = [sys.executable, str(HERE / "tools" / "dp_judge.py"), "--ws", args.ws, "--out", str(out),
           "--jobs", str(args.jobs), "--tag", "regress"] + (["--all"] if args.all else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit("the judge run failed")
    ws = pathlib.Path(args.ws)
    res = out / f"results_translated_regress_{ws.name}{'_all' if args.all else ''}.json"
    rows = {x["addr"]: {"verdict": x["verdict"], "c_sha256": x.get("c_sha256"), "failing": x.get("failing")}
            for x in json.loads(res.read_text(encoding="utf-8"))}
    base_p = HERE / "regress" / f"{args.name}{'_all' if args.all else ''}.json"
    print(r.stdout.splitlines()[0] if r.stdout else "")
    if args.update or not base_p.exists():
        base_p.parent.mkdir(exist_ok=True)
        base_p.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline recorded: {base_p} ({len(rows)} functions)")
        return
    base = json.loads(base_p.read_text(encoding="utf-8"))
    diff = []
    for a in sorted(set(base) | set(rows)):
        b, n = base.get(a), rows.get(a)
        if b != n:
            diff.append(f"  {a}: baseline {b}  now {n}")
    same_v = sum(1 for a in rows if a in base and base[a]["verdict"] == rows[a]["verdict"])
    same_c = sum(1 for a in rows if a in base and base[a]["c_sha256"] == rows[a]["c_sha256"])
    print(f"{len(rows)} functions: {same_v} same verdict, {same_c} same C, {len(diff)} differ")
    print("\n".join(diff) if diff else "identical to the baseline")
    sys.exit(1 if diff else 0)


if __name__ == "__main__":
    main()
