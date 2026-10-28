"""Parsing the harness and runner output, and the byte-exact comparison of one vector (used by core/check.py).

vectors.txt (TcVectors): V id set, R r4..r7, F, X, H fr12..fr15, L r0..r3, G r8..r14, Q fr0..fr15, C, M addr bytes,
E.
expected.txt / runner output: V, RET r0 fr0, RETX (the extra result word), HALT (an endless `bra .`; HALT SPIN: a wait
loop nothing in it ends, TcSpin), W, CALL (25, 36 or 57 fields), XF, FH, GH,
QH (fr0..fr15 and the FPSCR mode word), CJ, CS and XR (core/strict_judge), COV, FAULT, CODEREAD, CRASH, E.
"""
import json
import os
import pathlib
import re
import struct

from . import strict_judge


def _judge_memory():
    """target.json "judge_memory" of the workspace's target (DECOMP_PROJECT, else the current folder, as core/paths
    finds it): the image base, entry r15, stack window bottom, byte order and mirror rule of a target whose memory is
    not the Dreamcast's (Saturn SH-2). {} for the Dreamcast targets, outside a workspace, or without the section."""
    for c in (os.environ.get("DECOMP_PROJECT"), os.getcwd()):
        p = pathlib.Path(c) / "project.json" if c else None
        if p and p.exists():
            try:
                t = json.loads(p.read_text(encoding="utf-8"))["target"]
                tj = pathlib.Path(__file__).resolve().parents[1] / "targets" / t / "target.json"
                return json.loads(tj.read_text(encoding="utf-8")).get("judge_memory") or {}
            except (OSError, ValueError, KeyError, TypeError):
                return {}
    return {}


_JM = _judge_memory()
STACK_LO = int(_JM.get("stack_lo", "0x8CFF0000"), 16)
P4_OCA = (0xF4000000, 0xF5000000)  # P4 operand-cache address array: stores logged as W lines (tc_p4.c, TcOracle)
P4_QACR = (0xFF000038, 0xFF000040)  # QACR0/1, the store queues' area registers: logged the same way (TcExit.p4)
IMAGE_BASE = int(_JM.get("image_base", "0x8C010000"), 16)
SP = int(_JM.get("entry_sp", "0x8CFFE000"), 16)
STACK_HI = int(_JM.get("stack_hi", "0x8D000000"), 16)  # the end of the stack's RAM (the readable judge's stack window)
RAM = tuple(int(v, 16) for v in _JM.get("ram", ("0x8C000000", "0x8D000000")))  # main RAM: a number in it is an address
FLOAT_REGS = _JM.get("float_regs", True)  # FPU argument registers fr4..fr11; off (SH-2: no FPU): an f32 goes as an int
UNMAPPED = [tuple(int(v, 16) for v in r) for r in _JM.get("unmapped", ())]  # guest RAM the readable build cannot map
WORD = ">I" if _JM.get("endian") == "big" else "<I"  # a guest word's bytes (W lines, image words) as a number
MIRROR_RULE = _JM.get("word_mirror", True)  # same_word's 0x0c/0x8c RAM views (the SH-4's); off: exact words only
FRAME_MAX = 0x1000  # deepest own-frame constant below the entry r15 measured: TC 0x640, PS2 0x334, Spawn 0x6f0
FRAME_LO = SP - FRAME_MAX  # the own frame's default bottom; readable_stack.frame_lo() reads "readable_frame_max"


def judged(a, frame_lo=FRAME_LO, named=()):
    """Whether a written guest address is compared: anything below the original's own frame (frame_lo; the frame
    and what lies above it, stack arguments and the caller's frame, are its scratch), a word of a global the oracle
    names, or a P4 operand-cache store. [STACK_LO, frame_lo) is ordinary memory: Spawn keeps globals there (save
    data at 0x8cff9000), so a store there the C code leaves out is a miss. QACR0/1 stores are judged too."""
    return a < frame_lo or (a & ~3) in named or P4_OCA[0] <= a < P4_OCA[1] or P4_QACR[0] <= a < P4_QACR[1]


