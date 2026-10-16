"""Fresh-oracle check: every matched harness carries today's oracle stamp; with --recount, every matched raw C passes.

usage: python tools/fresh_oracle.py --project DIR [--recount] [--workers N] [--list FILE]
  (no flag)   lists each matched function (main ledger, catalog) whose harness is not current, by kind: no harness,
              no stamp, an old stamp (oracle inputs changed: core/oracle_stamp), or callee records that differ from
              the callees' own harnesses (core/callee_drift)
  --recount   also re-judges every matched function's raw C (matched/ADDR.c) on its own harness with the raw judge
              (check.py: lint, /W4 /WX build, dev and hidden vectors), N in parallel (default config "workers"), and
              prints passed/total against the catalog's matched count
  --list      write the not-current addresses to FILE (one per line) for tools/reprepare_stale.py or requeue
Read-only on the project: today's stamp is computed in memory (core/oracle_fresh.Stamper), recount builds go to a
temporary folder. Exit 0 only when every harness is current (and, with --recount, every raw C passes).
Re-prepare the not-current harnesses (servers stopped; bounded, resumable):
  python tools/bounded.py 7200 python tools/reprepare_stale.py --project DIR --stamps --max 100
Run it under tools/bounded.py: python tools/bounded.py 3600 python tools/fresh_oracle.py --project DIR --recount
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import use_project  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--recount", action="store_true")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--list")
    a = ap.parse_args()
    use_project(a.project)
    from core import oracle_fresh, paths
    cfg = paths.config()
    workers = max(1, min(a.workers or cfg.get("workers", 4), cfg.get("max_workers", 56)))
    ok, facts = oracle_fresh.report(cfg, recount_too=a.recount, workers=workers, log=lambda m: print(m, flush=True))
    if a.list:
        stale = oracle_fresh.scan(cfg, oracle_fresh.matched_functions())
        pathlib.Path(a.list).write_text("".join(f"{x}  # {k}\n" for x, (k, _) in sorted(stale.items())),
                                        encoding="utf-8")
        print(f"wrote {len(stale)} address(es) to {a.list}")
    print(("PASS" if ok else "FAIL") + " fresh oracle")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
