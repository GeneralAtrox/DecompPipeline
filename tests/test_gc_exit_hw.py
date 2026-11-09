"""GameCube translator: four Skies of Arcadia failures and the planted wrong answers that show each fix.

1. Callee-saved registers at exit (80085414 r25, 80116b84 r20, 80259798 r14, 8025b868 r29). The strict judge's exit
   frame holds r14..r31 and f14..f31, but the C runtime printed 0 for all of them, so a function that leaves one changed
   (an lmw from a saved context, a stack overrun into a save slot) failed even when its C was right, and a wrong
   restore could never be told apart. Every return now reports each callee-saved register the function writes
   (tc_xr / tc_xrf, a value of 0 left out), and the runtime prints what was reported.
2. Hardware reads keep their order (80232b90, 80232d4c, 80235e4c). Two lhz from one device were folded into one C
   expression, whose operand order C leaves open: MSVC read 0xcc005006 before 0xcc005004. A hardware read is no longer
   moved past another hardware read, nor into an expression that has one, and one whose value is unused stays.
3. A hardware read the code always makes stays unconditional (80265c34, 80265e48). srw's "count >= 32 gives 0" is a ?:,
   and the read of 0xcc006438 had been moved into its other arm, so the C skipped it when the count was large.
4. A call's other result is read before the next call (80240634): r4 after a call (tc_lastr4) was carried as a value
   past the calls that followed, so three sums of three calls' r4 all read the last one's.
5. lfs keeps a signalling NaN (801e203c). C's float-to-double conversion quiets it (0xff81dfa3 came back 0xffc1dfa3
   through stfs); px_lfs widens the single bit for bit as the 750CL does.
6. The oracle's own faults, fixed in its Ghidra scripts and shown by the judge on Skies (SKIES_TRANSLATE_SCORE.md):
   a read whose first bytes were never written made the fault handler zero the written bytes after them in the page
   (80211298, through the call record's stack words; 800e6e58, a word whose low half was just stored). A subnormal
   single needs no fixup: Ghidra widens it exactly (800e6e58's expected.txt is the same with a widening fixup).
8. A self-recursive body (px_rec) whose f1 only a call sets passes the last mocked call's f1 at an inner frame's pointer
   call (80211298, hidden vector 96), not 0.0.
9. An invalid division (0/0, inf/inf) gives the 750CL's default NaN, +0x7ff8... (px_fdiv), not x86's negative one
   (general correctness; no Skies verdict turned on it).
10. fnmadd / fnmsub (px_fnm, ppc_fnmadds / ppc_fnmsubs) negate the result unless it is a NaN, which keeps its sign
   (801231f8: a NaN through frsqrte's Newton steps; the C passed 0xffc00000 where the original passes 0x7fc00000).
11. An invalid add, sub, multiply or fused op (inf - inf, 0 * inf) gives the 750CL's +NaN too (px_fadd .. px_fmsub,
   ppc_fmadds / ppc_fmsubs): 801231f8's frsqrte(inf) = 0, then inf * 0.
7. The structurer's flag hop carried through a second hop keeps its value (800d8214, 801a2d60, 8025ce88), as the SH2
   structurer's: checked against the gotos with cfgcheck.
The paired-single side (psq_l / psq_st through an unquantized GQR: SLEIGH's float2float made every NaN 0x7fc00000 in
the oracle, 8024f838, 80252100, 80252150, 802521d8) is TcFixups "psq_l" / "psq_st"; its wiring is checked here and its
effect by the judge on Skies (SKIES_TRANSLATE_SCORE.md).
Skipped without vcvars (MSVC) for the runtime part. Run: python tests/test_gc_exit_hw.py
"""
import json
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
GC = TOOL / "targets" / "gamecube_ppc"
RT = GC / "runtime"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(GC / "translator")]

from test_fmac import vcvars  # noqa: E402
from ppctranslate import emit  # noqa: E402
from ppctranslate.image import Dol  # noqa: E402
from ppctranslate.translate import Facts, Signature, translate_function  # noqa: E402

BASE = 0x80003100
BLR = 0x4E800020


