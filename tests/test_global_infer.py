"""Machine names for unnamed data globals (core/global_infer, refine.py globals).

One pass and one fail control each:
1. accesses: a literal address and a local assigned one give (offset, width, kind); a non-constant term is indexed;
   a local reassigned to something else (control) no longer counts; a mirror form folds to its canonical address;
   an absurd offset is another pointer (control);
2. extents: an address inside another's reach joins it; two adjacent words stay two globals (control); an indexed
   access runs to the next boundary, never past a named global or MAX_OPEN; an address only passed gets MAX_VALUE;
3. type: one width gives that type, f32-only gives f32, mixed widths a byte blob;
4. names: neutral by type and address, a clash gets a suffix; a callee-parameter name is used when the registry has one
   and a placeholder parameter name (control) is not;
5. the entry carries "machine": "globals" and is written without overriding an existing name.
Synthetic data. Run: python -W error tests/test_global_infer.py (also from test_tooling.py).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import global_infer as gi  # noqa: E402

MIRROR = {"0c": "8c"}
RAW = """u32 fn_8c000000(void)
{
    u32 GD = 0x0c94d0c4u;
    u32 q = 0x8c900010u;
    r1 = rd32(0x0c94b108u);
    r2 = rd32(GD + 0x14u);
    wr8(0x8c900000u + 1u, 5);
    r3 = rd32(0x8c900040u + (i << 2));
    r4 = rdf32(0x8c900080u + 8u);
    r5 = rd32(q + 4u);
    q = rd32(q);
    r6 = rd32(q + 8u);
    r7 = rd32(0x8c900100u + 0x20000u);
    /* rd32(0x8c9fffffu) */
    return r1;
}
"""


class Accesses(unittest.TestCase):
    def setUp(self):
        self.acc = gi.parse_accesses(RAW, MIRROR)

    def test_literal_and_local(self):
        self.assertEqual(self.acc[0x8c94b108], [(0, 4, "int", False, 0)])   # mirror folded
        self.assertEqual(self.acc[0x8c94d0c4], [(0x14, 4, "int", False, 0)])  # through a local
        self.assertEqual(self.acc[0x8c900000], [(1, 1, "int", False, 0)])
        self.assertEqual(self.acc[0x8c900080], [(8, 4, "f32", False, 0)])

    def test_indexed_stride(self):
        self.assertEqual(self.acc[0x8c900040], [(0, 4, "int", True, 4)])

    def test_controls(self):
        self.assertEqual(self.acc[0x8c900010], [(4, 4, "int", False, 0)])  # only q + 4: rd32(q + 8) comes after q = rd32(q)
        self.assertNotIn(0x8c9fffff, self.acc)                              # a comment
        self.assertNotIn(0x8c900100, self.acc)                              # offset 0x20000: another pointer


class Extents(unittest.TestCase):
    def test_join_adjacent_open_and_value(self):
        acc = {0x8c900000: [(0, 4, "int", False, 0), (0x10, 4, "int", False, 0)],
               0x8c900008: [(0, 4, "int", False, 0)],           # inside 0x8c900000's reach: joins it
               0x8c900014: [(0, 4, "int", False, 0)],           # adjacent, not inside: its own global
               0x8c900100: [(0, 4, "int", True, 4)],            # open: to the next boundary
               0x8c900400: []}                                  # only passed
        bounds = [0x8c900140]
        got = gi.clusters(sorted(acc), acc, bounds, 0x8c901000)
        spans = [(lo, hi) for lo, hi, _ in got]
        self.assertEqual(spans, [(0x8c900000, 0x8c900014), (0x8c900014, 0x8c900018), (0x8c900100, 0x8c900140),
                                 (0x8c900400, 0x8c900400 + gi.MAX_VALUE)])
        self.assertEqual(got[0][2], [0x8c900000, 0x8c900008])

    def test_open_is_capped_and_stops_at_a_named_global(self):
        acc = {0x8c900000: [(0, 4, "int", True, 4)]}
        (lo, hi, _), = gi.clusters([0x8c900000], acc, [], 0x8d000000)
        self.assertEqual(hi - lo, gi.MAX_OPEN)
        (lo, hi, _), = gi.clusters([0x8c900000], acc, [0x8c900080], 0x8d000000)
        self.assertEqual(hi, 0x8c900080)


class Names(unittest.TestCase):
    def test_types_and_names(self):
        acc = {0x8c900000: [(0, 4, "int", False, 0), (4, 4, "int", False, 0)],
               0x8c900100: [(0, 4, "f32", False, 0)],
               0x8c900200: [(0, 1, "int", False, 0), (4, 4, "int", False, 0)],
               0x8c900300: [(0, 2, "int", False, 0)],
               0x8c900400: []}
        p = gi.propose(list(acc), acc, [], 0x8d000000, {}, {"g_f32_900100"})
        self.assertEqual(p[0x8c900000][:2], ("u32 g_words_900000[2]", "g_words_900000"))
        self.assertEqual(p[0x8c900100][1], "g_f32_900100_2")           # clash with a name in use
        self.assertEqual(p[0x8c900200][0], "u8 g_bytes_900200[8]")     # mixed widths: a blob
        self.assertEqual(p[0x8c900300][0], "u16 g_u16_900300")
        self.assertTrue(p[0x8c900400][2].startswith("address passed only"))
        self.assertEqual(gi.entry(*[p[0x8c900000][i] for i in (0, 2, 3)])["machine"], "globals")
        be = gi.propose(list(acc), acc, [], 0x8d000000, {}, set(), be=True)  # big-endian: be_ types, bytes stay u8
        self.assertEqual([be[a][0] for a in sorted(be)][:4], ["be_u32 g_words_900000[2]", "be_f32 g_f32_900100",
                                                             "u8 g_bytes_900200[8]", "be_u16 g_u16_900300"])


class Evidence(unittest.TestCase):
    class Reg:
        functions = {"8c001000": {"proto": "void Fx_Spawn(u32 table, u32 a, u32 count)"}}

    def test_parameter_name(self):
        raw = "a.r[0] = 0x0c900000u;\na.r[1] = 0x0c900010u;\n(void)tc_call(0x8c001000u, &a);\n"
        got = gi.arg_evidence(raw, MIRROR, self.Reg())
        self.assertEqual(got, {0x8c900000: ("Fx_Spawn", "table")})  # `a` is a placeholder name: control

    def test_unnamed_callee(self):
        raw = "a.r[0] = 0x0c900000u;\n(void)tc_call(0x8c002000u, &a);\n"
        self.assertEqual(gi.arg_evidence(raw, MIRROR, self.Reg()), {})


class DataFunctions(unittest.TestCase):
    """readable_check.data_functions: a function pointer raw C stores in its mirror form (0x0c9099b8u, the Spawn
    overlays and PS2) is declared by name; with the mirror unknown (control) it was missed and the agent's
    `fn_8c9099b8` failed to link."""

    def run_it(self, mirror):
        import tempfile
        from unittest import mock
        from core import paths, readable_check, retire
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            (t / "matched").mkdir()
            (t / "harness" / "8c9099b8").mkdir(parents=True)
            (t / "harness" / "8c9099b8" / "meta.json").write_text("{}")
            (t / "matched" / "8c000000.c").write_text("wr32(p + 200u, 0x0c9099b8u);\nwr32(p + 4u, 0x0c9099b9u);\n")
            (t / "catalog.json").write_text('{"functions": [{"addr": "8c9099b8"}, {"addr": "8c000000"}]}')
            with mock.patch.object(paths, "MATCHED", t / "matched"), mock.patch.object(paths, "HARNESS", t / "harness"),                     mock.patch.object(paths, "CATALOG", t / "catalog.json"),                     mock.patch.object(retire, "mirrors", lambda ws: mirror),                     mock.patch.object(readable_check, "_STARTS", {}):
                class Reg:
                    functions = {}
                return readable_check.data_functions(Reg(), "8c000000", {"callees": []})

    def test_mirror_form(self):
        self.assertEqual(self.run_it({"0c": "8c"}), ["8c9099b8"])
        self.assertEqual(self.run_it({}), [])  # control: no mirror known, the literal is 0c9099b8


if __name__ == "__main__":
    unittest.main()
