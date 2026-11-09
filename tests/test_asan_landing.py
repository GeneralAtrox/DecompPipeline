"""tools/asan_landing.py, with planted faults: a kit host-C commit with no ASan line, a run whose plant was not reported, a
replay with an ASan error, a core game left out, a line made on another tree; and the live history since the rule landed.
Run: python -W error tests/test_asan_landing.py
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import asan_landing as al  # noqa: E402

GOOD = """started 01:02 build run clean
run plant_tc: exit 1 in 1 s; ASan errors 1;
   heap-buffer-overflow on address 0x044007b8
run plant_ps2: exit 1 in 1 s; ASan errors 1;
run plant_spawn: exit 1 in 1 s; ASan errors 1;
run tc_frontend: exit 0 in 14 s; ASan errors 0; EXIT: --stop-at reached after 3000 vertical blanks
run ps2_111340: exit 0 in 513 s; ASan errors 0; EXIT: --stop-at reached after 3061 vertical blanks
run spawn_attract: exit 0 in 40 s; ASan errors 0; EXIT: --stop-at reached after 3000 vertical blanks
"""
SATURN = "run plant_robopit: exit 0 in 2 s; ASan errors 1;\nrun robopit_route: exit 0 in 50 s; ASan errors 0;\n"
TREE = "1a2b3c4d5e6f" + "0" * 28


class Trailer(unittest.TestCase):
    def test_a_whole_run_gives_the_line(self):
        line, why = al.trailer("dreamcast", GOOD, TREE)
        self.assertIsNone(why)
        self.assertEqual(line, "ASan: dreamcast tree 1a2b3c4d5e6f: plant caught; tc_frontend 0 errors; ps2_111340 0 errors; "
                               "spawn_attract 0 errors")

    def test_plant_not_reported_is_refused(self):
        _, why = al.trailer("dreamcast", GOOD.replace("plant_ps2: exit 1 in 1 s; ASan errors 1", "plant_ps2: exit 0 in 1 s; ASan errors 0"), TREE)
        self.assertIn("ps2's build did not report its planted overflow", why)
        _, why = al.trailer("dreamcast", GOOD.replace("run plant_spawn", "run plant_other"), TREE)
        self.assertIn("no plant run", why)

    def test_plant_an_error_in_a_replay_is_refused(self):
        _, why = al.trailer("dreamcast", GOOD.replace("tc_frontend: exit 0 in 14 s; ASan errors 0", "tc_frontend: exit 0 in 14 s; ASan errors 2"), TREE)
        self.assertIn("tc_frontend: 2 ASan error(s)", why)
        _, why = al.trailer("dreamcast", GOOD.replace("ps2_111340: exit 0", "ps2_111340: exit killed"), TREE)
        self.assertIn("exit killed", why)

    def test_plant_a_core_game_left_out_is_refused(self):
        _, why = al.trailer("dreamcast", "\n".join(ln for ln in GOOD.splitlines() if "spawn_attract" not in ln), TREE)
        self.assertIn("spawn has no replay", why)


class History(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = pathlib.Path(t.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.base = self.commit("docs/x.md", "start")

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.dir, capture_output=True, text=True, timeout=60, check=True).stdout.strip()

    def commit(self, path, msg):
        f = self.dir / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(msg + "\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def amend_with(self, *consoles, tree=None):
        """The last commit's message gets a line per console, made on its tree (or another)."""
        tree = tree or self.git("rev-parse", "HEAD^{tree}")
        msg = self.git("log", "-1", "--format=%B").split("\n\nASan:")[0]
        lines = [al.trailer(c, GOOD if c == "dreamcast" else SATURN, tree)[0] for c in consoles]
        self.git("commit", "-q", "--amend", "-m", msg + "\n\n" + "\n".join(lines))

    def bad(self):
        return al.problems(f"{self.base}..HEAD", self.dir)

    def test_plant_a_kit_change_with_no_line_is_found(self):
        self.commit("port/pvr/pvr_draw.c", "pvr: draw faster")
        self.assertEqual(len(self.bad()), 1)
        self.assertIn("no ASan: line covers it", self.bad()[0])

    def test_the_landings_last_commit_covers_the_ones_before(self):
        self.commit("port/pvr/pvr_draw.c", "pvr: one")
        self.commit("tools/x.py", "a tool")
        self.commit("port/sdk/kamui2_scene.c", "sdk: two")
        self.amend_with("dreamcast")
        self.assertEqual(self.bad(), [])

    def test_plant_a_shared_change_needs_every_console(self):
        self.commit("port/runtime/frame_extra.c", "runtime: shared")
        self.amend_with("dreamcast")
        self.assertIn("dreamcast, saturn", self.bad()[0])
        self.amend_with("dreamcast", "saturn")
        self.assertEqual(self.bad(), [])

    def test_plant_a_line_made_on_another_tree_is_refused(self):
        self.commit("port/pvr/pvr_draw.c", "pvr: one")
        self.amend_with("dreamcast", tree="f" * 40)
        self.assertTrue(any("not this commit's" in b for b in self.bad()))

    def test_na_needs_a_reason_and_other_files_need_nothing(self):
        self.commit("port/tools/part_draw.py", "a kit tool, not host C")
        self.commit("port/pvr/pvr_draw.c", "pvr: a comment\n\nASan: n/a a comment only")
        self.assertEqual(self.bad(), [])
        self.commit("port/pvr/pvr_parts.c", "pvr: another\n\nASan: n/a")
        self.assertIn("n/a with no reason", " ".join(self.bad()))


class Live(unittest.TestCase):
    def test_every_kit_landing_since_the_rule_has_its_result(self):
        first = al.git("log", "--diff-filter=A", "--format=%H", "--", "tools/asan_landing.py").stdout.split()
        if not first:
            self.skipTest("the rule's commit is not in this checkout")
        bad = al.problems(f"{first[-1]}..HEAD")
        self.assertEqual(bad, [], "run the ASan builds and replays (tools/asan_landing.py), and add the line to the landing's last commit")


if __name__ == "__main__":
    unittest.main()