def named_words(meta):
    """The words of the globals the oracle's meta names (compared even inside the frame window), and of the hardware
    registers its listed hardware ops store to (meta "hw_ops" "writes": TcHw, logged as W lines on both sides)."""
    hw = ((meta or {}).get("hw_ops") or {}).get("writes") or ()
    return {int(g, 16) & ~3 for g in [*((meta or {}).get("globals") or ()), *hw] if isinstance(g, str)}


def _lines(path):
    """The file's lines, the file closed at once (no ResourceWarning under python -W error)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.readlines()


def parse_vectors(path):
    vecs, cur = {}, None
    for line in _lines(path):
        t = line.split()
        if not t:
            continue
        if t[0] == "V":
            cur = {"id": int(t[1]), "set": t[2], "patches": []}
            vecs[cur["id"]] = cur
        elif t[0] == "R":
            cur["r"] = [int(x, 16) for x in t[1:5]]
        elif t[0] == "H":  # fr12..fr15 at entry (functions with runtime state, meta fr_state)
            cur["fh"] = [int(x, 16) for x in t[1:5]]
        elif t[0] == "L":  # r0..r3 at entry (parameters there)
            cur["low"] = [int(x, 16) for x in t[1:5]]
        elif t[0] == "G":  # r8..r14 at entry (runtime state, meta r_state)
            cur["gs"] = [int(x, 16) for x in t[1:8]]
        elif t[0] == "Q":  # fr0..fr15 at entry (meta fpu_state: the whole FPU is state)
            cur["fq"] = [int(x, 16) for x in t[1:17]]
        elif t[0] == "M":
            cur["patches"].append((int(t[1], 16), bytes.fromhex(t[2]) if len(t) > 2 else b""))
    return vecs


# target, r4..r7, fr4..fr11, s0..s7, fr12..fr15, r0..r3, r8..r14; with meta fpu_state fr0..fr3, xf0..xf15, mode
CALL_FIELDS = 57


def parse_results(path):
    res, cur = {}, None
    if not os.path.exists(path):
        return res
    for line in _lines(path):
        t = line.split()
        if not t:
            continue
        k = t[0]
        if k == "V":
            cur = {"set": t[2], "writes": {}, "calls": [], "faults": [], "xf": [0] * 16, "cov": set(), "done": False}
            res[int(t[1])] = cur
        elif cur is None:
            continue
        elif k == "RET":
            cur["r0"], cur["fr0"] = int(t[1], 16), int(t[2], 16)
        elif k == "RETX":
            cur["retx"] = int(t[1], 16)
        elif k == "HALT":  # the run ended in an endless `bra .` (TcExit; tc_halt in C): it never returns
            cur["halt"] = "spin" if t[1:2] == ["SPIN"] else "loop"  # HALT SPIN: TcSpin; tc_spin in C
        elif k == "W":
            a, data = int(t[1], 16), bytes.fromhex(t[2]) if len(t) > 2 else b""
            for i, b in enumerate(data):
                cur["writes"][a + i] = b
        elif k == "CALL":
            call = [int(x, 16) for x in t[1:CALL_FIELDS + 1]]
            cur["calls"].append(call + [0] * (CALL_FIELDS - len(call)))  # older or narrower records: zero-padded
        elif k == "CA":  # the call before is through a function pointer: the argument registers the original set
            cur.setdefault("call_args", {})[len(cur["calls"]) - 1] = t[1:]  # since its entry or last call (TcTrack)
        elif k == "FN":  # the memory words a float store left a NaN in (TcTrack): the only NaN-tolerant words
            cur["float_nan"] = {int(x, 16) for x in t[1:]}
        elif k == "XF":
            cur["xf"] = [int(x, 16) for x in t[1:17]]
        elif k == "FH":  # fr12..fr15 at exit: only for vectors that set them at entry (line H)
            cur["fh"] = [int(x, 16) for x in t[1:5]]
        elif k == "GH":  # r8..r14 at exit: only for vectors that set them at entry (line G)
            cur["gs"] = [int(x, 16) for x in t[1:8]]
        elif k == "RO":  # r4..r7 at exit: only for a function whose callers read them (TcOut, meta r_exports)
            cur["ro"] = [int(x, 16) for x in t[1:5]]
        elif k == "QH":  # fr0..fr15 and the FPSCR mode word at exit: only for vectors with line Q
            cur["fq"] = [int(x, 16) for x in t[1:17]]
            cur["mode"] = int(t[17], 16) if len(t) > 17 else None
        elif k == "COV":
            cur["cov"] = {int(x) for x in t[1:]}
        elif k == "FAULT":
            cur["faults"].append(" ".join(t[1:]))
        elif k == "IM":  # SR.IMASK at exit (TcSr): only for functions that read or write SR
            cur["imask"] = int(t[1], 16)
        elif k == "GB":  # GBR at exit (TcHw): only for functions whose listed hardware ops move GBR
            cur["gbr"] = int(t[1], 16)
        elif k == "FS":  # FPSCR at exit (TcFpscr): only for functions whose original reads or writes FPSCR
            cur["fpscr"] = int(t[1], 16)
        elif k == "FC":  # FPSCR at each call, with FS
            cur["fpscr_calls"] = [int(x, 16) for x in t[1:]]
        elif k in ("CJ", "CS", "XR"):  # leftover junk, call snapshots, the exit frame (core/strict_judge)
            strict_judge.parse(cur, t)
        elif k == "CODEREAD":
            cur["coderead"] = t[1]
        elif k == "CRASH":
            cur["crash"] = t[1]
        elif k == "E":
            cur["done"] = True
    return res


def is_nan(bits):
    return (bits & 0x7F800000) == 0x7F800000 and (bits & 0x007FFFFF) != 0


def same_f32(a, b):
    return a == b or (is_nan(a) and is_nan(b))


def same_word(a, b):
    """Equal, or one main-RAM address through its two views: 0x0c000000..0x0cffffff (P0/U0) and 0x8c...... (P1), which
    the runtime and the port fold together (tc_rt_internal.h). Matched C writes a pool's 0x0c...... address as
    0x8c......, and the judge compares it as a value only where it leaves the function: a call argument, the result,
    a stored word, r8..r14 at exit (the mirror rule, docs/PROOF_GATES.md). tools/mirror_ptrs.py lists the functions
    and callees that compare such a pointer raw, for review."""
    return a == b or (MIRROR_RULE and (a ^ b) == 0x80000000 and (a >> 24) & 0x7F == 0x0C)


REG_INDEX = {f"r{i}": i - 3 for i in range(4, 8)} | {f"fr{i}": i + 1 for i in range(4, 12)} | {
    f"s{i}": 13 + i for i in range(8)} | {  # sN: the stack word @(4N,r15) at the call (a stack argument)
    f"fr{i}": 9 + i for i in range(12, 16)} | {  # fr12..fr15: runtime state a callee reads (sh4_fr)
    f"r{i}": 25 + i for i in range(4)} | {  # r0..r3: a callee taking them (args.rlo)
    f"r{i}": 21 + i for i in range(8, 15)} | {  # r8..r14: runtime state a callee reads (sh4_r)
    f"fr{i}": 36 + i for i in range(4)} | {  # meta fpu_state: fr0..fr3, xf0..xf15 and the mode word at every call
    f"xf{i}": 40 + i for i in range(16)} | {"fpscr": 56}
# Compared at every call of a function whose whole FPU is state (meta fpu_state), whatever the callee.
FPU_REGS = [f"fr{i}" for i in range(16)] + [f"xf{i}" for i in range(16)] + ["fpscr"]


def same_arg(reg, e, a):
    """A call argument's comparison: floats as f32 (NaN matches NaN), words and stack words by the mirror rule."""
    if reg.startswith(("fr", "xf")):
        return same_f32(e, a)
    return same_word(e, a) if reg[0] in "rs" else e == a


