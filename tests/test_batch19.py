"""Batch 19: `refine.py library` (core/library.py, core/library_decls.py), today's oracle stamp without writes
(core/oracle_fresh.py) and tools/final_audit.py on a tiny fixture project. The library tests compile for real with
Visual Studio (skipped when it is not installed). Run: python -W error tests/test_batch19.py (also from
tests/test_tooling.py)."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP, TOOL, SyncCase, meta, put  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import library, library_decls, oracle_stamp, paths  # noqa: E402

D1, D2, RAW = "8c011000", "8c011100", "8c010100"  # RAW: test_sync's B, an unnamed function with a harness
NAMES = {"functions": {D1: {"proto": "u32 Dup_One(u32 x)"}, D2: {"proto": "u32 Dup_Two(u32 x)"}}, "globals": {}}
VCVARS = pathlib.Path(paths.config()["vcvars"])
HAS_VS = VCVARS.exists()


def src(name, extra="", body="return helper(x);"):
    return (f'#include "game.h"\n\n{extra}static u32 helper(u32 x)\n{{\n    return x + 1u;\n}}\n\n'
            f"u32 {name}(u32 x)\n{{\n    {body}\n}}\n")


class Decls(unittest.TestCase):
    def test_external_declarations_only(self):
        text = ('#include "game.h"\n\ntypedef struct Loc { int a; } Loc;\nstatic int helper(Loc *l);\n'
                "f32 sqrtf(f32 x);\nLoc *use_loc(void);\nstruct Fwd;\n/* c */\nu32 fn_8c000000(u32 a)\n{\n"
                "    return a;\n}\nstatic int helper(Loc *l) { return l->a; }\n")
        decls, skipped = library_decls.external_decls(text)
        self.assertEqual(decls, [("f32 sqrtf(f32 x);", 5), ("u32 fn_8c000000(u32 a);", 9)])
        self.assertEqual([ln for ln, _ in skipped], [6])  # names the file's own type: not compilable outside it
        unit, info = library_decls.combined_unit([(D1, pathlib.Path(f"x/{D1}.c"), text)])
        self.assertIn(f'#line 5 "{D1}.c"\nf32 sqrtf(f32 x);', unit)
        self.assertEqual(info["checked"], 2)


@unittest.skipUnless(HAS_VS, "Visual Studio not installed")
class Library(SyncCase):
    """Two files that each compile alone but conflict together are caught; a clean pair passes."""

    def setUp(self):
        super().setUp()
        put(self.ws / "readable/names/S02.json", NAMES)
        (self.ws / "readable/src" / "8c010000.c").unlink()  # test_sync's file: raw names, not this test's subject
        put(self.ws / f"harness/{D1}/meta.json", meta(D1, "u32 fn_8c011000(u32 param_r4)", [("param_r4", "r4")],
                                                      [{"addr": RAW, "regs": ["r4", "r5"],
                                                        "signature": "u32 fn_8c010100(u32 param_1, s32 param_2)"}]))

    def build(self, one, two):
        put(self.ws / f"readable/src/{D1}.c", one)
        put(self.ws / f"readable/src/{D2}.c", two)
        log = []
        ok, facts = library.build(self.cfg, self.ws / "build" / "library", workers=2, log=log.append)
        return ok, facts, "\n".join(log)

    def test_clean_pair_passes_and_is_cached(self):
        one = src("Dup_One", body="return helper(x) + fn_8c010100(x, 2);")  # an unnamed callee: its raw prototype
        ok, facts, log = self.build(one, src("Dup_Two"))
        self.assertTrue(ok, log)
        self.assertEqual((facts["clean"], facts["unnamed"]), (2, 1))
        self.assertIn("PASS library", log)
        ok, _, log = self.build(one, src("Dup_Two"))
        self.assertTrue(ok, log)
        self.assertIn("(2 from the object cache)", log)

    def test_global_defined_twice(self):
        ok, facts, log = self.build(src("Dup_One", "u32 g_shared = 1u;\n\n"), src("Dup_Two", "u32 g_shared = 2u;\n\n"))
        self.assertFalse(ok)
        self.assertEqual(facts["clean"], 2, log)  # each compiles alone
        self.assertEqual((facts["archive"], facts["declarations"]), (1, 0), log)  # same type: only the archive
        self.assertIn("LNK4006", log)
        self.assertIn("_g_shared already defined", log)
        ok, facts, log = self.build(src("Dup_One", "u32 g_shared = 1u;\n\n"),
                                    src("Dup_Two", "f32 g_shared = 2.0f;\n\n"))
        self.assertFalse(ok)
        self.assertEqual((facts["archive"], facts["declarations"]), (1, 1), log)  # and the types differ
        self.assertRegex(log, r"declarations: 8c011100\.c\(3\): .*(C2371|C4142)")
        ok, facts, log = self.build(src("Dup_One", "u32 g_shared;\n\n"), src("Dup_Two", "f32 g_shared;\n\n"))
        self.assertFalse(ok)  # tentative definitions are common symbols (no archive finding); the types still differ
        self.assertEqual(facts["declarations"], 1, log)

    def test_conflicting_prototypes(self):
        ok, facts, log = self.build(src("Dup_One", "u32 Ext_Thing(u32 a);\n\n"), src("Dup_Two", "u32 Ext_Thing(f32 a);\n\n"))
        self.assertFalse(ok)
        self.assertEqual((facts["clean"], facts["archive"]), (2, 0), log)  # alone and linked: nothing to see
        self.assertGreaterEqual(facts["declarations"], 1, log)
        self.assertRegex(log, rf"declarations: {D2}\.c\(\d+\): .*(C2371|C4028)")

    def test_prototype_differs_from_game_h(self):
        ok, facts, log = self.build(src("Dup_One"), src("Dup_Two", "u32 Dup_One(f32 x);\n\n"))
        self.assertFalse(ok)
        self.assertEqual(facts["failing"], [D2], log)
        self.assertRegex(log, rf"{D2}: {D2}\.c\(\d+\): .*(C2371|C4028)")
        self.assertIn("route: the file", log)


class FreshStamp(unittest.TestCase):
    def test_stamp_of_equals_stamp_from_files(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        put(d / "TcA.java", "class A {}")
        put(d / "TcB.java", "class B {}")
        put(d / "codes.txt", "1 2\n")
        put(d / "facts.txt", f"ram 1 2\ncode_ranges {d / 'codes.txt'}\n")
        cfg = {"binary_sha256": "x", "ghidra_home": "g"}
        via_files = oracle_stamp.stamp(cfg, D1, d, str(d / "facts.txt"), None, 48, 48)
        body = (d / "facts.txt").read_text(encoding="utf-8")
        via_memory = oracle_stamp.stamp_of(cfg, D1, {"TcB.java": d / "TcB.java", "TcA.java": d / "TcA.java"},
                                           oracle_stamp.facts_hashed(body), None, 48, 48)
        self.assertEqual(via_files, via_memory)
        self.assertNotEqual(via_memory, oracle_stamp.stamp_of(cfg, D1, {"TcA.java": d / "TcA.java"},
                                                              oracle_stamp.facts_hashed(body), None, 48, 48))


AUDIT_RUNNER = """
import os, runpy, sys
sys.path[:0] = [{tool!r}, {tools!r}]
from core import oracle_fresh, readable_check
readable_check.check_readable = lambda cfg, addr, *a, **k: {{"verdict": "PASS", "stage": "compare", "failing": 0,
                                                             "total": 96, "lint": [], "build": []}}
