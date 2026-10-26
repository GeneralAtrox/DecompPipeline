"""Batch 18 items 4 to 7: callee records against the callees' harnesses (core/callee_drift), a variadic function's
own entry thunk (readable_abi.thunk_c), declared extents of named globals and their overlaps (core/global_extents,
core/coverage). Run: python -W error tests/test_batch18.py (also from tests/test_tooling.py)."""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import A, SyncCase, make_ws, put  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import callee_drift, coverage, doctor, global_extents, oracle_stamp, paths, readable  # noqa: E402
from core import readable_abi  # noqa: E402
from core import retire as core_retire  # noqa: E402

CALLER, CALLEE = "8c020000", "8c020100"
REC = "f32 fn_8c020100(void)"  # Ghidra's stale view of the callee, what TcPrepare records in its callers
OWN = "void fn_8c020100(u32 param_r4)"  # the callee's own harness signature (authoritative)


def drift_ws(own_meta):
    ws = make_ws()
    put(ws / f"harness/{CALLER}/meta.json", {"addr": CALLER, "signature": "void fn_8c020000(void)",
                                             "callees": [{"addr": CALLEE, "regs": ["r4"], "signature": REC}]})
    put(ws / f"harness/{CALLEE}/meta.json", dict({"addr": CALLEE, "callees": []}, **own_meta))
    return ws


class CalleeDrift(unittest.TestCase):
    def test_record_differs_from_the_callee(self):
        ws = drift_ws({"signature": OWN})
        self.assertEqual(callee_drift.scan(ws / "harness"), {CALLER: [(CALLEE, REC, OWN)]})
        ws = drift_ws({"signature": REC})
        self.assertEqual(callee_drift.scan(ws / "harness"), {})

    def test_adopt_takes_the_callees_harness_signature(self):
        ws = drift_ws({"signature": OWN})
        meta = json.loads((ws / f"harness/{CALLER}/meta.json").read_text())
        meta["callees"] += [{"addr": "8c020200", "regs": [], "signature": "void fn_8c020200(void)"},  # no harness
                            {"addr": CALLER, "regs": [], "signature": "void fn_8c020000(void)"}]  # recursive
        changed = callee_drift.adopt(meta, callee_drift.Signatures(ws / "harness"))
        self.assertEqual(changed, [(CALLEE, REC, OWN)])
        self.assertEqual(meta["callees"][0], {"addr": CALLEE, "regs": ["r4"], "signature": OWN,
                                              "ghidra_signature": REC})  # the compared registers stay
        self.assertEqual(meta["callees"][1]["signature"], "void fn_8c020200(void)")
        self.assertEqual(callee_drift.drift(meta, callee_drift.Signatures(ws / "harness")), [])
        self.assertEqual(callee_drift.adopt(meta, callee_drift.Signatures(ws / "harness")), [])  # idempotent

    def test_callers_left_stale_and_stamp_current(self):
        ws = drift_ws({"signature": REC})
        self.assertEqual(callee_drift.callers_left_stale(ws / "harness", CALLEE, REC), [])
        self.assertEqual(callee_drift.callers_left_stale(ws / "harness", CALLEE, OWN), [CALLER])
        meta = dict(json.loads((ws / f"harness/{CALLER}/meta.json").read_text()), oracle_stamp="s1")
        self.assertIsNone(oracle_stamp.current(meta, "s1", ws / "harness"))
        self.assertEqual(oracle_stamp.current(meta, "s2", ws / "harness"), "oracle inputs changed")
        put(ws / f"harness/{CALLEE}/meta.json", {"addr": CALLEE, "signature": OWN, "callees": []})
        self.assertIn(CALLEE, oracle_stamp.current(meta, "s1", ws / "harness"))

    def test_doctor_fails_on_a_matched_caller(self):
        from test_retire import A as RA, B as RB, T, make_ws as retire_ws
        ws = retire_ws("drift")
        core_retire.apply(ws, core_retire.scan(ws))
        put(ws / f"harness/{RA}/meta.json", {"signature": "void fn_a(void)",
                                             "callees": [{"addr": T}, {"addr": RB, "signature": REC}]})
        put(ws / f"harness/{RB}/meta.json", {"signature": OWN, "callees": []})
        ok, lines, facts = doctor.check(ws)
        self.assertFalse(ok)
        self.assertTrue(any("1 matched function harness(es) with callee records that differ" in ln and RA in ln
                            for ln in lines), lines)
        self.assertEqual(facts["stale_callee_harnesses"], 1)
        put(ws / f"harness/{RA}/meta.json", {"signature": "void fn_a(void)",
                                             "callees": [{"addr": T}, {"addr": RB, "signature": OWN}]})
        ok, lines, facts = doctor.check(ws)
        self.assertTrue(ok, lines)


