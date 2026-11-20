"""Code after a jump table whose default returns is dropped when no label of it is named (MSVC C4702, unreachable code, under
/W4 /WX; Too Human 82b01e98: a return block copied into an early return left its original, unlabelled, right after the
bctr table `{ u32 g_ = E; switch (g_) { case V: goto L; default: break; } <tail call> return r3; }`; structure.ends took the
closing `}` for an op that runs on, so prune kept it). A label something names still stops the drop.
usage: python tests/test_x360_dead_after_table.py
"""
import pathlib
import sys
import unittest

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path[:0] = [str(X360 / "translator")]
from xenontranslate import structure as S  # noqa: E402

TABLE = ["    { u32 g_ = 5u;", "    switch (g_) {", "    case 0x10u: goto L_c;", "    default: break;", "    }",
         "    return 1ull;", "    }"]


def pruned(lines):
    us = S.parse(lines)
    memo = {}
    S.prune(us, S.all_refs(us, memo), memo)
    return us


def text(us):
    return " ".join(x for u in us for x in (u.lines or [u.name or ""]))


class DeadAfterTable(unittest.TestCase):
    def test_table_ending_in_return_ends(self):
        self.assertTrue(S.ends(S.parse(TABLE)[0]))

    def test_code_after_it_without_a_named_label_is_dropped(self):
        t = text(pruned(TABLE + ["    r3 = 7ull;", "    return r3;", "L_c:;", "    return 9ull;"]))
        self.assertNotIn("7ull", t)
        self.assertIn("9ull", t)

    def test_a_named_label_keeps_what_follows(self):
        t = text(pruned(["    if (a) goto L_t;"] + TABLE + ["L_t:;", "    r3 = 7ull;", "    return r3;", "L_c:;", "    return 9ull;"]))
        self.assertIn("7ull", t)

    def test_table_that_runs_on_does_not_end(self):
        run_on = [x for x in TABLE if "return 1ull" not in x]
        self.assertFalse(S.ends(S.parse(run_on)[0]))


if __name__ == "__main__":
    unittest.main()