def arg_name(reg):
    """How the agent sets a compared call register."""
    if reg[0] == "s":
        return f"{reg} (stack word @({4 * int(reg[1:])},r15), args.s[{reg[1:]}])"
    if reg in ("fr12", "fr13", "fr14", "fr15"):
        return f"{reg} (vector register, sh4_fr({reg[2:]}))"
    if reg.startswith("xf"):
        return f"{reg} (XMTRX, sh4_xf({reg[2:]}))"
    if reg == "fpscr":
        return "the FPSCR mode bits (FR 0x200000, SZ 0x100000, PR 0x80000: sh4_frchg, sh4_fschg, sh4_fpchg)"
    n = int(reg[1:]) if reg[0] == "r" else -1
    if 0 <= n <= 3:
        return f"{reg} (args.rlo[{n}])"
    if 8 <= n <= 14:
        return f"{reg} (runtime state, sh4_r({n}))"
    return reg


class Memory:
    def __init__(self, image, vec):
        self.image, self.patches = image, vec["patches"]

    def initial(self, a):
        for base, data in reversed(self.patches):
            if base <= a < base + len(data):
                return data[a - base]
        if IMAGE_BASE <= a < IMAGE_BASE + len(self.image):
            return self.image[a - IMAGE_BASE]
        return 0


