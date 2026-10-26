"""tools/regress_project.ghidra_copies: a gate run and a --targeted (catalog) run keep their own private Ghidra copies,
so running one after the other copies nothing again (finding R4); Ghidra is never run (the catalog step is faked).
1. plain, catalog, plain, catalog: each mode copies its master and slots once; the second round copies nothing;
2. a reused catalog master gets back the very catalog files its own step made, though a plain run copied the live
   catalog over the shared work state in between (and a run whose fixture needs the catalog does the same in setup);
3. a changed live catalog or changed catalog scripts copy again and run the step again; a kept catalog cut short
   (no made_on.txt) or made on another copy runs the step again.
Run: python -W error tests/test_regress_copies.py.
"""
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

import regress_project as rp  # noqa: E402


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Copies(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="rcopies", dir=TMP))
        self.d = d
        put(d / "live" / "ghidra" / "G.gpr", "project")
        put(d / "live" / "ghidra" / "G.rep" / "data.bin", "program")
        put(d / "live" / "state" / "catalog.json", "live catalog")
        put(d / "scripts" / "TcCatalog.java", "v1")
        self.live = {"catalog": d / "live" / "state" / "catalog.json", "slots": d / "live" / "slots",
                     "ghidra": d / "live" / "ghidra"}
        self.copies, self.steps = [], []
        real = rp.copy_tree_if_stale
        self.p = mock.patch.object(rp, "copy_tree_if_stale", side_effect=lambda s, dst, st: (
            real(s, dst, st) and (self.copies.append(dst.relative_to(d / "gwork").as_posix()) or True)))
        self.p.start()
        self.addCleanup(self.p.stop)

    def paths(self):  # setup()'s redirection: a fresh namespace each run, as each run is its own process
        state = self.d / "work" / "state"
        return types.SimpleNamespace(
            GHIDRA_PROJECT=self.d / "gwork" / "master", GHIDRA_SLOTS=self.d / "gwork" / "slots", GHIDRA_NAME="G",
            CATALOG=state / "catalog.json", CODE_RANGES=state / "code_ranges.txt",
            FUNCTION_BODIES=state / "function_bodies.txt", sync_scripts=lambda: self.d / "scripts")

    def run_mode(self, catalog):
        paths = self.paths()
        if not catalog:  # setup() copies the live catalog into the work state on a plain run
            put(paths.CATALOG, self.live["catalog"].read_text(encoding="utf-8"))

        def step(cfg):  # ghidra.catalog: the fixups change the master, TcCatalog writes the three files
            n = len(self.steps) + 1
            self.steps.append(n)
            put(paths.GHIDRA_PROJECT / "G.rep" / "data.bin", f"fixed {n}")
            for f in (paths.CATALOG, paths.CODE_RANGES, paths.FUNCTION_BODIES):
                put(f, f"rebuilt {n} {f.name}")

        self.copies.clear()
        rp.ghidra_copies(paths, types.SimpleNamespace(catalog=step), {}, self.live, 2, catalog, lambda m: None)
        return paths

    def test_alternating_modes(self):
        self.run_mode(False)
        self.assertEqual(self.copies, ["master", "slots/slot1", "slots/slot2"])
        p = self.run_mode(True)
        self.assertEqual(self.copies, ["master_catalog", "slots_catalog/slot1", "slots_catalog/slot2"])
        self.assertEqual((p.GHIDRA_PROJECT.name, p.GHIDRA_SLOTS.name), ("master_catalog", "slots_catalog"))
        self.assertEqual(self.steps, [1])
        self.assertEqual((self.d / "gwork" / "master" / "G.rep" / "data.bin").read_text(encoding="utf-8"), "program")
        p = self.run_mode(False)
        self.assertEqual((self.copies, p.CATALOG.read_text(encoding="utf-8")), ([], "live catalog"))
        p = self.run_mode(True)
        self.assertEqual((self.copies, self.steps), ([], [1]))  # nothing copied, the step not run again
        for f in (p.CATALOG, p.CODE_RANGES, p.FUNCTION_BODIES):  # its own step's files, not the live catalog
            self.assertEqual(f.read_text(encoding="utf-8"), f"rebuilt 1 {f.name}")

    def test_changes_copy_again(self):
        self.run_mode(False)
        self.run_mode(True)
        put(self.d / "scripts" / "TcCatalog.java", "v2")  # changed catalog scripts: the catalog copy only
        self.run_mode(True)
        self.assertEqual((self.copies[0], self.steps), ("master_catalog", [1, 2]))
        self.run_mode(False)
        self.assertEqual(self.copies, [])
        put(self.live["catalog"], "live catalog 2")  # a changed live project: both modes copy again
        self.run_mode(False)
        self.assertEqual(self.copies, ["master", "slots/slot1", "slots/slot2"])
        self.run_mode(True)
        self.assertEqual((self.copies[0], self.steps), ("master_catalog", [1, 2, 3]))

    def test_kept_catalog_checked(self):
        self.run_mode(True)
        out = self.d / "gwork" / "catalog_out"
        (out / "made_on.txt").unlink()  # cut short before its mark
        self.run_mode(True)
        self.assertEqual(self.steps, [1, 2])
        put(out / "made_on.txt", "another copy")
        self.run_mode(True)
        self.assertEqual(self.steps, [1, 2, 3])
        (out / "code_ranges.txt").unlink()
        self.run_mode(True)
        self.assertEqual(self.steps, [1, 2, 3, 4])
        self.run_mode(True)
        self.assertEqual(self.steps, [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main(verbosity=1)
