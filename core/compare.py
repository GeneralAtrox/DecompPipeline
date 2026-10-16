"""Parsing the harness and runner output, and the byte-exact comparison of one vector (used by core/check.py).

vectors.txt (TcVectors): V id set, R r4..r7, F, X, H fr12..fr15, L r0..r3, G r8..r14, Q fr0..fr15, C, M addr bytes,
E.
expected.txt / runner output: V, RET r0 fr0, RETX (the extra result word), W, CALL (25, 36 or 57 fields), XF, FH, GH,
QH (fr0..fr15 and the FPSCR mode word), COV, FAULT, CODEREAD, CRASH, E.
"""
import os
import struct

STACK_LO = 0x8CFF0000
P4_OCA = (0xF4000000, 0xF5000000)  # P4 operand-cache address array: stores logged as W lines (tc_p4.c, TcOracle)
IMAGE_BASE = 0x8C010000
SP = 0x8CFFE000


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
        elif k == "W":
            a, data = int(t[1], 16), bytes.fromhex(t[2]) if len(t) > 2 else b""
            for i, b in enumerate(data):
                cur["writes"][a + i] = b
        elif k == "CALL":
            call = [int(x, 16) for x in t[1:CALL_FIELDS + 1]]
            cur["calls"].append(call + [0] * (CALL_FIELDS - len(call)))  # older or narrower records: zero-padded
        elif k == "XF":
            cur["xf"] = [int(x, 16) for x in t[1:17]]
        elif k == "FH":  # fr12..fr15 at exit: only for vectors that set them at entry (line H)
            cur["fh"] = [int(x, 16) for x in t[1:5]]
        elif k == "GH":  # r8..r14 at exit: only for vectors that set them at entry (line G)
            cur["gs"] = [int(x, 16) for x in t[1:8]]
        elif k == "QH":  # fr0..fr15 and the FPSCR mode word at exit: only for vectors with line Q
            cur["fq"] = [int(x, 16) for x in t[1:17]]
            cur["mode"] = int(t[17], 16) if len(t) > 17 else None
        elif k == "COV":
            cur["cov"] = {int(x) for x in t[1:]}
        elif k == "FAULT":
            cur["faults"].append(" ".join(t[1:]))
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


REG_INDEX = {f"r{i}": i - 3 for i in range(4, 8)} | {f"fr{i}": i + 1 for i in range(4, 12)} | {
    f"s{i}": 13 + i for i in range(8)} | {  # sN: the stack word @(4N,r15) at the call (a stack argument)
    f"fr{i}": 9 + i for i in range(12, 16)} | {  # fr12..fr15: runtime state a callee reads (sh4_fr)
    f"r{i}": 25 + i for i in range(4)} | {  # r0..r3: a callee taking them (args.rlo)
    f"r{i}": 21 + i for i in range(8, 15)} | {  # r8..r14: runtime state a callee reads (sh4_r)
    f"fr{i}": 36 + i for i in range(4)} | {  # meta fpu_state: fr0..fr3, xf0..xf15 and the mode word at every call
    f"xf{i}": 40 + i for i in range(16)} | {"fpscr": 56}
# Compared at every call of a function whose whole FPU is state (meta fpu_state), whatever the callee.
FPU_REGS = [f"fr{i}" for i in range(16)] + [f"xf{i}" for i in range(16)] + ["fpscr"]


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
        if exp.get("retx") != act.get("retx"):
            return [f"return value {reg}: expected 0x{exp.get('retx', 0):08x}, got 0x{act.get('retx', 0):08x}"]
        return []
    out = []
    if ret in ("u32", "s32", "u64") and exp["r0"] != act.get("r0"):
        out.append(f"return value r0{' (low word)' if ret == 'u64' else ''}: expected 0x{exp['r0']:08x}, "
                   f"got 0x{act.get('r0', 0):08x}")
    if ret == "u64" and exp.get("retx") != act.get("retx"):
        out.append(f"return value r1 (high word): expected 0x{exp.get('retx', 0):08x}, got 0x{act.get('retx', 0):08x}")
    if ret == "f32" and not same_f32(exp["fr0"], act.get("fr0", 0)):
        out.append(f"return value fr0: expected 0x{exp['fr0']:08x} ({_f(exp['fr0'])}), "
                   f"got 0x{act.get('fr0', 0):08x} ({_f(act.get('fr0', 0))})")
    return out


def mode_text(m):
    return "none" if m is None else f"0x{m:08x} (FR={m >> 21 & 1} SZ={m >> 20 & 1} PR={m >> 19 & 1})"


def compare_state(exp, act):
    """fr12..fr15 (FH), r8..r14 (GH) and the whole FPU (QH) at exit, for functions with runtime state (meta fr_state,
    r_state, fpu_state)."""
    diffs = []
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
            if got is None or exp["gs"][i] != got[i]:
                diffs.append(f"r{8 + i} (runtime state, sh4_r({8 + i})) at exit: expected 0x{exp['gs'][i]:08x}, "
                             + ("got nothing" if got is None else f"got 0x{got[i]:08x}"))
    return diffs


def compare_vector(meta, image, vec, exp, act):
    if act is None or not act["done"]:
        return ["no result for this vector (the C code crashed, hung or timed out)"]
    diffs = []
    if "crash" in act:
        diffs.append(f"C code raised exception {act['crash']} (crash)")
    if act.get("coderead"):
        diffs.append(f"R8: read machine code at {act['coderead']}")
    for f in act["faults"]:
        diffs.append(f"invalid guest memory access: {f}")
    diffs += compare_return(meta, exp, act)
    mem = Memory(image, vec)
    words = sorted({a & ~3 for a in set(exp["writes"]) | set(act["writes"])
                    if a < STACK_LO or P4_OCA[0] <= a < P4_OCA[1]})
    bad = []
    for w in words:
        ev = [exp["writes"].get(w + i, mem.initial(w + i)) for i in range(4)]
        av = [act["writes"].get(w + i, mem.initial(w + i)) for i in range(4)]
        if ev == av:
            continue
        eb, ab = struct.unpack("<I", bytes(ev))[0], struct.unpack("<I", bytes(av))[0]
        if is_nan(eb) and is_nan(ab):
            continue
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
        regs = callee_regs.get(ec[k][0], [])
        if meta.get("fpu_state"):
            regs = regs + [r for r in FPU_REGS if r not in regs]
        for reg in regs:
            i = REG_INDEX[reg]
            if reg.startswith(("fr", "xf")) and same_f32(ec[k][i], ac[k][i]):
                continue
            if ec[k][i] != ac[k][i]:
                diffs.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: argument {arg_name(reg)} expected 0x{ec[k][i]:08x}, "
                             f"got 0x{ac[k][i]:08x}")
    for i in range(16):
        if not same_f32(exp["xf"][i], act["xf"][i]):
            diffs.append(f"xf{i} (XMTRX): expected 0x{exp['xf'][i]:08x}, got 0x{act['xf'][i]:08x}")
    return diffs + compare_state(exp, act)