def _f(bits):
    return f"{struct.unpack('<f', struct.pack('<I', bits & 0xFFFFFFFF))[0]:.7g}"


def compare_return(meta, exp, act):
    ret, reg = meta["ret"], meta.get("ret_reg")
    if reg:  # a nonstandard result register (meta ret_reg): the runner reports it as RETX
        ex, ax = exp.get("retx"), act.get("retx")
        if ex != ax and (ex is None or ax is None or not same_word(ex, ax)):
            return [f"return value {reg}: expected 0x{exp.get('retx', 0):08x}, got 0x{act.get('retx', 0):08x}"]
        return []
    out = []
    r0_same = exp["r0"] == act.get("r0") or (ret != "u64" and same_word(exp["r0"], act.get("r0", 0)))
    if ret in ("u32", "s32", "u64") and not r0_same:
        out.append(f"return value r0{' (low word)' if ret == 'u64' else ''}: expected 0x{exp['r0']:08x}, "
                   f"got 0x{act.get('r0', 0):08x}")
    if ret == "u64" and exp.get("retx") != act.get("retx"):
        out.append(f"return value r1 (high word): expected 0x{exp.get('retx', 0):08x}, got 0x{act.get('retx', 0):08x}")
    if (ret == "f32" or meta.get("fr0_result")) and not same_f32(exp["fr0"], act.get("fr0", 0)):
        out.append(f"return value fr0: expected 0x{exp['fr0']:08x} ({_f(exp['fr0'])}), "
                   f"got 0x{act.get('fr0', 0):08x} ({_f(act.get('fr0', 0))})"
                   + ("" if ret == "f32" else f"; the original returns in fr0 but the signature returns {ret}: the "
                      "function needs a raw_ret f32 override (core/ret_reg.py, core/sig_override.py)"))
    return out


def mode_text(m):
    return "none" if m is None else f"0x{m:08x} (FR={m >> 21 & 1} SZ={m >> 20 & 1} PR={m >> 19 & 1})"


