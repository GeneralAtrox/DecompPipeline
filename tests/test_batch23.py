"""Batch 23: readable work without an agent. Look-alikes across projects (core/lookalike: find with relocations masked,
map through the paired slots, propose without overwriting, rewrite for the target), machine drafts (core/draft,
core/draft_calls, core/draft_run: what is converted, what is left for the lint to refuse, the readability gate, the
run hook) and re-adopting earlier attempts (core/readopt: newest strict PASS, backup, redo list, never a FAIL).
Synthetic data; the readable lint is the real one, the build and compare are faked (the real-judge controls are in
STAGE_REPORT.md). Run: python -W error tests/test_batch23.py (also from tests/test_tooling.py).
"""
import json
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import draft, draft_run, fn_claim, lookalike, paths, readable, readable_headers, readopt, refine  # noqa: E402
from core.readable_lint import readable_lint  # noqa: E402

S, T = 0x8C010000, 0x8C020000


class FakeImage:
    """xmatch.Image's interface over a dict of words: {addr: u16}, {addr: u32} pool words."""

    def __init__(self, base, words, pool, fns):
        self.data = bytearray(0x40000)
        self.functions = {a: {"addr": a, "bytes": n} for a, n in fns.items()}
        for i, w in enumerate(words):
            struct.pack_into("<H", self.data, base - 0x8C010000 + 2 * i, w)
        for a, v in pool.items():
            struct.pack_into("<I", self.data, a - 0x8C010000, v)

    def u16(self, a):
        return struct.unpack_from("<H", self.data, a - 0x8C010000)[0]

    def u32(self, a):
        return struct.unpack_from("<I", self.data, a - 0x8C010000)[0]

    def in_image(self, a, n=2):
        return 0x8C010000 <= a and a + n <= 0x8C010000 + len(self.data)


CODE = [0xD101, 0x000B, 0x6012, 0x0009]  # mov.l @(1,PC),r1 (the pool word at +8); rts; mov.l @r1,r0; nop


def images(target_global=0x8C0B0000, last=0x0009):
    src = FakeImage(S, CODE, {S + 8: 0x8C0A0000}, {f"{S:08x}": 8})
    tgt = FakeImage(T, CODE[:3] + [last], {T + 8: target_global}, {f"{T:08x}": 8})
    return src, tgt


def source(functions, globals_):
    src = lookalike.Source.__new__(lookalike.Source)
    src.reg = readable.Registry.empty({"functions": functions, "globals": globals_})
    src.fn_by_name = {readable.parse_proto(e["proto"])[1]: a for a, e in src.reg.functions.items()}
    src.gl_by_name = {readable.global_name(e["decl"]): a for a, e in src.reg.globals.items()}
    src.extents = {"8c0a0000": 8}
    return src


SRC_TEXT = '#include "game.h"\n\nu32 Count_Get(void)\n{\n    return g_count;\n}\n'