oracle_fresh.judge_one = lambda cfg, addr, root: ("PASS", "96 vectors")
sys.argv = ["final_audit.py", "--project", {ws!r}, "--recount", "--workers", "2"]
sys.exit(runpy.run_path({script!r}, run_name="__main__"))
"""


def audit_ws():
    """A tiny project: two matched functions with readable files, one excluded hardware function with its note."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix="audit", dir=TMP))
    a, b, hw = "8c010000", "8c010040", "8c010080"
    put(ws / "project.json", {"name": "audit fixture", "target": "dreamcast_sh4", "ghidra_name": "fixture",
                              "binary": "game.bin", "game_end": "8c010100"})
    (ws / "game.bin").write_bytes(bytes(0x100))  # zeros: padding, every byte explained
    put(ws / "HARDWARE_FUNCTIONS.md", f"| {hw} | 4 B | reads a hardware register |\n")
    stats = {k: 0 for k in ("bytes", "insns", "blocks", "cyclo", "loops", "callees", "callers", "indirect_calls",
                            "jump_tables", "fpu", "fpu_special", "fp_mode", "hw_refs", "sys_ops", "globals",
                            "func_ptrs")}
    put(ws / "state/catalog.json", {"functions": [dict(stats, addr=x, hw_refs=int(x == hw)) for x in (a, b, hw)]})
    put(ws / "state/ledger.json", {"functions": {a: {"status": "matched"}, b: {"status": "matched"}}})
    put(ws / "state/systems.json", {"systems": [{"id": "S01", "hint": "", "functions": [a, b, hw]}]})
    put(ws / "state/function_bodies.txt", "".join(f"{x} {x} {int(x, 16) + 0x40:08x}\n" for x in (a, b, hw)))
    put(ws / "state/code_ranges.txt", "")
    put(ws / "readable/include/types.h", "#ifndef TYPES_H\n#define TYPES_H\n#endif\n")
    names = {"functions": {a: {"proto": "u32 One_Run(u32 x)"}, b: {"proto": "u32 Two_Run(u32 x)"}}, "globals": {}}
    put(ws / "readable/names/S01.json", names)
    rows = {}
    for x, name in ((a, "One_Run"), (b, "Two_Run")):
        put(ws / f"readable/src/{x}.c", src(name))
        put(ws / f"matched/{x}.c", "/* raw */\n")
        put(ws / f"harness/{x}/meta.json", meta(x, f"u32 fn_{x}(u32 param_r4)", [("param_r4", "r4")]))
        rows[x] = {"status": "readable"}
    put(ws / "state/readable.json", rows)
    return ws, (a, b)