def compare_state(exp, act):
    """fr12..fr15 (FH), r8..r14 (GH) and the whole FPU (QH) at exit, for functions with runtime state (meta fr_state,
    r_state, fpu_state); SR's interrupt mask (IM) for a function that reads or writes SR (TcSr); GBR (GB) for one
    whose listed hardware ops move it (TcHw, project.json "hw_ops"); FPSCR at exit (FS) and at each call (FC) for one
    whose original reads or writes FPSCR (TcFpscr, meta fpscr_state)."""
    diffs = []
    if "fpscr" in exp:
        if exp["fpscr"] != act.get("fpscr"):
            got = "nothing" if act.get("fpscr") is None else f"0x{act['fpscr']:08x}"
            diffs.append(f"FPSCR at exit: expected 0x{exp['fpscr']:08x}, got {got}: write FPSCR with sh4_lds_fpscr "
                         "where the original does (lds ...,fpscr), reading it with sh4_sts_fpscr (sts fpscr,...)")
        want, got = exp.get("fpscr_calls", []), act.get("fpscr_calls", [])
        for i in range(max(len(want), len(got))):  # the first call whose FPSCR differs (a missing call: CALL says so)
            w, g = (want[i] if i < len(want) else None), (got[i] if i < len(got) else None)
            if w != g:
                diffs.append(f"FPSCR at call {i + 1}: expected {'no call' if w is None else f'0x{w:08x}'}, got "
                             f"{'no call' if g is None else f'0x{g:08x}'}: write FPSCR with sh4_lds_fpscr where the "
                             "original does, before or after that call as it does")
                break
    if "imask" in exp and exp["imask"] != act.get("imask"):
        got = act.get("imask", "nothing")
        diffs.append(f"SR interrupt mask (bits 4..7) at exit: expected {exp['imask']}, got {got}: "
                     "set SR with sh4_ldc_sr where the original does (ldc), reading it with sh4_stc_sr (stc)")
    if "gbr" in exp and exp["gbr"] != act.get("gbr"):
        got = "nothing" if act.get("gbr") is None else f"0x{act['gbr']:08x}"
        diffs.append(f"GBR at exit: expected 0x{exp['gbr']:08x}, got {got}: set GBR with sh4_ldc_gbr where the "
                     "original does (ldc ...,gbr), reading it with sh4_stc_gbr (stc gbr,...)")
    if "fq" in exp:
        got = act.get("fq")
        for i in range(16):
            if got is None or not same_f32(exp["fq"][i], got[i]):
                diffs.append(f"fr{i} (sh4_fr({i})) at exit: expected 0x{exp['fq'][i]:08x} ({_f(exp['fq'][i])}), "
                             + ("got nothing" if got is None else f"got 0x{got[i]:08x} ({_f(got[i])})"))
        if exp.get("mode") != act.get("mode"):
            diffs.append(f"FPSCR mode bits at exit: expected {mode_text(exp.get('mode'))}, got {mode_text(act.get('mode'))}")
    if "fh" in exp:
        got = act.get("fh")
        for i in range(4):
            if got is None or not same_f32(exp["fh"][i], got[i]):
                diffs.append(f"fr{12 + i} (vector register, sh4_fr({12 + i})) at exit: expected 0x{exp['fh'][i]:08x} "
                             f"({_f(exp['fh'][i])}), " + ("got nothing" if got is None else f"got 0x{got[i]:08x} ({_f(got[i])})"))
    if "gs" in exp:
        got = act.get("gs")
        for i in range(7):
            if got is None or not same_word(exp["gs"][i], got[i]):
                diffs.append(f"r{8 + i} (runtime state, sh4_r({8 + i})) at exit: expected 0x{exp['gs'][i]:08x}, "
                             + ("got nothing" if got is None else f"got 0x{got[i]:08x}"))
    return diffs


def compare_outputs(meta, exp, act):
    """r4..r7 at exit (RO) that the function's callers read after calling it (meta r_exports, TcOut): a hand-written
    leaf hands a result back there (Spawn 8c0602a0: r4 past the vertex stream). Its C sets them with sh4_set_r."""
    diffs = []
    if "ro" not in exp:
        return diffs
    got = act.get("ro")
    for r in (meta or {}).get("r_exports") or ():
        i = int(r[1:]) - 4
        if got is None or exp["ro"][i] != got[i]:
            diffs.append(f"{r} at exit (its callers read it: sh4_set_r({r[1:]}, ...) before returning): expected "
                         f"0x{exp['ro'][i]:08x}, " + ("got nothing" if got is None else f"got 0x{got[i]:08x}"))
    return diffs


def spin_wait(cfg, addr):
    """Whether TcSpin applies to the function at addr: project.json "spin_waits" ({address: evidence}, e.g.
    {"8c010080": "waits on vblank word 8cfe000c"}) names it. Opt-in, since a repeating state is not always a wait (TC
    8c04362c's ring-buffer search). An entry without its evidence is refused."""
    waits = cfg.get("spin_waits") or {}
    if not isinstance(waits, dict):
        raise ValueError('project.json "spin_waits" must map each address to its evidence')
    addr = addr.lower()
    if addr not in waits:
        return False
    if not isinstance(waits[addr], str) or not waits[addr].strip():
        raise ValueError(f'project.json "spin_waits" {addr}: no evidence (say what the loop waits on)')
    return True


