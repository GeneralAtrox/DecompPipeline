"""The judge's guest stack window (core/compare.judged, readable_stack.frame_lo): only the original's own frame is
scratch.

compare dropped every write at or above STACK_LO (0x8cff0000), and the readable judge's pointer pairing, out-pointer
copies and varargs used the same bound, so stores to real globals in 0x8cff0000..0x8cffcfff (Spawn's save data at
0x8cff9000 and 0x8cff6100) were never judged. Now the scratch starts at frame_lo() (entry r15 less
"readable_frame_max", default 0x1000), as in what the agent sees, and a global the oracle names is judged even inside
it. One planted miss and one control per site:
1. compare_vector: a store to 0x8cff9000 the C code leaves out FAILs (it passed before); the own frame (0x8cffdf00)
   and the stack arguments (0x8cffe004) stay scratch; a named global in the frame window is judged; frame_lo moves
   the bound; the judge's callers pass frame_lo();
2. readable_check.map_stack_args: a global 0x8cff9000 passed as an argument is never paired with a host local (that
   hid a readable call passing &local for &g_save); a frame address still pairs;
3. readable_stack.pair_stores: a stored global address is no local; a store into 0x8cff9000 pairs like any memory;
4. readable_outptr: a callee out-pointer to 0x8cff9000 is a global, so no copy lines are made for it;
5. readable_varargs._read_cstr: a format string at 0x8cff9000 reads from the initial memory.
Synthetic data. Run: python -W error tests/test_judge_window.py (also from test_tooling.py).
"""
import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import compare, readable_outptr, readable_stack, readable_varargs  # noqa: E402
from core.readable_check import map_stack_args  # noqa: E402
from core.readable_outptr import Spec  # noqa: E402

SAVE, FRAME, ARG = 0x8CFF9000, 0x8CFFDF00, 0x8CFFE004  # Spawn's save data; SP - 0x100; stack argument 1
R4 = compare.REG_INDEX["r4"]
HOST = (0x00100000, 0x00200000, None)  # the readable run's host stack
VEC = {"patches": []}
META = {"ret": "void", "callees": [{"addr": "8c0683f0", "regs": ["r4"]}], "globals": []}


def word(addr, v):
    return {addr + i: (v >> (8 * i)) & 0xFF for i in range(4)}


def result(writes=None, calls=()):
    return {"done": True, "faults": [], "r0": 0, "fr0": 0, "writes": writes or {}, "calls": [list(c) for c in calls],
            "xf": [0] * 16}


def call(r4):
    row = [0x8C0683F0] + [0] * 56
    row[compare.REG_INDEX["r4"]] = r4
    return row


class Compare(unittest.TestCase):
    def judge(self, addr, meta=META, **kw):
        return compare.compare_vector(meta, b"", VEC, result(word(addr, 0x12345678)), result(), **kw)

    def test_planted_miss_below_the_frame(self):
        d = self.judge(SAVE)
        self.assertEqual(len(d), 1, d)
        self.assertIn("memory 0x8cff9000", d[0])
        self.assertIn("your code did not write here", d[0])
        same = result(word(SAVE, 0x12345678))
        self.assertEqual(compare.compare_vector(META, b"", VEC, same, same), [])  # control: the store made

    def test_own_frame_and_stack_args_are_scratch(self):
        self.assertEqual(self.judge(FRAME), [])
        self.assertEqual(self.judge(ARG), [])
        self.assertTrue(self.judge(compare.FRAME_LO - 4))  # the word just below the frame is memory

    def test_named_global_in_the_frame_window(self):
        self.assertTrue(self.judge(FRAME, dict(META, globals=["8cffdf00"])))
        self.assertEqual(self.judge(FRAME, dict(META, globals=["8cffdf04"])), [])

    def test_frame_lo_moves_the_bound(self):
        self.assertTrue(self.judge(FRAME, frame_lo=compare.SP - 0xF0))
        self.assertEqual(self.judge(SAVE, frame_lo=compare.STACK_LO), [])  # the old bound: the miss is hidden
        self.assertEqual(readable_stack.frame_lo(), compare.FRAME_LO)

    def test_judges_pass_frame_lo(self):
        """check.py, readable_check.py and xemu_check.py compare with the project's frame_lo()."""
        for rel in ("core/check.py", "core/readable_check.py", "tools/xemu_check.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                     and getattr(c.func, "attr", getattr(c.func, "id", "")) == "compare_vector"]
            self.assertTrue(calls, rel)
            for c in calls:
                # frame_lo is the 6th; check.py and readable_check.py add core/strict_judge's modes and report list
                self.assertIn(len(c.args) + len(c.keywords), (6, 8), f"{rel}:{c.lineno}")
            self.assertIn("frame_lo()", (ROOT / rel).read_text(encoding="utf-8"), rel)


class Pairing(unittest.TestCase):
    def test_global_argument_is_not_a_local(self):
        exp, act = result(calls=[call(SAVE)]), result(calls=[call(0x00190000)])
        self.assertEqual(map_stack_args(META, exp, act, HOST), [])
        self.assertEqual(act["calls"][0][R4], 0x00190000)  # not rewritten: the call differs
        self.assertTrue(any("argument" in x for x in compare.compare_vector(META, b"", VEC, exp, act)))
        exp, act = result(calls=[call(0x8CFFDC18)]), result(calls=[call(0x00190000)])
        self.assertEqual(map_stack_args(META, exp, act, HOST), [])
        self.assertEqual(act["calls"][0][R4], 0x8CFFDC18)  # control: a frame address pairs

    def test_pair_stores(self):
        w = 0x8C0FC508
        exp, act = {"writes": word(w, SAVE)}, {"writes": word(w, 0x0019FF10)}
        readable_stack.pair_stores(exp, act, HOST, {}, {}, lambda a: 0)
        self.assertNotEqual(act["writes"], exp["writes"])  # &g_save is no local
        exp, act = {"writes": word(SAVE, 0x8CFFDFAC)}, {"writes": word(SAVE, 0x0019FF10)}
        readable_stack.pair_stores(exp, act, HOST, {}, {}, lambda a: 0)
        self.assertEqual(act["writes"], exp["writes"])  # a local's address stored into the save data pairs


class OutPointers(unittest.TestCase):
    def test_global_out_pointer_gets_no_copy(self):
        s = Spec("8c0b3188", 2, "u32", 4, "r5", True)
        exp = {0: {"calls": [[0x8C0B3188, 0, SAVE] + [0] * 30]}}
        self.assertEqual(readable_outptr.lines_for(exp, [s], {"u32": 4}.get), {})
        self.assertEqual(readable_outptr.trace_lines(exp, [s._replace(size="trace")]), {})
        frame = {0: {"calls": [[0x8C0B3188, 0, 0x8CFFDE58] + [0] * 30]}}
        self.assertEqual(readable_outptr.lines_for(frame, [s], {"u32": 4}.get), {0: ["O 0 8c0b3188 2 8cffde58 4"]})


class Varargs(unittest.TestCase):
    def test_format_in_a_global_reads(self):
        class Mem:
            def initial(self, a):
                return b"%d\0"[a - SAVE] if SAVE <= a < SAVE + 3 else 0
        self.assertEqual(readable_varargs._read_cstr(Mem(), {"writes": {}}, SAVE), b"%d")
        self.assertIsNone(readable_varargs._read_cstr(Mem(), {"writes": {}}, FRAME))  # control: built in the frame


if __name__ == "__main__":
    unittest.main(warnings="error")