def translate(words, sig=None):
    """The C for one function made of the instruction words, at 0x80003100 in a one-section DOL (sig: generic)."""
    code = b"".join(struct.pack(">I", w) for w in words)
    hdr = bytearray(0x100)
    struct.pack_into(">I", hdr, 0x00, 0x100)        # text 0: file offset
    struct.pack_into(">I", hdr, 0x48, BASE)         # address
    struct.pack_into(">I", hdr, 0x90, len(code))    # size
    struct.pack_into(">I", hdr, 0xE0, BASE)         # entry
    dol = Dol(bytes(hdr) + code)
    facts = Facts.from_target(json.loads((GC / "target.json").read_text(encoding="utf-8")), {})
    src, _ = translate_function(dol, BASE, sig or Signature.generic_for(BASE), facts, [(BASE, BASE + len(code))])
    return src


class ExitRegisters(unittest.TestCase):
    SAVE = [0x9421FFE0, 0x9321001C]                 # stwu r1,-0x20(r1); stw r25,0x1c(r1)
    RESTORE = [0x8321001C, 0x38210020, BLR]          # lwz r25,0x1c(r1); addi r1,r1,0x20; blr

    def test_restored_register_is_not_reported(self):
        src = translate(self.SAVE + [0x3B20002B] + self.RESTORE)  # li r25,0x2b between save and restore
        self.assertNotIn("tc_xr", src)

    def test_changed_register_is_reported(self):
        src = translate([0x3B20002B, BLR])  # li r25,0x2b; blr: r25 is 0x2b at exit
        self.assertIn("tc_xr(25u, 0x2bu);", src)
        # a planted wrong answer: the save slot overrun (80085414) reports what the slot holds, not 0
        src = translate(self.SAVE + [0x3B20002B, 0x93210008, 0x3800002A, 0x9001001C] + self.RESTORE)
        self.assertRegex(src, r"tc_xr\(25u, (0x2au|42u|rd32\(0x817fdffcu\))\);")


class HardwareReads(unittest.TestCase):
    def test_order(self):  # lis r4,0xcc00; lhz r3,0x5004(r4); lhz r0,0x5006(r4); rlwimi r0,r3,16,0,15; mr r3,r0
        src = translate([0x3C80CC00, 0xA0645004, 0xA0045006, 0x5060801E, 0x7C030378, BLR])
        self.assertLess(src.index("0xcc005004u"), src.index("0xcc005006u"))
        line = next(x for x in src.splitlines() if "0xcc005004u" in x)
        self.assertNotIn("0xcc005006u", line)  # the planted wrong answer: both reads in one expression

    def test_not_skipped(self):  # lis r4,0xcc00; lwz r31,0x6438(r4); srw r3,r31,r3
        src = translate([0x3C80CC00, 0x83E46438, 0x7FE31C30, BLR])
        line = next(x for x in src.splitlines() if "0xcc006438u" in x)
        self.assertNotIn("?", line)  # the planted wrong answer: the read in the ?:'s arm

    def test_unused_read_is_kept(self):  # lis r4,0xcc00; lhz r0,0x5006(r4); li r3,0 (80232b90: lhz r4,0(r29), unused)
        src = translate([0x3C80CC00, 0xA0045006, 0x38600000, BLR])
        self.assertIn("rd16(0xcc005006u)", src)  # the planted wrong answer: dead-code elimination drops the read

    def test_ram_reads_still_fold(self):  # the same shape on RAM: no device, the expression may hold both
        src = translate([0x3C808030, 0xA0645004, 0xA0045006, 0x5060801E, 0x7C030378, BLR])
        self.assertTrue(any("0x80305004u" in x and "0x80305006u" in x for x in src.splitlines()), src)


class LastResult(unittest.TestCase):
    def test_not_carried_past_a_call(self):  # bl; mr r25,r4; bl; add r3,r25,r4; blr
        src = translate([0x48000F01, 0x7C992378, 0x48000EF9, 0x7C792214, BLR])
        self.assertFalse([x for x in src.splitlines() if x.count("tc_lastr4()") > 1], src)  # the planted wrong answer
        self.assertLess(src.index("tc_lastr4()"), src.rindex("tc_call("), src)


class DefaultNaN(unittest.TestCase):
    def test_fdiv_gives_the_positive_default_nan(self):
        """fdivs f1,f1,f2 with 0/0 gives +QNaN on the 750CL, not x86's negative default NaN."""
        src = translate([0xEC211024, BLR])  # fdivs f1,f1,f2; blr
        self.assertIn("px_fdiv(", src)
        self.assertIn("if (r != r && a == a && b == b) return px_b2f(0x7ff8000000000000ull);", src)
        self.assertLess(src.index("static f64 px_b2f("), src.index("static f64 px_fdiv("))


