"""PowerPC's NaN results in scalar float arithmetic, through the real translator and runtime (leg A of the vote:
tools/x360_vote.py's machinery, one cl build) and through the oracle (leg B: Ghidra's emulation with TcFixups).

The rule (Power ISA Book I, "Floating-Point Operations": the NaN result; core/ghidra_shared/ppc/TcNaN.java states it
independently): the result of an operation with a NaN operand is the first NaN of frA, frB, frC, quieted (bit 12 set),
its sign kept; an SNaN has no priority over a QNaN. fmsub does not flip a NaN frB's sign and the fnm* forms do not negate
a NaN result. An invalid operation without a NaN operand (inf - inf, 0 * inf, 0 / 0, sqrt of a negative) gives
0x7ff8000000000000 (x86 gives 0xfff8000000000000). A single-precision result keeps the top 23 fraction bits.
Every expected value below is worked out from that rule by hand, not taken from an instruction corpus; non-NaN controls
check nothing else moved. Registers: f2 = frA, f3 = frC (fmul, multiply-adds) or frB (fadd, fsub, fdiv), f4 = frB of
the multiply-adds. Compared bit for bit (no NaN-payload tolerance).

Leg A prints "SKIPPED: needs cl" without config.local.json's vcvars; leg B needs ghidra_home and java_home (build box A or
Build box B) and a temp folder with no part starting with "." (Ghidra refuses such a project path): set X360_VOTE_WORK.
usage: python tests/test_x360_fp_nan.py
"""
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools"), str(TOOL / "targets" / "xbox360_ppc" / "tools"), str(TOOL)]
import vote_corpus as VC  # noqa: E402
import vote_leg_a as A  # noqa: E402
from core import toolcfg  # noqa: E402

CFG = toolcfg.load(TOOL)
HAVE_CL = bool(CFG.get("vcvars"))
HAVE_GHIDRA = bool(CFG.get("ghidra_home") and CFG.get("java_home"))

ONE, TWO, THREE, FOUR = 0x3FF0000000000000, 0x4000000000000000, 0x4008000000000000, 0x4010000000000000
INF, DEF = 0x7FF0000000000000, 0x7FF8000000000000
NQ, PQ = 0xFFF8000000000123, 0x7FF8000000000456       # a negative and a positive QNaN with payloads


def case(label, insn, ins, outs):
    lines = [f"test_{label}:"] + [f"  #_ REGISTER_IN {k} 0x{v:x}" for k, v in ins.items()]
    lines += [f"  {x}" for x in ([insn] if isinstance(insn, str) else insn)] + ["  blr"]
    lines += [f"  #_ REGISTER_OUT {k} 0x{v:x}" for k, v in outs.items()]
    return "\n".join(lines)


FMA = lambda op, a, c, b, want: case(f"{op}_{a:x}_{c:x}_{b:x}", f"{op} f1, f2, f3, f4",  # noqa: E731
                                     {"f2": a, "f3": c, "f4": b}, {"f1": want})
AB = lambda op, a, b, want: case(f"{op}_{a:x}_{b:x}", f"{op} f1, f2, f3", {"f2": a, "f3": b}, {"f1": want})  # noqa: E731

NAN_CASES = [
    # fmsub: frB's NaN keeps its sign (a * c - b must not flip it)
    FMA("fmsub", ONE, ONE, NQ, NQ),
    FMA("fmsub", ONE, ONE, PQ, PQ),
    # fnmadd / fnmsub: a NaN result is not negated
    FMA("fnmadd", ONE, ONE, PQ, PQ),
    FMA("fnmadd", ONE, ONE, NQ, NQ),
    FMA("fnmsub", ONE, ONE, NQ, NQ),
    FMA("fnmsub", 0x7FF8000000000789, ONE, ONE, 0x7FF8000000000789),
    # single form: not negated, and the low 29 fraction bits cleared (0x56789abc -> 0x40000000)
    FMA("fnmadds", ONE, ONE, 0x7FF8123456789ABC, 0x7FF8123440000000),
    # order: frB's NaN before frC's
    FMA("fmadd", ONE, 0x7FF8000000000011, 0xFFF8000000000022, 0xFFF8000000000022),
    # invalid operations without a NaN operand: the default QNaN, positive
    AB("fsub", INF, INF, DEF),
    AB("fadd", INF, 0xFFF0000000000000, DEF),
    AB("fmul", 0, INF, DEF),
    AB("fdiv", 0, 0, DEF),
    AB("fdiv", INF, 0xFFF0000000000000, DEF),
    FMA("fmadd", INF, 0, ONE, DEF),
    case("fsqrt_minus_one", "fsqrt f1, f2", {"f2": 0xBFF0000000000000}, {"f1": DEF}),
    # two NaNs: frA's, though frB is the signalling one
    AB("fadd", 0x7FF8000000000001, 0x7FF0000000000002, 0x7FF8000000000001),
    AB("fsub", 0xFFF8000000000001, 0x7FF0000000000002, 0xFFF8000000000001),
    AB("fadd", 0x7FF0000000000002, 0xFFF8000000000003, 0x7FF8000000000002),   # SNaN frA: quieted, still frA's
    AB("fmul", ONE, NQ, NQ),
    # an SNaN alone is quieted, sign and payload kept
    AB("fadd", ONE, 0xFFF0000000000005, 0xFFF8000000000005),
    case("fsqrt_snan", "fsqrt f1, f2", {"f2": 0x7FF0000000000009}, {"f1": 0x7FF8000000000009}),
    # single op: payload truncated to the top 23 fraction bits
    AB("fadds", 0x7FF8123456789ABC, ONE, 0x7FF8123440000000),
    AB("fmuls", 0xFFF8000FFFFFFFFF, ONE, 0xFFF8000FE0000000),
]
CONTROL_CASES = [
    AB("fadd", ONE, TWO, THREE),
    AB("fsub", INF, ONE, INF),
    AB("fdiv", ONE, FOUR, 0x3FD0000000000000),
    AB("fmul", 0x8000000000000000, ONE, 0x8000000000000000),
    FMA("fmsub", TWO, THREE, ONE, 0x4014000000000000),       # 2 * 3 - 1 = 5
    FMA("fnmadd", TWO, THREE, ONE, 0xC01C000000000000),      # -(2 * 3 + 1) = -7
    FMA("fnmsub", TWO, THREE, ONE, 0xC014000000000000),      # -(2 * 3 - 1) = -5
    FMA("fnmadds", TWO, THREE, ONE, 0xC01C000000000000),
    case("fsqrt_four", "fsqrt f1, f2", {"f2": FOUR}, {"f1": TWO}),
]


