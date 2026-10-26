"""Readable stage, one function: a locked-down agent rewrites verified raw C as readable C; the readable judge decides.

Same sandbox discipline as agent.py (read-only inputs, work.c only, `python check.py` only, tamper check).
Results go to state/readable.json, separate from the main ledger the server owns. A pass is copied to
readable/src/<addr>.c. The sandbox (core/readable_sandbox) is lean: context.md holds every declaration and type the
function needs; index.txt lists the rest.
Everything here reads the run's validated header snapshot (core/header_snapshot), and the sandbox's check.py passes
it on. Each attempt and each pass records its hash (`headers`).
"""
import datetime
import shutil
import threading
import time

from . import agent, busylog, callee_drift, check, filelock, global_extents, header_snapshot, infra, paths, readable
from . import lean_proof, readable_lean, verdict_memo
from . import readable_check as rcheck
from .readable_context import context_md, global_hint, pool_constants  # noqa: F401 (refine.py coverage uses them)
from .readable_lean import own_pool  # noqa: F401
from .readable_sandbox import (CHECK_LAUNCHER, TASK, build_sandbox, check_launcher,  # noqa: F401 (batch 25: moved)
                               sandbox_decls, stub_c, tree_hashes)

LEDGER = paths.STATE / "readable.json"
_LOCK = threading.Lock()


def dump(led):
    """readable.json's text: one line per function (filelock.dumps_rows): an update 72 ms on Toy Commander, not 150."""
    return filelock.dumps_rows(led, None)



def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_ledger():
    return paths.read_json(LEDGER, {})


def update(addr, drop=(), **fields):
    """Merge fields into addr's row (and remove the keys in drop). Safe across processes (two refine runs, a tool):
    the read-modify-write holds the file lock of state/readable.json (core/filelock)."""
    def merge(led):
        row = led.setdefault(addr, {})
        row.update(fields)
        for k in drop:
            row.pop(k, None)
        return row

    with _LOCK:
        return filelock.update_json(LEDGER, merge, {}, dump=dump)[1]


# an adopted machine file's row says so (core/draft_run: readable_by machine); an agent then working on it is told
MACHINE_NOTE = ("\n## No agent has named this file yet\n\nIt is a machine readable file (core/rtx): its locals are "
                "n1, x1, ... and its numbers are as the code has them. Give them the names their meaning calls for "
                "(the headers' constants for the numbers) while you work on it.\n")
FILE_KEPT = ("readable", "stale")  # statuses that describe an existing readable/src file (set by a pass or verify)


def kept_status(addr, row):
    """The status an existing readable/src file keeps when a redo of it does not pass, or None (no file to keep)."""
    if (readable.SRC / f"{addr}.c").exists():
        st = row.get("redo_of") if row.get("status") == "running" else row.get("status")
        return st if st in FILE_KEPT else None
    return None


def redo_not_passed(addr, kept, outcome, note=None, attempts=()):
    """A redo of a function whose old readable/src file stays: the row keeps the old verdict (readable or stale), and
    records the redo in `redo_failed`. Never a status (failed, blocked, queued, error) that does not describe the file
    on disk. Returns the run log's status text."""
    update(addr, status=kept, finished=now(), drop=("redo_of",),
           redo_failed={"at": now(), "outcome": outcome, "note": (note or "")[:300], "attempts": list(attempts)})
    return f"{outcome} (redo; the old file stays {kept})"


class PassNotRecorded(RuntimeError):
    """An attempt passed and readable/src holds its file, but the ledger write of `readable` failed."""


def record_pass(addr, headers, tries=3):
    """Mark addr readable after its passing file was copied to readable/src. The write is tried `tries` times (each
    already retries a refused file for filelock.RETRY_S); still failing raises PassNotRecorded, which record_error
    answers with `readable` too, never `error` or the old verdict: the status must describe the file on disk."""
    for i in range(tries):
        try:
            update(addr, status="readable", finished=now(), headers=headers,
                   drop=("redo_of", "redo_failed", "stale_headers", "note"))
            return "readable"
        except (OSError, ValueError, filelock.LockTimeout) as e:
            if i == tries - 1:
                raise PassNotRecorded(f"passed, readable/src/{addr}.c written, but the ledger write failed: "
                                      f"{type(e).__name__}: {e}") from e
            time.sleep(1.0)


