"""refine.py verify: re-judge readable/src against the current registry and headers.

A non-PASS is judged a second time on its own (one build, nothing else running in this process) before it is
recorded: a disagreement between the two judgements is reported as FLAKY and the file is not marked STALE for it
(the second, unloaded judgement is recorded). With lean=True the files are built against their lean sandbox
headers only (readable_lean): a report that changes nothing in the ledger.
refine.py verify runs against a validated snapshot of the current headers (core/header_snapshot), never a fallback,
so STALE means stale. A PASS records the snapshot hash (`headers`). A STALE row gets `stale_headers`, which says
whether the file last passed against older headers or against these same ones. That field is a report only.
Batch 21: each judgement holds the function's claim (core/fn_claim), and so does each ledger record, which is written
only while readable/src/ADDR.c is still the file judged (sha256). A function another process holds (a run rewriting
it) is reported BUSY and left alone; an infrastructure error twice (core/infra) is reported INFRA, not recorded.
Batch 23 (--adopt): before a file is recorded STALE, the function's earlier attempts are judged newest first
(core/readopt); the first strict PASS replaces the file (READOPTED, the old one backed up), else the function goes on
the redo list state/redo.txt (`refine.py run @state/redo.txt --redo`) and is recorded STALE as before.
"""
import concurrent.futures

from . import build_prune, fn_claim, header_snapshot, infra, paths, readable, refine
from . import readable_check as rcheck


def judge(cfg, reg, addr, lean=False, tag="rd_verify"):
    """The judgement of readable/src/ADDR.c under the function's claim (core/fn_claim), with `src_sha` (the file
    judged). BUSY: another process holds the function (a run rewriting it); INFRA: an infrastructure error twice
    (core/infra). Neither is recorded in the ledger."""
    missing = refine.raw_missing(addr)
    if missing:  # nothing verified to judge against: report and go on
        return {"verdict": "RAW_NOT_MATCHED", "note": missing}
    src = readable.SRC / f"{addr}.c"
    for n in (1, 2):
        try:
            with fn_claim.claim(addr, "verify", fn_claim.wait_s(cfg)):
                sha = paths.sha256_file(src)
                res = rcheck.check_readable(cfg, addr, str(src), paths.BUILD / addr / (tag + ("_lean" if lean else "")),
                                            reg=reg, lean=lean)
                build_prune.after_job(cfg, addr)  # under the claim: its build outputs
            if res["verdict"] != "PASS" and infra.is_infra(res.get("build"), res.get("run_error")):
                raise infra.InfraError(why(res))
            return dict(res, src_sha=sha)
        except fn_claim.Busy as e:
            return {"verdict": "BUSY", "note": str(e)}
        except Exception as e:  # one broken function must not stop the run
            note = f"{type(e).__name__}: {e}"[:300]
            if not infra.is_infra_exc(e):
                return {"verdict": "ERROR", "note": note}
            if n == 2:
                return {"verdict": "INFRA", "note": note}
    raise AssertionError("unreachable")


def why(res):
    return "; ".join((res.get("lint") or []) + (res.get("build") or []))[:300] or \
        f"{res.get('failing')}/{res.get('total')} vectors differ"


def record(cfg, addr, res, rows, cur, log, readopt_reg=None):
    """Write one final judgement to the ledger (the caller holds the claim). Returns (not a pass, stale stamp).
    readopt_reg (verify --adopt, core/readopt): a STALE file is first replaced by its newest earlier attempt that
    strictly passes (recorded readable, `source: readopt attempt-NN`); none passing puts it on state/redo.txt."""
    if res["verdict"] in ("RAW_NOT_MATCHED", "ERROR"):
        refine.update(addr, status=res["verdict"].lower(), note=res["note"])
        log(f"{res['verdict']} {addr}: {res['note']}")
        return True, None
    if res["verdict"] == "PASS":
        refine.update(addr, status="readable", verified=refine.now(), headers=cur,
                      drop=("stale_headers", "note"))  # an older status's note no longer describes it
        return False, None
    try:
        stale_harness = refine.harness_stale(cfg, addr)
    except infra.InfraError as e:  # the raw check could not build: say nothing about the file
        log(f"INFRA {addr}: raw check: {e} (not recorded)"[:400])
        return True, None
    except Exception as e:
        stale_harness = None
        log(f"ERROR {addr}: raw check: {type(e).__name__}: {e}"[:300])
    if stale_harness:  # not the file's fault: the raw C fails this harness too
        refine.update(addr, status="harness_stale", note=stale_harness)
        log(f"HARNESS_STALE {addr}: {stale_harness}")
        return True, None
    if readopt_reg is not None:  # batch 23: an earlier attempt that passes the current judge, before STALE
        from . import readopt
        got = readopt.readopt(cfg, readopt_reg, addr, log)
        if got:
            log(f"READOPTED {addr}: {got} passes the current judge (the stale file is backed up)")
            return False, None
        log(f"REDO {addr}: no earlier attempt passes; on {paths.show(readopt.REDO_LIST)} "
            f"({readopt.add_redo([addr])} listed)")
    stamp = header_snapshot.stamp_note(rows.get(addr, {}).get("headers"), cur)  # report only (item 3)
    refine.update(addr, status="stale", note=f"{res['stage']}: {why(res)}", stale_headers=stamp)
    log(f"STALE {addr}: {res['stage']}: {why(res)} [{stamp}]")
    return True, stamp