class Cascade(unittest.TestCase):
    """core/cascade: Y re-prepared adopts Z's harness signature and changes its own, which makes X stale."""
    X, Y, Z = "8c030000", "8c030100", "8c030200"  # X calls Y, Y calls Z

    def setUp(self):
        self.h = make_ws() / "harness"
        X, Y, Z = self.X, self.Y, self.Z
        put(self.h / f"{Z}/meta.json", {"signature": "void fn_z(u32 p)", "callees": []})  # the reference
        put(self.h / f"{Y}/meta.json", {"signature": "void fn_y(void)",
                                        "callees": [{"addr": Z, "signature": "f32 fn_z(void)"}]})  # Ghidra's view
        put(self.h / f"{X}/meta.json", {"signature": "void fn_x(void)",
                                        "callees": [{"addr": Y, "signature": "void fn_y(void)"}]})
        self.calls, self.adopts = [], True

    def prepare(self, addr):
        """ghidra.prepare as seen from the harness folder: Ghidra's records, then adopt (unless switched off)."""
        self.calls.append(addr)
        ghidra_view = {self.Y: ("u32 fn_y(void)", self.Z, "f32 fn_z(void)"),
                       self.X: ("void fn_x(void)", self.Y, "void fn_y(void)")}[addr]
        meta = {"addr": addr, "signature": ghidra_view[0],
                "callees": [{"addr": ghidra_view[1], "signature": ghidra_view[2]}]}
        if self.adopts:
            callee_drift.adopt(meta, callee_drift.Signatures(self.h))
        put(self.h / f"{addr}/meta.json", meta)

    def run_cascade(self, **kw):
        from core import cascade
        log = []
        out = cascade.cascade(self.h, {self.X, self.Y}, self.prepare, lambda a: "FAIL 3/96" if a == self.X else None,
                              log=log.append, **kw)
        return out, log

    def test_iterates_until_nothing_changes(self):
        out, log = self.run_cascade()
        self.assertEqual(self.calls, [self.Y, self.X])  # Z, the callee, is never re-prepared
        self.assertEqual((out["rounds"], out["left"], out["stuck"]), (2, {}, []))
        self.assertEqual(out["failed"], {self.X: "FAIL 3/96"})  # judged on its new harness: reported, not hidden
        self.assertIn("stale records of 1 callee(s) (8c030200); re-prepare 1: 8c030100", log[0])
        self.assertIn("no matched harness", log[-1])

    def test_dry_and_stuck(self):
        out, log = self.run_cascade(dry=True)
        self.assertEqual((self.calls, out["rounds"], list(out["left"])), ([], 0, [self.Y]))
        self.adopts = False  # before batch 18: a re-prepare reproduces Ghidra's stale record every time
        out, log = self.run_cascade(per_fn=2)
        self.assertEqual(self.calls, [self.Y, self.X, self.Y, self.X])  # each per_fn times, then it stops
        self.assertEqual((list(out["left"]), out["stuck"]), ([self.X, self.Y], [self.X, self.Y]))


class VariadicOwn(unittest.TestCase):
    def test_thunk_passes_stack_words_and_the_anchor(self):
        reg = readable.Registry.empty({"functions": {A: {"proto": "void Log_Printf(f32 x, const char *fmt, ...)",
                                                          "varargs": "printf"}}})
        meta = {"addr": A, "ret": "void", "callees": [], "params": [
            {"name": "param_fr4", "kind": "f32", "storage": "fr4", "stack_offset": -1},
            {"name": "param_s0", "kind": "u32", "storage": "stack", "stack_offset": 0}]}
        text = readable_abi.thunk_c(reg, A, meta)
        words = ", ".join(f"rd32(0x{0x8CFFE000 + k:08x}u)" for k in range(4, 32, 4))
        call = f"fr[0], (const char *)(uintptr_t)rd32(0x8cffe000u), {words}"
        self.assertIn(f"rd_va_probe({call});", text)
        self.assertIn(f"Log_Printf({call});", text)
        self.assertIn("static void rd_va_probe(f32 a0, const char * a1, ...)", text)
        self.assertIn("rd_set_va_anchor((u32)(uintptr_t)v, 0x8cffe004u);", text)
        reg = readable.Registry.empty({"functions": {A: {"proto": "void Log_Run(f32 x, const char *fmt)"}}})
        self.assertNotIn("rd_va_probe", readable_abi.thunk_c(reg, A, meta))

    def test_anchor_pairs_the_first_variable_word(self):
        from core.readable_check import map_stack_args
        meta = {"callees": [{"addr": "8c0683f0", "regs": ["r4", "r5", "r6"]}]}
        exp = {"calls": [[0x8C0683F0, 0x8CFFDC18, 0x8C0A0000, 0x8CFFE004] + [0] * 30]}
        good = {"calls": [[0x8C0683F0, 0x00190000, 0x8C0A0000, 0x00190400] + [0] * 30]}
        stack = (0x00100000, 0x00200000, (0x00190400, 0x8CFFE004))
        self.assertEqual(map_stack_args(meta, exp, good, stack), [])
        self.assertEqual(good["calls"][0][3], 0x8CFFE004)
        shifted = {"calls": [[0x8C0683F0, 0x00190000, 0x8C0A0000, 0x00190404] + [0] * 30]}
        self.assertTrue(map_stack_args(meta, exp, shifted, stack))

    def test_pointer_call_pairs_its_ca_registers(self):
        """A call through a function pointer (a target meta's callees do not list) is judged on the original's CA
        registers (judge hole 5); a local's address among them pairs one to one, as for a listed callee (TC
        8c05cc8c: &color to g_meshLightFn)."""
        from core.readable_check import map_stack_args
        meta = {"callees": []}
        exp = {"calls": [[0x8CC01900, 0x8CFFDFD8] + [0] * 32, [0x8CC01900, 0x8CFFDFD8] + [0] * 32],
               "call_args": {0: ["r4"], 1: ["r4"]}}
        stack = (0x00100000, 0x00200000, None)
        good = {"calls": [[0x8CC01900, 0x001AFC8C] + [0] * 32, [0x8CC01900, 0x001AFC8C] + [0] * 32]}
        self.assertEqual(map_stack_args(meta, exp, good, stack), [])
        self.assertEqual([c[1] for c in good["calls"]], [0x8CFFDFD8, 0x8CFFDFD8])
        two = {"calls": [[0x8CC01900, 0x001AFC8C] + [0] * 32, [0x8CC01900, 0x001AFC90] + [0] * 32]}
        self.assertTrue(map_stack_args(meta, exp, two, stack))  # one guest slot, two host slots: no pairing
        self.assertEqual(two["calls"][1][1], 0x001AFC90)
        bare = {"calls": [[0x8CC01900, 0x001AFC8C] + [0] * 32]}
        self.assertEqual(map_stack_args(meta, {"calls": exp["calls"][:1]}, bare, stack), [])
        self.assertEqual(bare["calls"][0][1], 0x001AFC8C)  # no CA: nothing judged, nothing paired


