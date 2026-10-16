"""Fixtures for sync-names (core/sync_run.py, core/sync_names.py, core/raw_names.py): readable files kept in step
with the names registry, verify running it first, and the consistency check's raw-name audit. A synthetic workspace
in a temp folder; the judge is the real readable lint (R3 exact prototype, RR4 raw names) with a faked build and
run, except in SyncCompile, which builds the synced file for real at /W4 /WX when Visual Studio is installed.

usage: python tests/test_sync.py   (python tests/test_tooling.py runs these too)
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
_TMP = tempfile.TemporaryDirectory(prefix="sync_", dir=TOOL / "tests" / ".work")
TMP = pathlib.Path(_TMP.name)
PROJECT = {"target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin"}
if not (pathlib.Path(os.environ.get("DECOMP_PROJECT", "")) / "project.json").exists():  # alone: a project for paths
    (TMP / "project.json").write_text(json.dumps(PROJECT), encoding="utf-8")
    os.environ["DECOMP_PROJECT"] = str(TMP)
for p in (str(TOOL / "tools"), str(TOOL)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import doctor, header_lint, header_snapshot, paths, raw_names, readable, readable_abi  # noqa: E402
from core import readable_verify, refine  # noqa: E402
from core import readable_check as rcheck  # noqa: E402
from core import retire as core_retire  # noqa: E402
from core import sync_run  # noqa: E402
from core.readable_lint import raw_name_lint, readable_lint  # noqa: E402
from core.sync_names import Sync, SyncRefused  # noqa: E402

atexit.register(_TMP.cleanup)

A, B, C = "8c010000", "8c010100", "8c010200"
TYPES = ('#ifndef TYPES_H\n#define TYPES_H\n\ntypedef struct Vec3 {\n    f32 x;\n    f32 y;\n    f32 z;\n} Vec3;\n\n'
         '#endif\n')
NAMED = {"functions": {A: {"proto": "u32 Thing_Run(Vec3 *v, s32 count)"},
                       B: {"proto": "u32 Vec3_Scale(Vec3 *v, s32 k)"},
                       C: {"proto": "void Str_Copy(char *dst, const char *src, u32 saved_r4)",
                           "storage": ["r0", "r1", "r4"]}}, "globals": {}}
OLD = """#include "game.h"

/* Scales v by count with fn_8c010100, plus one; copies with fn_8c010200. */
u32 fn_8c010000(u32 param_r4, s32 param_r5)
{
    Vec3 *v = (Vec3 *)(uintptr_t)param_r4;
    char name[8];

    fn_8c010200(0, (u32)(uintptr_t)name, (u32)(uintptr_t)name);
    return fn_8c010100((u32)(uintptr_t)v, param_r5) + 1u;
}
"""
NEW = """#include "game.h"