def spin_arg(cfg, addr):
    """TcPrepare's script arg 10: "-" for a function spin_waits does not list, else "spin", and ":" with the words
    its evidence names (8 hex digits, without the P0..P4 bits): a wait loop that calls functions halts only when its
    exits read no other memory (TcSpin.proves). "8c0116c0": "... the vblank counter word 8cfe000c ..." gives
    "spin:0cfe000c" (several: "spin:0cfe000c_0cfe0054")."""
    if not spin_wait(cfg, addr):
        return "-"
    words = sorted({int(w, 16) & 0x1FFFFFFF for w in re.findall(r"(?<![0-9a-fA-F])(?:0x)?([0-9a-fA-F]{8})(?![0-9a-fA-F])",
                                                               cfg["spin_waits"][addr.lower()])})
    return "spin" + (":" + "_".join(f"{w:08x}" for w in words) if words else "")  # analyzeHeadless.bat splits at commas


def run_flag(cfg, addr):
    """(word, callee, n) when project.json "run_flags" lists the function at addr, else None: a loop that runs while a
    word the function sets is non-zero, and only a callee clears it (TcCalls). {"8c02d2a8": {"word": "8c5423d4",
    "callee": "8c02d930", "call": 3, "why": "<the store of 0 and the call path to it>"}}: in the test run and every
    runtime the Nth mocked call (from 1) to callee stores 0 into the word. Opt-in, one per function; an entry without
    its evidence, or with a bad address or count, is refused."""
    flags = cfg.get("run_flags") or {}
    if not isinstance(flags, dict):
        raise ValueError('project.json "run_flags" must map each function address to its flag')
    e = flags.get(addr.lower())
    if e is None:
        return None
    if not isinstance(e, dict) or not isinstance(e.get("why"), str) or not e["why"].strip():
        raise ValueError(f'project.json "run_flags" {addr}: no evidence ("why": which callee stores 0 there, and how)')
    try:
        word, callee = (int(e[k], 16) for k in ("word", "callee"))
    except (KeyError, TypeError, ValueError):
        raise ValueError(f'project.json "run_flags" {addr}: "word" and "callee" are addresses (hex)') from None
    n = e.get("call")
    if word % 4 or not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 64:
        raise ValueError(f'project.json "run_flags" {addr}: an aligned word and "call" 1..64')
    return word, callee, n


def run_flag_arg(cfg, addr):
    """TcPrepare's script arg 12: "-" for a function run_flags does not list, else "rf:WORD_CALLEE_N" (8 hex digits
    each, N decimal; no ",;= ": analyzeHeadless.bat splits arguments at them)."""
    f = run_flag(cfg, addr)
    return "-" if f is None else f"rf:{f[0]:08x}_{f[1]:08x}_{f[2]}"


def compare_halt(exp, act):
    """How the run ended: "loop" (an endless `bra .`, tc_halt), "spin" (a wait loop nothing in it ends, tc_spin) or
    None (it returned)."""
    if exp == act:
        return []
    if exp == "loop" and not act:
        return ["the original never returns here: it ends in an endless loop (`bra .`, or a callee that is one); "
                "call tc_halt() at that point (after the tc_call when the callee is the loop)"]
    if exp == "spin" and not act:
        return ["the original waits here forever on this input (a loop nothing in it changes: only an interrupt or the "
                "hardware ends it); keep the loop and its condition and call tc_spin() as its body (as the body's "
                "last statement when the loop calls functions: the run halts after their first pass)"]
    if act == "loop" and not exp:
        return ["your code called tc_halt(), but the original does not halt on this input"]
    if act == "spin" and not exp:
        return ["your code called tc_spin(), but the original does not wait forever on this input: call it only as "
                "the body of the loop the original waits in, with the loop's own condition"]
    if exp == "loop":
        return ["your code called tc_spin(), but the original ends here in an endless `bra .`: call tc_halt()"]
    return ["your code called tc_halt(), but the original waits here in a loop: keep the loop and call tc_spin() "
            "as its body"]


