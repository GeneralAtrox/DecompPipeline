"""Coverage gate report (core/proof.py, docs/PROOF_GATES.md): how many matched functions the original's block
coverage leaves below the gate (state matched_low_coverage), and the coverage distribution. Read-only.

usage: python tools/coverage_gate.py --project DIR [--project DIR ...] [--report] [--top N] [--list FILE] [--json FILE]
           [--min-pct P] [--allow N] [--min-blocks N]

  --report     the per-project figure and distribution (the default; it writes nothing)
  --top N      also list the N functions with the most unreached blocks (default 15)
  --list FILE  the matched_low_coverage functions, one per line, for @FILE (the reach pass: tools/call_coverage.py
               reach @FILE re-prepares them; or the watcher's hints, or requeue): the only file written, and only there
  --min-pct, --allow, --min-blocks  try other gate settings (this run only; config "coverage_gate" otherwise)
Exit 0.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import workspace  # noqa: E402
from core import proof  # noqa: E402  (pure: no core.paths)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", action="append", required=True)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--list")
    ap.add_argument("--json")
    ap.add_argument("--min-pct", type=float)
    ap.add_argument("--allow", type=int)
    ap.add_argument("--min-blocks", type=int)
    a = ap.parse_args()
    out, low_all = {}, []
    for p in a.project:
        ws = workspace(p)
        cfg = proof.project_cfg(ws)
        gate = proof.coverage_settings(cfg)
        for k, v in (("min_block_pct", a.min_pct), ("allow_uncovered_blocks", a.allow), ("min_blocks", a.min_blocks)):
            if v is not None:
                gate[k] = v
        cfg = dict(cfg, coverage_gate=gate)
        rows = proof.scan(ws, cfg=cfg)
        f = proof.figure(rows)
        dist = proof.distribution(rows)
        low = sorted((a_ for a_, r in rows.items() if r["state"] == proof.LOW),
                     key=lambda x: (-rows[x]["coverage"]["uncovered"], x))
        low_all += low
        share = round(100.0 * f[proof.LOW] / f["matched"], 1) if f["matched"] else 0.0
        print(f"== {ws}")
        print("  " + proof.summary_line(f, gate))
        print(f"  below the gate: {f[proof.LOW]}/{f['matched']} ({share}%)")
        print("  block coverage of the matched functions' originals: " + ", ".join(
            f"{k}%: {n}" for k, n in dist["pct"].items()))
        print("  unreached blocks per function: " + ", ".join(f"{k}: {n}" for k, n in dist["uncovered"].items()))
        for x in low[:a.top]:
            c = rows[x]["coverage"]
            print(f"    {x}  {c['blocks_hit']}/{c['blocks_total']} blocks ({c['pct']}%), {c['uncovered']} unreached, "
                  f"{c['allowed']} allowed")
        out[str(ws)] = {"gate": gate, "figure": f, "share_low_pct": share, "distribution": dist, "low": low}
    if a.list:
        pathlib.Path(a.list).write_text("".join(x + "\n" for x in low_all), encoding="utf-8")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
