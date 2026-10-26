"""Headless Ghidra: one-time project setup and per-function preparation."""
import os
import re
import shutil
import contextlib
import threading

from . import (callee_drift, compare, fsca, ghidra_broker, ghidra_server, hw_ops, oracle_stamp, paths, prepare_timing,
               sig_override, stack_uninit, strict_judge, target_hooks, toolcfg)
from .packet_split import MAX_LINES, packet_files, split_packet, strip_status  # noqa: F401 (re-exported)

# Ghidra locks a project while any process has it open, even with -readOnly. So parallel preparation uses
# slots: slot 0 is the master project (the only one that saves: bookmarks for matched/failed functions),
# slots 1.. are copies under ghidra_slots/, opened read-only. Copies are refreshed when the catalog changes.
_SLOT_COND = threading.Condition()
_slots_busy = set()
_draining = [0]  # all_slots() waiting: new reservations hold back so it is not starved
_MARKS_LOCK = threading.Lock()
_FACTS_LOCK = threading.Lock()


def _env(cfg):
    env = dict(os.environ)
    env["JAVA_HOME"] = toolcfg.need(cfg, "java_home")
    env["PATH"] = os.path.join(env["JAVA_HOME"], "bin") + os.pathsep + env["PATH"]
    return env


def whole_timeout(cfg):
    """Seconds for a whole-program Ghidra run (setup, catalog, seed, graph): "ghidra_setup_timeout_s", else at least an
    hour. "ghidra_timeout_s" bounds one function's prepare, far too short to analyse a large boot file."""
    return int(cfg.get("ghidra_setup_timeout_s") or max(3600, int(cfg["ghidra_timeout_s"])))