EXTENT_NAMES = {"functions": {}, "globals": {"8c0d0000": {"decl": "Vec3 g_points[2]"},
                                             "8c0d0010": {"decl": "u32 g_inside"},
                                             "8c0d0040": {"decl": "Vec3 *g_cursor"},
                                             "8c0d0100": {"decl": "char g_open[]"}}}


class GlobalExtents(SyncCase):
    def test_pure_helpers(self):
        ext = [(0x100, 24), (0x110, 4), (0x140, 4)]
        self.assertEqual(global_extents.containing(ext, 0x117), (0x100, 24))
        self.assertIsNone(global_extents.containing(ext, 0x118))
        self.assertEqual(global_extents.preceding(ext, 0x120), (0x110, 4))
        self.assertEqual(global_extents.overlaps(ext), [(0x100, 24, 0x110)])
        text, at = global_extents.probe_text([("8c0d0000", "Vec3 g_points[2]")])
        self.assertIn("typedef Vec3 rdx_0_t[2];", text)
        self.assertEqual(sorted(at.values()), ["8c0d0000", "8c0d0000"])

    def test_real_sizes_and_coverage(self):
        put(self.ws / "readable/names/S01.json", EXTENT_NAMES)
        sizes, unknown = global_extents.extents(self.cfg, readable.Registry(), self.ws / "build" / "ext")
        self.assertEqual(sizes, {"8c0d0000": 24, "8c0d0010": 4, "8c0d0040": 4})
        self.assertEqual(list(unknown), ["8c0d0100"])
        m = json.loads((self.ws / f"harness/{A}/meta.json").read_text())
        m["globals"] = ["8c0d0014", "8c0d0020", "8c0d0044", "8c0d0100"]
        put(self.ws / f"harness/{A}/meta.json", m)
        if not self.cfg["binary_path"].exists():  # batch 20's pointer constants read the image size
            self.cfg["binary_path"].write_bytes(bytes(0x100))
        out = []
        problems = coverage.report(self.cfg, [A], log=out.append)
        text = "\n".join(out)
        self.assertIn("globals without a name: 2", text)
        self.assertIn("8c0d0020  used by 8c010000; follows `u32 g_inside` (0x8c0d0010), 4 bytes", text)
        self.assertIn("8c0d0044  used by", text)
        self.assertIn("overlapping named globals: 1", text)
        self.assertIn("`u32 g_inside` (0x8c0d0010) starts inside `Vec3 g_points[2]` (0x8c0d0000), 24 bytes", text)
        self.assertIn("8c0d0100 `char g_open[]`: sizeof returns 0", text)
        self.assertEqual(problems, 1 + 2 + 2 + 1 + 1)  # A and its 2 callees unnamed; 2 globals; overlap; size
        self.assertTrue(paths.BUILD.is_relative_to(self.ws))


if __name__ == "__main__":
    unittest.main(verbosity=1)
