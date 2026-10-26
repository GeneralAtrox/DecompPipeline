"""ghidra.export keeps its private copy of slot 1 while slot 1 is the very same copy, instead of copying the project
for every call (port/tools/sdk_ref.py exports 5 names a call); Ghidra is never run (_headless is faked).
1. two exports from an unchanged slot 1: one copy;
2. a refreshed slot 1 (copied_from.txt rewritten, even with the same text) copies again;
3. with no slot copies the master is copied for every export (prepares write marks into it);
4. an export copy that lost its project file is copied again.
Run: python -W error tests/test_export_copy.py.
"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import ghidra, paths  # noqa: E402


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class ExportCopy(unittest.TestCase):
    def setUp(self):
        d = self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        put(d / "ghidra" / "G.gpr", "master")
        self.copies, self.runs = [], []
        real = ghidra.shutil.copytree
        ps = [mock.patch.object(paths, "GHIDRA_PROJECT", d / "ghidra"),
              mock.patch.object(paths, "GHIDRA_SLOTS", d / "slots"), mock.patch.object(paths, "GHIDRA_NAME", "G"),
              mock.patch.object(paths, "sync_scripts", lambda: d / "scripts"),
              mock.patch.object(ghidra.shutil, "copytree", lambda s, t, **k: self.copies.append(pathlib.Path(s).name)
                                or real(s, t, **k)),
              mock.patch.object(ghidra, "_headless", lambda cfg, args, log, project=None, **k: self.runs.append(
                  (project / "G.gpr").read_text(encoding="utf-8")))]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.cfg = {"binary_path": d / "game.bin"}

    def slot1(self, text="1:2"):
        put(self.d / "slots" / "slot1" / "G.gpr", f"slot1 {text}")
        put(self.d / "slots" / "slot1" / "copied_from.txt", text)

    def export(self):
        ghidra.export(self.cfg, ["8c010000"], self.d / "out")

    def test_slot_copy_kept(self):
        self.slot1()
        self.export()
        self.export()
        self.assertEqual(self.copies, ["slot1"])
        self.assertEqual(self.runs, ["slot1 1:2", "slot1 1:2"])
        self.slot1("1:3")  # refreshed
        self.export()
        self.assertEqual((self.copies[-1], self.runs[-1]), ("slot1", "slot1 1:3"))
        st = (self.d / "slots" / "slot1" / "copied_from.txt").stat()
        self.slot1("1:3")  # refreshed again with the same stamp: a newer mtime
        os.utime(self.d / "slots" / "slot1" / "copied_from.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        self.export()
        self.assertEqual(len(self.copies), 3)
        (self.d / "export_copy" / "G.gpr").unlink()
        self.export()
        self.assertEqual(len(self.copies), 4)
        self.assertEqual(self.runs[-1], "slot1 1:3")

    def test_master_copied_every_time(self):
        self.export()
        self.export()
        self.assertEqual(self.copies, ["ghidra", "ghidra"])


if __name__ == "__main__":
    unittest.main()