/* Scales v by count with Vec3_Scale, plus one; copies with Str_Copy. */
u32 Thing_Run(Vec3 *v, s32 count)
{
    char name[8];

    Str_Copy(name, name, 0);
    return Vec3_Scale(v, count) + 1u;
}
"""


def put(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def meta(addr, sig, params, callees=()):
    return {"addr": addr, "signature": sig, "ret": sig.split()[0], "globals": [], "callees": list(callees),
            "params": [{"name": n, "storage": s} for n, s in params]}


def make_ws():
    ws = pathlib.Path(tempfile.mkdtemp(prefix="ws", dir=TMP))
    put(ws / "project.json", PROJECT)
    put(ws / "readable/include/types.h", TYPES)
    put(ws / "readable/names/S01.json", {"functions": {}, "globals": {}})
    put(ws / f"readable/src/{A}.c", OLD)
    sig_b, sig_c = "u32 fn_8c010100(u32 param_1, s32 param_2)", "void fn_8c010200(u32 param_1, u32 param_r0, u32 param_r1)"
    put(ws / f"harness/{A}/meta.json", meta(A, "u32 fn_8c010000(u32 param_r4, s32 param_r5)",
                                            [("param_r4", "r4"), ("param_r5", "r5")],
                                            [{"addr": B, "regs": ["r4", "r5"], "signature": sig_b},
                                             {"addr": C, "regs": ["r4", "r0", "r1"], "signature": sig_c}]))
    put(ws / f"harness/{B}/meta.json", meta(B, sig_b, [("param_1", "r4"), ("param_2", "r5")]))
    put(ws / f"harness/{C}/meta.json", meta(C, sig_c, [("param_1", "r4"), ("param_r0", "r0"), ("param_r1", "r1")]))
    put(ws / f"matched/{A}.c", "/* raw */")
    put(ws / "state/readable.json", {A: {"status": "readable", "attempts": [{"n": 1, "verdict": "PASS"}]}})
    return ws


class SyncCase(unittest.TestCase):
    """A fresh workspace; the judge is the real lint plus self.broken (a text that fails "compare")."""

    broken = None

    def setUp(self):
        self.ws = make_ws()
        rd = self.ws / "readable"
        self.patches = [mock.patch.object(readable, "READABLE", rd), mock.patch.object(readable, "INCLUDE", rd / "include"),
                        mock.patch.object(readable, "NAMES", rd / "names"), mock.patch.object(readable, "SRC", rd / "src"),
                        mock.patch.object(paths, "HARNESS", self.ws / "harness"),
                        mock.patch.object(paths, "MATCHED", self.ws / "matched"),
                        mock.patch.object(paths, "BUILD", self.ws / "build"),
                        mock.patch.object(refine, "LEDGER", self.ws / "state/readable.json"),
                        mock.patch.object(refine, "harness_stale", lambda cfg, a: None),
                        mock.patch.object(header_snapshot, "ACTIVE", None),
                        mock.patch.object(rcheck, "check_readable", self.judge)]
        for p in self.patches:
            p.start()
        self.cfg = paths.config()
        self.judged = []

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def judge(self, cfg, addr, src_file, build_dir, sets=("dev", "hidden"), reg=None, **kw):
        reg = reg or readable.Registry()
        m = paths.read_json(paths.HARNESS / addr / "meta.json")
        proto = reg.proto(addr, m)
        src = pathlib.Path(src_file).read_text(encoding="utf-8")
        self.judged.append(src)
        alias = {c["addr"] for c in m["callees"]
                 if readable_abi.raw_alias_ok(reg, c, rcheck.callee_storage(paths.HARNESS / addr, c))}
        lint = readable_lint(src, readable.parse_proto(proto)[1], proto) + raw_name_lint(src, reg, alias | {addr})
        if lint:
            return {"verdict": "FAIL", "stage": "lint", "lint": lint}
        if self.broken and self.broken in src:
            return {"verdict": "FAIL", "stage": "compare", "failing": 5, "total": 96, "lint": [], "build": []}
        return {"verdict": "PASS", "stage": "compare", "failing": 0, "total": 96, "lint": [], "build": []}

    def name_them(self):
        put(self.ws / "readable/names/S01.json", NAMED)

    def src(self):
        return (self.ws / f"readable/src/{A}.c").read_text(encoding="utf-8")

    def row(self):
        return refine.load_ledger()[A]


class SyncNames(SyncCase):
    def test_named_since_then_synced(self):
        self.assertEqual(self.judge(self.cfg, A, self.ws / f"readable/src/{A}.c", None)["verdict"], "PASS")
        self.name_them()
        self.assertEqual(self.judge(self.cfg, A, self.ws / f"readable/src/{A}.c", None)["stage"], "lint")  # R3
        out = sync_run.sync(self.cfg, [A], workers=1, log=lambda m: None)
        self.assertEqual(out[A]["outcome"], "synced", out)
        self.assertEqual(out[A]["tier"], "clean/all")
        self.assertEqual(self.src(), NEW)
        row = self.row()
        self.assertEqual(row["status"], "readable")
        att = row["attempts"][-1]
        self.assertEqual((att["n"], att["kind"], att["verdict"]), (2, "sync", "PASS"))
        self.assertFalse({"tokens_in", "tokens_out", "cost_usd"} & set(att))
        whats = {(r["from"], r["what"]) for r in att["renames"]}
        self.assertTrue({(f"fn_{A}", "definition"), (f"fn_{B}", "call"), (f"fn_{C}", "call"),
                         (f"fn_{B}", "comment")} <= whats, whats)
        self.assertEqual(self.judge(self.cfg, A, self.ws / f"readable/src/{A}.c", None)["verdict"], "PASS")

    def test_idempotent(self):
        self.name_them()
        sync_run.sync(self.cfg, [A], workers=1, log=lambda m: None)
        text, atts = self.src(), len(self.row()["attempts"])
        self.judged.clear()
        out = sync_run.sync(self.cfg, [A], workers=1, log=lambda m: None)
        self.assertEqual(out, {})
        self.assertEqual((self.src(), len(self.row()["attempts"])), (text, atts))
        self.assertEqual(self.judged, [])  # nothing to rename: not even judged

    def test_refused_keeps_file(self):
        self.broken = "Str_Copy("  # every rewrite fails the compare (Str_Copy's raw name never fits: all rename it)
        self.name_them()
        refine.update(A, status="stale", note="lint: R3")
        lines = []
        out = sync_run.sync(self.cfg, [A], workers=1, log=lines.append)
        self.assertEqual(out[A]["outcome"], "refused")
        self.assertIn("compare: 5/96 vectors differ", out[A]["why"])
        self.assertEqual(self.src(), OLD)
        row = self.row()
        self.assertEqual((row["status"], len(row["attempts"])), ("stale", 1))
        self.assertIn("compare", row["sync_refused"]["why"])
        self.assertTrue(any(ln.startswith(f"SYNC REFUSED {A}") for ln in lines), lines)

    def test_required_tier_keeps_a_fitting_raw_name(self):
        self.broken = "Vec3_Scale("  # renaming the call fails; its raw name still fits Vec3_Scale's placement
        self.name_them()
        out = sync_run.sync(self.cfg, [A], workers=1, log=lambda m: None)
        self.assertEqual((out[A]["outcome"], out[A]["tier"], out[A]["raw_left"]),
                         ("synced", "cast/required", [f"fn_{B}"]))
        self.assertIn(f"return fn_{B}((u32)(uintptr_t)v, count) + 1u;", self.src())
        self.assertIn("Str_Copy((char *)name, (const char *)name, 0);", self.src())
        bad, _ = sync_run.raw_report(readable.Registry(), [A])
        self.assertEqual(bad, {A: {f"fn_{B}": "Vec3_Scale"}})  # what the audit then reports

    def test_dry_writes_nothing(self):
        self.name_them()
        before = (self.ws / "state/readable.json").read_bytes()
        out = sync_run.sync(self.cfg, [A], workers=1, dry=True, log=lambda m: None)
        self.assertEqual(out[A]["outcome"], "synced")
        self.assertEqual((self.src(), (self.ws / "state/readable.json").read_bytes()), (OLD, before))

    def test_verify_syncs_instead_of_stale(self):
        self.name_them()
        lines = []
        _, bad = readable_verify.verify(self.cfg, [A], workers=1, sync=True, log=lines.append)
        self.assertEqual(bad, 0, lines)
        self.assertEqual((self.row()["status"], self.src()), ("readable", NEW))
        self.assertFalse(any("STALE" in ln for ln in lines), lines)
        self.assertTrue(any("raw names left for functions without a registry name: 0 use(s)" in ln for ln in lines))

    def test_verify_still_marks_a_real_break_stale(self):
        self.broken = "+ 1u"  # the file itself is wrong: no rename can fix it
        self.name_them()
        lines = []
        _, bad = readable_verify.verify(self.cfg, [A], workers=1, sync=True, log=lines.append)
        self.assertEqual(bad, 1)
        self.assertEqual((self.row()["status"], self.src()), ("stale", OLD))
        self.assertTrue(any(ln.startswith(f"STALE {A}") for ln in lines), lines)
        self.assertTrue(any(ln.startswith("RAW NAMES: 1 readable file") for ln in lines), lines)

    def test_prototype_change_of_a_named_function(self):
        self.name_them()
        sync_run.sync(self.cfg, [A], workers=1, log=lambda m: None)
        named = json.loads(json.dumps(NAMED))
        named["functions"][A]["proto"] = "u32 Thing_Run(Vec3 *vec, s32 n)"  # the designer renames its parameters
        put(self.ws / "readable/names/S01.json", named)
        out = sync_run.sync(self.cfg, [A], workers=1, log=lambda m: None)
        self.assertEqual(out[A]["outcome"], "synced", out)
        self.assertEqual(self.src(), NEW.replace("Vec3 *v, s32 count", "Vec3 *vec, s32 n").replace("(v, count)",
                                                                                                    "(vec, n)"))
        self.assertEqual(self.row()["attempts"][-1]["renames"][-1]["what"], "prototype")


class SyncText(SyncCase):
    """The rewrite itself, without the judge."""

    def sync(self, text, mode="clean", names=None):
        self.name_them()
        reg = readable.Registry()
        m = paths.read_json(paths.HARNESS / A / "meta.json")
        names = names or {f"fn_{a}": readable.parse_proto(reg.functions[a]["proto"])[1] for a in (A, B, C)}
        s = Sync(reg, A, m, lambda c: rcheck.callee_storage(paths.HARNESS / A, c), names, mode)
        return s.run(text), s

    def test_cast_mode_and_storage_order(self):
        new, _ = self.sync(OLD, mode="cast")
        self.assertIn("Str_Copy((char *)name, (const char *)name, 0);", new)  # r0, r1, r4: placed by storage
        self.assertIn("Vec3_Scale((Vec3 *)v, count)", new)

    def test_type_change_without_a_local_keeps_a_shim(self):
        text = OLD.replace("    Vec3 *v = (Vec3 *)(uintptr_t)param_r4;\n", "").replace("(u32)(uintptr_t)v,", "param_r4,")
        new, s = self.sync(text)
        self.assertIn("u32 Thing_Run(Vec3 *v, s32 count)\n{\n    u32 param_r4 = (u32)(uintptr_t)v;\n", new)
        self.assertEqual(s.shims, ["param_r4 (u32) from v (Vec3 *)"])

    def test_clashing_local_renamed_other_clash_refused(self):
        text = OLD.replace("char name[8];", "u32 count = 0u;\n    char name[8];").replace(
            "+ 1u;", "+ 1u + count;")
        new, s = self.sync(text)  # the local count moves aside for the new parameter count
        self.assertIn("u32 count_local = 0u;", new)
        self.assertIn("Vec3_Scale(v, count) + 1u + count_local;", new)
        self.assertIn("local count renamed count_local", s.shims)
        with self.assertRaises(SyncRefused):  # count used, not declared here: nothing to move aside
            self.sync(OLD.replace("+ 1u;", "+ 1u + count;"))

    def test_float_meets_int_refused(self):
        named = json.loads(json.dumps(NAMED))
        named["functions"][B]["proto"] = "u32 Vec3_Scale(f32 k, Vec3 *v)"
        with self.assertRaises(SyncRefused):  # raw r4 is u32, the new k is f32 in fr4: no raw argument for k
            put(self.ws / "readable/names/S01.json", named)
            reg = readable.Registry()
            m = paths.read_json(paths.HARNESS / A / "meta.json")
            Sync(reg, A, m, lambda c: rcheck.callee_storage(paths.HARNESS / A, c), {f"fn_{B}": "Vec3_Scale"},
                 "clean").run(OLD)

    def test_raw_names_scan(self):
        put(self.ws / f"readable/src/{B}.c", "/* fn_8c0abcde */ void f(void) { fn_8c0abcde(); fn_8c0abcde(); }")
        bad, counts = raw_names.scan(self.ws)
        self.assertEqual(bad, {})
        self.assertEqual(counts, (5, 4, 2))  # B.c: 2 uses of 1 (not its comment); A.c: 3 uses of 3
        self.name_them()
        bad, _ = raw_names.scan(self.ws)
        self.assertEqual(set(bad[A]), {f"fn_{A}", f"fn_{B}", f"fn_{C}"})


class SyncAudit(unittest.TestCase):
    def test_doctor_fails_on_raw_name_of_named_function(self):
        from test_retire import A as RA, T, make_ws as retire_ws
        ws = retire_ws("raw")
        core_retire.apply(ws, core_retire.scan(ws))
        put(ws / f"readable/src/{RA}.c", f"void Foo_Run(void)\n{{\n    fn_{T}();\n    fn_8c0abcde();\n}}\n")
        ok, lines, facts = doctor.check(ws)
        self.assertFalse(ok)
        self.assertTrue(any(f"fn_{T} (Foo_Tail)" in ln for ln in lines), lines)
        self.assertTrue(any("raw names left for functions without a registry name: 1 use(s) of 1" in ln
                            for ln in lines), lines)
        put(ws / f"readable/src/{RA}.c", "void Foo_Run(void)\n{\n    Foo_Tail();\n    fn_8c0abcde();\n}\n")
        ok, lines, facts = doctor.check(ws)
        self.assertTrue(ok, lines)
        self.assertEqual((facts["raw_named_files"], facts["raw_unnamed_uses"]), (0, 1))


class SyncCompile(SyncCase):
    def test_synced_file_builds_at_w4(self):
        self.name_them()
        sync_run.sync(self.cfg, [A], workers=1, log=lambda m: None)
        inc = self.ws / "build" / "inc"  # the port header (prototypes) followed by the synced function
        put(inc / "types.h", TYPES)
        put(inc / "game.h", readable.game_h(readable.Registry()) + self.src().replace('#include "game.h"', ""))
        self.assertEqual(header_lint.compile_game_h(self.cfg, inc, self.ws / "build" / "probe"), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