class Lookalike(unittest.TestCase):
    def test_find_identical_and_near(self):
        src, tgt = images()
        same, near = lookalike.find(src, tgt, {f"{S:08x}"}, {f"{T:08x}"})
        self.assertEqual(same, {f"{T:08x}": [{"from": f"{S:08x}", "pairs": {"8c0a0000": "8c0b0000"}}]})
        self.assertEqual(near, {})
        src, tgt = images(last=0x0019)  # one other instruction word: near-identical, never used
        same, near = lookalike.find(src, tgt, {f"{S:08x}"}, {f"{T:08x}"})
        self.assertEqual((same, near), ({}, {f"{T:08x}": {"from": f"{S:08x}", "differ": 1}}))
        src, tgt = images(target_global=0x0C0B0000)  # a mirror address in the target's pool pairs canonically
        same, _ = lookalike.find(src, tgt, {f"{S:08x}"}, {f"{T:08x}"}, mirror={"0c": "8c"})
        self.assertEqual(same[f"{T:08x}"][0]["pairs"], {"8c0a0000": "8c0b0000"})

    def test_map_propose_rewrite(self):
        src = source({f"{S:08x}": {"proto": "u32 Count_Get(void)"}}, {"8c0a0000": {"decl": "u32 g_count[2]"}})
        names = lookalike.map_names(src, f"{S:08x}", f"{T:08x}", {"8c0a0004": "8c0b0004"}, SRC_TEXT)
        self.assertEqual(names["g_count"]["tgt"], "8c0b0000")  # a slot inside the extent gives the base
        self.assertEqual(names["Count_Get"]["tgt"], f"{T:08x}")
        bad = lookalike.map_names(src, f"{S:08x}", f"{T:08x}", {"8c0a0000": "8c0b0000", "8c0a0004": "8c0b0010"},
                                  SRC_TEXT)
        self.assertIsNone(bad["g_count"]["tgt"])  # two slots, two bases: unmapped, never judged
        tgt_reg = readable.Registry.empty({"functions": {f"{T:08x}": {"proto": "u32 Score_Read(void)"}},
                                           "globals": {"8c0c0000": {"decl": "u32 g_count"}}})
        prop = lookalike.propose(src, tgt_reg, {f"{T:08x}": names}, "tc")
        self.assertEqual(prop["kept"], {f"{T:08x}": "Score_Read"})  # the target's own name is never overwritten
        self.assertEqual(prop["functions"], {})
        self.assertTrue(any("g_count" in c for c in prop["conflicts"]))  # the target names g_count elsewhere
        ren = {n: lookalike.target_name(tgt_reg, m) for n, m in names.items()}
        self.assertEqual(lookalike.rewrite(SRC_TEXT, ren).splitlines()[2:5],
                         ["u32 Score_Read(void)", "{", "    return g_8c0b0000;"])

    def test_one_name_many_addresses(self):
        src = source({f"{S:08x}": {"proto": "void View_Nop(void)"}}, {})
        text = '#include "game.h"\n\nvoid View_Nop(void)\n{\n}\n'
        mapped = {t: lookalike.map_names(src, f"{S:08x}", t, {}, text) for t in ("8c020000", "8c020100")}
        prop = lookalike.propose(src, readable.Registry.empty(), mapped, "tc")
        self.assertEqual(prop["functions"], {})
        self.assertTrue(any("one source name for 2" in c for c in prop["conflicts"]))

    def test_retarget(self):
        text = '#include "game.h"\n\nvoid Ship_Hit(Ship *ship, s32 dmg)\n{\n    ship->hp -= dmg;\n}\n'
        got = lookalike.retarget(text, "void fn_8c020000(u32 param_r4, s32 param_r5)")
        self.assertIn("void fn_8c020000(u32 param_r4, s32 param_r5)\n{\n    param_r4->hp -= param_r5;", got)
        self.assertIsNone(lookalike.retarget(text, "void fn_8c020000(u32 param_r4)"))  # another parameter count

    def test_wrong_global_fails_the_compare(self):
        """A look-alike whose global maps to the wrong target address: the target's code reads the paired address,
        the adopted file the mapped one; a compare of what each reads (the judge's memory compare, in miniature)
        fails, the right mapping passes."""
        src = source({f"{S:08x}": {"proto": "u32 Count_Get(void)"}}, {"8c0a0000": {"decl": "u32 g_count"}})
        src.extents = {"8c0a0000": 4}
        _, tgt = images()
        reads = tgt.u32(T + 8)  # what the original reads: its pool word

        def compare(pairs):
            names = lookalike.map_names(src, f"{S:08x}", f"{T:08x}", pairs, SRC_TEXT)
            return int(names["g_count"]["tgt"], 16) == reads

        self.assertTrue(compare({"8c0a0000": "8c0b0000"}))
        self.assertFalse(compare({"8c0a0000": "8c0b0004"}))