# ------------------------------------------------------------------------------------- shared by test_x360_ca32.py
def parse(texts):
    cases = [dict(c, skip=False) for c in VC.parse_text("\n".join(texts), "hand.s")]
    assert len({c["label"] for c in cases}) == len(cases), "duplicate labels"
    return cases


def mismatches(cases, plan, got, names):
    """["label reg: want X got Y"] for every output register not equal bit for bit (cr: its 32 bits)."""
    bad = []
    for i, (c, (fn, _, why)) in enumerate(zip(cases, plan)):
        g = got.get(i)
        if why is not None or g is None or g.get("crash") or g.get("unsupported"):
            bad.append(f"{c['label']}: not run ({why or (g or {}).get('crash') or (g or {}).get('unsupported')})")
            continue
        mine = A.ours(c, g, names(fn))
        for k, exp, _ in c["reg_out"]:
            if mine[k] != exp:
                act = "None" if mine[k] is None else f"{mine[k]:x}"
                bad.append(f"{c['label']} {k}: want {exp:x} got {act}")
    return bad


def work_dir(prefix, dotted_ok=True):
    base = pathlib.Path(os.environ.get("X360_VOTE_WORK") or (TOOL / "tests" / ".work" if dotted_ok
                                                              else tempfile.gettempdir()))
    if not dotted_ok and any(p.startswith(".") for p in base.parts):
        raise AssertionError(f"Ghidra refuses a project under {base} (a folder starting with '.'): set X360_VOTE_WORK")
    base.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def leg_a(texts):
    """Translator and runtime: the list of mismatches."""
    cases = parse(texts)
    plan, groups = A.prepare(cases)
    srcs = []
    for n, g in enumerate(groups):
        src, why = A.translate_group(n, list(g))
        assert src is not None, why
        srcs.append(src)
    d = work_dir("x360hand_")
    try:
        ok, msg = A.build(d, srcs, CFG)
        assert ok, msg
        got = A.run(d, cases, plan)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    names = [A.named(s) for s in srcs]
    return mismatches(cases, plan, got, lambda fn: names[fn])


def leg_b(texts):
    """The oracle (Ghidra with TcFixups): the list of mismatches."""
    import vote_leg_b as B
    cases = parse(texts)
    d = work_dir("x360handb_", dotted_ok=False)
    try:
        plan = B.prepare(d, cases)
        ok, msg = B.run(d, CFG, timeout=1200)
        assert ok, msg + " " + ((d / "headless.log").read_text(errors="replace")[-1500:]
                                if (d / "headless.log").is_file() else "")
        got = B.collect(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return mismatches(cases, plan, got, B.all_named)


@unittest.skipUnless(HAVE_CL, "needs cl")
class Translator(unittest.TestCase):
    def test_nan_results(self):
        self.assertEqual(leg_a(NAN_CASES + CONTROL_CASES), [])


@unittest.skipUnless(HAVE_GHIDRA, "needs ghidra_home and java_home")
class Oracle(unittest.TestCase):
    def test_nan_results(self):
        self.assertEqual(leg_b(NAN_CASES + CONTROL_CASES), [])


if __name__ == "__main__":
    if not HAVE_CL:
        print("SKIPPED: needs cl (the translator cases)")
    if not HAVE_GHIDRA:
        print("SKIPPED: needs ghidra_home and java_home (the oracle cases)")
    unittest.main()
