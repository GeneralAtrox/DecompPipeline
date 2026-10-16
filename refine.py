"""Readable stage commands (separate from decomp.py, which the running server and agents use).

  python refine.py --project DIR check ADDR [--work F] [--agent]   judge a readable file (default readable/src/ADDR.c)
  python refine.py --project DIR run ADDR|SYSTEM|@FILE... [--workers N] [--model M] [--attempts K] [--redo]
                                       [--effort low|medium|high|xhigh|max|default] [--draft off|adopt|start]
                                                                    agents rewrite matched raw C as readable C; the
                                                                    machine draft first (core/draft_run; default
                                                                    config readable_draft, else start): a draft that
                                                                    passes the judge and reads as named C is adopted
                                                                    (source: draft), no agent; start: a failing draft
                                                                    is the agent's starting work.c
  python refine.py --project DIR draft ADDR|SYSTEM|@FILE... [--adopt] [--workers N]
                                                                    judge the machine drafts (runs/ADDR/readable/
                                                                    draft.c); --adopt: adopt the passing, readable ones
  python refine.py --project DIR adopt ADDR|SYSTEM|@FILE...          a function without a passing file: adopt its newest
                                                                    earlier attempt that passes the current judge
                                                                    (core/readopt; source: readopt attempt-NN)
  python refine.py --project DIR lookalike --from SRC_WS [--apply] [--judge | --adopt] [--near N]
                                                                    functions identical to another project's readable
                                                                    ones (core/lookalike_adopt): report and names
                                                                    proposal in state/lookalike.json; --apply writes the
                                                                    proposal; --judge measures; --adopt adopts PASSes
  python refine.py --project DIR header                              write readable/include/game.h (port header), then
                                                                    lint the headers (one owner per name) and build
                                                                    game.h at /W4 /WX; exit 1 on any problem
  python refine.py --project DIR status                              counts from state/readable.json
  python refine.py --project DIR coverage [ADDR|SYSTEM...]           callees and globals still without a name (a
                                                                    global counts as named inside a named global's
                                                                    declared extent only), named globals that
                                                                    overlap; no target: every system's functions
  python refine.py --project DIR verify [ADDR|SYSTEM|@FILE...] [--workers N] [--lean] [--no-sync] [--adopt]
                                                                    sync-names first (not with --no-sync or --lean),
                                                                    then re-judge readable/src against the current
                                                                    registry; a broken file is marked "stale" for
                                                                    `run --redo` (a non-PASS is judged again alone
                                                                    first; --lean: build from the lean sandbox headers,
                                                                    report only; --adopt: a STALE file's newest earlier
                                                                    attempt that passes is adopted instead, else the
                                                                    function goes on state/redo.txt for run --redo)
  python refine.py --project DIR library [--out DIR] [--workers N]  every readable/src file compiled as one program
                                                                    with the port's game.h (core/library): each file a
                                                                    translation unit at /W4 /WX (objects cached by
                                                                    content hash), archived with lib.exe /WX (a symbol
                                                                    defined twice), plus one unit of every file's
                                                                    external declarations (conflicting declarations);
                                                                    exit 1 on any diagnostic (default out build/library)
  python refine.py --project DIR sync-names [ADDR|SYSTEM|@FILE...] [--workers N] [--dry]
                                                                    replace raw fn_ADDR / g_ADDR names the registry has
                                                                    named since, in readable/src (core/sync_run); a file
                                                                    is written only when the judge passes it; exit 1
                                                                    when a rename is refused (the file is kept)
  python refine.py --project DIR varargs                             audit the variadic call convention at every call
                                                                    the harnesses record (core/varargs_audit); exit 1
                                                                    on a call site that does not fit it
  python refine.py --project DIR strings [ADDR|SYSTEM|@FILE...] [--scope] [--write]
                                                                    machine names for the read-only strings raw C
                                                                    passes that have no registry name (core/
                                                                    rodata_strings); dry run unless --write (adds each
                                                                    to its owner system's names file; never renames)
  python refine.py --project DIR requests [SYSTEM|ADDR|@FILE...] [--json]
                                                                    the design gaps agents reported for failed, stale
                                                                    and redo-failed functions (missing fields, names,
                                                                    prototypes), by system and by what they need
                                                                    (core/design_requests)
run first judges the matched raw C on the function's harness with check.py (verify does for a file that fails):
when the raw C fails too, the harness is stale (an older oracle made it), the function is marked "harness_stale"
and no agent runs; re-prepare it with `decomp.py prepare ADDR` (servers stopped) and run again. It then judges a
do-nothing stub (refine.preflight): a registry, header or callee-record problem marks the function "blocked" with
the reason, and no agent runs. A --redo (or a stale redo) that does not pass leaves the old readable/src file and
its verdict (readable or stale) in place and records the attempt in the row's `redo_failed`.
run, verify, sync-names and check build only against a validated, run-local snapshot of readable/include and readable/names
(core/header_snapshot): designers' edits during a run change nothing it compiles against. When the live headers do not
validate, run waits (readable_header_wait_s, default 900) and then uses the last good snapshot; verify, sync-names and check stop.
Targets (ADDR, SYSTEM, @FILE with CRLF or LF) name each function once; repeats are dropped and counted (core/targets).
One worker per function (core/fn_claim): run holds the function's claim for its whole cycle, verify, sync-names and a
manual check while they build or judge it; a rival skips it as busy after function_lock_wait_s (15) and writes nothing.
An OS refusal of a file the tool wrote is retried once, then `infra_error` (core/infra); `status` lists such rows.
"""
import argparse
import collections
import concurrent.futures
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("addr")
    c.add_argument("--work")
    c.add_argument("--agent", action="store_true", help="dev set only, agent-facing output")
    c.add_argument("--headers", help="a run's validated header snapshot (the agents' check.py passes it)")
    r = sub.add_parser("run")
    r.add_argument("targets", nargs="+")
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--model")
    r.add_argument("--attempts", type=int, default=3)
    r.add_argument("--redo", action="store_true", help="also functions already readable or failed")
    r.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max", "default"),
                   help="effort of these attempts (default: config readable_effort, else effort)")
    r.add_argument("--draft", choices=("off", "adopt", "start"),
                   help="the machine draft (core/draft): adopt one that passes; start: also the agent's start "
                        "(default: config readable_draft, else start)")
    sub.add_parser("header")
    sub.add_parser("status")
    cv = sub.add_parser("coverage")
    cv.add_argument("targets", nargs="*")
    v = sub.add_parser("verify")
    v.add_argument("targets", nargs="*")
    v.add_argument("--workers", type=int, default=4)
    v.add_argument("--lean", action="store_true", help="build against each file's lean sandbox headers; report only")
    v.add_argument("--no-sync", action="store_true", help="do not run sync-names first")
    v.add_argument("--adopt", action="store_true", help="a STALE file: adopt its newest attempt that passes")
    lb = sub.add_parser("library")
    lb.add_argument("--out", help="build folder (default: build/library of the project)")
    lb.add_argument("--workers", type=int, help="parallel compiles (default: config workers, at most max_workers)")
    sn = sub.add_parser("sync-names")
    sn.add_argument("targets", nargs="*")
    sn.add_argument("--workers", type=int, default=4)
    sn.add_argument("--dry", action="store_true", help="judge the renamed files, write nothing")
    sub.add_parser("varargs")
    ad = sub.add_parser("adopt")
    ad.add_argument("targets", nargs="+")
    dr = sub.add_parser("draft")
    dr.add_argument("targets", nargs="+")
    dr.add_argument("--adopt", action="store_true", help="adopt a passing, readable draft (no readable file yet)")
    dr.add_argument("--workers", type=int, default=4)
    lk = sub.add_parser("lookalike")
    lk.add_argument("--from", dest="source", required=True, help="the source project's workspace")
    lk.add_argument("--apply", action="store_true", help="write the proposal into readable/names and include/sys")
    lk.add_argument("--judge", action="store_true", help="judge the rewritten files against headers + proposal")
    lk.add_argument("--adopt", action="store_true", help="judge against the live headers, adopt strict PASSes")
    lk.add_argument("--near", type=int, default=4, help="near-identical: at most N differing words/constants")
    st = sub.add_parser("strings")
    st.add_argument("targets", nargs="*")
    st.add_argument("--scope", action="store_true", help="every in-scope function with matched raw C")
    st.add_argument("--write", action="store_true", help="add the names to readable/names/strings.json")
    rq = sub.add_parser("requests")
    rq.add_argument("targets", nargs="*")
    rq.add_argument("--json", action="store_true", help="the grouped requests as JSON")
    a = ap.parse_args()
    if a.project:
        os.environ["DECOMP_PROJECT"] = str(pathlib.Path(a.project).resolve())
    from core import header_snapshot, paths
    cfg = paths.config()
    agent = getattr(a, "agent", False)
    snap = None
    try:
        if getattr(a, "headers", None):  # an agent's check.py: the run's snapshot, validated by the run
            header_snapshot.use_dir(pathlib.Path(a.headers))
        elif a.cmd in ("check", "verify", "sync-names", "library", "adopt", "draft"):  # the current headers, validated: no wait
            snap = header_snapshot.acquire(cfg, log=(lambda m: print(m, flush=True)) if a.cmd != "check" else
                                           (lambda m: None), wait_s=0, fallback=False)
            header_snapshot.use(snap)
    except header_snapshot.HeaderError as e:
        print(f"{'DEV ' if agent else ''}FAIL stage=headers (the project's headers, not the readable file): {e}")
        sys.exit(0 if agent else 2)
    try:
        command(a, cfg)
    finally:
        if snap:
            snap.release()


