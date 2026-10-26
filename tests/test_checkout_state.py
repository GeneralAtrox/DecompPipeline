"""Two DP checkouts on one live project can't change each other's prepares (core/paths.sync_scripts, ghidra.write_facts).
Every checkout wrote the project's one state/target_facts.txt and state/ghidra_scripts/, which Ghidra reads later: on
Spawn (2026-10-21 05:48-06:52) some checkout's oracle_deadline_s 450 (every config 900) reached live prepares and
changed harness 8c04a900. Facts and scripts are now content-named and written once.
1. two processes (checkouts) with other deadlines and another TcVectors.java, on one state folder: after B wrote,
   A's facts file and scripts folder still hold A's, and A's oracle stamp is the same as before;
2. an unchanged config and scripts reuse their file and folder (same path, same stamp);
3. prune_unused drops only content entries nobody used for UNUSED_S (never the one in use);
4. the fsca table (paths.fsca_table: state/fsca/<sha16>.bin, written once): an old checkout writing
   state/fsca_table.bin in another format (the one-table 512 KB file, or the current one) and a new checkout on the
   same state folder: each reads the table it wrote, the new one's oracle stamp never changes, and a table of other
   content (another checkout's) is left alone until nobody used it for UNUSED_S.
5. shared scripts (paths.target_scripts, target.json "shared_scripts"): a target naming core/ghidra_shared/ppc gets
   TcSingle.java beside its own scripts, in sync_scripts' folder and in the stamp; one that names none does not; a
   name in both places, or a missing folder, is refused. An edit of TcSingle moves the stamps of a target naming it
   (a prepare's and oracle_fresh's alike), not another's. While GameCube's TcFixups keeps its own copy of the rule
   (6a924b7), that copy is TcSingle's, character for character.
Run: python -W error tests/test_checkout_state.py (also from test_tooling.py).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import fsca, paths  # noqa: E402

CHECKOUT = r"""
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from core import ghidra, oracle_stamp, paths
state, own = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
paths.STATE, paths.SCRIPTS, paths.TARGET_SCRIPTS = state, state / "ghidra_scripts", own
paths.INPUTS = state.parent / "TcInputs.java"  # the project's, the same for both
cfg = dict(paths.config(), ghidra_timeout_s=int(sys.argv[4]))
facts, scripts = ghidra.write_facts(cfg), paths.sync_scripts()
print(json.dumps({"facts": facts, "scripts": str(scripts),
                  "stamp": oracle_stamp.stamp(cfg, "8c010000", scripts, facts, None, 4, 4)}))
"""


class CheckoutState(unittest.TestCase):
    def setUp(self):
        self.root = TMP / "checkout_state"
        shutil.rmtree(self.root, ignore_errors=True)
        self.state = self.root / "state"
        self.state.mkdir(parents=True)
        (self.root / "TcInputs.java").write_text("// the project's input generator\n", encoding="utf-8")
        for name, extra in (("a", ""), ("b", "\n// checkout B's oracle change\n")):
            own = self.root / name
            shutil.copytree(paths.TARGET_SCRIPTS, own)
            (own / "TcVectors.java").write_bytes((own / "TcVectors.java").read_bytes() + extra.encode())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def checkout(self, name, timeout_s):
        p = subprocess.run([sys.executable, "-c", CHECKOUT, str(TOOL), str(self.state), str(self.root / name),
                            str(timeout_s)], capture_output=True, text=True, timeout=120, env=os.environ)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_two_checkouts(self):
        a = self.checkout("a", 600)  # oracle_budget_s 450
        facts_a = pathlib.Path(a["facts"]).read_bytes()
        scripts_a = {p.name: p.read_bytes() for p in pathlib.Path(a["scripts"]).glob("*.java")}
        self.assertIn("oracle_budget_s 450", facts_a.decode())
        b = self.checkout("b", 900)  # oracle_budget_s 750, and another TcVectors.java
        self.assertNotEqual(a["facts"], b["facts"])
        self.assertNotEqual(a["scripts"], b["scripts"])
        self.assertNotEqual(a["stamp"], b["stamp"])
        self.assertIn("oracle_budget_s 750", pathlib.Path(b["facts"]).read_text(encoding="utf-8"))
        # B wrote nothing A reads
        self.assertEqual(pathlib.Path(a["facts"]).read_bytes(), facts_a)
        self.assertEqual({p.name: p.read_bytes() for p in pathlib.Path(a["scripts"]).glob("*.java")}, scripts_a)
        self.assertEqual(scripts_a["TcVectors.java"], (self.root / "a" / "TcVectors.java").read_bytes())
        # A again: the same file, folder and stamp
        self.assertEqual(self.checkout("a", 600), a)

    def test_prune(self):
        folder = self.root / "facts"
        folder.mkdir()
        old, used, other, tmp = (folder / "0123456789abcdef.txt", folder / "fedcba9876543210.txt",
                                 folder / "notes.txt", folder / ".tmp-0123456789abcdef-1-2")
        for p in (old, used, other, tmp):
            p.write_text("x", encoding="utf-8")
        week = time.time() - paths.UNUSED_S - 60
        for p in (old, used, other, tmp):
            os.utime(p, (week, week))
        paths.prune_unused(folder, used.name)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [used.name, other.name])


NEW = r"""
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from core import fsca, oracle_stamp, paths
state = pathlib.Path(sys.argv[2])
paths.STATE, paths.CATALOG = state, state / "catalog.json"
paths.FSCA_TABLE = state / "fsca" / paths.FSCA_TABLE.name
cfg = {"fsca_table": True, "binary_sha256": "ab", "ghidra_home": "g"}
before = oracle_stamp.stamp_of(cfg, "8c010000", {}, "-", None, 4, 4)
table = paths.fsca_table()  # what this checkout's TcPrepare and runners read
print(json.dumps({"table": str(table), "sha": hashlib.sha256(table.read_bytes()).hexdigest(), "before": before,
                  "stamp": oracle_stamp.stamp_of(cfg, "8c010000", {}, "-", None, 4, 4)}))
