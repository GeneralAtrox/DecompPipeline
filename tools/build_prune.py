"""Prune a workspace's build/ by hand (core/build_prune.py): the compiler outputs of functions no job holds.

usage: python tools/build_prune.py --project DIR [--dry-run] [--age-h H] [--cap-gb G] [--min-age-min M] [--json FILE]

A sweep as the automatic one runs it (settings: config.json "build_prune", overridden here), without its interval.
--dry-run deletes nothing and reports what the sweep would free (functions the orchestrator holds are left out; a
claim held elsewhere is only seen by a real run). --age-h 0 --cap-gb 0 prunes every function older than --min-age-min.
"""
import argparse
import json
import pathlib
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--age-h", type=float)
    ap.add_argument("--cap-gb", type=float)
    ap.add_argument("--min-age-min", type=float)
    ap.add_argument("--json")
    a = ap.parse_args()
    use_project(a.project)
    from core import build_prune, paths
    cfg = paths.config()
    over = {k: v for k, v in (("age_h", a.age_h), ("cap_gb", a.cap_gb), ("min_age_min", a.min_age_min))
            if v is not None}
    cfg = dict(cfg, build_prune=dict(cfg.get("build_prune") or {}, **over))
    t0 = time.monotonic()
    rep = build_prune.sweep(cfg, dry_run=a.dry_run, log=print)
    rep["seconds"] = round(time.monotonic() - t0, 1)
    print(build_prune.report_text(rep))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
