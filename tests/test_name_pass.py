"""name_pass tests: what the model may decide (names only) and what stays the raw signature's (types, order, storage);
a look-alike name takes its address; a bad parameter name or count falls back. The lift's vector locals and the webs join.
Synthetic data; no session runs. Run: python -W error tests/test_name_pass.py.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import name_pass  # noqa: E402
import stack_params  # noqa: E402

from core import readable  # noqa: E402
from core.rtx import webs  # noqa: E402


def meta(sig, *storage):
    params = [{"name": f"param_{i}", "storage": s, "stack_offset": -1} for i, s in enumerate(storage)]
    return {"signature": sig, "params": params}


class Entries(unittest.TestCase):
    def setUp(self):
        self.reg = readable.Registry.empty()

    def entry(self, m, name, params, used=None):
        return name_pass.build_entry(self.reg, "8c000040", m, name, params, set() if used is None else used)

    def test_types_stay_the_raw_signatures(self):
        e = self.entry(meta("s32 fn_8c000040(u32 param_1, f32 param_2)", "r4", "fr4"), "Car_Turn", ["car", "angle"])
        self.assertEqual(e["proto"], "s32 Car_Turn(u32 car, f32 angle)")
        self.assertNotIn("storage", e)

    def test_a_name_in_use_takes_the_address(self):
        used = {"View_Nop"}
        e = self.entry(meta("void fn_8c000040(void)"), "View_Nop", [], used)
        self.assertEqual(e["proto"], "void View_Nop_8c000040(void)")
        self.assertIn("View_Nop_8c000040", used)

    def test_bad_params_fall_back(self):
        m = meta("u32 fn_8c000040(u32 param_1, u32 param_2)", "r4", "r5")
        self.assertEqual(self.entry(m, "A_B", ["only_one"])["proto"], "u32 A_B(u32 arg1, u32 arg2)")
        self.assertEqual(self.entry(m, "A_C", ["n1", "param_2"])["proto"], "u32 A_C(u32 arg1, u32 arg2)")
        self.assertEqual(self.entry(m, "A_D", ["same", "same"])["proto"], "u32 A_D(u32 same, u32 arg2)")

    def test_storage_kept_when_not_the_convention(self):
        e = self.entry(meta("u32 fn_8c000040(u32 param_1, u32 param_r4)", "r5", "r4"), "A_B", ["first", "second"])
        self.assertEqual(e["storage"], ["r5", "r4"])

    def test_names_refused(self):
        for bad in ("fn_8c000040", "lower_case", "NoUnderscore", "A_8c000040", "", "A_"):
            self.assertFalse(name_pass.valid_name(bad), bad)
        self.assertTrue(name_pass.valid_name("Car_ApplyDamage"))


class Reply(unittest.TestCase):
    def test_only_lines_for_the_batch(self):
        text = 'sure:\n{"addr":"8c000040","name":"A_B","params":[]}\n{"addr":"8c000060","name":"C_D","params":["x"]}\nnot json'
        self.assertEqual(name_pass.parse_reply(text, {"8c000040"}), {"8c000040": ("A_B", [])})


class Join(unittest.TestCase):
    def test_a_path_without_an_assignment_holds_the_entry_value(self):
        joined = webs._join({"a": frozenset({("def", 1)})}, {})
        self.assertEqual(joined["a"], frozenset({("def", 1), ("entry", "a")}))


class StackParams(unittest.TestCase):
    def test_a_buffer_argument_is_found_by_position(self):
        text = "x = A(1u, (u32)frame_buf36, B((u32)frame_buf8 + 4)); y = C(frame_buf4);"
        self.assertEqual(stack_params.buffer_args(text), {("A", 1), ("B", 0)})

    def test_only_a_u32_or_s32_parameter_changes(self):
        got, changed = stack_params.retyped("u32 A(u32 dst, s32 src, f32 k);", {1, 2})
        self.assertEqual(got, "u32 A(u32 dst, void *src, f32 k);")
        self.assertTrue(changed)
        self.assertFalse(stack_params.retyped("u32 A(void *dst);", {0})[1])


if __name__ == "__main__":
    unittest.main()