"""
OLD = r"""
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from core import fsca
p = pathlib.Path(sys.argv[2]) / "fsca_table.bin"
if sys.argv[3] == "one-table":
    p.write_bytes(fsca.pairs())  # before ecc5573: `python -m core.fsca` wrote one table (512 KB)
else:
    fsca.ensure(p)  # 92e32c0 and before: every prepare made state/fsca_table.bin its current file
"""


class FscaState(unittest.TestCase):
    def setUp(self):
        self.state = TMP / "fsca_state" / "state"
        shutil.rmtree(self.state.parent, ignore_errors=True)
        self.state.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.state.parent, ignore_errors=True)

    def run_py(self, src, *args):
        p = subprocess.run([sys.executable, "-c", src, str(TOOL), str(self.state), *args], capture_output=True,
                           text=True, timeout=300, env=os.environ)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def test_old_writer_new_reader(self):
        legacy = self.state / "fsca_table.bin"
        self.run_py(OLD, "one-table")
        one_table = legacy.read_bytes()
        self.assertEqual(len(one_table), fsca.SIZE // 2)
        new = json.loads(self.run_py(NEW).stdout)  # the new checkout: its own table, the old one untouched
        table = pathlib.Path(new["table"])
        self.assertEqual(table, self.state / "fsca" / f"{fsca.FILE_SHA256[:16]}.bin")
        self.assertEqual(new["sha"], fsca.FILE_SHA256)
        self.assertEqual(legacy.read_bytes(), one_table)  # an old reader still gets the table it wrote
        self.assertEqual(new["before"], new["stamp"])  # the stamp names the content, not whether it was there yet
        mtime = table.stat().st_mtime_ns
        for kind in ("current", "one-table"):  # the old checkout writes again, both formats: the new one's table holds
            self.run_py(OLD, kind)
            self.assertEqual(len(legacy.read_bytes()), fsca.SIZE if kind == "current" else fsca.SIZE // 2)
            again = json.loads(self.run_py(NEW).stdout)
            self.assertEqual(again, new)
            self.assertEqual(table.read_bytes(), fsca.file())
        log = (self.state / "fsca" / "events.log").read_text(encoding="utf-8")
        self.assertEqual(log.count("rewritten"), 1)  # written once: the first use only
        self.assertGreaterEqual(table.stat().st_mtime_ns, mtime)
        # another checkout's table (other content): kept while in use, dropped once nobody used it for UNUSED_S
        other = self.state / "fsca" / "0123456789abcdef.bin"
        other.write_bytes(b"another generator's table")
        self.run_py(NEW)
        self.assertTrue(other.exists())
        week = time.time() - paths.UNUSED_S - 60
        os.utime(other, (week, week))
        os.utime(table, (week, week))
        self.run_py(NEW)
        self.assertEqual(sorted(p.name for p in table.parent.iterdir()), sorted(["events.log", table.name]))
        self.assertEqual(legacy.read_bytes(), one_table)  # never the old checkout's file


class SharedScripts(unittest.TestCase):
    def setUp(self):
        self.root = TMP / "shared_scripts"
        shutil.rmtree(self.root, ignore_errors=True)
        self.target, self.state = self.root / "target", self.root / "state"
        shutil.copytree(paths.TARGET, self.target, ignore=shutil.ignore_patterns("runtime*", "template", "xemu"))
        self.state.mkdir()
        (self.root / "TcInputs.java").write_text("// the project's input generator", encoding="utf-8")
        self.saved = paths.TARGET, paths.TARGET_SCRIPTS, paths.STATE, paths.SCRIPTS, paths.INPUTS
        paths.TARGET, paths.TARGET_SCRIPTS = self.target, self.target / "ghidra_scripts"
        paths.STATE, paths.SCRIPTS = self.state, self.state / "ghidra_scripts"
        paths.INPUTS = self.root / "TcInputs.java"

    def tearDown(self):
        paths.TARGET, paths.TARGET_SCRIPTS, paths.STATE, paths.SCRIPTS, paths.INPUTS = self.saved
        shutil.rmtree(self.root, ignore_errors=True)

    def share(self, folders):
        tj = self.target / "target.json"
        tj.write_text(json.dumps(dict(json.loads(tj.read_text(encoding="utf-8")), shared_scripts=folders)),
                      encoding="utf-8")

    def test_shared(self):
        own = paths.target_scripts()
        self.assertNotIn("TcSingle.java", own)
        self.share(["ppc"])
        got = paths.target_scripts()
        self.assertEqual(got["TcSingle.java"], paths.SHARED_SCRIPTS / "ppc" / "TcSingle.java")
        shared = {p.name for p in (paths.SHARED_SCRIPTS / "ppc").glob("*.java")}  # TcSingle, TcNaN
        self.assertEqual({n: got[n] for n in shared}, {n: paths.SHARED_SCRIPTS / "ppc" / n for n in shared})
        self.assertEqual({n: p for n, p in got.items() if n not in shared}, own)
        folder = paths.sync_scripts()
        self.assertEqual((folder / "TcSingle.java").read_bytes(), got["TcSingle.java"].read_bytes())
        (paths.TARGET_SCRIPTS / "TcSingle.java").write_text("// a second copy\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "TcSingle.java is in both"):
            paths.target_scripts()
        self.share(["nowhere"])
        with self.assertRaisesRegex(RuntimeError, "no folder"):
            paths.target_scripts()

    def test_gamecube_copy(self):
        # until GameCube's TcFixups calls TcSingle, its own copy of the rule (6a924b7) must stay the shared one
        import re
        gc = (TOOL / "targets" / "gamecube_ppc" / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        shared = (paths.SHARED_SCRIPTS / "ppc" / "TcSingle.java").read_text(encoding="utf-8")
        if "static long[] singleResult" not in gc:
            self.skipTest("GameCube's TcFixups has no copy of the rule")
        for m in ("static final FloatFormat F8", "static long singleSource", "static long[] singleResult"):
            end = r"[^\n]*\n" if "F8" in m else r".*?\n    }\n"  # the line, or the method to its closing brace
            got = [re.search(re.escape(m) + end, t, re.S) for t in (gc, shared)]
            self.assertTrue(all(got), m)
            self.assertEqual(got[0].group(0), got[1].group(0), m)

    def test_stamp(self):
        # the stamp hashes the shared files of a target that names them: an edit of TcSingle moves its stamps (both
        # the stamp of a prepare, over sync_scripts' folder, and oracle_fresh's), and not those of a target without
        from core import oracle_fresh, oracle_stamp
        shared = self.root / "shared"
        shutil.copytree(paths.SHARED_SCRIPTS, shared)
        saved, paths.SHARED_SCRIPTS = paths.SHARED_SCRIPTS, shared
        try:
            cfg = paths.config()

            def stamps():  # a prepare's, and oracle_fresh's with its scripts (both with no facts file)
                return (oracle_stamp.stamp(cfg, "82000000", paths.sync_scripts(), None, None, 4, 4),
                        oracle_stamp.stamp_of(cfg, "82000000", oracle_fresh.Stamper(cfg).scripts, "-", None, 4, 4))

            single = shared / "ppc" / "TcSingle.java"
            alone = stamps()
            single.write_bytes(single.read_bytes() + b"// an edit\n")
            self.assertEqual(stamps(), alone)  # no "shared_scripts": TcSingle is not this target's
            self.share(["ppc"])
            before = stamps()
            self.assertNotEqual(before, alone)
            single.write_bytes(single.read_bytes() + b"// another edit\n")
            after = stamps()
            self.assertTrue(after[0] != before[0] and after[1] != before[1])
            self.assertEqual(after[0], after[1])  # a prepare's stamp is the one oracle_fresh expects
        finally:
            paths.SHARED_SCRIPTS = saved


if __name__ == "__main__":
    unittest.main()
