"""The helper ABI of matched C (tools/helper_abi.py, docs/PROOF_GATES.md section 4). One pass and one fail control
each:
1. helpers: a callee the metas measure reading r0..r3 is one, a callee of r4..r7 only is not;
2. the asm: the constant each register holds at the jsr (its delay slot first), none past a branch target;
3. the C: a call that passes no rlo value for a register the helper takes is listed (TC 8c03a4b0's builder of
   r4..r7 only, 8c0248e0's call_plain, a TcArgs zeroed by a setter); one that sets them directly, through a wrapper's
   parameters, a setter, a local or pool word holding the target, or the other RAM view, is not; a register holding
   the helper's own address (the jsr's) is not an argument; a constant the C passes other than the asm's is listed,
   a variable is not; a call it cannot follow is counted, not listed.
Synthetic data. Run: python -W error tests/test_helper_abi.py.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import helper_abi  # noqa: E402
from test_float_gaps import insns  # noqa: E402

DIV = "8c067c64"
ABI = {DIV: ["r0", "r1"]}
# TC 8c03a4b0 at 8c03a53c: r0 = 3 (divisor), r1 = player_num - 1 (dividend, set in the delay slot)
BEAMS = ["mov.l 0x8c0101f0,r2   ; = 0x8c067c64  (function 8c067c64)", "mov #0x3,r0", "add #-0x1,r4",
         "jsr @r2   ; call 8c067c64", "_mov r4,r1", "rts", "_nop"]


def flags(c, asm=BEAMS, abi=ABI):
    ins = insns(asm)
    return helper_abi.check(helper_abi.asm_sites(ins, abi), helper_abi.c_sites(c, abi, helper_abi.pools(ins, abi)),
                            abi)


def kinds(c, **kw):
    return sorted({w.split(":")[0] for w in flags(c, **kw)})


class Helpers(unittest.TestCase):
    def test_measured(self):
        metas = [{"callees": [{"addr": DIV, "regs": ["r0", "r1"]}, {"addr": "8c011498", "regs": ["r4", "r5"]}]},
                 {"callees": [{"addr": "8c061630", "regs": ["r0", "r4"]}]}]
        self.assertEqual(helper_abi.helpers(metas), {DIV: ["r0", "r1"], "8c061630": ["r0"]})


class Asm(unittest.TestCase):
    def test_constants(self):
        (site,) = helper_abi.asm_sites(insns(BEAMS), ABI)
        self.assertEqual((site[1], site[2]), (DIV, {"r0": (3, None), "r1": None}))

    def test_block_start(self):
        asm = ["mov #0x5,r0", "bt 0x8c010104", "mov r4,r1", "jsr @r2   ; call 8c067c64", "_nop", "rts", "_nop"]
        (site,) = helper_abi.asm_sites(insns(asm), ABI)
        self.assertEqual(site[2], {"r0": None, "r1": None})  # 8c010104 is a branch target: r0 comes from above


class C(unittest.TestCase):
    def test_builder_without_rlo(self):  # TC 8c03a4b0
        c = ("static TcArgs args_r4567(u32 r4, u32 r5, u32 r6, u32 r7)\n{\n"
             "    TcArgs args = { { r4, r5, r6, r7 }, { 0.0f }, { 0u } };\n    return args;\n}\n"
             "void f(s32 v, u32 r5, u32 r6, u32 r7)\n{\n    TcArgs args;\n"
             "    args = args_r4567((u32)v - 1u, r5, r6, r7);\n    r4 = tc_call(0x8c067c64u, &args) + 1u;\n}\n")
        self.assertEqual(kinds(c), ["missing"])
        fixed = c.replace("    r4 = tc_call", "    args.rlo[0] = 3u;\n    args.rlo[1] = (u32)v - 1u;\n    r4 = tc_call")
        self.assertEqual(flags(fixed), [])

    def test_plain_wrapper(self):  # TC 8c0248e0: call_plain passes r4..r7 only
        plain = ("static u32 call_plain(u32 target, u32 sp, u32 r4, u32 r5, u32 r6, u32 r7)\n{\n"
                 "    TcArgs args = {0};\n    args.r[0] = r4;\n    return tc_call(target, &args);\n}\n")
        self.assertEqual(kinds(plain + "void f(void)\n{\n    r0 = call_plain(0x8c067c64u, sp, r4, r5, r6, r7);\n}\n"),
                         ["missing"])
        rr = ("static u32 call_rr(u32 target, u32 a4, u32 sp, u32 r0v, u32 r1v)\n{\n    TcArgs a;\n"
              "    a.r[0] = a4;\n    a.rlo[0] = r0v;\n    a.rlo[1] = r1v;\n    return tc_call(target, &a);\n}\n")
        self.assertEqual(flags(rr + "void f(void)\n{\n    r0 = call_rr(0x8c067c64, r4, r15, 3, r4 - 1u);\n}\n"), [])
        self.assertEqual(kinds(rr + "void f(void)\n{\n    r0 = call_rr(0x8c067c64, r4, r15, 7, r4 - 1u);\n}\n"),
                         ["value"])  # the asm's r0 is 3

    def test_setters(self):  # Spawn 8c0317e8's set_rlo; PS2's clear_args zeroes rlo in a loop
        setter = ("static void set_rlo(TcArgs *a, u32 r0, u32 r1)\n{\n    a->rlo[0] = r0;\n    a->rlo[1] = r1;\n}\n"
                  "static void clear_args(TcArgs *a)\n{\n    u32 i;\n    for (i = 0u; i < 4u; i++) {\n"
                  "        a->r[i] = 0u;\n        a->rlo[i] = 0u;\n    }\n}\n")
        body = "void f(u32 g)\n{\n    TcArgs args;\n    clear_args(&args);\n%s    ret = tc_call(0x8c067c64u, &args);\n}\n"
        self.assertEqual(flags(setter + body % "    set_rlo(&args, 3u, g);\n"), [])
        self.assertEqual(kinds(setter + body % ""), ["missing"])

    def test_target_forms(self):  # ovl 8c58e0a0's local FA; PS2's 0x0c view and its pool word load
        sets = "    a.rlo[0] = 3u;\n    a.rlo[1] = r4 - 1u;\n"
        local = "void f(void)\n{\n    TcArgs a = {0};\n    u32 FA = 0x8c067c64u;\n%s    r0 = tc_call(FA, &a);\n}\n"
        self.assertEqual(flags(local % sets), [])
        self.assertEqual(kinds(local % ""), ["missing"])
        self.assertEqual(kinds("void f(void)\n{\n    TcArgs a = {0};\n    r0 = tc_call(0x0c067c64u, &a);\n}\n"),
                         ["missing"])
        pool = "void f(void)\n{\n    TcArgs a = {0};\n%s    r0 = tc_call(rd32(0x8c0101f0u), &a);\n}\n"
        self.assertEqual(flags(pool % sets), [])
        self.assertEqual(kinds(pool % ""), ["missing"])

    def test_jsr_register(self):  # PS2 8c187958 reads r3, the register the jsr goes through
        abi = {"8c187958": ["r0", "r3"]}
        asm = ["mov.l 0x8c0101f0,r3   ; = 0x0c187958  (function 8c187958)", "mov #0x20,r0",
               "jsr @r3   ; call 8c187958", "_nop", "rts", "_nop"]
        c = "void f(void)\n{\n    TcArgs a = {0};\n    a.rlo[0] = 32u;\n%s    tc_call(0x8c187958u, &a);\n}\n"
        self.assertEqual(flags(c % "", asm=asm, abi=abi), [])
        self.assertEqual(flags(c % "    a.rlo[3] = 0x8c187958u;\n", asm=asm, abi=abi), [])

    def test_values(self):
        c = "void f(u32 n)\n{\n    TcArgs a = {0};\n    a.rlo[0] = %s;\n    a.rlo[1] = n;\n    tc_call(0x8c067c64u, &a);\n}\n"
        self.assertEqual(flags(c % "(1u + 2u)"), [])
        self.assertEqual(flags(c % "n"), [])  # a variable: not judged
        self.assertEqual(kinds(c % "1580u"), ["value"])

    def test_unfollowed(self):  # a TcArgs passed in: counted, not listed
        c = "void f(TcArgs *p)\n{\n    tc_call(0x8c067c64u, p);\n}\n"
        self.assertEqual(helper_abi.c_sites(c, ABI), [(DIV, None)])
        self.assertEqual(flags(c), [])


if __name__ == "__main__":
    unittest.main()