@unittest.skipUnless(HAS_VS, "Visual Studio not installed")
class FinalAudit(unittest.TestCase):
    def test_tiny_project(self):
        ws, funcs = audit_ws()
        env = dict(os.environ, DECOMP_PROJECT=str(ws))
        code = AUDIT_RUNNER.format(tool=str(TOOL), tools=str(TOOL / "tools"), ws=str(ws),
                                   script=str(TOOL / "tools" / "final_audit.py"))
        # today's stamps and game.h (as refine.py header writes it), in the fixture's own process (paths binds to
        # the project at import)
        stamp = subprocess.run([sys.executable, "-c", f"import sys; sys.path[:0] = [{str(TOOL)!r}]\n"
                                "from core import oracle_fresh, paths, readable\nst = oracle_fresh.Stamper(paths.config())\n"
                                "(readable.INCLUDE / 'game.h').write_text(readable.game_h(readable.Registry()))\n"
                                f"print(' '.join(st.today(x) for x in {list(funcs)!r}))"],
                               env=env, capture_output=True, text=True, timeout=300)
        for x, s in zip(funcs, stamp.stdout.split()):
            m = json.loads((ws / f"harness/{x}/meta.json").read_text())
            put(ws / f"harness/{x}/meta.json", dict(m, oracle_stamp=s))
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=900)
        lines = r.stdout.splitlines()
        for name in ("oracle", "headers", "verify", "coverage", "raw names", "library", "completeness",
                     "consistency", "excluded"):
            self.assertTrue(any(ln.startswith(f"PASS {name}:") for ln in lines), (name, r.stdout, r.stderr))
        self.assertTrue(any(ln.startswith("INFO usage:") for ln in lines), r.stdout)
        self.assertIn("2/2 pass (N = 3 catalog game functions - 1 excluded hw/sys; 2 matched)", r.stdout)
        self.assertIn("1 excluded hw/sys function(s), 1 with an intent note", r.stdout)
        self.assertTrue(any(ln.startswith("VERDICT PASS") for ln in lines), r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = list((ws / "state").glob("final_audit-*.txt"))
        self.assertEqual(len(out), 1)
        self.assertIn("== library", out[0].read_text(encoding="utf-8"))
        (ws / "HARDWARE_FUNCTIONS.md").write_text("nothing\n", encoding="utf-8")
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=900)
        self.assertIn("FAIL excluded:", r.stdout)
        self.assertIn("VERDICT FAIL", r.stdout)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
