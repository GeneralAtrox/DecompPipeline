"""A hand-written asm module judged and translated as one unit (D2; PS2's Lib_RenderModelA 8c188420: local `bsr`
subroutines pass a cursor in r4 and flag bits in r9, and 8c189000 jumps through `braf r9` into a run of bra's). DP's
oracle runs a bsr into the function's own extent as part of it; the translator used to emit every bsr as a mocked
call (r4 and r9 kept, the subroutine's work lost). Pass and fail controls:
1. the C runner on translator C (compiled, the raw runtime): a caller bsr's twice to a leaf that loads @r4+ and
   shifts the word's bit 0 into r9 (rotcl in the rts slot), then returns @r4 + r9. Inside the body the bsr is a jump
   that sets PR and the leaf's rts returns through PR: 0x102 (the third word plus flag bits 0b10). The same caller
   with the leaf outside its body (a separate function, mocked) keeps r4 and r9: 2;
2. the C: no tc_call for the local bsr's, `pr = ret; goto` and a PR dispatch at the rts; a bsr out of the body is
   still a call (control);
3. `and #7,r0; shll2 r0; braf r0` into eight bra's: a switch over the 8 targets (the bit-width pass); flag bits
   built by rotcl in a local subroutine bound `shll2 r9; braf r9` the same way; an unbounded braf gets no table;
4. core/asm_modules: the leaf is a local piece of a module (reads r9 at entry, hands back r9 and r4); a leaf
   handing back only r4 is TcOut's class; a leaf sharing nothing is a plain private callee; a leaf whose address a
   word of the image holds is no local;
5. lint's 60 KB cap: a unit (a catalog_overrides not_entry piece in its blocks) may pass it, through check.run_check
   as the judge reads project.json; a function without a piece in its blocks still fails it, and a unit's other lint
   errors still fail.
Synthetic data. Run: python -W error tests/test_asm_module.py.
"""
import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from sh4translate import translate as tx  # noqa: E402
from core import asm_modules, check, lint, mid_entries, toolcfg  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
WORK = ROOT / "tests" / ".work"
VC64 = toolcfg.load(ROOT).get("vcvars")
BASE = 0x8c000000
LEAF_AT = BASE + 0x14
# caller: sts.l pr,@-r15; bsr leaf (mov #0,r9); bsr leaf (nop); mov.l @r4,r0; add r9,r0; lds.l @r15+,pr; rts; nop
CALLER = [0x4f22, 0xb007, 0xe900, 0xb005, 0x0009, 0x6042, 0x309c, 0x4f26, 0x000b, 0x0009]
# leaf at +0x14: mov.l @r4+,r0; tst #1,r0; rts; rotcl r9
LEAF = [0x6046, 0xc801, 0x000b, 0x4924]
SIG = tx.Signature("fn_8c000000", "u32 fn_8c000000(u32 param_r4)", [("param_r4", "u32", "r4")], "u32", None, {})


def image(words):
    return tx.Image(b"".join(struct.pack("<H", w) for w in words), BASE)


def translate(words, body=None, entries=None):
    src = tx.translate_function(image(words), BASE, SIG, tx.TargetFacts(), body, entries=entries)
    return src[0] if isinstance(src, tuple) else src