def compare_vector(meta, image, vec, exp, act, frame_lo=FRAME_LO, strict=None, report=None):
    """The differences of one vector (empty: it matches). frame_lo: the original's own frame bottom
    (readable_stack.frame_lo(); writes from there up are its scratch, unless meta names them). strict, report:
    core/strict_judge.compare's modes and the list its report-only differences go to."""
    if act is None or not act["done"]:
        return ["no result for this vector (the C code crashed, hung or timed out)"]
    diffs = []
    if "crash" in act:
        diffs.append(f"C code raised exception {act['crash']} (crash)")
    if act.get("coderead"):
        diffs.append(f"R8: read machine code at {act['coderead']}")
    for f in act["faults"]:
        diffs.append(f"invalid guest memory access: {f}")
    diffs += compare_halt(exp.get("halt"), act.get("halt"))
    if not exp.get("halt"):  # a halted run has no return value
        diffs += compare_return(meta, exp, act)
    mem = Memory(image, vec)
    named = named_words(meta)
    words = sorted({a & ~3 for a in set(exp["writes"]) | set(act["writes"]) if judged(a, frame_lo, named)})
    bad = []
    for w in words:
        ev = [exp["writes"].get(w + i, mem.initial(w + i)) for i in range(4)]
        av = [act["writes"].get(w + i, mem.initial(w + i)) for i in range(4)]
        if ev == av:
            continue
        eb, ab = struct.unpack(WORD, bytes(ev))[0], struct.unpack(WORD, bytes(av))[0]
        if is_nan(eb) and is_nan(ab) and w in exp.get("float_nan", ()):
            continue  # a float NaN the original stored: its payload is the FPU's (an integer word compares exactly)
        if same_word(eb, ab) and all(w + i in exp["writes"] and w + i in act["writes"] for i in range(4)):
            continue  # a whole stored word on both sides: a pointer through the other RAM view
        bad.append(f"memory 0x{w:08x}: expected {bytes(ev).hex()}, got {bytes(av).hex()}"
                   + ("" if any(w + i in act["writes"] for i in range(4)) else " (your code did not write here)")
                   + ("" if any(w + i in exp["writes"] for i in range(4)) else " (the original does not write here)"))
    if bad:
        diffs.extend(bad[:6])
        if len(bad) > 6:
            diffs.append(f"... and {len(bad) - 6} more differing memory words")
    callee_regs = {int(c["addr"], 16): c["regs"] for c in meta["callees"]}
    ec, ac = exp["calls"], act["calls"]
    for k in range(max(len(ec), len(ac))):
        if k >= len(ec):
            diffs.append(f"call #{k + 1}: the original makes only {len(ec)} call(s); yours calls 0x{ac[k][0]:08x}")
            break
        if k >= len(ac):
            diffs.append(f"call #{k + 1}: missing; the original calls 0x{ec[k][0]:08x} here (you made {len(ac)} call(s))")
            break
        if ec[k][0] != ac[k][0]:
            diffs.append(f"call #{k + 1}: expected target 0x{ec[k][0]:08x}, got 0x{ac[k][0]:08x}")
            break
        regs = callee_regs.get(ec[k][0], exp.get("call_args", {}).get(k, []))  # unlisted: the original's own (CA)
        if meta.get("fpu_state"):
            regs = regs + [r for r in FPU_REGS if r not in regs]
        for reg in regs:
            i = REG_INDEX[reg]
            alt = strict_judge.alternative(exp, k, i)
            if same_arg(reg, ec[k][i], ac[k][i]) or (alt is not None and same_arg(reg, alt, ac[k][i])):
                continue
            diffs.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: argument {arg_name(reg)} expected 0x{ec[k][i]:08x}"
                         + ("" if alt is None else f" (junk a callee left there: the original never set it) or "
                            f"0x{alt:08x} (its value before)") + f", got 0x{ac[k][i]:08x}")
    for i in range(16):
        if not same_f32(exp["xf"][i], act["xf"][i]):
            diffs.append(f"xf{i} (XMTRX): expected 0x{exp['xf'][i]:08x}, got 0x{act['xf'][i]:08x}")
    return diffs + compare_state(exp, act) + compare_outputs(meta, exp, act) + strict_judge.compare(meta, exp, act, REG_INDEX, strict_judge.XR_SH,
                                                                  same_word, strict, report, WORD)