RAW = """#include "tc_rt.h"

/* notes */
u32 fn_8c010000(u32 param_r4)
{
    TcArgs args = {{0, 0, 0, 0}, {0, 0, 0, 0, 0, 0, 0, 0}};
    f32 k;

    /* mov.l @(1,PC),r1 */
    k = rdf32(0x8c010040u);
    wrf32(0x8c0a0004u, k);
    if (rd32(0x8c0a0000u) != 0) {
        args.r[0] = param_r4;
        args.r[1] = 0x8c0a0008u;
        (void)tc_call(0x8c020000u, &args);
        return 0;
    }
    wr32(0x8c0a0000u, tc_call(0x8c020100u, &args));
    return rd32(0x8c0b0000u) + 4;
}
"""
PROTO = "s32 Thing_Check(Actor *actor)"
META = {"addr": "8c010000", "signature": "u32 fn_8c010000(u32 param_r4)", "callees": [],
        "params": [{"name": "param_r4", "storage": "r4", "stack_offset": -1}]}


def names():
    reg = readable.Registry.empty({"functions": {"8c010000": {"proto": PROTO}}})
    reg.typedefs = {"Actor": "struct"}
    half = struct.unpack("<I", struct.pack("<f", 0.5))[0]
    return reg, draft.Names({0x8C0A0000: ("g_count", "s32", []), 0x8C0A0004: ("g_scale", "f32", []),
                             0x8C0A0008: ("g_name", "const char", ["8"]), 0x8C0B0000: ("g_list", "Actor *", [])},
                            {"8c020000": "Log_Print", "8c020100": "Count_Get"}, reg.kind,
                            pool={0x8C010040: lambda v, n: half},
                            callees={"8c020000": ("Log_Print", ["r4", "r5"], "v", False),
                                     "8c020100": ("Count_Get", [], "i", False)})


def make(raw):
    reg, nm = names()
    text = draft.rewrite_memory(draft.strip_comments(raw).replace('"tc_rt.h"', '"game.h"'), nm)
    text = draft.rewrite_tc_calls(text, nm.callees)
    text = draft.rewrite_values(text, nm)
    return draft_run.header(text, reg, "8c010000", META, PROTO)


class Draft(unittest.TestCase):
    def test_converted(self):
        got = make(RAW)
        for line in ("s32 Thing_Check(Actor *actor)", "    k = 0.5f;", "    g_scale = k;", "    if (g_count != 0) {",
                     "        Log_Print(actor, g_name);", "    g_count = Count_Get();"):
            self.assertIn(line + "\n", got)
        self.assertNotIn("TcArgs", got)
        self.assertNotIn("/*", got)
        # u32 arithmetic on a pointer global stays raw (it would become pointer arithmetic): the lint refuses it
        self.assertIn("rd32(0x8c0b0000u) + 4", got)
        self.assertTrue(any(e.startswith("RR2: 'rd32'") for e in readable_lint(got, "Thing_Check", PROTO)))
        clean = make(RAW.replace("rd32(0x8c0b0000u) + 4", "1"))
        self.assertEqual(readable_lint(clean, "Thing_Check", PROTO), [])
        self.assertEqual(draft_run.readable_gaps(clean), [])

    def test_tc_call_left_is_refused(self):
        """An argument set in another block (not right before the call): the call stays raw, TcArgs too, and the
        readable lint refuses the draft: a draft never relaxes a lint."""
        raw = RAW.replace("        args.r[1] = 0x8c0a0008u;\n", "").replace(
            "    if (rd32(0x8c0a0000u) != 0) {", "    args.r[1] = 0x8c0a0008u;\n    if (rd32(0x8c0a0000u) != 0) {")
        got = make(raw.replace("rd32(0x8c0b0000u) + 4", "1"))
        self.assertIn("tc_call(0x8c020000u, &args)", got)
        lint = readable_lint(got, "Thing_Check", PROTO)
        self.assertTrue(any(e.startswith("RR2: 'tc_call'") for e in lint), lint)
        self.assertTrue(any(e.startswith("RR2: 'TcArgs'") for e in lint), lint)

    def test_side_effects_and_void(self):
        self.assertTrue(draft.side_effect_free("(u32)x + y[2]"))
        self.assertFalse(draft.side_effect_free("f(x)"))
        self.assertFalse(draft.side_effect_free("x = 2"))
        self.assertFalse(draft.side_effect_free("i++"))
        _, nm = names()
        self.assertEqual(draft.rewrite_tc_calls("void f(void)\n{\n    TcArgs a = {{0}};\n    return tc_call(0x8c020000u, "
                                                "&a);\n}\n", nm.callees, own_void=True).count("Log_Print(0, 0);"), 1)

    def test_gaps(self):
        self.assertEqual(draft_run.readable_gaps("void F(void)\n{\n    u32 r4 = param_r4; /* fr4 */\n    fn_8c012345();\n}"),
                         ["fn_8c012345", "param_r4", "r4"])