def record_error(addr, note, passed=False):
    """refine.py run's last resort for an exception: `error`, unless an old file keeps its verdict, or the exception
    came after a pass wrote readable/src (passed, PassNotRecorded): then `readable`. When even this write fails the row
    stays as it was (`running`) and the run log says so; `refine.py verify ADDR` sets the status from the file."""
    if passed:
        try:
            update(addr, status="readable", finished=now(), note=note[:300],
                   drop=("redo_of", "redo_failed", "stale_headers"))
            return f"readable (ledger written on the second try; {note})"
        except Exception as e:  # noqa: BLE001  (the run must go on; verify repairs the row)
            return f"readable on disk, ledger NOT written ({type(e).__name__}: {e}); run `refine.py verify {addr}`"
    kept = kept_status(addr, load_ledger().get(addr, {}))
    if kept:
        return redo_not_passed(addr, kept, "error", note)
    update(addr, status="error", note=note[:300])
    return f"error: {note}"


def format_for_agent(res):
    if res["stage"] == "lint":
        return "DEV FAIL - rule violations (fix these first):\n" + "\n".join(f"  - {e}" for e in res["lint"])
    text = check.format_for_agent(res)
    if res.get("out_pointers") and res.get("failing"):  # readable_outptr: how the mocked callee's output arrives
        text += (f"\nNOTE: {', '.join(res['out_pointers'])} fill(s) a local of yours through a pointer. The judge "
                 "puts the callee's output in that local at the call (an initializer such as `= {0}` is fine), "
                 "except in the bytes raw.c writes there before the call: write those as raw.c does. Pass the "
                 "local raw.c passes, of the pointee type, in that argument.")
    return text


def raw_missing(addr):
    """Why there is no verified raw C to rewrite or judge against, or None: no matched/ADDR.c (the function is not
    matched, or its matched file was moved away, e.g. to matched_stale/), or no harness meta. Such a function is
    reported as "raw not matched" and skipped, never a crash."""
    if not (paths.MATCHED / f"{addr}.c").exists():
        return f"no matched/{addr}.c (raw not matched)"
    if not paths.read_json(paths.HARNESS / addr / "meta.json"):
        return f"no harness/{addr}/meta.json (raw not matched)"
    return None


def harness_stale(cfg, addr, on_pass=None, memo=None):
    """Why the function's harness cannot judge it, or None: the verified raw C (matched/) must still pass its own
    harness with check.py. One made by an older oracle fails it too, so a readable rewrite could never pass (and
    agents would be paid to chase it): re-prepare the harness (decomp.py prepare ADDR, servers stopped).
    on_pass(result): called with check.run_check's result when it passes (reprepare_stale --verdicts).
    memo (core/verdict_memo.RawPass, the readable stage's): a PASS kept on the very inputs the judge would read now
    is taken without judging again, and a new PASS is kept; never with on_pass, which needs the judge's result."""
    missing = raw_missing(addr)
    if missing:
        return missing
    src = paths.MATCHED / f"{addr}.c"
    memo = None if on_pass else memo
    key = memo.key_of(addr, src) if memo is not None else None  # before the judge reads its inputs
    if key is not None and memo.passed(addr, key) is not None:
        return None
    res = check.run_check(cfg, addr, str(src), build_dir=paths.BUILD / addr / "rd_rawcheck")
    infra.check(res.get("build"), res.get("run_error"), what="raw check")  # a refused file is no stale harness
    if res["verdict"] == "PASS":
        if on_pass:
            on_pass(res)
        if memo is not None:
            memo.record(addr, key, res.get("total"))
        return None
    return (f"the matched raw C fails this harness too ({res['stage']}, {res.get('failing')}/{res.get('total')} "
            f"vectors): re-prepare it with `decomp.py prepare {addr}`")


def stale_callee_records(meta):
    """Callees whose record in this harness has another signature than the callee's own harness (Ghidra's stale view
    of the callee, or the callee re-prepared since): readable C would be declared and placed by the record.
    [(addr, recorded, the callee's own)]."""
    return callee_drift.drift(meta, callee_drift.Signatures(paths.HARNESS))


_RAW_PASSED = {}  # addr -> the verdict_memo key of the inputs harness_stale_kept saw pass
_RAW_MEMO = {}  # state folder -> its verdict_memo.RawPass
_MEMO_LOCK = threading.Lock()


def raw_memo():
    """This workspace's kept raw-judge PASSes (core/verdict_memo.RawPass), one per process."""
    with _MEMO_LOCK:
        return _RAW_MEMO.setdefault(paths.STATE, verdict_memo.RawPass())