def addresses(a, by_id):
    """The command's targets (ADDR, SYSTEM, @FILE: core/targets), each function once; a bad item stops the command."""
    from core import targets
    try:
        return targets.addresses(a.targets, by_id, log=lambda m: print(m, flush=True))
    except (OSError, ValueError) as e:
        print(f"targets: {e}", flush=True)
        sys.exit(2)


def command(a, cfg):
    from core import fn_claim, header_lint, header_snapshot, infra, paths, readable, refine, systems
    from core import readable_check as rcheck
    if a.cmd == "check":
        addr = paths.norm_addr(a.addr)
        work = a.work or str(readable.SRC / f"{addr}.c")
        sets = ("dev",) if a.agent else ("dev", "hidden")
        bdir = paths.BUILD / addr / ("rd_agent" if a.agent else "rd_manual")
        if a.agent or a.headers:  # the agent's check.py: inside its run's cycle, which holds the function's claim
            res = rcheck.check_readable(cfg, addr, work, bdir, sets=sets)
        else:
            try:
                with fn_claim.claim(addr, "check", fn_claim.wait_s(cfg)):
                    res = rcheck.check_readable(cfg, addr, work, bdir, sets=sets)
            except fn_claim.Busy as e:
                print(f"BUSY {e}")
                sys.exit(3)
        if a.agent:
            meta = paths.read_json(paths.HARNESS / addr / "meta.json")
            vecs = rcheck.check.parse_vectors(paths.HARNESS / addr / "vectors.txt")
            for fd in res.get("first_diffs", []):
                fd["inputs"] = rcheck.check._describe_inputs(meta, vecs[fd["vector"]])
            res.setdefault("vectors", {})
            print(refine.format_for_agent(res))
            sys.exit(0)  # a nonzero exit reads to the agent as a refused tool call, not as a FAIL to fix
        else:
            print(f"{res['verdict']}  stage={res['stage']}  failing={res.get('failing')}/{res.get('total')}")
            for e in res.get("lint", []) + res.get("build", []):
                print("  ", e)
            for fd in res.get("first_diffs", []):
                print(f"  vector {fd['vector']}: " + "; ".join(fd["diffs"][:4]))
        sys.exit(0 if res["verdict"] == "PASS" else 1)
    elif a.cmd == "header":  # write game.h, then the header lint and a game.h build at /W4 /WX (live files)
        try:
            reg = readable.Registry()
            (readable.INCLUDE / "game.h").write_text(readable.game_h(reg), encoding="utf-8")
            print(f"wrote {readable.INCLUDE / 'game.h'}: {len(reg.functions)} functions, {len(reg.globals)} globals")
            problems = header_lint.lint(readable.INCLUDE)
        except readable.RegistryError as e:
            problems = [f"names registry: {e}"]
        if not problems:
            probe = paths.BUILD / "_header_probe" / str(os.getpid())
            problems = header_lint.compile_game_h(cfg, readable.INCLUDE, probe)
            __import__("shutil").rmtree(probe, ignore_errors=True)
        for p in problems:
            print(f"HEADER PROBLEM: {p}")
        print(f"headers: {len(problems)} problem(s); readable runs wait for these headers, then use the last good "
              "snapshot" if problems else "headers OK: no name defined twice, game.h builds at /W4 /WX")
        sys.exit(1 if problems else 0)
    elif a.cmd == "status":
        led = refine.load_ledger()
        print(dict(collections.Counter(v.get("status") for v in led.values())),
              f"redo_failed (old file kept): {sum(1 for v in led.values() if v.get('redo_failed'))}")
        bad = infra.rows(led)  # core/infra: not verdicts; a plain `run` (a kept file: `run --redo`) tries them again
        print(f"infrastructure errors, not verdicts (run them again; `run --redo` for a kept file): {len(bad)}"
              + "".join(f"\n  {x}: {n[:160]}" for x, n in sorted(bad.items())))
    elif a.cmd == "coverage":  # core/coverage: names missing for these functions, overlapping named globals
        from core import coverage
        _, sys_list = systems.load()
        by_id = {s["id"]: s["functions"] for s in sys_list}
        targets = addresses(a, by_id) or sorted({x for s in sys_list for x in s["functions"]})
        coverage.report(cfg, targets)
    elif a.cmd == "varargs":  # core/varargs_audit: the variadic convention against every recorded call site
        from core import varargs_audit
        reg = readable.Registry()
        lines, bad = varargs_audit.report(varargs_audit.audit(reg, cfg), varargs_audit.variadic_functions(reg))
        print("\n".join(lines))
        sys.exit(1 if bad else 0)
    elif a.cmd == "requests":  # core/design_requests: the design gaps agents reported, by system and by need
        from core import design_requests
        _, sys_list = systems.load()
        groups = design_requests.collect(sys_list, addresses(a, {s["id"]: s["functions"] for s in sys_list}))
        print(__import__("json").dumps(groups, indent=1) if a.json else "\n".join(design_requests.report(groups)))
    elif a.cmd == "strings":  # core/rodata_strings: machine names for unnamed read-only strings (batch 26)
        from core import rodata_strings, scope
        _, sys_list = systems.load()
        todo = addresses(a, {s["id"]: s["functions"] for s in sys_list}) if a.targets else []
        if a.scope:
            todo += [x for x in scope.scope_functions(paths.ROOT) if x not in todo]
        if not todo:
            print("strings: no targets (ADDR|SYSTEM|@FILE, or --scope)")
            sys.exit(2)
        try:
            rodata_strings.command(cfg, todo, write=a.write, log=lambda m: print(m, flush=True))
        except readable.RegistryError as e:
            print(f"strings: the names registry: {e}")
            sys.exit(2)
    elif a.cmd == "library":  # core/library: the whole program at /W4 /WX, symbols defined twice, conflicting decls
        from core import library
        workers = max(1, min(a.workers or cfg.get("workers", 4), cfg.get("max_workers", 56)))
        ok, _ = library.build(cfg, pathlib.Path(a.out) if a.out else paths.BUILD / "library", workers=workers,
                              log=lambda m: print(m, flush=True))
        sys.exit(0 if ok else 1)
    elif a.cmd in ("verify", "sync-names"):
        from core import readable_verify, sync_run
        _, sys_list = systems.load()
        by_id = {s["id"]: s["functions"] for s in sys_list}
        files = sorted(p.stem for p in readable.SRC.glob("8c*.c"))
        if a.targets:
            want = set(addresses(a, by_id))
            files = [f for f in files if f in want]
        log = lambda m: print(m, flush=True)  # noqa: E731
        if a.cmd == "sync-names":
            out = sync_run.sync(cfg, files, workers=a.workers, dry=a.dry, log=log)
            sys.exit(1 if any(r["outcome"] == "refused" for r in out.values()) else 0)
        _, bad = readable_verify.verify(cfg, files, workers=a.workers, lean=a.lean, sync=not a.no_sync, log=log,
                                        adopt=a.adopt)
        sys.exit(1 if bad else 0)
    elif a.cmd == "adopt":  # core/readopt: the newest earlier attempt that passes the current judge
        from core import readopt
        _, sys_list = systems.load()
        out = readopt.adopt_command(cfg, addresses(a, {s["id"]: s["functions"] for s in sys_list}))
        print(f"adopted {sum(v.startswith('ADOPTED') for v in out.values())} of {len(out)}")
    elif a.cmd == "draft":  # core/draft_run: the machine draft of each function, judged (and adopted)
        from core import draft_run
        _, sys_list = systems.load()
        out = draft_run.command(cfg, addresses(a, {s["id"]: s["functions"] for s in sys_list}), adopt_ok=a.adopt,
                                workers=a.workers, log=lambda m: print(m, flush=True))
        print(f"drafts: {dict(collections.Counter(v.split(':')[0][:40] for v in out.values()))}")
    elif a.cmd == "lookalike":  # core/lookalike_adopt: identical functions of another project, its readable files
        from core import lookalike_adopt
        res = lookalike_adopt.run(cfg, pathlib.Path(a.source).resolve(), apply=a.apply, judge=a.judge,
                                  adopt=a.adopt, near_max=a.near, log=lambda m: print(m, flush=True))
        got = collections.Counter(v["outcome"] for v in res.get("judged", {}).values())
        print(f"lookalike: {len(res['identical'])} identical, {len(res['near'])} near-identical; judged: "
              f"{dict(got)}; report: {paths.show(paths.STATE / 'lookalike.json')}")
        sys.exit(2 if res.get("headers_error") else 0)
    elif a.cmd == "run":
        _, sys_list = systems.load()
        by_id = {s["id"]: s["functions"] for s in sys_list}
        since = refine.now()  # a function another process finishes after this is not redone by this run
        main_led = paths.read_json(paths.LEDGER)["functions"]
        done = refine.load_ledger()
        todo, skipped = [], []
        for addr in addresses(a, by_id):  # each function once (core/targets); run_one claims it (core/fn_claim)
            if not (readable.SRC.parent / "names").exists():
                continue
            if main_led.get(addr, {}).get("status") != "matched" or refine.raw_missing(addr):
                skipped.append(addr)
                continue
            if not fn_claim.skip_reason(addr, done.get(addr, {}), a.redo):
                todo.append(addr)
        if skipped:
            print(f"RAW_NOT_MATCHED (skipped, no verified raw C): {' '.join(skipped)}", flush=True)
        print(f"{len(todo)} function(s) to rewrite", flush=True)
        if not todo:
            return
        try:  # every sandbox, preflight and check of this run reads only this snapshot
            snap = header_snapshot.acquire(cfg, log=lambda m: print(m, flush=True))
        except header_snapshot.HeaderError as e:
            print(f"HEADERS: no run: {e}", flush=True)
            sys.exit(2)
        header_snapshot.use(snap)
        try:
            run_all(a, cfg, todo, since)
        finally:
            snap.release()


