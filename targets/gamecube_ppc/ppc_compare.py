"""GameCube PowerPC harness: parsing vectors.txt, expected.txt and the runner's output, and the byte-exact comparison
of one vector (the PowerPC counterpart of core/compare.py, used by ppc_check).

vectors.txt (TcVectors.writeVector): V id set, R r3..r10, F f1..f8 (double bits), C r3 per mocked call, D f1 per
mocked call (double bits), M addr bytes, E.
expected.txt / runner output: V, RET r3 f1 r4, W addr bytes (below STACK_LO), CALL target r3..r10 f1..f8 s0..s7 (25
fields; expected.txt: a CA line after one through a function pointer), FN (expected.txt: the words a float store left
a NaN in), CJ, CS and XR (core/strict_judge), COV, FAULT, CODEREAD, CRASH, E.
"""
import os
import struct

from core import strict_judge

STACK_LO = 0x817F0000
SP = 0x817FE000
RAM = (0x80000000, 0x81800000)
CALL_FIELDS = 25
# Index of each compared callee register in a CALL record (after the target at 0).
REG_INDEX = {f"r{i}": i - 2 for i in range(3, 11)} | {f"f{i}": 8 + i for i in range(1, 9)} | {
    f"s{i}": 17 + i for i in range(8)}  # sN: the word at r1+8+4N at the call (a stack argument)


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
            cur = {"id": int(t[1]), "set": t[2], "patches": [], "r": [0] * 8, "f": [0] * 8}
            vecs[cur["id"]] = cur
        elif cur is None:
            continue
        elif t[0] == "R":
            cur["r"] = [int(x, 16) for x in t[1:9]]
        elif t[0] == "F":
            cur["f"] = [int(x, 16) for x in t[1:9]]
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
            cur["r4"] = int(t[3], 16) if len(t) > 3 else 0
        elif k == "W":
            a, data = int(t[1], 16), bytes.fromhex(t[2]) if len(t) > 2 else b""
            for i, b in enumerate(data):
                cur["writes"][a + i] = b
        elif k == "CALL":
            call = [int(x, 16) for x in t[1:CALL_FIELDS + 1]]
            if "nofp" in t[CALL_FIELDS + 1:]:  # TcOracle.noFloatArgs: crxor 6,6,6 before a variadic call
                cur.setdefault("nofp", set()).add(len(cur["calls"]))
            cur["calls"].append(call + [0] * (CALL_FIELDS - len(call)))
        elif k == "CA":  # the call before is through a function pointer: the argument registers the original set
            cur.setdefault("call_args", {})[len(cur["calls"]) - 1] = t[1:]  # since its entry or last call (TcTrack)
        elif k == "FN":  # the memory words a float store left a NaN in (TcTrack): the only NaN-tolerant words
            cur["float_nan"] = {int(x, 16) for x in t[1:]}
        elif k == "COV":
            cur["cov"] = {int(x) for x in t[1:]}
        elif k == "FAULT":
            cur["faults"].append(" ".join(t[1:]))
        elif k in ("CJ", "CS", "XR"):  # leftover junk, call snapshots, the exit frame (core/strict_judge)
            strict_judge.parse(cur, t)
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


def dol_sections(dol):
    """The DOL's loaded sections: [(address, bytes)] (7 text, 11 data; bss and the rest of MEM1 are zero)."""
    out = []
    for i in range(18):
        off, addr, size = (struct.unpack_from(">I", dol, base + 4 * i)[0] for base in (0, 0x48, 0x90))
        if size:
            out.append((addr, dol[off:off + size]))
    return out


class Memory:
    """A byte's value before the run: the vector's patches over the DOL image (as both the oracle and runner load)."""

    def __init__(self, sections, vec):
        self.sections, self.patches = sections, vec["patches"]

    def initial(self, a):
        for base, data in reversed(self.patches):
            if base <= a < base + len(data):
                return data[a - base]
        for base, data in self.sections:
            if base <= a < base + len(data):
                return data[a - base]
        return 0


