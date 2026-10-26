"""Stale jump-in parents (core/joined.py; decomp.py requeue_joined, run by setup, catalog and seed).
1. grown: a function the run removed whose entry now lies in another function's body, which did not hold it before;
   stale: of those parents, the matched ones whose C still names the entry (a 0x literal or a name ending in it);
2. requeue_joined on a workspace: the stale parent is requeued with a note (tools/requeue.py offline: status queued,
   matched C moved to matched_stale/); a parent whose C names nothing, and one not matched, are left alone.
The replay over the game repos' history is in the docstring of core/joined.py.
Run: python -W error tests/test_joined.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))
from test_sync import PROJECT, TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

import decomp  # noqa: E402
from core import joined, paths  # noqa: E402

# Spawn ovl 8c5a70c0 (matched; its C calls its own epilogue 8c5a73a2), 8c5a9a00 (grew over 8c5a9a9c, which its C did
# inline), 8c5b23a0 (not matched), 8c010000 (unchanged); 8c5a73a2, 8c5a9a9c and 8c5b30d2 are removed by the join.
BEFORE = {"8c010000", "8c5a70c0", "8c5a73a2", "8c5a9a00", "8c5a9a9c", "8c5b23a0", "8c5b30d2"}
AFTER = {"8c010000", "8c5a70c0", "8c5a9a00", "8c5b23a0"}
BODIES_BEFORE = """8c010000 8c010000 8c010020
8c5a70c0 8c5a70c0 8c5a7380
8c5a73a2 8c5a73a2 8c5a73b0
8c5a9a00 8c5a9a00 8c5a9a9c
8c5a9a9c 8c5a9a9c 8c5a9b00
8c5b23a0 8c5b23a0 8c5b30d2
8c5b30d2 8c5b30d2 8c5b3100
"""
BODIES_AFTER = """8c010000 8c010000 8c010020
8c5a70c0 8c5a70c0 8c5a7380
8c5a70c0 8c5a73a2 8c5a73b0
8c5a9a00 8c5a9a00 8c5a9b00
8c5b23a0 8c5b23a0 8c5b3100
"""
C = {"8c5a70c0": "u32 fn_8c5a70c0(u32 a) {\n    return tc_call(0x8c5a73a2u, a);\n}\n",
     "8c5a9a00": "void fn_8c5a9a00(void) {\n    g_count += 1;  /* 9a9c: the loop tail, inline */\n}\n",
     "8c010000": "void fn_8c010000(void) {\n}\n"}


class Rule(unittest.TestCase):
    def test_grown_and_stale(self):
        g = joined.grown(BEFORE, joined.read_bodies(BODIES_BEFORE), AFTER, joined.read_bodies(BODIES_AFTER))
        self.assertEqual(g, {"8c5a70c0": ["8c5a73a2"], "8c5a9a00": ["8c5a9a9c"], "8c5b23a0": ["8c5b30d2"]})
        self.assertEqual(joined.stale(g, C.get), {"8c5a70c0": ["8c5a73a2"]})

    def test_names(self):  # every way a matched C names a function: PS2 8c17c080 (tc_callf), Spawn 8c5a73e0 (call_fn)
        g = {"8c000000": ["8c17c0e0"]}
        for text in ("fr0 = tc_callf(0x8c17c0e0u, &a);", "(void)call_fn(0x8C17C0E0u, f);", "fn_8c17c0e0(a);",
                     "LAB_8c17c0e0: ;", "/* jmp to 0x8c17c0e0 */"):
            self.assertEqual(joined.stale(g, lambda _a: text), {"8c000000": ["8c17c0e0"]}, text)
        for text in ("tc_call(0x8c17c0e04);", "x = 0x18c17c0e0;", "g_8c17c0e0a = 1;", "8c17c0e0"):
            self.assertEqual(joined.stale(g, lambda _a: text), {}, text)


class RequeueJoined(unittest.TestCase):
    def test_requeues_only_the_stale_parent(self):
        with tempfile.TemporaryDirectory(prefix="joined_", dir=TOOL / "tests" / ".work") as d:
            ws = pathlib.Path(d)
            (ws / "project.json").write_text(json.dumps(dict(PROJECT, name="joined")), encoding="utf-8")
            st = ws / "state"
            st.mkdir()
            (ws / "matched").mkdir()
            led = {"loop": False, "functions": {a: {"addr": a, "status": "matched", "verdict": "PASS"} for a in C}}
            led["functions"]["8c5b23a0"] = {"addr": "8c5b23a0", "status": "failed"}
            (st / "ledger.json").write_text(json.dumps(led), encoding="utf-8")
            (st / "catalog.json").write_text(json.dumps({"functions": [{"addr": a} for a in sorted(AFTER)]}),
                                             encoding="utf-8")
            (st / "function_bodies.txt").write_text(BODIES_AFTER, encoding="utf-8")
            for a, text in C.items():
                (ws / "matched" / f"{a}.c").write_text(text, encoding="utf-8")
            with mock.patch.multiple(paths, LEDGER=st / "ledger.json", CATALOG=st / "catalog.json",
                                     FUNCTION_BODIES=st / "function_bodies.txt", MATCHED=ws / "matched"):
                out = decomp.requeue_joined(ws, "catalog", BEFORE, joined.read_bodies(BODIES_BEFORE))
            self.assertEqual(out, {"8c5a70c0": ["8c5a73a2"]})
            rows = json.loads((st / "ledger.json").read_text(encoding="utf-8"))["functions"]
            self.assertEqual(rows["8c5a70c0"]["status"], "queued")
            self.assertIn("8c5a73a2 joined this function's body (TcJumpIn)", rows["8c5a70c0"]["requeued"])
            self.assertTrue((ws / "matched_stale" / "8c5a70c0.c").exists())  # the job judges it first
            self.assertEqual(rows["8c5a9a00"]["status"], "matched")  # grew, but its C names nothing removed
            self.assertTrue((ws / "matched" / "8c5a9a00.c").exists())
            self.assertEqual(rows["8c5b23a0"]["status"], "failed")  # not matched
            self.assertEqual(rows["8c010000"], led["functions"]["8c010000"])


if __name__ == "__main__":
    unittest.main()