def harness_stale_kept(cfg, addr):
    """harness_stale for draft_run.first, whose cycle (fn_claim.cycle) goes on to refine(), which checks the same
    fact: a PASS is kept under the key of the inputs it was judged on (core/verdict_memo: every input run_check
    reads, hashed before judging), for raw_pass_kept."""
    key = verdict_memo.key(addr, paths.MATCHED / f"{addr}.c", verdict_memo.judge_stamp())
    stale = harness_stale(cfg, addr, memo=raw_memo())
    if stale is None:
        _RAW_PASSED[addr] = key
    return stale


def raw_pass_kept(addr):
    """True (once per kept PASS) when harness_stale_kept passed addr's raw C on the very inputs the judge would read
    now; anything changed since (the C, a harness or packet file, the judge's code, the config) judges again."""
    key = _RAW_PASSED.pop(addr, None)
    return key is not None and key == verdict_memo.key(addr, paths.MATCHED / f"{addr}.c", verdict_memo.judge_stamp())


def preflight(cfg, reg, addr, meta):
    """A tooling problem that no agent can fix, found before an agent is paid, or None: the registry refusing the
    prototypes or placements, headers that do not compile, a callee record older than the callee's harness. The
    check (full headers, as the agent's check.py builds) runs on a do-nothing stub: it must reach the compare."""
    stale = stale_callee_records(meta)
    if stale:
        return ("callee records that differ from the callees' own harness signatures (a prepare since batch 18 takes "
                f"them from the callees' harnesses: re-prepare this function, servers stopped: `decomp.py prepare "
                f"{addr}`, or tools/reprepare_stale.py for all of them): "
                + "; ".join(f"{a}: `{o}` now `{n}`" for a, o, n in stale))[:600]
    bdir = paths.BUILD / addr / "rd_preflight"
    bdir.mkdir(parents=True, exist_ok=True)
    try:  # the registry refuses this prototype, or one game.h needs for the callees or the sandbox
        (bdir.parent / "rd_preflight_stub.c").write_text(stub_c(reg, reg.proto(addr, meta)), encoding="utf-8")
        res = rcheck.check_readable(cfg, addr, str(bdir.parent / "rd_preflight_stub.c"), bdir, sets=("dev",), reg=reg,
                                    stub=True)
        sigs, data = sandbox_decls(reg, addr, meta)  # and the sandbox's lean game.h
        lean = readable_lean.headers_for(reg, meta, list(sigs) + data, sigs,
                                         (paths.MATCHED / f"{addr}.c").read_text(encoding="utf-8"),
                                         global_extents.for_process(cfg, reg),
                                         readable_lean.current_text(readable.SRC / f"{addr}.c"))
        readable.game_h(reg, addrs=list(sigs) + data + lean["functions"], raw_sigs=sigs, lean_globals=lean["globals"])
    except readable.RegistryError as e:
        return f"registry: {e}"
    proof = lean_proof.prove(cfg, lean.get("views"), bdir.parent / "rd_preflight_proof")  # context.md's projections
    if proof:
        return "; ".join(proof)[:600]
    if res["stage"] == "compare" and not res.get("run_error"):
        return None
    why = "; ".join((res.get("lint") or []) + (res.get("build") or []) + [res.get("run_error") or ""]).strip("; ")
    infra.check(why, what="preflight")  # an OS refusal of a file the tool wrote is not `blocked` (core/infra)
    return f"a do-nothing stub stops at {res['stage']}: {why}"[:600]


