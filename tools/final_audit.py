"""The goal's final audit in one bounded process: one PASS/FAIL line per check and a final verdict.

usage: python tools/bounded.py 14400 python tools/final_audit.py --project DIR [--recount] [--workers N] [--no-sync]
Checks, in order (core/final_audit.py):
  oracle      every matched harness carries today's oracle stamp (tools/fresh_oracle.py); --recount also re-judges
              every matched raw C on its harness and needs passed == the catalog's matched count
  headers     `refine.py header`'s lint and game.h build (on a run-local snapshot, which verify and library then use),
              and readable/include/game.h equal to what the names registry generates
  verify      `refine.py verify` over readable/src: N/N pass, N = the catalog's game functions minus the excluded
              hardware/system ones; 0 missing files, STALE, HARNESS_STALE, failed, FLAKY, RAW_NOT_MATCHED, errors
  coverage    `refine.py coverage` over every system: 0 functions, callees, globals without a name or prototype, 0
              overlaps, 0 unsized (excluded hw/sys functions are listed apart, never counted)
  calls       call-site coverage (tools/call_coverage.py): no matched function with a call site no vector reaches
              that the reach pass has not tried; the figure, and each unreached site with the pass's reason
  raw names   no readable file calls a named function by its raw fn_ name (batch 18's audit)
  library     `refine.py library` clean (every file compiled together with game.h at /W4 /WX)
  completeness  tools/completeness.py: every game-range byte explained, no stale known_runs.tsv entry
  consistency tools/doctor.py (core/doctor.check)
  excluded    every excluded hw/sys function has its intent note (project.json "intent_notes", default
              HARDWARE_FUNCTIONS.md) and no readable file
  extras      only for a project with project.json "extra_functions" (core/scope.py): the extra functions counted
              apart; INFO while some are open (not matched, or no readable file yet), FAIL when one's readable file
              does not pass; PASS when every one not excluded passes
  usage       INFO: tools/usage_report.py readable (tokens and attempts per readable function), appended in full
The verdict is PASS only when every check passes and --recount ran; without it, or while extra functions are open,
at best INCOMPLETE. Exit 0 on PASS.
Output: every line, with each check's details, in state/final_audit-<timestamp>.txt; stdout gets the check lines.
Writes: that file only, except what verify does today (sync-names renames raw names in readable/src and the
readable.json rows it judges), run-local header snapshots under state/readable_snapshots, and build output under
build/ (verify's builds, the library's object cache build/library); the recount builds in a temporary folder.
"""
import argparse
import datetime
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import use_project  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--recount", action="store_true", help="also the full raw recount (the goal's audit needs it)")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--no-sync", action="store_true", help="verify without sync-names first (writes no readable/src)")
    a = ap.parse_args()
    ws = use_project(a.project)
    from core import final_audit as fa
    from core import header_snapshot, paths
    cfg = paths.config()
    workers = max(1, min(a.workers or cfg.get("workers", 4), cfg.get("max_workers", 56)))
    out = fa.Out(lambda m: print(m, flush=True))
    t0 = time.time()
    when = datetime.datetime.now()
    out.detail(f"final audit of {ws} at {when:%Y-%m-%d %H:%M:%S}; workers {workers}; recount {a.recount}; "
               f"sync {not a.no_sync}")
    results = []

    def run(name, fn, *args):
        print(f"... {name}", flush=True)
        out.detail(f"== {name}")
        t = time.time()
        try:
            ok, text = fn(*args)
        except Exception as e:  # noqa: BLE001  (a crashing check is a FAIL, the others still run)
            ok, text = False, f"the check crashed: {type(e).__name__}: {e}"[:300]
        out.result(ok, name, f"{text} [{time.time() - t:.0f} s]")
        results.append((name, ok))

    run("oracle", fa.check_oracle, cfg, out, a.recount, workers)
    snap = None

    def headers():
        nonlocal snap
        ok, text, snap = fa.check_headers(cfg, out)
        return ok, text

    run("headers", headers)
    try:
        if snap:
            header_snapshot.use(snap)
            run("verify", fa.check_verify, cfg, ws, out, workers, not a.no_sync)
        else:
            out.result(False, "verify", "not run: the headers do not validate")
            results.append(("verify", False))
        run("coverage", fa.check_coverage, cfg, ws, out)
        run("calls", fa.check_calls, out)
        run("raw names", fa.check_raw_names, ws, out)
        if snap:
            run("library", fa.check_library, cfg, out, workers)
        else:
            out.result(False, "library", "not run: the headers do not validate")
            results.append(("library", False))
    finally:
        if snap:
            snap.release()
    run("completeness", fa.check_completeness, ws, out)
    run("consistency", fa.check_doctor, ws, out)
    run("excluded", fa.check_excluded, ws, out)
    if paths.scope().extras:
        run("extras", fa.check_extras, ws, out)
    run("usage", fa.usage, ws, out)
    failed = [n for n, ok in results if ok is False]
    extras_open = any(n == "extras" and ok is None for n, ok in results)
    verdict = "FAIL" if failed else ("PASS" if a.recount and not extras_open else "INCOMPLETE")
    line = (f"VERDICT {verdict}: {len([r for r in results if r[1] is True])} check(s) pass, {len(failed)} fail"
            + (f" ({', '.join(failed)})" if failed else "")
            + ("" if a.recount else "; the raw recount was not run (--recount)")
            + ("; extra functions are open (the extras check)" if extras_open else "")
            + f"; {(time.time() - t0) / 60:.1f} min")
    out.lines.append(line)
    print(line, flush=True)
    dest = paths.STATE / f"final_audit-{when:%Y%m%d-%H%M%S}.txt"
    dest.write_text("\n".join(out.lines) + "\n", encoding="utf-8")
    print(f"full output: {dest}", flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