class FnmNaN(unittest.TestCase):
    def test_fnmsub_keeps_a_nans_sign(self):
        """801231f8: fnmsub of a NaN keeps its sign on the 750CL. The planted wrong answer is the old -(...)."""
        src = translate([0xFC2118BC, BLR])  # fnmsub f1,f1,f2,f3; blr
        self.assertIn("px_fnm(", src)
        self.assertIn("return x != x ? x : -x;", src)
        ppc = (GC / "runtime" / "tc_ppc.c").read_text(encoding="utf-8")
        self.assertIn("f64 r = ppc_fmsubs(a, c, b); return r != r ? r : -r; }", ppc)
        self.assertIn("f64 r = ppc_fmadds(a, c, b); return r != r ? r : -r; }", ppc)


class InvalidOpNaN(unittest.TestCase):
    def test_arith_gives_the_positive_default_nan(self):
        """801231f8: inf * 0 is +QNaN on the 750CL. The planted wrong answer is the helpers without the NaN line."""
        src = translate([0xEC2100B2, 0xFC4118FA, 0xFC2110BC, BLR])  # fmuls f1,f1,f2; fmadd f2,f1,f3,f2; fnmsub; blr
        for h in ("px_fmul(", "px_fmadd(", "px_fnm(px_fmsub("):
            self.assertIn(h, src)
        self.assertIn("return r != r && a == a && b == b ? px_b2f(0x7ff8000000000000ull) : r;", src)
        ppc = (GC / "runtime" / "tc_ppc.c").read_text(encoding="utf-8")
        self.assertEqual(ppc.count("if (s != s && a == a && b == b && c == c) return bitsd(0x7ff8000000000000ull);"), 2)


class RecursiveF1(unittest.TestCase):
    def test_inner_frame_f1_is_the_last_result(self):
        """80211298: a body that calls itself (px_rec) and through a pointer, with no float code: in an inner frame f1
        holds the last mocked call's f1, which the pointer call passes on. The planted wrong answer is the old 0.0."""
        bl = lambda frm, to: 0x48000001 | ((to - frm) & 0x03FFFFFC)  # noqa: E731
        w = [0x9421FFF0, 0x7C0802A6, 0x90010014,  # stwu r1,-0x10(r1); mflr r0; stw r0,0x14(r1)
             0x2C030000, 0x41820018,  # cmpwi r3,0; beq end
             0x7C8903A6, 0x4E800421,  # mtctr r4; bctrl
             0x3860FFFF, 0x7C632214]  # li r3,-1; add r3,r3,r4
        w += [bl(BASE + 4 * len(w), BASE), 0x80010014, 0x7C0803A6, 0x38210010, BLR]  # bl self; epilogue
        sig = Signature("fn_80003100", "u32 fn_80003100(u32 param_1, u32 param_2)",
                        [("param_1", "u32", "r3", -1), ("param_2", "u32", "r4", -1)], "u32")
        src = translate(w, sig)
        self.assertIn("px_rec(", src)
        self.assertIn("a.f[0] = tc_lastf();", src, src)
        self.assertNotIn("a.f[0] = 0.0;", src)