class Hook(unittest.TestCase):
    """draft_run.before_agent's modes and fn_claim.cycle's use of it (judge faked)."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="drh", dir=TMP))
        (self.d / "src").mkdir()
        self.patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                        mock.patch.object(readable, "SRC", self.d / "src"), mock.patch.object(paths, "RUNS", self.d)]
        for p in self.patches:
            p.start()
        self.path = self.d / "draft.c"
        self.path.write_text("/* draft */", encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def hook(self, res, gaps, mode):
        with mock.patch.object(draft_run, "judge", lambda *a: (self.path, res, gaps)):
            return draft_run.before_agent({}, None, "8c010000", {}, mode, lambda m: None)

    def test_modes(self):
        ok = {"verdict": "PASS", "stage": "compare"}
        bad = {"verdict": "FAIL", "stage": "compare", "failing": 3, "total": 96}
        self.assertIsNone(self.hook(ok, [], "off"))
        self.assertIsNone(self.hook(bad, [], "adopt"))
        self.assertEqual(self.hook(bad, [], "start")[1][0], self.path)
        self.assertEqual(self.hook(ok, ["r4"], "start")[0], "start")  # passes but not readable: never adopted
        self.assertFalse((self.d / "src" / "8c010000.c").exists())
        self.assertEqual(self.hook(ok, [], "adopt"), ("adopted", "readable"))
        row = refine.load_ledger()["8c010000"]
        self.assertEqual((row["status"], row["source"], row["attempts"][-1]["kind"]), ("readable", "draft", "draft"))

    def test_cycle(self):
        seen = []
        with mock.patch.object(draft_run, "first", lambda *a: ("adopted", "readable")), \
                mock.patch.object(refine, "refine", lambda *a, **k: seen.append(k)):
            self.assertEqual(fn_claim.cycle({}, "8c010000", 3, lambda m: None, draft="adopt"), "readable")
        self.assertEqual(seen, [])  # no agent
        with mock.patch.object(draft_run, "first", lambda *a: ("start", (self.path, "note"))), \
                mock.patch.object(refine, "refine", lambda *a, **k: seen.append(k) or "failed"):
            fn_claim.cycle({}, "8c010000", 3, lambda m: None, draft="start")
        self.assertEqual(seen[0]["start"], (self.path, "note"))


class Readopt(unittest.TestCase):
    """readopt: the newest strict PASS of the earlier attempts, the old file backed up; else the redo list."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="rdo", dir=TMP))
        (self.d / "src").mkdir()
        self.patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                        mock.patch.object(readable, "SRC", self.d / "src"), mock.patch.object(paths, "RUNS", self.d),
                        mock.patch.object(paths, "BUILD", self.d / "build"),
                        mock.patch.object(readopt, "BACKUP", self.d / "backup"),
                        mock.patch.object(readopt, "REDO_LIST", self.d / "redo.txt"),
                        mock.patch.object(readopt.rcheck, "check_readable", self.judge)]
        for p in self.patches:
            p.start()
        self.a = "8c010000"
        for n, text in ((1, "PASS one"), (2, "PASS two"), (3, "FAIL three")):
            p = self.d / self.a / "readable" / f"attempt-{n:02d}" / "work.c"
            p.parent.mkdir(parents=True)
            p.write_text(text, encoding="utf-8")
        (self.d / "src" / f"{self.a}.c").write_text("STALE old", encoding="utf-8")
        refine.update(self.a, status="stale", note="build: C2065")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    @staticmethod
    def judge(cfg, addr, src, build_dir, **kw):
        text = pathlib.Path(src).read_text(encoding="utf-8")
        if text.startswith("PASS"):
            return {"verdict": "PASS", "stage": "compare", "failing": 0, "total": 96}
        return {"verdict": "FAIL", "stage": "compare", "failing": 4, "total": 96, "lint": [], "build": []}

    def test_newest_pass_adopted(self):
        self.assertEqual(readopt.readopt({}, None, self.a, lambda m: None), "readopt attempt-02")
        self.assertEqual((self.d / "src" / f"{self.a}.c").read_text(encoding="utf-8"), "PASS two")
        self.assertEqual([p.read_text(encoding="utf-8") for p in (self.d / "backup").iterdir()], ["STALE old"])
        row = refine.load_ledger()[self.a]
        self.assertEqual((row["status"], row["source"], row["attempts"][-1]["from_attempt"]),
                         ("readable", "readopt attempt-02", 2))
        self.assertNotIn("note", row)

    def test_never_a_fail(self):
        for n in (1, 2):
            (self.d / self.a / "readable" / f"attempt-{n:02d}" / "work.c").write_text("FAIL", encoding="utf-8")
        logs = []
        self.assertIsNone(readopt.readopt({}, None, self.a, logs.append))
        self.assertEqual((self.d / "src" / f"{self.a}.c").read_text(encoding="utf-8"), "STALE old")
        self.assertTrue(any("same text as a file already judged" in m for m in logs))
        self.assertEqual(readopt.add_redo([self.a, self.a]), 1)
        self.assertEqual([ln for ln in (self.d / "redo.txt").read_text(encoding="utf-8").splitlines()
                          if not ln.startswith("#")], [self.a])

    def test_strict_pass_only(self):
        with mock.patch.object(readopt.rcheck, "check_readable",
                               lambda *a, **k: {"verdict": "PASS", "stage": "compare", "run_error": "crash"}):
            self.assertIsNone(readopt.readopt({}, None, self.a, lambda m: None))

    def test_adopt_command_failed_row(self):
        (self.d / "src" / f"{self.a}.c").unlink()
        refine.update(self.a, status="failed")
        with mock.patch.object(fn_claim, "LOCKS", self.d / "locks"), \
                mock.patch.object(refine, "raw_missing", lambda a: None), \
                mock.patch.object(readopt, "readable", readable):
            out = readopt.adopt_command({"function_lock_wait_s": 0.2}, [self.a], lambda m: None)
        self.assertEqual(out, {self.a: "ADOPTED (readopt attempt-02)"})
        self.assertEqual(refine.load_ledger()[self.a]["status"], "readable")
        self.assertEqual(json.loads((self.d / "readable.json").read_text(encoding="utf-8"))[self.a]["source"],
                         "readopt attempt-02")