def verify(cfg, files, workers=4, lean=False, log=print, sync=False, adopt=False):
    """Judge files (addresses); returns {addr: final result}. Records the ledger unless lean. sync: run sync-names
    first (core/sync_run), so a name given since a file was written is a rename, never STALE; a synced file's
    passing judgement is its verdict. adopt: a STALE file's earlier attempts are tried first (core/readopt)."""
    from . import sync_run
    reg = readable.Registry()
    first = {}
    if sync and not lean:
        first = {a: r["result"] for a, r in sync_run.sync(cfg, files, workers=workers, reg=reg, log=log).items()
                 if r["outcome"] == "synced"}
    cur, rows = header_snapshot.active_hash(), refine.load_ledger()
    todo = [a for a in files if a not in first]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for addr, res in zip(todo, ex.map(lambda a: judge(cfg, reg, a, lean), todo)):
            first[addr] = res
    final, flaky = dict(first), []
    for addr in files:  # a second judgement, alone, of every file that did not pass
        res = first[addr]
        if res["verdict"] in ("PASS", "RAW_NOT_MATCHED", "ERROR", "BUSY", "INFRA"):
            continue
        again = judge(cfg, reg, addr, lean, tag="rd_verify_alone")
        if again["verdict"] != res["verdict"] or again.get("stage") != res.get("stage"):
            flaky.append(addr)
            log(f"FLAKY {addr}: in the parallel run {res['verdict']} ({res.get('stage')}: {why(res)}), "
                f"alone {again['verdict']} ({again.get('stage')}: {why(again) if again['verdict'] != 'PASS' else 'ok'})")
        final[addr] = again
    bad, stamps, busy = 0, [], []
    for addr in files:
        res = final[addr]
        if res["verdict"] in ("BUSY", "INFRA"):  # not judged: nothing is recorded
            if res["verdict"] == "BUSY":
                busy.append(addr)
            bad += res["verdict"] == "INFRA"
            log(f"{res['verdict']} {addr}: {res['note']} (not recorded)")
            continue
        if lean:
            bad += res["verdict"] != "PASS"
            if res["verdict"] != "PASS":
                log(f"{res['verdict']} {addr}: {res['note']}" if "note" in res else
                    f"LEAN_FAIL {addr}: {res['stage']}: {why(res)}")
            continue
        try:
            with fn_claim.claim(addr, "verify", fn_claim.wait_s(cfg)):
                src = readable.SRC / f"{addr}.c"
                if res.get("src_sha") and not (src.exists() and paths.sha256_file(src) == res["src_sha"]):
                    log(f"CHANGED {addr}: readable/src/{addr}.c was rewritten during the judgement (not recorded)")
                    continue
                failed, stamp = record(cfg, addr, res, rows, cur, log, reg if adopt and not lean else None)
        except fn_claim.Busy as e:
            busy.append(addr)
            log(f"BUSY {addr}: {e} (not recorded)")
            continue
        bad += failed
        stamps += [stamp] if stamp else []
    passed = sum(final[a]["verdict"] == "PASS" for a in files)
    log(f"{passed}/{len(files)} readable files pass" + (" (lean headers)" if lean else "")
        + (f"; {len(busy)} busy, not judged (another process holds them): {' '.join(busy)}" if busy else "")
        + (f"; {len(flaky)} judged differently alone: {' '.join(flaky)}" if flaky else "")
        + (f"; stale: {sum(s.startswith('written against older') for s in stamps)} written against older headers, "
           f"{sum(s.startswith('it passed against these same') for s in stamps)} against these same headers"
           if stamps else ""))
    raw, counts = sync_run.raw_report(reg, files)
    if raw:
        log(f"RAW NAMES: {len(raw)} readable file(s) still use raw names of named functions or globals: " + "; ".join(
            f"{a}: {' '.join(f'{r} ({n})' for r, n in sorted(m.items()))}" for a, m in sorted(raw.items())))
    log(sync_run.raw_names.unnamed_line(counts))
    return final, bad