def run_all(a, cfg, todo, since=None):
    """One worker per function: run_one holds the function's claim for its whole cycle (a function another process
    holds is skipped as busy, its row untouched) and retries an infrastructure error once (core/fn_claim)."""
    from core import fn_claim, refine
    lock = __import__("threading").Lock()

    def one(addr):
        def log(msg):
            with lock:
                print(f"[{addr}] {msg}", flush=True)
        try:
            return addr, fn_claim.run_one(cfg, addr, a.attempts, log, redo=a.redo, since=since, model=a.model,
                                          effort=a.effort, draft=a.draft or cfg.get("readable_draft", "start"))
        except Exception as e:  # one bad function must not stop the batch (an old file keeps its verdict)
            try:  # after a pass wrote readable/src (PassNotRecorded) the status is readable, never error
                return addr, refine.record_error(addr, str(e), passed=isinstance(e, refine.PassNotRecorded))
            except Exception as e2:  # the ledger itself cannot be written: the row stays; verify repairs it
                return addr, (f"error: {e}; the ledger write failed too ({type(e2).__name__}: {e2}); "
                              f"run `refine.py verify {addr}`")

    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for addr, status in ex.map(one, todo):
            print(f"== {addr}: {status}", flush=True)


if __name__ == "__main__":
    main()
