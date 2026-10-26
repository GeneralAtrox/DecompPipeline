"""Xbox 360 harness: parsing vectors.txt, expected.txt and the runner's output, and the byte-exact comparison of one
vector (the counterpart of targets/gamecube_ppc/ppc_compare.py with 64-bit registers and the Xbox 360 memory map).

vectors.txt (the oracle's TcVectors): V id set, R r3..r10 (64-bit hex), F f1..f13 (double bits), C r3 per mocked
call (64-bit, up to 16), D f1 per mocked call (double bits), M addr bytes, E.
expected.txt / runner output: V, RET r3 f1 (64-bit hex each), W addr bytes (outside the stack region), CALL target
r3..r10 f1..f13 s0..s7 (30 fields; sN = the doubleword at r1+0x50+8N at the call), COV, FAULT, CODEREAD, CRASH, E.
"""
import os
import struct

IMAGE_BASE = 0x82000000
HEAP = (0x40000000, 0x40100000)
STACK = (0x70000000, 0x70100000)
KERNEL = (0x7FFF0000, 0x80000000)
SP = 0x700FE000
STACK_ARGS = 0x50  # r1 + 0x50 + 8N: the Nth stack doubleword at a call (DESIGN.md, open question 4)
CALL_FLOATS = 13
CALL_FIELDS = 1 + 8 + CALL_FLOATS + 8
# Index of each compared callee register in a CALL record (after the target at 0).
REG_INDEX = {f"r{i}": i - 2 for i in range(3, 11)} | {f"f{i}": 8 + i for i in range(1, CALL_FLOATS + 1)} | {
    f"s{i}": 9 + CALL_FLOATS + i for i in range(8)}


def _lines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.readlines()


def parse_vectors(path):
    vecs, cur = {}, None
    for line in _lines(path):
        t = line.split()
        if not t:
            continue
        if t[0] == "V":
            cur = {"id": int(t[1]), "set": t[2], "patches": [], "r": [0] * 8, "f": [0] * CALL_FLOATS}
            vecs[cur["id"]] = cur
        elif cur is None:
            continue
        elif t[0] == "R":
            cur["r"] = [int(x, 16) for x in t[1:9]]
        elif t[0] == "F":
            cur["f"] = [int(x, 16) for x in t[1:CALL_FLOATS + 1]]
        elif t[0] == "C":
            cur["rets"] = [int(x, 16) for x in t[1:17]]
        elif t[0] == "D":
            cur["frets"] = [int(x, 16) for x in t[1:17]]
        elif t[0] == "M":
            cur["patches"].append((int(t[1], 16), bytes.fromhex(t[2]) if len(t) > 2 else b""))
    return vecs


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
            cur = {"set": t[2], "writes": {}, "calls": [], "faults": [], "cov": set(), "done": False}
            res[int(t[1])] = cur
        elif cur is None:
            continue
        elif k == "RET":
            cur["r3"], cur["f1"] = int(t[1], 16), int(t[2], 16)
        elif k == "W":
            a, data = int(t[1], 16), bytes.fromhex(t[2]) if len(t) > 2 else b""
            for i, b in enumerate(data):
                cur["writes"][a + i] = b
        elif k == "CALL":
            call = [int(x, 16) for x in t[1:CALL_FIELDS + 1]]
            if "nofp" in t[CALL_FIELDS + 1:]:  # crxor 6,6,6 before a variadic call: no float arguments
                cur.setdefault("nofp", set()).add(len(cur["calls"]))
            cur["calls"].append(call + [0] * (CALL_FIELDS - len(call)))
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


def nan64(bits):
    return (bits & 0x7FF0000000000000) == 0x7FF0000000000000 and (bits & 0x000FFFFFFFFFFFFF) != 0


def nan32(bits):
    return (bits & 0x7F800000) == 0x7F800000 and (bits & 0x007FFFFF) != 0


def same_f64(a, b):
    return a == b or (nan64(a) and nan64(b))


def _d(bits):
    return f"{struct.unpack('>d', struct.pack('>Q', bits & 0xFFFFFFFFFFFFFFFF))[0]:.17g}"


def in_stack(a):
    return STACK[0] <= a < STACK[1]