class Lfs(unittest.TestCase):
    def test_text(self):  # lis r4,0x8000; lfs f1,0x100(r4); blr
        self.assertIn("px_lfs(rdf32(0x80000100u))", translate([0x3C808000, 0xC0240100, BLR]))
        self.assertIn("return px_lfs(rdf32(ea));", emit.HELPERS["px_dq"])

    @unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
    def test_runtime(self):
        drv = "\n".join(['#include <stdio.h>', '#include <string.h>', '#include "tc_rt.h"', emit.HELPERS["px_b2f"],
                         emit.HELPERS["px_lfs"], r"""
static f32 w2f(u32 w) { f32 f; memcpy(&f, &w, 4); return f; } /* volatile words: no compile-time folding */
int main(void)
{
    static volatile u32 w[] = {0xff81dfa3u, 0x7f800001u, 0x7fc00000u, 0xffffffffu, 0x3f800000u, 0x00000001u, 0x80000000u};
    for (int i = 0; i < 7; i++)
        printf("%08x %08x %08x\n", w[i], ppc_single_bits(px_lfs(w2f(w[i]))), ppc_single_bits((f64)w2f(w[i])));
    return 0;
}
"""])
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gclfs_", dir=work) as d:
            pathlib.Path(d, "drv.c").write_text(drv, encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            src = " ".join(f'"{RT / n}"' for n in ("tc_rt.c", "tc_ppc.c"))
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /O2 /W4 /WX /I"{RT}" drv.c {src}"',
                               cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            run = subprocess.run([str(pathlib.Path(d, "drv.exe"))], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        rows = [ln.split() for ln in run.stdout.splitlines()]
        for word, lfs, _ in rows:
            self.assertEqual(lfs, word)  # every single comes back bit for bit
        planted = {word: plain for word, _, plain in rows}
        self.assertEqual(planted["ff81dfa3"], "ffc1dfa3")  # the planted wrong answer: C's conversion quiets it
        self.assertEqual(planted["7f800001"], "7fc00001")


class FlagHop(unittest.TestCase):
    def test_carried_value_survives_a_second_hop(self):
        """800d8214, 801a2d60, 8025ce88: a hop out of a do-while(0) block whose landing is past a second block set a
        second value over the first, and the second's test reset it to 0, so the code after the second block ran the
        wrong way. The structured form must run as the gotos do (the SH2 reduction, checked by cfgcheck)."""
        sys.path[:0] = [str(TOOL / "targets" / "saturn_sh2" / "translator" / "tools")]
        import cfgcheck
        from test_own_translators import Small
        from ppctranslate.cfgstruct import structure_cfg
        g = Small.HOP_TWICE.splitlines()
        o = structure_cfg(g)
        self.assertIsNotNone(o)
        self.assertIsNone(cfgcheck.differs(g, o, 64))  # the planted wrong answer (the old lowering) differs here
        self.assertFalse([x for x in o if re.search(r"fl_ = \d+u; fl_ = \d+u;", x)])


class Mirror(unittest.TestCase):
    def test_reprepared_harness_replaces_the_copy(self):  # dp_judge judged a stale mirror copy of a re-prepared harness
        import importlib.util
        spec = importlib.util.spec_from_file_location("dp_judge", GC / "translator" / "tools" / "dp_judge.py")
        dj = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dj)
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcmir_", dir=work) as d:
            ws, out = pathlib.Path(d, "ws"), pathlib.Path(d, "out")
            (ws / "state").mkdir(parents=True)
            (ws / "harness" / "80001000").mkdir(parents=True)
            (ws / "game.dol").write_bytes(b"x")
            (ws / "project.json").write_text('{"binary": "game.dol"}', encoding="utf-8")
            exp = ws / "harness" / "80001000" / "expected.txt"
            exp.write_text("old", encoding="utf-8")
            dj.mirror(ws, out, ["80001000"])
            exp.write_text("new oracle", encoding="utf-8")
            dj.mirror(ws, out, ["80001000"])
            self.assertEqual((out / "harness" / "80001000" / "expected.txt").read_text(encoding="utf-8"), "new oracle")


class Wiring(unittest.TestCase):
    def test_text(self):
        rt = (RT / "tc_rt.c").read_text(encoding="utf-8")
        self.assertIn('fprintf(out, " %08x", g_xr[k - 14]);', rt)
        self.assertIn("memset(g_xr, 0, sizeof g_xr);", rt)
        self.assertIn("void tc_xr(u32 k, u32 v)", (GC / "runtime_rd" / "rd_mem.c").read_text(encoding="utf-8"))
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        self.assertIn('} else if (m.matches("psq_lu?x?")) {', fix)
        self.assertIn('} else if (kind.equals("psq_st")) {', fix)
        self.assertIn("if (bank.io.get((int) st[2])[2] != s) {", fix)
        # 8003a370: the fixup's own memory reads leave the load log the pointer repair reads; its writes add to
        # bank.stores, so psq_st walks a copy
        self.assertIn("while (reads.size() > n) reads.remove(reads.size() - 1);", fix)
        self.assertIn("for (long[] st : new java.util.ArrayList<>(bank.stores)) {", fix)
        bank = (GC / "ghidra_scripts" / "TcBank.java").read_text(encoding="utf-8")
        self.assertIn("if (ioStep >= 0 && io.get(ioStep)[0] == f) stored[2] = ioStep;", bank)
        self.assertTrue(re.search(r'"px_lfs"', (GC / "rtx.py").read_text(encoding="utf-8")))
        # 80211298: a read never hands the fault handler bytes that were written (it zeroes them in the page)
        self.assertIn("int got = keepWritten(f, size, res, stopOnUninitialized);", bank)
        self.assertIn("int k = Math.max(initRun(f + n, size - n), 1);", bank)
        self.assertIn(".getInitializedByteCount(skip, m);", bank)


if __name__ == "__main__":
    unittest.main()