def compare_return(meta, exp, act):
    ret, out = meta["ret"], []
    if ret in ("u32", "s32") and exp["r3"] != act.get("r3"):
        out.append(f"return value r3: expected 0x{exp['r3']:08x}, got 0x{act.get('r3', 0):08x}")
    if ret == "u64" and (exp["r3"], exp["r4"]) != (act.get("r3"), act.get("r4")):
        out.append(f"return value r3:r4 (high:low word): expected 0x{exp['r3']:08x}{exp['r4']:08x}, "
                   f"got 0x{act.get('r3', 0):08x}{act.get('r4', 0):08x}")
    if ret in ("f32", "f64") and not same_f64(exp["f1"], act.get("f1", 0)):
        out.append(f"return value f1: expected 0x{exp['f1']:016x} ({_d(exp['f1'])}), "
                   f"got 0x{act.get('f1', 0):016x} ({_d(act.get('f1', 0))})")
    return out


def arg_name(reg):
    if reg[0] == "s":
        n = int(reg[1:])
        return f"{reg} (stack word at r1+{8 + 4 * n} at the call, args.s[{n}])"
    if reg[0] == "f":
        return f"{reg} (args.f[{int(reg[1:]) - 1}])"
    return f"{reg} (args.r[{int(reg[1:]) - 3}])"


def compare_memory(sections, vec, exp, act):
    mem = Memory(sections, vec)
    words = sorted({a & ~3 for a in set(exp["writes"]) | set(act["writes"]) if a < STACK_LO})
    bad = []
    for w in words:
        ev = bytes(exp["writes"].get(w + i, mem.initial(w + i)) for i in range(4))
        av = bytes(act["writes"].get(w + i, mem.initial(w + i)) for i in range(4))
        if ev == av:
            continue
        if nan32(struct.unpack(">I", ev)[0]) and nan32(struct.unpack(">I", av)[0]) and w in exp.get("float_nan", ()):
            continue  # a float NaN the original stored: its payload is the FPU's (an integer word compares exactly)
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
        for reg in callee_regs.get(ec[k][0], exp.get("call_args", {}).get(k, [])):  # unlisted: the original's (CA)
            if reg[0] == "f" and k in exp.get("nofp", ()):
                continue  # the call site cleared cr1[eq]: f1..f8 are not arguments of this variadic call
            i = REG_INDEX[reg]
            e, a, alt = ec[k][i], ac[k][i], strict_judge.alternative(exp, k, i)
            same = same_f64 if reg[0] == "f" else int.__eq__
            if same(e, a) or (alt is not None and same(alt, a)):
                continue
            junk = "" if alt is None else " (junk a callee left there: the original never set it) or 0"
            if reg[0] == "f":
                diffs.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: argument {arg_name(reg)} expected 0x{e:016x} "
                             f"({_d(e)}){junk}, got 0x{a:016x} ({_d(a)})")
            else:
                diffs.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: argument {arg_name(reg)} expected 0x{e:08x}{junk}, "
                             f"got 0x{a:08x}")
    return diffs


def compare_vector(meta, sections, vec, exp, act, strict=None, report=None):
    if act is None or not act["done"]:
        return ["no result for this vector (the C code crashed, hung or timed out)"]
    diffs = []
    if "crash" in act:
        diffs.append(f"C code raised exception {act['crash']} (crash)")
    if act.get("coderead"):
        diffs.append(f"R8: read machine code at {act['coderead']}")
    for f in act["faults"]:
        diffs.append(f"invalid guest memory access: {f}")
    diffs += compare_return(meta, exp, act) + compare_memory(sections, vec, exp, act) + compare_calls(meta, exp, act)
    return diffs + strict_judge.compare(meta, exp, act, REG_INDEX, strict_judge.XR_PPC, int.__eq__, strict, report,
                                        ">I")


def describe_inputs(meta, vec):
    parts = []
    for p in meta["params"]:
        st = p["storage"]
        if st[0] == "r" and st[1:].isdigit():
            parts.append(f"{p['name']}=0x{vec['r'][int(st[1:]) - 3]:08x}")
        elif st[0] == "f" and st[1:].isdigit():
            parts.append(f"{p['name']}={_d(vec['f'][int(st[1:]) - 1])}")
        else:
            parts.append(f"{p['name']} in {st}")
    if vec["patches"]:
        parts.append(f"{len(vec['patches'])} memory patch(es)")
    return ", ".join(parts)
