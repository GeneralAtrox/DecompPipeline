"""Xbox 360 harness: parsing vectors.txt, expected.txt and the runner's output, and the byte-exact comparison of one
vector (the counterpart of targets/gamecube_ppc/ppc_compare.py with 64-bit registers and the Xbox 360 memory map).

vectors.txt (the oracle's TcVectors): V id set, R r3..r10 (64-bit hex), F f1..f13 (double bits), C r3 per mocked
call (64-bit, up to 16), D f1 per mocked call (double bits), M addr bytes, E.
expected.txt / runner output: V, RET r3 f1 (64-bit hex each), W addr bytes (outside the stack region), CALL target
r3..r10 f1..f13 s0..s7 (30 fields; sN = the doubleword at r1+0x50+8N at the call; expected.txt: a CA line after one
through a function pointer), FN (expected.txt: the words a float store left a NaN in), CJ, CS and XR
(core/strict_judge; CS fields are STRICT_INDEX's), COV, FAULT, CODEREAD, CRASH, E.
The exit state (target.json "exit_state", core/strict_judge; expected.txt from TcExit, the runner's from the
translator's exit-state form run with "exit"): XS r0..r31 (64-bit), XF f0..f31 (double bits), XC CR (32-bit) CA SO OV CTR LR FPSCR,
XV n w0 w1 w2 w3 (a vector register not 0: the oracle's; one the C wrote: the runner's), XW addr bytes (stack writes).
"""
import os
import struct

from core import callee_drift, strict_judge
from core.strict_judge import EXIT_CLASSES

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
# A pointer argument's field in the stricter judge's lines (TcStrict.field, tc_strict.h: the GameCube's layout).
STRICT_INDEX = {f"r{i}": i - 2 for i in range(3, 11)} | {f"s{i}": 17 + i for i in range(8)}


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
        elif k == "CA":  # the call before is through a function pointer: the argument registers the original set
            cur.setdefault("call_args", {})[len(cur["calls"]) - 1] = t[1:]  # since its entry or last call (TcTrack)
        elif k == "FN":  # the memory words a float store left a NaN in (TcTrack): the only NaN-tolerant words
            cur["float_nan"] = {int(x, 16) for x in t[1:]}
        elif k in ("XS", "XF", "XC"):
            cur[k.lower()] = [int(x, 16) for x in t[1:]]
        elif k == "XV" and len(t) == 6:
            cur.setdefault("xv", {})[int(t[1])] = tuple(int(x, 16) for x in t[2:])
        elif k == "XW":
            a, data = int(t[1], 16), bytes.fromhex(t[2]) if len(t) > 2 else b""
            for i, b in enumerate(data):
                cur.setdefault("xw", {})[a + i] = b
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


def compare_memory(image, vec, exp, act, key="writes"):
    """The written words outside the stack region (key "writes"), or inside it (key "xw": the exit state's stack
    writes)."""
    mem = Memory(image, vec)
    ew, aw = exp.get(key) or {}, act.get(key) or {}
    words = sorted({a & ~3 for a in set(ew) | set(aw) if in_stack(a) == (key == "xw")})
    bad = []
    for w in words:
        ev = bytes(ew.get(w + i, mem.initial(w + i)) for i in range(4))
        av = bytes(aw.get(w + i, mem.initial(w + i)) for i in range(4))
        if ev == av:
            continue
        if nan32(struct.unpack(">I", ev)[0]) and nan32(struct.unpack(">I", av)[0]) and w in exp.get("float_nan", ()):
            continue  # a float NaN the original stored: its payload is the FPU's (an integer word compares exactly)
        bad.append(f"{'stack ' if key == 'xw' else ''}memory 0x{w:08x}: expected {ev.hex()}, got {av.hex()}"
                   + ("" if any(w + i in aw for i in range(4)) else " (your code did not write here)")
                   + ("" if any(w + i in ew for i in range(4)) else " (the original does not write here)"))
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
        # unlisted: the original's (CA) and, at a direct call, the callee's proven signature's (core/unchecked_args)
        for reg in callee_drift.call_regs(callee_regs, ec[k][0], exp.get("call_args", {}).get(k, []), REG_INDEX,
                                          caller=meta.get("addr")):
            if reg[0] == "f" and k in exp.get("nofp", ()):
                continue
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
                diffs.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: argument {arg_name(reg)} expected 0x{e:016x}{junk}, "
                             f"got 0x{a:016x}")
    return diffs


def _regs(names, want, have, kind, why):
    out = []
    for n, e, a in zip(names, want, have):
        if e == a or kind == "f" and same_f64(e, a):
            continue
        w = 8 if kind == "w" else 16 if kind in ("r", "f") else 1
        out.append(f"{n} at exit: expected 0x{e:0{w}x}, got 0x{a:0{w}x}{why}")
    return out


