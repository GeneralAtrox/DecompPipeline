"""Headless Ghidra: one-time project setup and per-function preparation."""
import math
import os
import shutil
import struct
import contextlib
import threading

from . import callee_drift, oracle_stamp, paths, sig_override, stack_uninit, toolcfg
from .packet_split import MAX_LINES, packet_files, split_packet  # noqa: F401 (re-exported)

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


def _headless(cfg, args, log_name, on_line=None, stop_flag=None, project=None):
    exe = os.path.join(toolcfg.need(cfg, "ghidra_home"), "support", "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
    cmd = [exe, str(project or paths.GHIDRA_PROJECT), paths.GHIDRA_NAME] + args
    rc, out, reason = paths.run_bounded(cmd, cfg["ghidra_timeout_s"], env=_env(cfg), on_line=on_line, stop_flag=stop_flag)
    paths.STATE.mkdir(parents=True, exist_ok=True)
    (paths.STATE / log_name).write_text(out, encoding="utf-8")
    if reason:
        raise RuntimeError(f"Ghidra {reason} (see {paths.show(paths.STATE / log_name)})")
    if "LockException" in out or "Unable to lock project" in out:
        raise RuntimeError(f"Ghidra project {project or paths.GHIDRA_PROJECT} is locked by another Ghidra process "
                           "(the GUI, or a CLI command such as decomp.py catalog). Close it and retry.")
    if rc != 0 or "SCRIPT ERROR" in out or "Script error" in out:
        tail = "\n".join(out.splitlines()[-25:])
        raise RuntimeError(f"Ghidra failed (rc={rc}):\n{tail}")
    return out


def write_fsca_table():
    """sin/cos for every fsca fraction, computed the way the patched SLEIGH computes the angle."""
    def f32(x):
        return struct.unpack("<f", struct.pack("<f", x))[0]

    two_pi = struct.unpack("<f", struct.pack("<I", 0x40C90FDB))[0]
    out = bytearray()
    for i in range(65536):
        angle = f32(f32(two_pi * float(i)) / 65536.0)
        out += struct.pack("<ff", f32(math.sin(angle)), f32(math.cos(angle)))
    paths.FSCA_TABLE.write_bytes(bytes(out))


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
    (state/orphan_functions.tsv). TcPrune: functions whose code runs into non-instructions are data seeded as code,
    removed (state/pruned_functions.tsv lists them).
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
            "-postScript", "TcPrune.java", "fix", str(paths.STATE / "pruned_functions.tsv"),
            "-postScript", "TcSwitch.java", "fix", str(paths.STATE / "switch_tables.tsv"),
            "-postScript", "TcCont.java", str(paths.STATE / "pr_continuations.tsv"), games]


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
    if cfg.get("fsca_table"):
        write_fsca_table()
    _headless(cfg, [
        "-import", str(binary), "-processor", cfg["ghidra_language"], "-loader", cfg["ghidra_loader"],
        "-loader-baseAddr", cfg["load_address"], "-scriptPath", str(paths.sync_scripts()),
        "-preScript", "TcSetup.java", *cfg.get("image_gaps", []), *(["-postScript", "TcSeed.java"] if cfg.get("seed_functions") else []),
        *_fixups(), "-postScript", "TcCatalog.java", str(paths.CATALOG), str(paths.CODE_RANGES), str(paths.FUNCTION_BODIES),
    ], "ghidra_setup.log")


def catalog(cfg):
    """Re-run TcPrune, TcSwitch and TcCatalog over the existing project (after changing the feature rules).
    Only with the server stopped: TcPrune and TcSwitch may change the master project."""
    _headless(cfg, [
        "-process", cfg["binary_path"].name, "-noanalysis", "-scriptPath", str(paths.sync_scripts()),
        *_fixups(), "-postScript", "TcCatalog.java", str(paths.CATALOG), str(paths.CODE_RANGES), str(paths.FUNCTION_BODIES),
    ], "ghidra_setup.log")


def seed(cfg):
    """Run TcSeed (function discovery: pointers, gaps after literal pools, the RAM mirror) on the existing project,
    then rewrite the catalog. Only with the server stopped: it changes the master project."""
    _headless(cfg, [
        "-process", cfg["binary_path"].name, "-scriptPath", str(paths.sync_scripts()), "-postScript", "TcSeed.java",
        *_fixups(), "-postScript", "TcCatalog.java", str(paths.CATALOG), str(paths.CODE_RANGES), str(paths.FUNCTION_BODIES),
    ], "ghidra_seed.log")


def graph(cfg):
    """Export calls / function pointers / globals / strings per function to state/graph.tsv."""
    _headless(cfg, [
        "-process", cfg["binary_path"].name, "-noanalysis", "-scriptPath", str(paths.sync_scripts()),
        "-postScript", "TcGraph.java", str(paths.STATE / "graph.tsv"),
    ], "ghidra_graph.log")