class Memory:
    """A byte's value before the run: the vector's patches over the loaded image (as both oracle and runner load);
    the heap, stack and kernel regions start zeroed."""

    def __init__(self, image, vec):
        self.image, self.patches = image, vec["patches"]

    def initial(self, a):
        for base, data in reversed(self.patches):
            if base <= a < base + len(data):
                return data[a - base]
        if IMAGE_BASE <= a < IMAGE_BASE + len(self.image):
            return self.image[a - IMAGE_BASE]
        return 0


def compare_return(meta, exp, act):
    ret, out = meta["ret"], []
    if ret not in ("void", "f32", "f64") and exp["r3"] != act.get("r3"):
        out.append(f"return value r3 (all 64 bits): expected 0x{exp['r3']:016x}, got 0x{act.get('r3', 0):016x}"
                   + upper_hint(meta, exp["r3"], act.get("r3", 0)))
    if ret in ("f32", "f64") and not same_f64(exp["f1"], act.get("f1", 0)):
        out.append(f"return value f1: expected 0x{exp['f1']:016x} ({_d(exp['f1'])}), "
                   f"got 0x{act.get('f1', 0):016x} ({_d(act.get('f1', 0))})")
    return out


def upper_hint(meta, e, a):
    """When only the upper word differs, say what the declared return type does with it."""
    if (e ^ a) >> 32 == 0 or (e ^ a) & 0xFFFFFFFF:
        return ""
    how = {"u32": "zero-extends", "s32": "sign-extends"}.get(meta["ret"])
    return (f" (only the upper word differs: {meta['ret']} {how} the low word; the original leaves 0x{e >> 32:08x})"
            if how else " (only the upper word differs)")


def arg_name(reg):
    if reg[0] == "s":
        n = int(reg[1:])
        return f"{reg} (stack doubleword at r1+0x{STACK_ARGS + 8 * n:x} at the call, args.s[{n}])"
    if reg[0] == "f":
        return f"{reg} (args.f[{int(reg[1:]) - 1}])"
    return f"{reg} (args.r[{int(reg[1:]) - 3}])"


def compare_memory(image, vec, exp, act):
    mem = Memory(image, vec)
    words = sorted({a & ~3 for a in set(exp["writes"]) | set(act["writes"]) if not in_stack(a)})
    bad = []
    for w in words:
        ev = bytes(exp["writes"].get(w + i, mem.initial(w + i)) for i in range(4))
        av = bytes(act["writes"].get(w + i, mem.initial(w + i)) for i in range(4))
        if ev == av:
            continue
        if nan32(struct.unpack(">I", ev)[0]) and nan32(struct.unpack(">I", av)[0]):
            continue
        bad.append(f"memory 0x{w:08x}: expected {ev.hex()}, got {av.hex()}"
                   + ("" if any(w + i in act["writes"] for i in range(4)) else " (your code did not write here)")
                   + ("" if any(w + i in exp["writes"] for i in range(4)) else " (the original does not write here)"))
    if len(bad) > 6:
        bad = bad[:6] + [f"... and {len(bad) - 6} more differing memory words"]
    return bad


def compare_calls(meta, exp, act):
    diffs = []
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
        for reg in callee_regs.get(ec[k][0], []):
            if reg[0] == "f" and k in exp.get("nofp", ()):
                continue
            i = REG_INDEX[reg]
            e, a = ec[k][i], ac[k][i]
            if reg[0] == "f":
                if not same_f64(e, a):
                    diffs.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: argument {arg_name(reg)} expected 0x{e:016x} "
                                 f"({_d(e)}), got 0x{a:016x} ({_d(a)})")
            elif e != a:
                diffs.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: argument {arg_name(reg)} expected 0x{e:016x}, "
                             f"got 0x{a:016x}")
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
    return diffs + compare_return(meta, exp, act) + compare_memory(image, vec, exp, act) + compare_calls(meta, exp, act)


def describe_inputs(meta, vec):
    parts = []
    for p in meta["params"]:
        st = p["storage"]
        if st[0] == "r" and st[1:].isdigit():
            parts.append(f"{p['name']}=0x{vec['r'][int(st[1:]) - 3]:016x}")
        elif st[0] == "f" and st[1:].isdigit():
            parts.append(f"{p['name']}={_d(vec['f'][int(st[1:]) - 1])}")
        else:
            parts.append(f"{p['name']} in {st}")
    if vec["patches"]:
        parts.append(f"{len(vec['patches'])} memory patch(es)")
    return ", ".join(parts)
