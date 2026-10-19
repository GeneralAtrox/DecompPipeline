"""The pointer views of matched C (tools/ptr_views.py, docs/PROOF_GATES.md section 4). One pass and one fail control
each:
1. literals: a RAM literal is a value site when the C keeps the pointer (a call argument, a stored word, a return, a
   compare), there or through the locals it is assigned to (PS2 8c07cedc: r9 = 0x8c532698u; r4 = r9 + i * size;
   wr32(r14 + 14204u, r4)); it is not when it only becomes an access address (rd32(T + 4u)), goes through a (u8)
   cast, or reaches a local that is assigned anew before any such use, or a file helper that only accesses through
   that parameter;
2. check: the C writes a pool word's other view (view), or an address up to 64 KB above one in the other view
   (offset); a literal in the pool's own view, in the code ranges or on the harness stack is not listed, nor a pool
   word in the code ranges.
Synthetic data. Run: python -W error tests/test_ptr_views.py.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import ptr_views  # noqa: E402
from test_float_gaps import insns  # noqa: E402

PTR, VIEW = 0x0C532698, 0x8C532698


def values(body, helpers=""):
    """{literal: value sites} of a function body."""
    lits = ptr_views.literals(helpers + "void f(void)\n{\n" + body + "\n}\n")
    return {v: n[1] for v, n in lits.items() if ptr_views.ram(v)}


class Literals(unittest.TestCase):
    def test_kept(self):
        self.assertEqual(values("u32 r9 = 0x8c532698u;\nr4 = r9 + i * 0x1cu;\nr4 += 4u;\nwr32(r14 + 14204u, r4);"),
                         {VIEW: 1})  # PS2 8c07cedc
        self.assertEqual(values("a.r[1] = 0x8c532698u;"), {VIEW: 1})
        self.assertEqual(values("(void)call2(0x8c5a2320u, r14, 0x8c532698u);")[VIEW], 1)
        self.assertEqual(values("if (p == 0x8c532698u) {\n}"), {VIEW: 1})
        self.assertEqual(values("return 0x8c532698u + b * 612u;"), {VIEW: 1})
        self.assertEqual(values("g = 0x8c532698u;\nwr32(sp + 4u, g + 8u);"), {VIEW: 1})

    def test_access_only(self):
        self.assertEqual(values("x = rd32(0x8c532698u + i);\nwr8(0x8c532698u, 1u);"), {VIEW: 0})
        self.assertEqual(values("u32 r0, r1, T;\nT = 0x8c532698u;\nx = rd32(T + 4u);\nr1 = rd32((u32)m + T);"),
                         {VIEW: 0})
        self.assertEqual(values("r5 = 0x8c532698u;\nwr8(p, (u8)r5);"), {VIEW: 0})  # the view bit is cut off
        self.assertEqual(values("r3 = 0x8c532698u;\nr4 = rd32(r3 + 4u);\nr3 = rd32(r4);\nwr32(p, r3);"),
                         {VIEW: 0})  # r3 is something else by then
        helper = "static void inc16(u32 addr)\n{\n    wr16(addr, (u16)(rd16(addr) + 1u));\n}\n"
        self.assertEqual(values("inc16(0x8c532698u);", helper), {VIEW: 0})
        keeps = "static void put(u32 v)\n{\n    wr32(0x8c100000u, v);\n}\n"
        self.assertEqual(values("put(0x8c532698u);", keeps)[VIEW], 1)

    def test_comments(self):
        self.assertEqual(values("r14 = rd32(0x8c02ea08u);  /* 0x8c532698 */"), {0x8C02EA08: 0})  # PS2 8c02e910


class Check(unittest.TestCase):
    POOL = ["mov.l 0x8c010200,r9  ; = 0x0c532698  (global 8c532698)",
            "mov.l 0x8c010204,r3  ; = 0x0c187958  (function 8c187958)", "rts", "_nop"]

    def test_pool_words(self):
        self.assertEqual(ptr_views.pool_words(insns(self.POOL)), {PTR})

    def test_views(self):
        lits = ptr_views.literals("void f(void)\n{\n    u32 r9 = 0x8c532698u;\n    a.r[0] = r9;\n}\n")
        self.assertEqual(ptr_views.check({PTR}, lits),
                         [{"kind": "view", "pool": "0c532698", "c": "8c532698", "sites": 1, "values": 1}])
        same = ptr_views.literals("void f(void)\n{\n    a.r[0] = 0x0c532698u;\n}\n")
        self.assertEqual(ptr_views.check({PTR}, same), [])
        self.assertEqual(ptr_views.check({PTR}, lits, [(0x8C500000, 0x8C600000)]), [])  # a code address

    def test_offset(self):
        lits = ptr_views.literals("void f(void)\n{\n    a.r[0] = 0x8c540b78u;\n    sp = 0x8cffe000u - 52u;\n}\n")
        self.assertEqual(ptr_views.check({PTR}, lits), [{"kind": "offset", "pool": "0c532698", "c": "8c540b78",
                                                         "sites": 1, "values": 1, "delta": "0xe4e0"}])
        self.assertEqual(ptr_views.check({PTR, 0x8C600000}, lits), [])  # the pool uses the 0x8c view itself


if __name__ == "__main__":
    unittest.main()