def export(cfg, addrs, out):
    """Disassembly + Ghidra's pseudo-C of any functions (library ones too) into out/<addr>/, for reading.

    Works on a private copy of a read-only slot (the master when there are none), so it never waits on the server."""
    src = paths.GHIDRA_SLOTS / "slot1"
    src = src if (src / (paths.GHIDRA_NAME + ".gpr")).exists() else paths.GHIDRA_PROJECT
    dst = paths.GHIDRA_SLOTS.parent / "export_copy"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
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
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(paths.GHIDRA_PROJECT, dst, ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
    stamp.write_text(_stamp(), encoding="utf-8")


@contextlib.contextmanager
def slot(cfg, stop_flag=None):
    """Reserve a Ghidra project slot; yields its index. Prefers the master so queued marks get written."""
    n = max(1, int(cfg.get("ghidra_parallel", 1)))
    with _SLOT_COND:
        while True:
            free = [i for i in range(n) if i not in _slots_busy]
            if free and not _draining[0]:
                break
            if stop_flag is not None and stop_flag.is_set():
                raise RuntimeError("stopped while waiting for a Ghidra slot")
            _SLOT_COND.wait(1.0)
        i = free[0]
        _slots_busy.add(i)
    try:
        if i and not _copy_is_fresh(i):
            with slot_master(stop_flag):
                _refresh_copy(i)
        yield i
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
        yield
    finally:
        with _SLOT_COND:
            _slots_busy.difference_update(range(n))
            _SLOT_COND.notify_all()


def _hexes(values):
    return " ".join(f"{int(str(v), 16):08x}" for v in values)


def write_facts(cfg):
    """The target's calling convention and memory map (target.json "abi", "memory") as TcFacts reads them, plus the
    project's code ranges: state/target_facts.txt (rewritten only when it changes). Returns its path, or "-"."""
    text = facts_body(cfg)
    if text is None:
        return "-"
    tag = cfg.get("_facts_tag")  # tools/regress_project.py: a fixture run with its own config, beside the others
    out = paths.STATE / (f"target_facts_{tag}.txt" if tag else "target_facts.txt")
    with _FACTS_LOCK:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            paths.STATE.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, out)
    return str(out)


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
    if cfg.get("coverage_inputs"):  # constant-guided inputs in about a third of the vectors (TcGuide)
        lines.append("coverage_inputs 1")
    if cfg.get("ghidra_timeout_s"):  # vector generation stops (the function is refused) before Ghidra's timeout
        deadline = max(60, int(cfg['ghidra_timeout_s']) - 150)
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
    return "\n".join(lines) + "\n"


def dev_vectors(cfg, addr):
    """The dev vector count for addr: state/vectors.json {addr: count} overrides config "vectors_dev" (a function
    whose edge case only the hidden set showed gets more dev vectors; the hidden set is unchanged)."""
    over = paths.read_json(paths.STATE / "vectors.json") or {}
    return int(over.get(addr, cfg["vectors_dev"]))


def prepare(cfg, addr, on_line=None, stop_flag=None, slot_index=0, staging=None):
    """Caller holds Ghidra slot `slot_index` (see slot()). Only slot 0 (the master) writes queued marks.

    staging: {"scripts": dir, "packet": dir, "harness": dir, "hints": file or None, "callees": dir} runs a trial with
    other oracle scripts into other folders, read-only and without marks (the watcher's sandboxed experiments).
    Callee records take the callees' own harness signatures from paths.HARNESS (staging: "callees", default the
    same; core/callee_drift.adopt)."""
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
    out = _headless(cfg, [
        "-process", paths.config()["binary_path"].name, "-noanalysis", "-scriptPath", str(scripts),
        *([] if slot_index == 0 and not staging else ["-readOnly"]),
        "-postScript", "TcPrepare.java", addr, str(packet), str(harness), str(ndev),
        str(cfg["vectors_hidden"]), str(seed), str(paths.FSCA_TABLE), marks, str(hints) if hints else "-", facts,
    ], f"ghidra_prepare{'_staging' if staging else ''}_slot{slot_index}.log", on_line=on_line, stop_flag=stop_flag,
        project=project)
    if marks != "-":
        inflight.unlink()  # only reached when Ghidra succeeded; otherwise they are retried next time
    split_packet(packet)
    meta = paths.read_json(harness / "meta.json")
    if meta is None:
        raise RuntimeError("TcPrepare produced no meta.json:\n" + "\n".join(out.splitlines()[-25:]))
    # callee records take the callees' own harness signatures (Ghidra's view of a callee can be stale: callee_drift)
    callee_drift.adopt(meta, callee_drift.Signatures((staging or {}).get("callees") or paths.HARNESS))
    # batch 27: a names entry's raw_ret (core/sig_override); frame words read before any write (core/stack_uninit)
    sig_override.apply_from_names(meta, addr, paths.ROOT / "readable" / "names")
    meta["uninit_stack"] = stack_uninit.scan_packet(packet, callees=meta.get("callees") or [])
    paths.write_json(harness / "meta.json", meta)
    meta["oracle_stamp"] = oracle_stamp.stamp(cfg, addr, scripts, facts, hints, ndev, cfg["vectors_hidden"])
    oracle_stamp.record(harness / "meta.json", meta["oracle_stamp"])
    if not staging and was and was != callee_drift.own_signature(meta):  # its callers' records of it are stale now
        meta["stale_callers"] = callee_drift.callers_left_stale(paths.HARNESS, addr, meta["signature"])
    return meta, paths.read_json(harness / "oracle_summary.json")


def queue_mark(addr, status, note):
    """Queue a bookmark + plate comment, written into the Ghidra project on the next prepare run."""
    paths.STATE.mkdir(parents=True, exist_ok=True)
    with _MARKS_LOCK, open(paths.MARKS, "a", encoding="utf-8") as f:
        f.write(f"{addr}\t{status}\t{note}\n")
