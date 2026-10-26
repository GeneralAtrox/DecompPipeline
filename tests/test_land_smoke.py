"""tools/land_smoke.py, the live-prepare smoke for landings, with the preparation faked (no Ghidra):
1. only when needed: a change of prepare class (core/fsca.py, a Ghidra script) or of no known class smokes its
   target; docs, unit tests or the judge alone run nothing;
2. each game's smallest matched function with a 16+ dev vector harness is probed; a preparation error (ecc5573's:
   TcPrepare refused the one-table 512 KiB fsca file), a refusal, no valid vector or a non-PASS fails the smoke;
3. no Ghidra slot within the wait fails it ("no slot", exit 3): never skipped, and no probe starts.
Run: python -W error tests/test_land_smoke.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tools")]
import land_smoke  # noqa: E402

ECC5573 = ("Ghidra failed (rc=0): java.lang.IllegalStateException: fsca table /p/state/fsca_table.bin: 524288 bytes, "
           "not 1048576 (both tables: python -m core.fsca)")
PASS = {"dev": "48/48", "dev_valid": 48, "verdict": "PASS", "failing": "0/96"}


def workspace(root, name, fns, target="dreamcast_sh4"):
    """fns: {addr: (bytes, status, dev_valid or None (no harness), has matched C)}."""
    ws = pathlib.Path(root) / name
    (ws / "state").mkdir(parents=True)
    (ws / "project.json").write_text(json.dumps({"target": target}), encoding="utf-8")
    (ws / "state" / "catalog.json").write_text(json.dumps({"functions": [{"addr": a, "bytes": b}
                                                                         for a, (b, *_) in fns.items()]}), "utf-8")
    (ws / "state" / "ledger.json").write_text(json.dumps({"functions": {a: {"status": s}
                                                                        for a, (_, s, *_) in fns.items()}}), "utf-8")
    for a, (_, _, dev, c) in fns.items():
        if dev is not None:
            (ws / "harness" / a).mkdir(parents=True)
            (ws / "harness" / a / "oracle_summary.json").write_text(json.dumps({"dev_valid": dev}), "utf-8")
        if c:
            (ws / "matched").mkdir(exist_ok=True)
            (ws / "matched" / f"{a}.c").write_text("/* matched */\n", "utf-8")
    return str(ws)


class LandSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="landsmoke_", dir=TOOL / "tests" / ".work")
        fns = {"8c010000": (8, "matched", 48, True), "8c010100": (4, "matched", 8, True),  # too few vectors
               "8c010200": (6, "matched", 48, False), "8c010300": (2, "skipped", 48, True),  # no C; not matched
               "8c010400": (6, "matched", None, True), "8c010500": (16, "matched", 48, True)}  # no harness; bigger
        self.spaces = {k: workspace(self.tmp.name, k, fns) for k in land_smoke.GAMES}
        self.spaces["gc"] = workspace(self.tmp.name, "gc", fns, target="gamecube_ppc")
        self.p = mock.patch.object(land_smoke.toolcfg, "patterns", lambda tests: {"projects": self.spaces})
        self.p.start()
        self.runs = []
        self.out = pathlib.Path(self.tmp.name) / "smoke.json"

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def smoke(self, files, rows=None, slot=lambda limit, cap: True, projects=None):
        def run(ws, key, addr, timeout, out):
            self.runs.append((key, addr))
            return dict((rows or {}).get(key, PASS), addr=addr)
        with mock.patch.object(land_smoke.rt, "changed_files", lambda base, root: ("mb", files, None)), \
                mock.patch("sys.stdout"):
            rc = land_smoke.main(["--json", str(self.out)] + (["--projects", projects] if projects else []),
                                 run=run, slot=slot)
        return rc, json.loads(self.out.read_text(encoding="utf-8"))

    def test_only_when_needed(self):
        for files in (["docs/NEW_GAME.md", "tests/test_xmatch_ties.py"], ["core/lint.py"], ["port/tools/build.py"]):
            rc, rep = self.smoke(files)
            self.assertEqual((rc, self.runs), (0, []), files)
            self.assertIn("not needed", rep["result"])
        for files in (["core/fsca.py"], ["targets/dreamcast_sh4/ghidra_scripts/TcPrepare.java"], ["a_new_file.txt"]):
            self.runs.clear()
            rc, rep = self.smoke(files)
            self.assertEqual(rc, 0, files)
            self.assertEqual(self.runs, [(k, "8c010000") for k in land_smoke.GAMES], files)  # the smallest usable
        self.runs.clear()
        rc, rep = self.smoke(["targets/dreamcast_sh4/ghidra_scripts/TcPrepare.java"], projects="tc,gc")
        self.assertEqual((rc, self.runs), (0, [("tc", "8c010000")]))  # gc: its target is not affected
        self.assertIn("not smoked", rep["projects"]["gc"]["why"])

    def test_failures(self):
        rc, rep = self.smoke(["core/fsca.py"], rows={"ps2": {"error": ECC5573}})
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.runs), 3)  # every game still runs: the report names each
        self.assertIn("524288 bytes", rep["projects"]["ps2"]["why"])
        self.assertIn("FAIL (ps2)", rep["result"])
        for row, why in (({"unsupported": "uses fsca on an RM it does not model"}, "refused"),
                         ({"dev": "0/48", "dev_valid": 0}, "no valid dev vector"),
                         (dict(PASS, verdict="FAIL", failing="3/96"), "matched C FAIL 3/96"),
                         (dict(PASS, verdict="ORACLE"), "matched C ORACLE")):
            self.assertEqual(land_smoke.verdict(row)[:len(why)], why)
        self.assertIsNone(land_smoke.verdict(PASS))

    def test_no_slot(self):
        rc, rep = self.smoke(["core/fsca.py"], slot=lambda limit, cap: False)
        self.assertEqual((rc, self.runs), (3, []))
        self.assertIn("no slot", rep["result"])
        clock, slept = [0.0], []

        def sleep(s):
            slept.append(s)
            clock[0] += s
        self.assertFalse(land_smoke.wait_slot(1800, 2, count=lambda: 2, sleep=sleep, clock=lambda: clock[0]))
        self.assertEqual(clock[0], 1800)  # waited the whole limit, bounded
        counts = iter([2, 2, 1])
        clock[0] = 0
        self.assertTrue(land_smoke.wait_slot(1800, 2, count=lambda: next(counts), sleep=sleep, clock=lambda: clock[0]))
        self.assertEqual(clock[0], 60)

    def test_pick(self):
        ws = workspace(self.tmp.name, "none", {"8c010000": (8, "matched", 15, True)})
        self.assertIsNone(land_smoke.pick(ws))
        rc, rep = self.smoke(["core/fsca.py"])
        self.assertEqual(rep["projects"]["tc"]["addr"], "8c010000")


if __name__ == "__main__":
    unittest.main()