class Headers23(unittest.TestCase):
    """A #define whose /* comment closes on a later line is one item; the items after it keep their names."""

    TEXT = ("#ifndef T_H\n#define T_H\n"
            "#define BEAM_SLOTS 5 /* beams per unit,\n   see the effects table */\n"
            "typedef struct Beam { int life; } Beam;\n"
            "enum { UNIT_BEAM_EFFECTS = 3 };\n"
            "extern Beam g_beams[BEAM_SLOTS];\n#endif\n")

    def parse(self, text):
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "types.h").write_text(text, encoding="utf-8")
            return readable_headers.Headers(pathlib.Path(t)).finish()

    def test_multiline_comment_after_define(self):
        hs = self.parse(self.TEXT)
        for name in ("BEAM_SLOTS", "Beam", "UNIT_BEAM_EFFECTS"):
            self.assertIn(name, hs.by_name, name)
        self.assertIn("see the effects table */", hs.by_name["BEAM_SLOTS"].text)
        self.assertNotIn("BEAM_SLOTS", hs.by_name["Beam"].text)

    def test_control_old_parser_loses_names(self):
        with mock.patch.object(readable_headers, "_open_comment", lambda lines: False):
            hs = self.parse(self.TEXT)
        self.assertFalse({"Beam", "UNIT_BEAM_EFFECTS"} <= set(hs.by_name))


if __name__ == "__main__":
    unittest.main()
