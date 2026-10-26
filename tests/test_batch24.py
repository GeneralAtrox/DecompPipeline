"""Batch 24 tests: the adoption gate for machine drafts (core/draft_gate): only short drafts with no literal the
headers name are adopted; a longer or number-keeping draft that passes is the agent's start instead."""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import draft_gate, draft_run  # noqa: E402

HEADER = ("#define CLOCK_TIMER_HZ 781250u /* ticks per second, a comment that\n   continues on the next line */\n"
          "#define CLOCK_FPS_WINDOW_MS (1000u)\n#define MASK_HI 0x80000000u\n#define HALF 0.5f\n"
          "#define TWICE(x) ((x) * 2)\ntypedef enum { ANIM_REC_POSE = 1, ANIM_REC_200 = 200, ANIM_REC_LAST } AnimRec;\n"
          "/* #define IN_COMMENT 4242 */\n")
SHORT = '#include "game.h"\n\ns32 Config_NoOp6(void)\n{\n    return 0;\n}\n'
CLOCK = ('#include "game.h"\n\nvoid Clock_Tick(void)\n{\n    while (g_rem >= 781250u) {\n        g_rem -= 781250u;\n'
         '        g_ms = g_ms + 1u;\n    }\n    g_x = 0.5f;\n}\n')
ANIM = '#include "game.h"\n\ns32 Anim_Size(s32 t)\n{\n    return t == 200 ? 12 : 4;\n}\n'


class Gate(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (self.d / "sys").mkdir()
        (self.d / "sys" / "s09.h").write_text(HEADER, encoding="utf-8")
        (self.d / "game.h").write_text("#define g_rem (*(u32 *)0x8c0a0000u)\n#define G_SIZE 12\n", encoding="utf-8")

    def test_named_values(self):
        v = draft_gate.named_values(self.d)
        self.assertEqual(v[781250], ["CLOCK_TIMER_HZ"])  # its comment continues on the next line
        self.assertEqual(v[1000], ["CLOCK_FPS_WINDOW_MS"])
        self.assertEqual(v[0x80000000], ["MASK_HI"])
        self.assertEqual((v[1], v[200]), (["ANIM_REC_POSE"], ["ANIM_REC_200"]))
        self.assertNotIn(4242, v)  # inside a comment
        self.assertNotIn(12, v)  # only in the generated game.h
        self.assertNotIn(2, v)  # a function-like macro is not a constant

    def test_short_plain_draft_passes(self):
        self.assertEqual(draft_gate.reasons(SHORT, draft_gate.named_values(self.d)), [])

    def test_long_and_named_numbers_refused(self):
        got = draft_gate.reasons(CLOCK, draft_gate.named_values(self.d), max_lines=6)
        self.assertEqual(got, ["8 lines (drafts are adopted up to 6)", "781250u is CLOCK_TIMER_HZ"])  # once; 1u, 0.5f ok
        self.assertEqual(draft_gate.reasons(ANIM, draft_gate.named_values(self.d)), ["200 is ANIM_REC_200"])
        # the limit comes from the caller (config readable_draft_max_lines); none by default (readable-tx)
        self.assertEqual(draft_gate.reasons(CLOCK, {}, max_lines=8), [])
        self.assertEqual(draft_gate.reasons(CLOCK, {}), [])

    def test_judge_applies_the_gate(self):
        """A draft that passes the judge but fails the gate is not adoptable; with --draft start it is the agent's
        start (the batch 23 hook)."""
        path = self.d / "draft.c"
        ok = {"verdict": "PASS", "stage": "compare"}
        with mock.patch.object(draft_run, "make", lambda *a: (CLOCK, [])), \
                mock.patch.object(draft_run, "draft_path", lambda a: path), \
                mock.patch.object(draft_run.rcheck, "check_readable", lambda *a, **k: ok), \
                mock.patch.object(draft_gate.readable, "INCLUDE", self.d):
            _, res, gaps = draft_run.judge({"readable_draft_max_lines": 6}, None, "8c010000", {})
            self.assertFalse(draft_run.adoptable(res, gaps))
            self.assertTrue(draft_run.why(res, gaps).startswith("passes; not adopted: 8 lines"))
            _, res, gaps = draft_run.judge({"readable_draft_max_lines": 12}, None, "8c010000", {})
            self.assertEqual(gaps, ["781250u is CLOCK_TIMER_HZ"])
        with mock.patch.object(draft_run, "make", lambda *a: (SHORT, [])), \
                mock.patch.object(draft_run, "draft_path", lambda a: path), \
                mock.patch.object(draft_run.rcheck, "check_readable", lambda *a, **k: ok), \
                mock.patch.object(draft_gate.readable, "INCLUDE", self.d):
            _, res, gaps = draft_run.judge({}, None, "8c010000", {})
            self.assertTrue(draft_run.adoptable(res, gaps))


if __name__ == "__main__":
    unittest.main()
