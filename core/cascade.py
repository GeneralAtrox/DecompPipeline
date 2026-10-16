"""Re-prepare harnesses until no matched function's callee records differ from its callees' harnesses.

ghidra.prepare takes a caller's callee records from the callees' own harnesses (core/callee_drift: the callee's
harness is authoritative, Ghidra's view of it can be stale), so re-preparing a drifted caller fixes its records. A
re-prepare can also change the caller's own signature, and then the harnesses that record it are stale in turn.
cascade() repeats until a scan finds nothing:
  1. scan the matched function harnesses (callee_drift.scan);
  2. re-prepare the drifted callers, each at most `per_fn` times (one still drifting after that is reported as
     stuck, so the loop always ends); the callees are never re-prepared: their harnesses are the reference;
  3. judge each re-prepared function's matched raw C on its new harness; a FAIL is reported (that function needs a
     requeue, which this module does not do).
prepare(addr) and judge(addr) are passed in (tools/reprepare_stale.py: ghidra.prepare, refine.harness_stale), so
the loop is tested without Ghidra. reprepare_all() is the --stamps pass before it: harnesses whose oracle stamp is
not today's (core/oracle_fresh), re-prepared in parallel Ghidra slots.
"""
import concurrent.futures

from . import callee_drift


def reprepare_all(addrs, prepare, judge, workers=1, log=print):
    """Re-prepare every addr (tools/reprepare_stale.py --stamps: harnesses whose oracle stamp is not today's), workers
    at a time (prepare takes a Ghidra slot itself), and judge each one's matched raw C on its new harness. Returns
    {"prepared": [addr], "failed": {addr: why}, "errors": {addr: why}}. A re-prepared harness carries today's stamp,
    so a run cut short (tools/bounded.py) leaves only the rest for the next run."""
    out = {"prepared": [], "failed": {}, "errors": {}}

    def one(a):
        try:
            prepare(a)
        except Exception as e:  # noqa: BLE001  (one bad prepare must not stop the others)
            return a, "error", f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"
        return a, "failed" if (why := judge(a)) else "ok", why

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for n, (a, kind, why) in enumerate(ex.map(one, addrs), 1):
            if kind == "error":
                out["errors"][a] = why
                log(f"  {a}: prepare failed: {why}")
                continue
            out["prepared"].append(a)
            if kind == "failed":
                out["failed"][a] = why
                log(f"  {a}: re-prepared; its matched raw C FAILS the new harness: {why}")
            if n % 25 == 0:
                log(f"  {n}/{len(addrs)} re-prepared")
    return out


def cascade(harness_dir, matched, prepare, judge, rounds=8, per_fn=2, dry=False, log=print):
    """Run the loop. Returns {"rounds", "prepared": {addr: times}, "left": {caller: drift items}, "stuck": [addr],
    "failed": {addr: why the raw C fails its new harness}, "errors": {addr: why its prepare failed}}."""
    matched = set(matched)
    prepared, failed, errors, stuck = {}, {}, {}, []
    out = {"rounds": 0, "prepared": prepared, "left": {}, "stuck": stuck, "failed": failed, "errors": errors}
    for r in range(1, rounds + 1):
        drift = callee_drift.scan(harness_dir, matched)
        out["left"] = drift
        if not drift:
            log(f"round {r}: no matched harness has callee records that differ from the callees' harnesses")
            return out
        todo = [a for a in sorted(drift) if prepared.get(a, 0) < per_fn]
        stuck[:] = [a for a in sorted(drift) if prepared.get(a, 0) >= per_fn]
        callees = sorted({c for items in drift.values() for c, _, _ in items})
        log(f"round {r}: {len(drift)} matched harness(es) with stale records of {len(callees)} callee(s) "
            f"({' '.join(callees[:12])}); re-prepare {len(todo)}" + (f": {' '.join(todo)}" if todo else "")
            + (f"; stuck after {per_fn}: {' '.join(stuck)}" if stuck else ""))
        if dry or not todo:
            return out
        out["rounds"] = r
        for a in todo:
            prepared[a] = prepared.get(a, 0) + 1
            try:
                prepare(a)
            except Exception as e:  # noqa: BLE001  (one bad prepare must not stop the cascade)
                errors[a] = f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"
                log(f"  {a}: prepare failed: {errors[a]}")
                continue
            errors.pop(a, None)
            why = judge(a)
            if why:
                failed[a] = why
                log(f"  {a}: re-prepared; its matched raw C FAILS the new harness: {why}")
            else:
                failed.pop(a, None)
    out["left"] = callee_drift.scan(harness_dir, matched)
    if out["left"]:
        log(f"stopped after {rounds} round(s): {len(out['left'])} matched harness(es) still stale: "
            + callee_drift.describe(out["left"], 6))
    return out
