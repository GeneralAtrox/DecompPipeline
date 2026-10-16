"""Re-prepare matched harnesses whose callee records differ from the callees' harnesses, until none is left; with
--stamps, first every matched harness whose oracle stamp is not today's.

Since batch 18 ghidra.prepare takes a caller's callee records from the callees' own harness signatures (Ghidra's
view of a callee can be stale: core/callee_drift), so re-preparing a drifted caller fixes its records; a caller whose
own signature changes leaves its callers stale in turn. This tool repeats scan, re-prepare and judge until a scan
finds nothing (core/cascade.py; each caller at most --per-fn times, the callees are never touched). Every re-prepared
function's matched raw C is judged on its new harness with check.py; a FAIL is listed (requeue it with
tools/requeue.py).

usage: python tools/reprepare_stale.py --project DIR [--dry] [--rounds 8] [--per-fn 2] [--stamps [--max N]] [--slots N]
  --dry      print the plan (the first cascade round; with --stamps the stamp pass), prepare nothing
  --stamps   first re-prepare the matched harnesses that are not current by their oracle stamp (no stamp, or one made
             from other oracle inputs: core/oracle_fresh, tools/fresh_oracle.py), config "ghidra_parallel" at once in
             Ghidra slots, at most --max (default 100) per run; resumable: a re-prepared harness has today's stamp,
             so the next run takes the rest. Run it under tools/bounded.py and repeat until it says none is left
  --slots N  Ghidra slots for this run instead of config "ghidra_parallel" (slot copies are made on first use)
Servers stopped: it refuses while the project's server answers (prepare uses the master Ghidra project and writes
harness/). Exit 0 when nothing is left stale and every judged raw C passes; 1 otherwise. tools/doctor.py lists the
same callee-record harnesses ("callee records that differ").
"""
import argparse
import pathlib
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import server_state, use_project  # noqa: E402


def stamp_pass(cfg, a, prepare, judge):
    """The --stamps pass; returns (problems found, matched harnesses still not current after it)."""
    from core import cascade, oracle_fresh
    stale = {x: k for x, (k, _) in oracle_fresh.scan(cfg, oracle_fresh.matched_functions()).items()
             if k in ("no stamp", "old stamp")}
    todo = sorted(stale, key=lambda x: int(x, 16))[:max(0, a.max)]
    workers = max(1, int(cfg.get("ghidra_parallel", 1)))
    print(f"stamps: {len(stale)} matched harness(es) not current by oracle stamp; this run re-prepares {len(todo)} "
          f"({workers} Ghidra slot(s) at once)" + (" [dry]" if a.dry else ""), flush=True)
    if a.dry or not todo:
        return 0, len(stale)
    out = cascade.reprepare_all(todo, prepare, judge, workers=workers, log=lambda m: print(m, flush=True))
    left = len(stale) - len(out["prepared"])
    print(f"stamps: {len(out['prepared'])} re-prepared, {len(out['errors'])} prepare error(s), {len(out['failed'])} "
          f"raw C failing the new harness" + (f" ({' '.join(sorted(out['failed']))})" if out["failed"] else "")
          + f"; {left} left for the next run", flush=True)
    return len(out["errors"]) + len(out["failed"]), left


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--per-fn", type=int, default=2)
    ap.add_argument("--stamps", action="store_true")
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--slots", type=int)
    a = ap.parse_args()
    ws = use_project(a.project)
    if not a.dry and server_state(ws, timeout=3) is not None:
        sys.exit("the project's server is running: stop it first (prepare needs the master Ghidra project)")
    from core import cascade, ghidra, paths, refine
    cfg = paths.config()
    if a.slots:
        cfg["ghidra_parallel"] = a.slots  # this run only
    led = paths.read_json(paths.LEDGER)["functions"]
    matched = {x for x, r in led.items() if r.get("status") == "matched"}

    def prepare(addr, slot_index=0):
        meta, _ = ghidra.prepare(cfg, addr, slot_index=slot_index)
        if meta.get("stale_callers"):
            print(f"  {addr}: its signature changed; {len(meta['stale_callers'])} caller(s) now stale", flush=True)

    def prepare_in_slot(addr):
        with ghidra.slot(cfg) as i:
            prepare(addr, i)

    def judge(x):  # an infrastructure error (core/infra) counts as a failing judgement here, as before batch 21
        try:
            return refine.harness_stale(cfg, x)
        except refine.infra.InfraError as e:
            return str(e)

    bad = left = 0
    if a.stamps:
        bad, left = stamp_pass(cfg, a, prepare_in_slot, judge)
    out = cascade.cascade(paths.HARNESS, matched, prepare, judge,
                          rounds=a.rounds, per_fn=a.per_fn, dry=a.dry, log=lambda m: print(m, flush=True))
    n = sum(out["prepared"].values())
    print(f"{n} prepare(s) of {len(out['prepared'])} function(s) in {out['rounds']} round(s); still stale: "
          f"{len(out['left'])}; stuck: {len(out['stuck'])}; prepare errors: {len(out['errors'])}; raw C failing its "
          f"new harness: {len(out['failed'])}" + (f" ({' '.join(sorted(out['failed']))})" if out["failed"] else ""))
    return 0 if a.dry or not (out["left"] or out["failed"] or out["errors"] or bad or left) else 1


if __name__ == "__main__":
    sys.exit(main())
