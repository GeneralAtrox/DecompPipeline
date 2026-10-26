"""Fixtures for retire (core/retire.py, tools/retire.py), the consistency check (core/doctor.py) and honest readable
redo status (core/refine.py). Synthetic workspaces in a temp folder; no Ghidra, compiler or agent.

usage: python tests/test_retire.py   (python tests/test_tooling.py runs these too)
"""
import atexit
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="retire_", dir=TOOL / "tests" / ".work")
TMP = pathlib.Path(_TMP.name)
if not (pathlib.Path(os.environ.get("DECOMP_PROJECT", "")) / "project.json").exists():  # alone: a project for paths
    (TMP / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name": "fixture",
                                                  "binary": "game.bin"}), encoding="utf-8")
    os.environ["DECOMP_PROJECT"] = str(TMP)
for p in (str(TOOL / "tools"), str(TOOL)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import doctor, paths, refine  # noqa: E402
from core import retire as core_retire  # noqa: E402
import retire as retire_tool  # noqa: E402  (tools/retire.py)

A, B, SDK = "8c010000", "8c010100", "8c068000"  # current functions (SDK: outside the game range, still one)
X, Y, T = "8c010050", "8c010060", "8c010070"  # X: removed, recorded; Y: removed by a catalog pass; T: a tail entry


atexit.register(_TMP.cleanup)  # not tearDownModule: test_tooling.py runs these classes between its own


def put(ws, rel, data):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def make_ws(name):
    """A workspace with A, B, SDK functions and every kind of artefact left behind by X and Y."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix=name, dir=TMP))
    put(ws, "project.json", {"name": "fx", "target": "dreamcast_sh4", "references": [A], "priority": [B],
                             "game_end": SDK})
    put(ws, "state/catalog.json", {"functions": [{"addr": A}, {"addr": B}, {"addr": SDK}]})
    put(ws, "state/ledger.json", {"loop": False, "functions": {a: {"addr": a, "status": "matched"} for a in (A, B, Y)}})
    put(ws, "state/ledger_removed.json", {"why": f"catalog pass: {X} dead epilogue fragment; 8c0ffff0 data",
                                          "rows": {X: {"addr": X, "status": "matched"}}})
    put(ws, "state/readable.json", {A: {"status": "readable"}, X: {"status": "readable"}})
    put(ws, "state/systems.json", {"resolution": 2.0, "systems": [{"id": "S01", "hint": "", "functions": [A, X, Y]},
                                                                  {"id": "S02", "hint": "", "functions": [B]}]})
    put(ws, f"harness/{A}/meta.json", {"callees": [{"addr": T}]})
    for a in (B, X, Y):
        put(ws, f"harness/{a}/meta.json", {"callees": []})
    for a in (A, B, X, Y):
        put(ws, f"matched/{a}.c", "/* raw */")
        put(ws, f"packets/{a}/asm.txt", "asm")
    for rel in (f"work/{X}.c", f"runs/{X}/attempt-01/work.c", f"sandbox/{X}/TASK.md", f"sandbox/rd_{X}/TASK.md",
                f"build/{X}/rd_verify/x.obj", f"oracle/hints/{X}.txt", f"import/{X}.c", f"import/{X}.note",
                f"readable/src/{A}.c", f"readable/src/{X}.c", f"matched_stale/{Y}.c", "harness/README.txt"):
        put(ws, rel, "x")
    put(ws, "readable/names/S01.json", {"functions": {A: {"proto": "void Foo_Run(void)"},
                                                      X: {"proto": "void Foo_Frag(void)"},
                                                      T: {"proto": "void Foo_Tail(void)"},
                                                      "0c" + B[2:]: {"proto": "void Foo_Mirror(void)"}},
                                        "globals": {}})
    return ws


def kinds(plan):
    out = {}
    for i in plan["items"]:
        out.setdefault(i["kind"], set()).add(i["addr"])
    return out


class Retire(unittest.TestCase):
    def test_scan_finds_removed_only(self):
        ws = make_ws("scan")
        plan = core_retire.scan(ws)
        self.assertEqual({i["addr"] for i in plan["items"]}, {X, Y})
        k = kinds(plan)
        self.assertEqual(k["sandbox"], {X})
        self.assertEqual(k["ledger"], {Y})
        self.assertEqual(k["systems"], {X, Y})
        self.assertEqual(k["names"], {X})  # nothing refers to Foo_Frag
        self.assertEqual(len([i for i in plan["items"] if i["kind"] == "sandbox"]), 2)  # ADDR and rd_ADDR
        kept = {n["addr"]: n for n in plan["names_kept"]}
        self.assertEqual(set(kept), {T, "0c" + B[2:]})  # A's harness calls T: a name, kept
        self.assertEqual(kept["0c" + B[2:]]["alias_of"], B)  # target.json address_mirrors: B's RAM mirror

    def test_apply_moves_and_records(self):
        ws = make_ws("apply")
        records = core_retire.apply(ws, core_retire.scan(ws), note="test")
        for rel in (f"harness/{A}/meta.json", f"matched/{A}.c", f"matched/{B}.c", f"readable/src/{A}.c",
                    "harness/README.txt"):
            self.assertTrue((ws / rel).exists(), rel)  # current functions and unkeyed files are never touched
        for rel in (f"harness/{X}", f"matched/{Y}.c", f"sandbox/rd_{X}", f"readable/src/{X}.c", f"import/{X}.note"):
            self.assertFalse((ws / rel).exists(), rel)
        self.assertTrue((ws / f"retired/harness/{X}/meta.json").exists())
        self.assertTrue((ws / f"retired/sandbox/rd_{X}/TASK.md").exists())
        self.assertEqual(json.loads((ws / f"retired/ledger/{Y}.json").read_text())["entry"]["status"], "matched")
        led = json.loads((ws / "state/ledger.json").read_text())["functions"]
        self.assertEqual(set(led), {A, B})
        self.assertEqual(set(json.loads((ws / "state/readable.json").read_text())), {A})
        sy = json.loads((ws / "state/systems.json").read_text())["systems"]
        self.assertEqual([s["functions"] for s in sy], [[A], [B]])
        names = json.loads((ws / "readable/names/S01.json").read_text())["functions"]
        self.assertEqual(set(names), {A, T, "0c" + B[2:]})
        manifest = json.loads((ws / core_retire.MANIFEST).read_text())
        self.assertEqual(len(manifest), len(records))
        why_x = {r["reason"] for r in manifest if r["addr"] == X}
        self.assertEqual(why_x, {"test; ledger_removed.json: catalog pass: 8c010050 dead epilogue fragment"})
        self.assertTrue(all(r["reason"].startswith("test; ") for r in manifest))

    def test_idempotent(self):
        ws = make_ws("idem")
        core_retire.apply(ws, core_retire.scan(ws))
        again = core_retire.scan(ws)
        self.assertEqual(again["items"], [])
        self.assertEqual(core_retire.apply(ws, again), [])
        put(ws, f"harness/{X}/meta.json", "{}")  # the address comes back as an orphan: kept apart, both stay
        core_retire.apply(ws, core_retire.scan(ws))
        self.assertTrue((ws / f"retired/harness/{X}.1/meta.json").exists())

    def test_empty_catalog_refused(self):
        ws = make_ws("empty")
        put(ws, "state/catalog.json", {"functions": []})
        with self.assertRaises(RuntimeError):
            core_retire.scan(ws)

    def test_tool_dry_busy_cap(self):
        ws = make_ws("tool")
        quiet = lambda m: None  # noqa: E731
        _, recs, why = retire_tool.run(ws, dry=True, log=quiet)
        self.assertEqual((len(recs), why), (0, None))
        self.assertTrue((ws / f"harness/{X}").exists())  # dry: nothing moved
        put(ws, "state/readable.json", {A: {"status": "running"}, X: {"status": "readable"}})
        _, recs, why = retire_tool.run(ws, log=quiet)
        self.assertIn("running", why)
        self.assertEqual(recs, [])
        _, recs, why = retire_tool.run(ws, max_addrs=1, force=False, log=quiet)
        self.assertIsNotNone(why)
        _, recs, why = retire_tool.run(ws, force=True, log=quiet)
        self.assertIsNone(why)
        self.assertEqual({r["addr"] for r in recs}, {X, Y})
        self.assertEqual(retire_tool.run(ws, log=quiet)[0]["items"], [])


class Doctor(unittest.TestCase):
    def test_orphans_fail_then_pass(self):
        ws = make_ws("doc")
        ok, lines, facts = doctor.check(ws)
        self.assertFalse(ok)
        self.assertEqual((facts["orphan_addrs"], facts["readable_files"], facts["readable_files_functions"]), (2, 2, 1))
        self.assertTrue(any("not functions" in x for x in lines))
        core_retire.apply(ws, core_retire.scan(ws))
        ok, lines, facts = doctor.check(ws)
        self.assertTrue(ok, lines)
        self.assertEqual((facts["readable_files"], facts["matched"]), (1, 2))
        self.assertTrue(any("denominator: 2 matched functions" in x for x in lines))

    def test_matched_without_c_or_meta(self):
        ws = make_ws("doc2")
        core_retire.apply(ws, core_retire.scan(ws))
        (ws / f"matched/{B}.c").unlink()
        (ws / f"harness/{A}/meta.json").unlink()
        ok, lines, _ = doctor.check(ws)
        self.assertFalse(ok)
        self.assertTrue(any(f"without matched/ADDR.c: {B}" in x for x in lines))
        self.assertTrue(any(f"without harness/ADDR/meta.json: {A}" in x for x in lines))

    def test_config_and_status_notes(self):
        ws = make_ws("doc3")
        core_retire.apply(ws, core_retire.scan(ws))
        put(ws, "state/readable.json", {A: {"status": "failed"}})
        ok, _, facts = doctor.check(ws)
        self.assertTrue(ok)  # a status mismatch is reported (verify fixes it), not a failure
        self.assertEqual(facts["status_mismatch"], 1)
        put(ws, "project.json", {"references": [X]})
        self.assertFalse(doctor.check(ws)[0])


def fail_check(*a, **k):
    return {"verdict": "FAIL", "stage": "compare", "failing": 3, "total": 96}


class RedoStatus(unittest.TestCase):
    """A redo that does not pass keeps the old file's verdict and records redo_failed."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="redo", dir=TMP))
        (self.d / "src").mkdir()
        (self.d / "box").mkdir()
        (self.d / "box" / "work.c").write_text("/* attempt */", encoding="utf-8")
        self.patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                        mock.patch.object(refine.readable, "SRC", self.d / "src"),
                        mock.patch.object(refine.readable, "Registry", lambda: None),
                        mock.patch.object(paths, "RUNS", self.d / "runs"),
                        mock.patch.object(refine, "raw_missing", lambda a: None),
                        mock.patch.object(refine, "harness_stale", lambda cfg, a, **k: None),
                        mock.patch.object(refine, "preflight", lambda cfg, reg, a, meta: None),
                        mock.patch.object(refine, "build_sandbox", lambda *a: (self.d / "box", "task")),
                        mock.patch.object(refine, "tree_hashes", lambda box: {}),
                        mock.patch.object(refine.agent, "run_attempt", lambda *a: {}),
                        mock.patch.object(refine.rcheck, "check_readable", fail_check)]
        for p in self.patches:
            p.start()
        self.cfg = {"model": "m", "effort": None, "readable_effort": None}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def run_redo(self, status, with_file=True, attempts=2):
        if with_file:
            (self.d / "src" / f"{A}.c").write_text("/* old pass */", encoding="utf-8")
        refine.update(A, status=status, attempts=[{"n": 1, "verdict": "PASS"}])
        out = refine.refine(self.cfg, A, max_attempts=attempts, log=lambda m: None)
        return out, refine.load_ledger()[A]

    def test_failed_redo_keeps_verdict(self):
        for old in ("readable", "stale"):
            out, row = self.run_redo(old)
            self.assertEqual(row["status"], old, out)
            self.assertEqual((row["redo_failed"]["outcome"], row["redo_failed"]["attempts"]), ("failed", [2, 3]))
            self.assertNotIn("redo_of", row)
            self.assertEqual((self.d / "src" / f"{A}.c").read_text(), "/* old pass */")

    def test_no_file_fails(self):
        out, row = self.run_redo("queued", with_file=False)
        self.assertEqual((out, row["status"]), ("failed", "failed"))
        self.assertNotIn("redo_failed", row)

    def test_blocked_and_api_error(self):
        with mock.patch.object(refine, "preflight", lambda *a: "registry: refused"):
            _, row = self.run_redo("readable")
        self.assertEqual((row["status"], row["redo_failed"]["outcome"]), ("readable", "blocked"))
        with mock.patch.object(refine.agent, "run_attempt", lambda *a: {"api_error": "overloaded"}):
            _, row = self.run_redo("stale")
        self.assertEqual((row["status"], row["redo_failed"]["outcome"]), ("stale", "api_error"))

    def test_pass_replaces(self):
        with mock.patch.object(refine.rcheck, "check_readable", lambda *a, **k: {"verdict": "PASS", "stage": "compare"}):
            self.run_redo("stale")
            refine.update(A, redo_failed={"outcome": "failed"})
            out, row = self.run_redo("readable")
        self.assertEqual((out, row["status"]), ("readable", "readable"))
        self.assertNotIn("redo_failed", row)
        self.assertEqual((self.d / "src" / f"{A}.c").read_text(), "/* attempt */")

    def test_error_while_running(self):
        (self.d / "src" / f"{A}.c").write_text("x", encoding="utf-8")
        refine.update(A, status="running", redo_of="stale")
        refine.record_error(A, "boom")
        row = refine.load_ledger()[A]
        self.assertEqual((row["status"], row["redo_failed"]["outcome"]), ("stale", "error"))
        refine.update(B, status="running")
        refine.record_error(B, "boom")
        self.assertEqual(refine.load_ledger()[B]["status"], "error")

    def test_fresh_workspace_pass_creates_src(self):
        """A workspace's first readable pass: no readable/src yet (Spawn's first runs ended as infra_error)."""
        (self.d / "src").rmdir()
        with mock.patch.object(refine.rcheck, "check_readable", lambda *a, **k: {"verdict": "PASS", "stage": "compare"}):
            out, row = self.run_redo("queued", with_file=False)
        self.assertEqual((out, row["status"]), ("readable", "readable"))
        self.assertEqual((self.d / "src" / f"{A}.c").read_text(), "/* attempt */")

    def test_fresh_workspace_draft_adopt_creates_src(self):
        from core import draft_run
        (self.d / "src").rmdir()
        self.assertEqual(draft_run.adopt(A, self.d / "box" / "work.c", {"stage": "compare"}), "readable")
        self.assertEqual((self.d / "src" / f"{A}.c").read_text(), "/* attempt */")


if __name__ == "__main__":
    unittest.main(verbosity=1)