def refine(cfg, addr, model=None, max_attempts=3, log=print, stop_flag=None, effort=None, start=None):
    """Run up to max_attempts sessions on one function. Returns the final status. effort: the attempts' effort
    (refine.py run --effort); default config.json readable_effort (readable_effort_large when raw.c is over
    readable_large_bytes), else effort. start: (work.c, TASK.md note) the
    first attempt starts from (a machine draft: core/draft_run), else the do-nothing stub."""
    missing = raw_missing(addr)
    if missing:
        log(f"raw_not_matched: {missing}")
        update(addr, status="raw_not_matched", note=missing)
        return "raw_not_matched"
    reg = readable.Registry()
    meta = paths.read_json(paths.HARNESS / addr / "meta.json")
    stale = None if raw_pass_kept(addr) else harness_stale(cfg, addr, memo=raw_memo())  # the draft just judged them
    if stale:  # true of an old file too: verify records the same
        log(f"harness_stale: {stale}")
        update(addr, status="harness_stale", note=stale)
        return "harness_stale"
    old = load_ledger().get(addr, {})
    kept = kept_status(addr, old)  # a redo: the old file keeps its verdict unless an attempt passes
    with busylog.busy(busylog.slot(), "j"):
        blocked = preflight(cfg, reg, addr, meta)
    if blocked:
        log(f"blocked (tooling, no agent started): {blocked}")
        if kept:
            return redo_not_passed(addr, kept, "blocked", blocked)
        update(addr, status="blocked", note=blocked)
        return "blocked"
    runs = paths.RUNS / addr / "readable"
    runs.mkdir(parents=True, exist_ok=True)
    prev_work, previous = start or (None, "")
    if kept == "stale":
        prev_work = readable.SRC / f"{addr}.c"
        previous = ("\n## work.c passed before the headers or the test vectors changed\n\nNames, types or "
                    "prototypes were updated since, or the harness now reaches paths it did not (new hints, a re-matched "
                    "raw.c). Bring work.c up to date and fix what the check reports; keep the rest. Current result: "
                    f"{old.get('note', '')}\n")
    if kept and old.get("readable_by") == "machine":
        previous += MACHINE_NOTE
    rec = update(addr, status="running", started=now(), model=model or cfg["model"],
                 **({"redo_of": kept} if kept else {}))
    attempts = rec.get("attempts", [])
    first_new = len(attempts) + 1
    raw_path = paths.MATCHED / f"{addr}.c"  # batch 26: a large function's effort (missing: the attempt reports it)
    raw_bytes = raw_path.stat().st_size if raw_path.is_file() else None
    acfg = dict(cfg, model=model or cfg["model"], effort=agent.readable_effort(cfg, effort, raw_bytes),
                output_cap=cfg.get("readable_output_cap"), cache_ttl=cfg.get("readable_cache_ttl"))
    for _ in range(max_attempts):
        n = len(attempts) + 1
        box, task = build_sandbox(cfg, reg, addr, meta, prev_work, previous)
        before = tree_hashes(box)
        tdir = runs / f"attempt-{n:02d}"
        tdir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        with busylog.busy(busylog.slot(), "a"):
            info = agent.run_attempt(acfg, box, task, tdir / "transcript.jsonl", log, stop_flag or threading.Event())
        duration_s = round(time.time() - t0, 1)  # the agent session, as core/job records a raw attempt's
        work = tdir / "work.c"
        shutil.copyfile(box / "work.c", work)
        att = {"n": n, "model": acfg["model"], "headers": header_snapshot.active_hash(), "duration_s": duration_s,
               **info}
        if tree_hashes(box) != before:
            att.update(verdict="FAIL", tamper="a read-only sandbox file changed")
        else:
            with busylog.busy(busylog.slot(), "j"):
                res = rcheck.check_readable(cfg, addr, str(work), paths.BUILD / addr / "readable", reg=reg)
            att.update(verdict=res["verdict"], stage=res["stage"], lint=res.get("lint"), failing=res.get("failing"),
                       total=res.get("total"), first_diffs=res.get("first_diffs", [])[:2], run_error=res.get("run_error"),
                       build=(res.get("build") or [])[:4] if res["stage"] == "build" else None)
        attempts.append(att)
        update(addr, attempts=attempts)
        infra.check(att.get("build"), att.get("run_error"), what=f"attempt {n} judge")  # recorded; run_one retries
        if att["verdict"] == "PASS":
            readable.SRC.mkdir(parents=True, exist_ok=True)  # a fresh workspace has no readable/src yet
            shutil.copyfile(work, readable.SRC / f"{addr}.c")
            update(addr, readable_by="agent")
            return record_pass(addr, header_snapshot.active_hash())
        if info.get("api_error"):
            if kept:
                return redo_not_passed(addr, kept, "api_error", f"API error: {info['api_error']}",
                                       range(first_new, n + 1))
            update(addr, status="queued", note=f"API error: {info['api_error']}")
            return "queued"
        prev_work = work
        previous = f"\n## Previous attempt (#{n}) FAILED\n\nwork.c holds that attempt. Fix it.\n" \
                   f"Result: {att.get('stage')}, {att.get('failing')}/{att.get('total')} vectors differ. " \
                   f"{att.get('tamper') or ''}{'; '.join((att.get('lint') or []) + (att.get('build') or []))}\n"
    if kept:
        return redo_not_passed(addr, kept, "failed", f"{max_attempts} attempt(s) did not pass",
                               range(first_new, len(attempts) + 1))
    # The note says why this run failed: a note an earlier status left (a blocked preflight's) must not outlive it
    # (batch 20: 37 rows still carried a pre-batch-18 "callee records older" note after later runs).
    last = (attempts[-1].get("final_message") or "") if attempts else ""
    update(addr, status="failed", finished=now(), note=f"{max_attempts} attempt(s) did not pass; last: {last}"[:400])
    return "failed"