def compare_exit(meta, image, vec, exp, act):
    """The exit state's differences of one vector, {class: [diff]} (core/strict_judge.EXIT_CLASSES; every class has a
    list, empty when equal). {} when the harness has no exit state (an older harness, or the flag was off)."""
    if "xs" not in exp:
        return {}
    out = {c: [] for c in EXIT_CLASSES}
    if act is None or not act.get("done") or "xs" not in act:
        out["callee_saved_gpr"].append("no exit state for this vector (the exit-state form crashed or did not run)")
        return out
    ret = meta.get("ret")
    saved = " (callee-saved: the original restores it before returning)"
    xs, ys, xf, yf = exp["xs"], act["xs"], exp.get("xf", []), act.get("xf", [])
    gv = [n for n in [0] + list(range(2, 14)) if not (n == 3 and ret not in ("void", "f32", "f64"))]
    fv = [n for n in range(14) if not (n == 1 and ret in ("f32", "f64"))]
    out["callee_saved_gpr"] = _regs([f"r{n}" for n in range(14, 32)], xs[14:32], ys[14:32], "r", saved)
    out["stack_pointer"] = _regs(["r1"], xs[1:2], ys[1:2], "r", "")
    out["volatile_gpr"] = _regs([f"r{n}" for n in gv], [xs[n] for n in gv], [ys[n] for n in gv], "r", "")
    out["callee_saved_fpr"] = _regs([f"f{n}" for n in range(14, 32)], xf[14:32], yf[14:32], "f", saved)
    out["volatile_fpr"] = _regs([f"f{n}" for n in fv], [xf[n] for n in fv], [yf[n] for n in fv], "f", "")
    xc, yc = (exp.get("xc") or []) + [0] * 7, (act.get("xc") or []) + [0] * 7
    field = (lambda c, k: c >> (28 - 4 * k) & 15)
    for cls, ks in (("cr_nonvolatile", (2, 3, 4)), ("cr_volatile", (0, 1, 5, 6, 7))):
        out[cls] = _regs([f"cr{k} (lt gt eq so)" for k in ks], [field(xc[0], k) for k in ks],
                         [field(yc[0], k) for k in ks], "b", "")
    out["xer"] = _regs(["XER[CA]", "XER[SO]", "XER[OV]"], xc[1:4], yc[1:4], "b", "")
    out["ctr"] = _regs(["CTR"], xc[4:5], yc[4:5], "r", "")
    out["lr"] = _regs(["LR"], xc[5:6], yc[5:6], "r", "")
    out["fpscr"] = _regs(["FPSCR"], xc[6:7], yc[6:7], "w", "")
    ev, av, z = exp.get("xv") or {}, act.get("xv") or {}, (0, 0, 0, 0)
    for n in sorted(set(ev) | set(av)):
        e, a = ev.get(n, z), av.get(n, z)
        if e != a:
            out["vector"].append(f"v{n} at exit: expected {' '.join(f'{x:08x}' for x in e)}, "
                                 f"got {' '.join(f'{x:08x}' for x in a)}")
    out["stack_writes"] = compare_memory(image, vec, exp, act, key="xw")
    return {c: d[:4] + ([f"... and {len(d) - 4} more"] if len(d) > 4 else []) for c, d in out.items()}


def compare_vector(meta, image, vec, exp, act, strict=None, report=None, exit_act=None, modes=None, classes=None):
    """One vector's enforced differences. strict: core/strict_judge.modes; report: the list report-only differences
    (the stricter judge's and the exit state's) go to; exit_act: the exit-state form's run of this vector (None: no
    exit run, so no exit comparison); modes: core/strict_judge.exit_modes; classes: a Counter of the classes that
    differ."""
    if act is None or not act["done"]:
        return ["no result for this vector (the C code crashed, hung or timed out)"]
    diffs = []
    if "crash" in act:
        diffs.append(f"C code raised exception {act['crash']} (crash)")
    if act.get("coderead"):
        diffs.append(f"R8: read machine code at {act['coderead']}")
    for f in act["faults"]:
        diffs.append(f"invalid guest memory access: {f}")
    diffs += compare_return(meta, exp, act) + compare_memory(image, vec, exp, act) + compare_calls(meta, exp, act)
    diffs += strict_judge.compare(meta, exp, act, STRICT_INDEX, strict_judge.XR_PPC, int.__eq__, strict, report, ">I")
    if modes is not None and "xs" in exp:
        for cls, ds in compare_exit(meta, image, vec, exp, exit_act or {"done": False}).items():
            if not ds:
                continue
            if classes is not None:
                classes[cls] += 1
            if modes.get(cls) == "enforce":
                diffs += [f"exit state, {cls}: {d}" for d in ds]
            elif report is not None:
                report += [f"exit_state {cls} (report only): {d}" for d in ds]
    return diffs


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