def _headless(cfg, args, log_name, on_line=None, stop_flag=None, project=None, timeout=None, clock=None):
    exe = os.path.join(toolcfg.need(cfg, "ghidra_home"), "support", "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
    cmd = [exe, str(project or paths.GHIDRA_PROJECT), paths.GHIDRA_NAME] + args
    # a machine-wide token, unless held (slot); on a served slot (no token) in the slot's prio, e.g. a staged prepare
    with ghidra_broker.ensure(cfg, ghidra_broker.current_prio(), log_name, stop_flag):
        rc, out, reason = paths.run_bounded(cmd, timeout or cfg["ghidra_timeout_s"], env=_env(cfg), on_line=on_line, stop_flag=stop_flag)
    if clock:  # core/prepare_timing: the process has exited
        clock.mark("exit")
    paths.STATE.mkdir(parents=True, exist_ok=True)
    (paths.STATE / log_name).write_text(out, encoding="utf-8")
    if reason:
        raise RuntimeError(f"Ghidra {reason} (see {paths.show(paths.STATE / log_name)})")
    if "LockException" in out or "Unable to lock project" in out:
        raise RuntimeError(f"Ghidra project {project or paths.GHIDRA_PROJECT} is locked by another Ghidra process "
                           "(the GUI, or a CLI command such as decomp.py catalog). Close it and retry.")
    if rc != 0 or "SCRIPT ERROR" in out or "Script error" in out:
        tail = "\n".join(out.splitlines()[-25:])
        raise RuntimeError(f"Ghidra failed (rc={rc}): {_cause(out)}\n{tail}")
    return out


def _cause(out):
    """Why a run failed, for the first line of its error: a script's exception (e.g. TcPrepare's table check: "fsca
    table ...: 524288 bytes, not 1048576"), else the first Java exception in the log."""
    lines = out.splitlines()
    for pick in (lambda x: "SCRIPT ERROR" in x or "Script error" in x,
                 lambda x: "Exception" in x and not x.lstrip().startswith("at ")):
        for line in lines:
            if pick(line):
                why = re.split("SCRIPT ERROR|Script error", line, maxsplit=1)[-1]
                why = why.replace("(HeadlessAnalyzer)", "").strip(" :")
                return (why or line.strip())[:400]
    return "no script error or exception in the log (its tail follows)"


def write_fsca_table():
    """FSCA's sin/cos per angle fraction as the hardware gives them, round to nearest then toward zero (core/fsca.py,
    from tools/fsca_rom.py). The angle -> fraction map stays the patched SLEIGH's (TcPrepare). Rewritten only when
    it is not the current file (paths.fsca_table); every prepare and judge checks it the same way."""
    paths.fsca_table()


def _fixups():
    """TcOverride: hand-checked corrections from project.json "catalog_overrides" ({"data"|"not_entry"|"entry":
    [addr, ...], "entry_tables": [{"addr", "count"}]}, written to state/catalog_overrides.txt). TcPools: literal
    pools decoded as functions are removed and the real code after them gets its own function
    (state/pool_functions.tsv). TcDead: never-decoded dead functions (undefined bytes at a clean function boundary
    that nothing refers to, decoding to a prologue, a balanced frame or a leaf) are disassembled and made functions
    (state/dead_functions.tsv). TcRuns: unreferenced runs of hand-written code in the game range with no such signal
    (whole spans between two functions that decode as routines ending in rts, plus their tail-jump stubs) become
    functions (state/run_functions.tsv). TcOrphans:
    functions for instructions in no function that are referenced from outside or start a prologue
    (state/orphan_functions.tsv). TcJumpIn: a function reached only by `jmp` from one function whose frame it pops
    (a jump-in continuation) joins that function (state/jumpin_functions.tsv). TcPrune: functions whose code runs into non-instructions, and literal-pool words that
    decode as code, are data seeded as code, removed (state/pruned_functions.tsv lists them).
    TcSwitch: switch cases (and the slots of a jump into an unrolled sled) Ghidra left outside their function become
    part of it (state/switch_tables.tsv lists every table dispatch found). TcCont: the code a callee returns into after the function set PR to it before a tail jump
    (a PR continuation) is decoded, and its part in no function joins the function's body (state/pr_continuations.tsv
    lists every PR redirect). All run on the master before every TcCatalog, so slot copies refresh after them."""
    over = paths.STATE / "catalog_overrides.txt"
    paths.STATE.mkdir(parents=True, exist_ok=True)
    rules = paths.PROJECT.get("catalog_overrides") or {}
    over.write_text("".join(f"{kind} {paths.norm_addr(a)}\n" for kind, addrs in rules.items()
                            if kind in ("data", "not_entry", "entry") for a in addrs)
                    + "".join(f"table {paths.norm_addr(t['addr'])} {int(t['count'])}\n"
                              for t in rules.get("entry_tables") or []), encoding="utf-8")
    games = ",".join(f"{lo:x}-{hi:x}" for lo, hi in paths.game_ranges(paths.config()))
    return ["-postScript", "TcOverride.java", str(over),
            "-postScript", "TcPools.java", "fix", str(paths.STATE / "pool_functions.tsv"), str(over),
            "-postScript", "TcDead.java", str(paths.STATE / "dead_functions.tsv"), str(over),
            "-postScript", "TcRuns.java", str(paths.STATE / "run_functions.tsv"), str(over),
            games,
            "-postScript", "TcOrphans.java", str(paths.STATE / "orphan_functions.tsv"), str(over),
            "-postScript", "TcJumpIn.java", str(paths.STATE / "jumpin_functions.tsv"), games, str(over),
            "-postScript", "TcPrune.java", "fix", str(paths.STATE / "pruned_functions.tsv"),
            "-postScript", "TcSwitch.java", "fix", str(paths.STATE / "switch_tables.tsv"),
            "-postScript", "TcCont.java", str(paths.STATE / "pr_continuations.tsv"), games]


def fixups(cfg):
    """The catalog fixup scripts (_fixups) for targets that use them (target.json "catalog_fixups", default on: the
    SH-4 literal-pool, dead-code and switch repairs; the PowerPC target has none)."""
    return _fixups() if cfg.get("catalog_fixups", True) else []


def import_args(cfg):
    """How setup imports the binary: target.json "ghidra_import_args" (a loader with its own options, e.g. the
    GameCube DOL loader), else a raw binary at "load_address" with "ghidra_loader"."""
    if cfg.get("ghidra_import_args"):
        return ["-processor", cfg["ghidra_language"], *cfg["ghidra_import_args"]]
    return ["-processor", cfg["ghidra_language"], "-loader", cfg["ghidra_loader"], "-loader-baseAddr", cfg["load_address"]]


def setup_script_args(cfg):
    """TcSetup's arguments: project.json "image_gaps" (SH-4 combined images), "fixed_regs" pins as NAME:VALUE
    (analyzeHeadless splits script arguments at "=") and "seed_entries", a file of function entries (one hex address
    a line, GameCube REL modules linked into the image) relative to the workspace, as @PATH."""
    pins = [f"{k}:{int(str(v), 16):x}" for k, v in (cfg.get("fixed_regs") or {}).items()]
    seeds = [f"@{(paths.ROOT / cfg['seed_entries']).resolve()}"] if cfg.get("seed_entries") else []
    return [*cfg.get("image_gaps", []), *pins, *seeds]


def setup(cfg, force=False):
    binary = cfg["binary_path"]
    if paths.sha256_file(binary) != cfg["binary_sha256"]:
        raise RuntimeError(f"{binary} does not match the expected SHA-256")
    gpr = paths.GHIDRA_PROJECT / (paths.GHIDRA_NAME + ".gpr")
    if gpr.exists():
        if not force:
            raise RuntimeError("Ghidra project already exists; use --force to rebuild it")
        gpr.unlink()
        shutil.rmtree(paths.GHIDRA_PROJECT / (paths.GHIDRA_NAME + ".rep"), ignore_errors=True)
    paths.GHIDRA_PROJECT.mkdir(parents=True, exist_ok=True)
    paths.STATE.mkdir(parents=True, exist_ok=True)
    ghidra_server.stop_all()
    if cfg.get("fsca_table"):
        write_fsca_table()
    _headless(cfg, [
        "-import", str(binary), *import_args(cfg), "-scriptPath", str(paths.sync_scripts()),
        "-preScript", "TcSetup.java", *setup_script_args(cfg), *(["-postScript", "TcSeed.java"] if cfg.get("seed_functions") else []),
        *fixups(cfg), "-postScript", "TcCatalog.java", str(paths.CATALOG), str(paths.CODE_RANGES), str(paths.FUNCTION_BODIES),
    ], "ghidra_setup.log", timeout=whole_timeout(cfg))


def catalog(cfg):
    """Re-run TcPrune, TcSwitch and TcCatalog over the existing project (after changing the feature rules).
    Only with the server stopped: TcPrune and TcSwitch may change the master project."""
    ghidra_server.stop_all()
    _headless(cfg, [
        "-process", cfg["binary_path"].name, "-noanalysis", "-scriptPath", str(paths.sync_scripts()),
        *fixups(cfg), "-postScript", "TcCatalog.java", str(paths.CATALOG), str(paths.CODE_RANGES), str(paths.FUNCTION_BODIES),
    ], "ghidra_setup.log", timeout=whole_timeout(cfg))


def seed(cfg):
    """Run TcSeed (function discovery: pointers, gaps after literal pools, the RAM mirror) on the existing project,
    then rewrite the catalog. Only with the server stopped: it changes the master project."""
    ghidra_server.stop_all()
    _headless(cfg, [
        "-process", cfg["binary_path"].name, "-scriptPath", str(paths.sync_scripts()), "-postScript", "TcSeed.java",
        *fixups(cfg), "-postScript", "TcCatalog.java", str(paths.CATALOG), str(paths.CODE_RANGES), str(paths.FUNCTION_BODIES),
    ], "ghidra_seed.log", timeout=whole_timeout(cfg))


def graph(cfg):
    """Export calls / function pointers / globals / strings per function to state/graph.tsv."""
    _headless(cfg, [
        "-process", cfg["binary_path"].name, "-noanalysis", "-scriptPath", str(paths.sync_scripts()),
        "-postScript", "TcGraph.java", str(paths.STATE / "graph.tsv"),
    ], "ghidra_graph.log", timeout=whole_timeout(cfg))


def export(cfg, addrs, out):
    """Disassembly + Ghidra's pseudo-C of any functions (library ones too) into out/<addr>/, for reading.

    Works on a private copy of a read-only slot (the master when there are none), so it never waits on the server.
    A copy of slot 1 is kept while slot 1 is the very same copy (its copied_from.txt, text and mtime: every refresh
    rewrites it): TcExport opens the copy -readOnly, and port/tools/sdk_ref.py exports 5 names a call. A copy of the
    master (prepares on slot 0 write marks into it) is made afresh every time."""
    src = paths.GHIDRA_SLOTS / "slot1"
    src = src if (src / (paths.GHIDRA_NAME + ".gpr")).exists() else paths.GHIDRA_PROJECT
    dst = paths.GHIDRA_SLOTS.parent / "export_copy"
    mark, made = dst / "export_of.txt", None
    if src != paths.GHIDRA_PROJECT and (src / "copied_from.txt").exists():
        made = f"{src}\n{(src / 'copied_from.txt').read_text(encoding='utf-8')}\n" \
               f"{(src / 'copied_from.txt').stat().st_mtime_ns}"
    if not (made and mark.exists() and mark.read_text(encoding="utf-8") == made
            and (dst / (paths.GHIDRA_NAME + ".gpr")).exists()):
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
        if made:
            mark.write_text(made, encoding="utf-8")
    for i in range(0, len(addrs), 200):  # keep one command line short
        _headless(cfg, ["-process", cfg["binary_path"].name, "-noanalysis", "-readOnly",
                        "-scriptPath", str(paths.sync_scripts()), "-postScript", "TcExport.java", str(out),
                        *addrs[i:i + 200]], "ghidra_export.log", project=dst)


def _stamp():
    """Changes whenever the master project's functions may have changed (setup/catalog rewrite the catalog)."""
    st = paths.CATALOG.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def _copy_is_fresh(i):
    stamp = paths.GHIDRA_SLOTS / f"slot{i}" / "copied_from.txt"
    return stamp.exists() and stamp.read_text(encoding="utf-8") == _stamp()


def _refresh_copy(i):
    """Make slot i an up-to-date copy of the master. Caller holds slot 0 (so the master is not being written)."""
    dst = paths.GHIDRA_SLOTS / f"slot{i}"
    if _copy_is_fresh(i):
        return
    stamp = dst / "copied_from.txt"
    ghidra_server.stop(i)  # its server holds the old copy open
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(paths.GHIDRA_PROJECT, dst, ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
    stamp.write_text(_stamp(), encoding="utf-8")


MARKS_BATCH = 50  # with slot servers on, queued marks this many deep send the next prepare to the master


def _marks_waiting():
    try:
        with open(paths.MARKS, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _order(cfg, free, warm):
    """free slots in the order slot() takes them. Without slot servers (config "ghidra_serve"), the master first,
    so queued marks get written. With them, a slot in warm (its server runs: no JVM start, project or program
    open), then the other copies (the first prepare starts its server), the master last; but the master first once
    MARKS_BATCH marks wait, so marks are written in batches instead of never."""
    if not ghidra_server.settings(cfg)["enabled"] or (0 in free and _marks_waiting() >= MARKS_BATCH):
        return free
    return sorted(free, key=lambda i: (i == 0, i not in warm, i))


@contextlib.contextmanager
def slot(cfg, stop_flag=None, prio="tool", first=0):
    """Reserve a Ghidra project slot; yields its index, in _order (the master first without slot servers). With
    config "ghidra_broker" on, also a machine-wide token, in prio's turn (core/ghidra_broker), except on a served slot:
    its server holds a Lease for all its requests (core/ghidra_server.run takes one when the server starts).
    first=1: a read-only copy only (core/ghidra_server serves those; the queued marks wait for a master prepare)."""
    n = max(1, int(cfg.get("ghidra_parallel", 1)))
    # taken before _SLOT_COND: up() waits on ghidra_server's lock, which a server's start or close holds
    warm = {i for i in range(1, n) if ghidra_server.up(i)} if ghidra_server.settings(cfg)["enabled"] else set()
    with _SLOT_COND:
        while True:
            free = [i for i in range(first, n) if i not in _slots_busy]
            if free and not _draining[0]:
                break
            if stop_flag is not None and stop_flag.is_set():
                raise RuntimeError("stopped while waiting for a Ghidra slot")
            _SLOT_COND.wait(1.0)
        i = _order(cfg, free, warm)[0]
        _slots_busy.add(i)
    try:
        while True:
            # a copy is refreshed under the master (slot 0) before the token, never while holding one: the master's
            # own thread may be waiting in the queue for a token, and the tokens this process holds can fill the cap
            if i and not _copy_is_fresh(i):
                with slot_master(stop_flag):
                    _refresh_copy(i)
            if ghidra_server.serves(cfg, i, None):  # no token: the server's Lease stands for its JVM
                with ghidra_broker.prio_of(prio):
                    yield i
                return
            with ghidra_broker.token(cfg, prio, stop_flag=stop_flag):
                if i and not _copy_is_fresh(i):  # the master changed while this waited
                    with _SLOT_COND:
                        took = 0 not in _slots_busy
                        if took:
                            _slots_busy.add(0)
                    if not took:
                        continue  # the master is busy: give the token back and wait for it without one
                    try:
                        _refresh_copy(i)
                    finally:
                        with _SLOT_COND:
                            _slots_busy.discard(0)
                            _SLOT_COND.notify_all()
                yield i
                return
    finally:
        with _SLOT_COND:
            _slots_busy.discard(i)
            _SLOT_COND.notify_all()


@contextlib.contextmanager
def slot_master(stop_flag=None):
    """Exclusive use of the master project (slot 0)."""
    with _SLOT_COND:
        while 0 in _slots_busy:
            if stop_flag is not None and stop_flag.is_set():
                raise RuntimeError("stopped while waiting for the Ghidra master project")
            _SLOT_COND.wait(1.0)
        _slots_busy.add(0)
    try:
        yield
    finally:
        with _SLOT_COND:
            _slots_busy.discard(0)
            _SLOT_COND.notify_all()


@contextlib.contextmanager
def all_slots(cfg):
    """Every slot at once: nothing else is preparing (used to swap oracle scripts and recompile them)."""
    n = max(1, int(cfg.get("ghidra_parallel", 1)))
    with _SLOT_COND:
        _draining[0] += 1
        try:
            while any(i in _slots_busy for i in range(n)):
                _SLOT_COND.wait(1.0)
        finally:
            _draining[0] -= 1
        _slots_busy.update(range(n))
    try:
        ghidra_server.stop_all()  # the scripts are about to change: no server keeps the old ones loaded
        yield
    finally:
        with _SLOT_COND:
            _slots_busy.difference_update(range(n))
            _SLOT_COND.notify_all()


def try_slot(i):
    """Reserve slot i now if it is free (ghidra_server's idle reaper); True when reserved."""
    with _SLOT_COND:
        if i in _slots_busy or _draining[0]:
            return False
        _slots_busy.add(i)
        return True


def release_slot(i):
    with _SLOT_COND:
        _slots_busy.discard(i)
        _SLOT_COND.notify_all()


def _hexes(values):
    return " ".join(f"{int(str(v), 16):08x}" for v in values)


def write_facts(cfg):
    """The target's calling convention and memory map (target.json "abi", "memory") as TcFacts reads them, plus the
    project's code ranges: state/target_facts/<content_name>.txt. Returns its path, or "-".
    Written once, never changed: a checkout (or a regress fixture) with another config gets another file, so no
    checkout rewrites the facts another checkout's Ghidra is about to read. One shared target_facts.txt let any
    checkout's config reach every other's prepares (Spawn 2026-10-21 05:48-06:52: oracle_deadline_s 450 from some
    checkout, every config 900, changed live harness 8c04a900). The stamp hashes the file Ghidra read."""
    text = facts_body(cfg)
    if text is None:
        return "-"
    folder = paths.STATE / "target_facts"
    out = folder / f"{paths.content_name({'facts': text.encode('utf-8')})}.txt"
    with _FACTS_LOCK:
        if not out.exists():
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f".tmp-{out.stem}-{os.getpid()}-{threading.get_ident()}"
            tmp.write_text(text, encoding="utf-8")
            try:
                os.replace(tmp, out)
            except OSError:  # another process made it meanwhile (the same content) and a Ghidra has it open
                tmp.unlink(missing_ok=True)
        os.utime(out)
        paths.prune_unused(folder, out.name)
    return str(out)


def frame_lo(cfg):
    """The own frame bottom for cfg: entry r15 less "readable_frame_max" (readable_stack.frame_lo)."""
    from .compare import FRAME_MAX, SP, STACK_LO
    v = cfg.get("readable_frame_max", FRAME_MAX)
    return max(STACK_LO, SP - (int(v, 0) if isinstance(v, str) else int(v)))


def facts_body(cfg):
    """The facts file's text (write_facts writes it), or None when the target has no "abi"/"memory". Writes nothing:
    core/oracle_fresh computes today's oracle stamp from it without touching the project."""
    abi, mem = cfg.get("abi"), cfg.get("memory")
    if not abi or not mem:
        return None
    ret = abi["returns"]
    lines = [f"int_args {' '.join(abi['int_args'])}", f"float_args {' '.join(abi['float_args'])}",
             f"extra_inputs {' '.join(abi.get('extra_inputs_allowed', []))}",
             f"callee_saved {' '.join(abi['callee_saved'])}", f"return_int {ret['int']}", f"return_float {ret['float']}",
             f"return_int64_hi {ret['int64'][1]}", f"ram {_hexes(mem['ram'])}", f"entry_sp {_hexes([abi['entry_sp']])}",
             f"stack_lo {_hexes(mem['stack'][:1])}", f"return_sentinel {_hexes([abi['return_sentinel']])}",
             f"scratch {_hexes(mem['scratch'])}"]
    lines += [f"mmio {_hexes(r)}" for r in mem.get("mmio", [])]
    lines.append(f"code_ranges {paths.CODE_RANGES}")
    if cfg.get("fpscr_entry"):  # FPSCR at entry; "fpscr_model": model its RM and every field exactly (TcFpu)
        lines.append(f"fpscr_entry {_hexes([cfg['fpscr_entry']])}")
    if cfg.get("fpscr_model"):
        lines.append("fpscr_model 1")
    if cfg.get("param_summaries"):  # callee summaries decide the registers compared at calls (TcSum)
        lines.append("param_summaries 1")
    if cfg.get("readable_frame_max") is not None:  # the own frame bottom, the judge one (readable_stack.frame_lo)
        lines.append(f"frame_lo {frame_lo(cfg):08x}")
    if cfg.get("branch_coverage"):  # record branch directions (TcDirs; report only: the verdict's coverage grade)
        lines.append("branch_coverage 1")
    if cfg.get("targeted_vectors"):  # boundary-value vectors for branch directions no vector took (TcBound)
        lines.append("targeted_vectors 1")
    if cfg.get("coverage_inputs"):  # constant-guided inputs in about a third of the vectors (TcGuide)
        lines.append("coverage_inputs 1")
    if cfg.get("ghidra_timeout_s"):  # vector generation stops (the function is refused) before Ghidra's timeout
        deadline = max(60, int(cfg['ghidra_timeout_s']) - 150)
        if cfg.get("oracle_work_per_s"):  # budgets in work, never the clock (DC TcBudget): the same harness under load
            lines.append(f"oracle_work_per_s {int(cfg['oracle_work_per_s'])}")
            lines.append(f"oracle_budget_s {deadline}")
        else:
            lines.append(f"oracle_deadline_s {deadline}")
        # a first pass out of time gets this long more for a rescue with loop counts cut (TcCount); config
        # oracle_rescue_s overrides it (0: none, the refusal fixture of the deadline itself)
        rescue = min(90, (int(cfg['ghidra_timeout_s']) - deadline) * 3 // 5)
        lines.append(f"oracle_rescue_s {int(cfg.get('oracle_rescue_s', rescue))}")
    reach = cfg.get("reach_calls") or {}
    if int(reach.get("runs") or 0) > 0:  # the reach pass: vectors for call sites no vector reached (TcReach)
        lines.append(f"reach_calls {int(reach['runs'])} {int(reach.get('seconds') or 150)}")
    if cfg.get("min_valid_vectors"):  # a set with fewer valid vectors gets a rescue pass (TcVectors)
        lines.append(f"min_valid {int(cfg['min_valid_vectors'])}")
    if cfg.get("fpu_state"):  # mode-switching functions: every float register, XMTRX and FPSCR mode are state
        lines.append("fpu_state 1")
    lines += strict_judge.facts_lines(cfg)  # the stricter judge's flags and write facts (none while all are off)
    for name, value in (cfg.get("fixed_regs") or {}).items():  # registers the program keeps fixed (PowerPC r2/r13)
        lines.append(f"fixed_reg {name} {_hexes([value])}")
    for lo, hi in cfg.get("global_ranges") or []:  # where vectors may randomise globals (the writable sections)
        lines.append(f"global_range {_hexes([lo, hi])}")
    return "\n".join(lines) + "\n"


def dev_vectors(cfg, addr):
    """The dev vector count for addr: state/vectors.json {addr: count} overrides config "vectors_dev" (a function
    whose edge case only the hidden set showed gets more dev vectors; the hidden set is unchanged)."""
    over = paths.read_json(paths.STATE / "vectors.json") or {}
    return int(over.get(addr, cfg["vectors_dev"]))


def prepare(cfg, addr, on_line=None, stop_flag=None, slot_index=0, staging=None, timing=None):
    """Caller holds Ghidra slot `slot_index` (see slot()). Only slot 0 (the master) writes queued marks.

    staging: {"scripts": dir, "packet": dir, "harness": dir, "hints": file or None, "callees": dir} runs a trial with
    other oracle scripts into other folders, read-only and without marks (the watcher's sandboxed experiments).
    Callee records take the callees' own harness signatures from paths.HARNESS (staging: "callees", default the
    same; core/callee_drift.adopt).
    Each step is timed (core/prepare_timing: state/prepare_timing.jsonl; `timing`, a dict, receives the record)."""
    clock = prepare_timing.Clock(addr, slot_index, staging)
    try:
        res = _prepare(cfg, addr, clock.watch(on_line), stop_flag, slot_index, staging, clock)
    except BaseException:
        clock.finish(timing, ok=False)
        raise
    clock.finish(timing)
    return res


def _prepare(cfg, addr, on_line, stop_flag, slot_index, staging, clock):
    packet = paths.PACKETS / addr if not staging else staging["packet"]
    harness = paths.HARNESS / addr if not staging else staging["harness"]
    hints = paths.HINTS / f"{addr}.txt" if not staging else staging.get("hints")
    was = callee_drift.own_signature(paths.read_json(harness / "meta.json"))
    for d in (packet, harness):
        shutil.rmtree(d, ignore_errors=True)
    inflight = paths.MARKS.with_suffix(".inflight")
    if slot_index == 0 and not staging:
        with _MARKS_LOCK:  # take the queued marks; new ones keep appending to MARKS meanwhile
            if paths.MARKS.exists():
                with open(inflight, "a", encoding="utf-8") as dst:
                    dst.write(paths.MARKS.read_text(encoding="utf-8"))
                paths.MARKS.unlink()
    marks = str(inflight) if slot_index == 0 and not staging and inflight.exists() else "-"
    seed = int(addr, 16) & 0x7FFFFFFF
    project = paths.GHIDRA_PROJECT if slot_index == 0 else paths.GHIDRA_SLOTS / f"slot{slot_index}"
    scripts = staging["scripts"] if staging else paths.sync_scripts()
    facts, ndev, hints = write_facts(cfg), dev_vectors(cfg, addr), hints if hints and hints.exists() else None
    if cfg.get("fsca_table"):  # TcPrepare refuses any other table; a stale one failed every prepare (c346302)
        paths.fsca_table()  # (the PowerPC targets' TcPrepare never reads it: a 1 MB read and hash a prepare saved)
    tc_args = [addr, str(packet), str(harness), str(ndev), str(cfg["vectors_hidden"]), str(seed), str(paths.FSCA_TABLE),
               marks, str(hints) if hints else "-", facts, compare.spin_arg(cfg, addr)]
    tc_args.append(hw_ops.arg(cfg, addr))  # script arg 11: the hardware ops project.json "hw_ops" lists (TcHw)
    log_name = f"ghidra_prepare{'_staging' if staging else ''}_slot{slot_index}.log"
    if ghidra_server.serves(cfg, slot_index, staging):  # the slot's long-lived Ghidra (core/ghidra_server)
        out = ghidra_server.run(cfg, slot_index, project, scripts, _stamp(), tc_args, _env(cfg), on_line, stop_flag)
        clock.mark("exit")
        (paths.STATE / log_name).write_text(out, encoding="utf-8")
    else:
        out = _headless(cfg, [
            "-process", paths.config()["binary_path"].name, "-noanalysis", "-scriptPath", str(scripts),
            *([] if slot_index == 0 and not staging else ["-readOnly"]), "-postScript", "TcPrepare.java", *tc_args,
        ], log_name, on_line=on_line, stop_flag=stop_flag, project=project, clock=clock)
    if marks != "-":
        inflight.unlink()  # only reached when Ghidra succeeded; otherwise they are retried next time
    strip_status(packet)  # the status plate is the Ghidra project's, not the function's (core/packet_split)
    split_packet(packet)
    clock.mark("split_packet")
    meta = paths.read_json(harness / "meta.json")
    if meta is None:
        raise RuntimeError("TcPrepare produced no meta.json:\n" + "\n".join(out.splitlines()[-25:]))
    # callee records take the callees' own harness signatures (Ghidra's view of a callee can be stale: callee_drift)
    callee_drift.adopt(meta, callee_drift.Signatures((staging or {}).get("callees") or paths.HARNESS))
    clock.mark("adopt")
    # batch 27: a names entry's raw_ret (core/sig_override); frame words read before any write (core/stack_uninit)
    sig_override.apply_from_names(meta, addr, paths.ROOT / "readable" / "names")
    clock.mark("sig_override")
    uninit = target_hooks.get("uninit_stack")  # SH-4 asm by default (core/target_hooks)
    meta["uninit_stack"] = uninit(packet, meta) if uninit else stack_uninit.scan_packet(packet, callees=meta.get("callees") or [])
    clock.mark("stack_uninit")
    meta["oracle_stamp"] = oracle_stamp.stamp(cfg, addr, scripts, facts, hints, ndev, cfg["vectors_hidden"])
    paths.write_json(harness / "meta.json", meta)  # once, stamp included (oracle_stamp.record's bytes, one write fewer)
    clock.mark("oracle_stamp")
    if not staging and was and was != callee_drift.own_signature(meta):  # its callers' records of it are stale now
        meta["stale_callers"] = callee_drift.callers_left_stale(paths.HARNESS, addr, meta["signature"])
    return meta, paths.read_json(harness / "oracle_summary.json")


def queue_mark(addr, status, note):
    """Queue a bookmark + plate comment, written into the Ghidra project on the next prepare run."""
    paths.STATE.mkdir(parents=True, exist_ok=True)
    with _MARKS_LOCK, open(paths.MARKS, "a", encoding="utf-8") as f:
        f.write(f"{addr}\t{status}\t{note}\n")