THUNK = """#include "tc_rt.h"
u32 fn_8c000000(u32 param_r4);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    (void)out_fr0;
    *out_r0 = fn_8c000000(r[0]);
}
"""
# r4 = 8c010100 (M lines are bytes, little endian): words 2 (bit 0 clear: T=1), 5 (T=0), then 0x100
VECTORS = ("V 0 dev\nR 8c010100 0 0 0\nA " + " ".join(["00000000"] * 16) +
           "\nM 8c010100 02000000\nM 8c010104 05000000\nM 8c010108 00010000\nE\n")


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="asmmod_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        cls.c = {"unit": translate(CALLER + LEAF),
                 "mocked": translate(CALLER + LEAF, body=[(BASE, BASE + 0x12)], entries={BASE, LEAF_AT})}
        rt = SH4 / "runtime"
        srcs = ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")
        cmds = []
        for name, src in cls.c.items():
            b = d / name
            b.mkdir()
            (b / "work.c").write_text(src)
            (b / "thunk.c").write_text(THUNK)
            cmds.append(f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c thunk.c '
                        + " ".join(f'"{rt / s}"' for s in srcs))
        vc = str(VC64).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && ' + " && ".join(cmds) + '"', capture_output=True,
                           text=True, errors="replace", timeout=600)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:])
        (d / "image.bin").write_bytes(bytes(0x200))
        (d / "fsca.bin").write_bytes(bytes(1 << 20))
        (d / "code.txt").write_text("8c000000 8c000020\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_one(self, name):
        (self.d / "v.txt").write_text(VECTORS)
        r = subprocess.run([str(self.d / name / "runner.exe"), str(self.d / "image.bin"), str(self.d / "code.txt"),
                            str(self.d / "fsca.bin"), str(self.d / "v.txt"), "all", str(self.d / "out.txt")],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = (self.d / "out.txt").read_text()
        return [int(ln.split()[1], 16) for ln in out.splitlines() if ln.startswith("RET ")]

    def test_unit_runs_its_local_subroutine(self):
        self.assertEqual(self.run_one("unit"), [0x102])     # the leaf's r4 and r9, twice

    def test_mocked_leaf_keeps_r4_r9(self):                  # control: the leaf outside the body is a mocked call
        self.assertEqual(self.run_one("mocked"), [0x2])


class Shape(unittest.TestCase):
    def test_local_bsr_is_a_pr_jump(self):
        c = translate(CALLER + LEAF)
        self.assertNotIn("tc_call(", c)
        self.assertIn("pr = 0x8c000006u;", c)
        self.assertIn("pr = 0x8c00000au;", c)
        self.assertIn("if (pr != 0x8c000006u) {", c)    # the rts: back to the bsr that set PR (structured)

    def test_bsr_out_of_the_body_still_calls(self):
        c = translate(CALLER + LEAF, body=[(BASE, BASE + 0x12)], entries={BASE, LEAF_AT})
        self.assertEqual(c.count("tc_call(0x8c000014u"), 2)
        self.assertNotIn("goto L_8c000014", c)


def bra_run(n):
    """n `bra end; nop` pairs, then end: rts; nop."""
    out = []
    for k in range(n):
        out += [0xa000 | ((2 * (n - k) - 2) & 0xfff), 0x0009]
    return out + [0x000b, 0x0009]


class BrafWidth(unittest.TestCase):
    def switch_targets(self, words):
        ft = tx.FunctionTranslator(image(words), BASE, SIG, tx.TargetFacts())
        ft.discover()
        return sorted(t for ts in ft.switches.values() for t in ts)

    def test_masked_index(self):     # and #7,r0; shll2 r0; braf r0; nop; 8 x (bra end; nop); end
        words = [0xc907, 0x4008, 0x0023, 0x0009] + bra_run(8)
        self.assertEqual(self.switch_targets(words), [BASE + 8 + 4 * k for k in range(8)])

    def test_flag_bits_from_a_local_subroutine(self):
        # mov #0,r9 ... 3 x (bsr leaf2; nop) ... shll2 r9; braf r9; nop; 8 x (bra end; nop); end: rts; nop;
        # leaf2: tst #1,r4; rts; rotcl r9
        head = [0x4f22, 0xe900]
        calls = []
        for k in range(3):
            calls += [None, 0x0009]
        tail = [0x4908, 0x0923, 0x0009] + bra_run(8)
        leaf_at = BASE + 2 * (len(head) + len(calls) + len(tail))
        words = head + calls + tail + [0xc801, 0x000b, 0x4924]
        for k in range(3):
            at = BASE + 2 * (len(head) + 2 * k)
            words[len(head) + 2 * k] = 0xb000 | (((leaf_at - at - 4) // 2) & 0xfff)
        braf_at = BASE + 2 * (len(head) + len(calls) + 1)
        self.assertEqual(self.switch_targets(words), [braf_at + 4 + 4 * k for k in range(8)])

    def test_unbounded_braf_gets_no_table(self):  # control: braf r0 of an argument
        self.assertEqual(self.switch_targets([0x0423, 0x0009] + bra_run(2)), [])


class Detect(unittest.TestCase):
    def find(self, words, starts, extra_words=()):
        data = b"".join(struct.pack("<H", w) for w in words) + b"".join(struct.pack("<I", w) for w in extra_words)
        body = "\n".join(f"{s:08x} {s:08x} {e:08x}" for s, e in starts)
        return asm_modules.find(data, BASE, mid_entries.Bodies(body, [s for s, _ in starts]), [s for s, _ in starts])

    def test_register_passing_leaf_is_a_module_piece(self):
        got = self.find(CALLER + LEAF, [(BASE, BASE + 0x14), (LEAF_AT, LEAF_AT + 8)])
        self.assertEqual([(m["entries"], m["locals"]) for m in got["modules"]], [(["8c000000"], ["8c000014"])])
        ev = got["modules"][0]["evidence"]["8c000014"]
        self.assertIn("reads r9 at entry (no argument)", ev)
        self.assertTrue(any(e.startswith("hands back r9: 8c000000 reads it at 8c00000c") for e in ev), ev)

    def test_r4_only_leaf_is_tcout(self):   # leaf: add #0x20,r4; rts; nop
        got = self.find(CALLER[:5] + [0x6042, 0x0009, 0x4f26, 0x000b, 0x0009] + [0x7420, 0x000b, 0x0009, 0x0009],
                        [(BASE, BASE + 0x14), (LEAF_AT, LEAF_AT + 8)])
        self.assertEqual(got["modules"], [])
        self.assertEqual([t["addr"] for t in got["tcout"]], ["8c000014"])

    def test_plain_leaf(self):              # leaf: mov #1,r0; rts; nop (the C ABI's result only)
        got = self.find(CALLER[:5] + [0x0009] * 5 + [0xe001, 0x000b, 0x0009, 0x0009],
                        [(BASE, BASE + 0x14), (LEAF_AT, LEAF_AT + 8)])
        self.assertEqual((got["modules"], got["plain"]), ([], ["8c000014"]))

    def test_entry_a_piece_runs_on_into_joins_it(self):  # PS2 8c188b34: a piece's shared tail, no branch to it
        leaf = [0x6046, 0xc801, 0x4924, 0x000b, 0x0009]     # mov.l @r4+,r0; tst #1,r0; rotcl r9 | rts; nop
        got = self.find(CALLER + leaf, [(BASE, BASE + 0x14), (LEAF_AT, LEAF_AT + 6), (LEAF_AT + 6, LEAF_AT + 10)])
        m = got["modules"][0]
        self.assertEqual(m["locals"], ["8c000014", "8c00001a"])
        self.assertEqual(m["evidence"]["8c00001a"], ["no reference; 8c000014 runs on into it at 8c000018"])

    def test_pointer_word_makes_it_public(self):
        got = self.find(CALLER + LEAF, [(BASE, BASE + 0x14), (LEAF_AT, LEAF_AT + 8)], extra_words=(LEAF_AT,))
        self.assertEqual(got["modules"], [])


class ApplyDropsPieces(unittest.TestCase):
    """tools/asm_modules --apply: a piece leaves "entry" (Spawn 8c0602aa) and extra_functions (a list or its file)."""

    def test_entry_and_extra_list(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from asm_modules import drop_pieces
        data = {"catalog_overrides": {"entry": ["8c05fec2", "8c0602aa"]}, "extra_functions": ["8c05fec2", "8c0602a0"]}
        self.assertEqual(drop_pieces(data, ".", {"8c0602a0", "8c0602aa"}), (["8c0602aa"], ["8c0602a0"]))
        self.assertEqual(data, {"catalog_overrides": {"entry": ["8c05fec2"]}, "extra_functions": ["8c05fec2"]})

    def test_extra_file_and_nothing_to_drop(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from asm_modules import drop_pieces
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "extra.txt"
            f.write_text("8c05fec2  # drawer\n8c0602a0\n", encoding="utf-8")
            data = {"extra_functions": "extra.txt"}
            self.assertEqual(drop_pieces(data, td, {"8c0602a0"}), ([], ["8c0602a0"]))
            self.assertEqual(f.read_text(encoding="utf-8"), "8c05fec2  # drawer\n")
            self.assertEqual(drop_pieces(data, td, {"8c999999"}), ([], []))   # control: the file stays
            self.assertEqual(f.read_text(encoding="utf-8"), "8c05fec2  # drawer\n")


class UnitSizeCap(unittest.TestCase):
    META = {"name": "fn_8c000000", "signature": "u32 fn_8c000000(u32 param_r4)",
            "blocks": [["8c000000", "8c000013"], ["8c000014", "8c00001b"]]}
    BIG = ('#include "tc_rt.h"\nu32 fn_8c000000(u32 param_r4)\n{\n' + "    param_r4 = param_r4 + 1u;\n" * 2500
           + "    return param_r4;\n}\n")
    GLOBAL = "static u32 g;\n"          # R6: a global, another lint error
    SIZE = "work.c is larger than 60 KB"

    def test_big_unit_passes(self):
        self.assertGreater(len(self.BIG), 60000)
        self.assertEqual(lint.lint(self.BIG, self.META, ["8c000014"]), [])

    def test_big_non_unit_fails(self):      # controls: no piece, a piece outside the blocks, the old call
        for ne in ((), ["8c000020"], None):
            self.assertEqual(lint.lint(self.BIG, self.META, ne), [self.SIZE])
        self.assertEqual(lint.lint(self.BIG, self.META), [self.SIZE])

    def test_unit_other_errors_fail(self):
        errs = lint.lint(self.BIG + self.GLOBAL, self.META, ["0c000014"])   # P0 form of the piece
        self.assertNotIn(self.SIZE, errs)
        self.assertTrue(any(e.startswith("R6:") for e in errs), errs)

    def test_run_check_reads_project_not_entry(self):
        with tempfile.TemporaryDirectory() as d:
            h = pathlib.Path(d)
            (h / "meta.json").write_text(json.dumps(self.META), encoding="utf-8")
            (h / "work.c").write_text(self.BIG + self.GLOBAL, encoding="utf-8")
            for over, size in (({"not_entry": ["8c000014"]}, False), ({"entry": ["8c000014"]}, True), (None, True)):
                cfg = {"catalog_overrides": over} if over is not None else {}
                r = check.run_check(cfg, "8c000000", h / "work.c", harness_dir=h, fused=False)
                self.assertEqual((r["verdict"], r["stage"]), ("FAIL", "lint"))
                self.assertEqual(self.SIZE in r["lint"], size, (over, r["lint"]))


if __name__ == "__main__":
    unittest.main()
