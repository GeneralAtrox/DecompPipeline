"""Double precision (FPSCR.PR=1) in the SH-4 translator (decision 2): Spawn's library sets PR with `sts fpscr; or
0x80000; lds fpscr`, runs double code, and clears it (`and 0xffe7ffff`) before its calls. Pass and fail controls:
1. PR followed: the double forms become the runtime's pair helpers on fr n : fr n+1 (sh4_dadd, sh4_dfloat,
   sh4_dftrc), the FPSCR writes stay sh4_lds_fpscr, and the C holds no double (lint R9);
2. refused: an instruction undefined with PR=1 (fmac), a double operand on an odd register, and the same code where
   FPSCR is not state (meta fpscr_state off: nothing writes PR at run time); an lds loading a PR not followed (TC
   8c0611d8) leaves PR unknown: translated when nothing after it depends on PR, refused where something does;
3. the pair helpers themselves (runtime/sh4_double.h, the one definition every runtime includes), compiled as the
   judge builds them (x64) and as the port kit does (32-bit, /arch:SSE2 /O1 /fp:strict): the pairs move as bits (a
   NaN or signalling-NaN low word intact), ftrc saturates (NaN, +-2^31), PR=0 runs the single instruction on fr n and
   leaves fr n+1 alone (fcnvsd and fcnvds: SH4D_UNDEFINED), fcnvsd and fcnvds convert a denormal exactly and give MXCSR
   back as it was, and the double operations round by MXCSR.RC (FPSCR.RM) and flush by DAZ|FTZ (FPSCR.DN).
Synthetic machine code and values. Run: python -W error tests/test_dc_double.py.
"""
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
from test_fmac import vcvars  # noqa: E402
from sh4translate.translate import Image, Signature, TranslateError, translate_function  # noqa: E402

BASE = 0x8C010000
FADD_DR2_DR4 = 0xF420


def code(op8=FADD_DR2_DR4):
    words = [0x036A,    # 0  sts fpscr,r3
             0xD107,    # 2  mov.l @(0x20),r1 = 0x00080000 (PR)
             0x231B,    # 4  or r1,r3
             0x436A,    # 6  lds r3,fpscr          PR=1
             op8,       # 8  fadd dr2,dr4
             0x445A,    # a  lds r4,fpul
             0xF62D,    # c  float fpul,dr6
             0xF43D,    # e  ftrc dr4,fpul
             0xD204,    # 10 mov.l @(0x24),r2 = 0xffe7ffff
             0x036A,    # 12 sts fpscr,r3
             0x2329,    # 14 and r2,r3
             0x436A,    # 16 lds r3,fpscr          PR=0
             0x000B,    # 18 rts
             0x005A,    # 1a sts fpul,r0
             0x0009, 0x0009]
    return struct.pack(f"<{len(words)}H", *words) + struct.pack("<II", 0x00080000, 0xFFE7FFFF)


def sig(fpscr_state=True):
    return Signature("fn_8c010000", "u32 fn_8c010000(u32 a0)", [("a0", "u32", "r4")], "u32", fpu_state=True,
                     fpscr_state=fpscr_state)


def tx(op8=FADD_DR2_DR4, fpscr_state=True):
    return translate_function(Image(code(op8), BASE), BASE, sig(fpscr_state))


class Double(unittest.TestCase):
    def test_followed(self):
        c = tx()
        c = c if isinstance(c, str) else c[0]
        for want in ("sh4_dadd(&fr4, &fr5, fr2, fr3);", "sh4_dfloat(&fr6, &fr7, fpul);",
                     "fpul = (u32)sh4_dftrc(fr4, fr5);", "sh4_lds_fpscr(r3);", "fadd dr2,dr4"):
            self.assertIn(want, c)
        self.assertNotRegex(c, r"\bdouble\b")

    def test_refused(self):
        with self.assertRaisesRegex(TranslateError, "PR=1"):
            tx(0xF42E)          # fmac fr0,fr2,fr4: undefined with PR=1
        with self.assertRaisesRegex(TranslateError, "odd register"):
            tx(0xF430)          # fadd dr3?: fr3 is no DR
        with self.assertRaises(TranslateError):
            tx(fpscr_state=False)

    def test_loaded_pr_unknown(self):
        # TC 8c0611d8: FPSCR or-ed with a shifted constant (the exception enables), loaded in rts's delay slot. The PR
        # it loads is not followed, so PR is unknown after it: nothing there depends on PR, so it translates; an
        # instruction that does is refused.
        head = [0x016A, 0xE00E, 0x4018, 0x210B]     # sts fpscr,r1; mov #0xe,r0; shll8 r0; or r0,r1
        void = Signature("fn_8c010000", "void fn_8c010000(void)", [], "void", fpu_state=True, fpscr_state=True)
        for tail, ok in (([0x000B, 0x416A], True), ([0x416A, 0xF420, 0x000B, 0x0009], False)):
            img = Image(struct.pack(f"<{len(head + tail)}H", *(head + tail)), BASE)
            if ok:
                c = translate_function(img, BASE, void)
                self.assertIn("sh4_lds_fpscr(r1);", c if isinstance(c, str) else c[0])
            else:
                with self.assertRaisesRegex(TranslateError, "FPSCR.PR not known"):
                    translate_function(img, BASE, void)


HELPERS = r"""#include <stdio.h>
#include <emmintrin.h>
#include "tc_rt.h"
static int g_pr = 1, g_undef;
#define SH4D_PR() g_pr
#define SH4D_UNDEFINED(s) ((void)(s), g_undef++)
s32 sh4_ftrc(f32 x) { return (s32)x + 1000; }          /* stubs: show that PR=0 runs the single helper */
f32 sh4_fsqrt_rm(f32 x) { return x * 0.5f; }
#include "sh4_double.h"

static f32 W(u32 u) { Sh4dWord w; w.u = u; return w.f; }
static u32 B(f32 f) { Sh4dWord w; w.f = f; return w.u; }
static f32 h, l;
static void put(u32 a, u32 b) { Sh4dWord w; w.u = a; h = w.f; w.u = b; l = w.f; } /* no f32 return (x87) */
static void show(const char *s) { printf("%s %08x %08x\n", s, B(h), B(l)); }
static void csr(unsigned int bits) { _mm_setcsr((_mm_getcsr() & ~0xe040u) | bits); }

int main(void)
{
    unsigned int c0;
    u32 v;
    put(0x3ff00000u, 0x00000000u); sh4_dadd(&h, &l, W(0x3cb00000u), W(0)); show("add");          /* 1 + 2^-52 */
    put(0x3ff00000u, 0x7f800001u); sh4_dmul(&h, &l, W(0x3ff00000u), W(0)); show("snan-low");     /* bits kept */
    put(0x40100000u, 0); sh4_dsqrt(&h, &l); show("sqrt");
    put(0xc0000000u, 0); sh4_ddiv(&h, &l, W(0x40000000u), W(0)); show("div");
    put(0x40000000u, 0); sh4_dsub(&h, &l, W(0x3ff00000u), W(0)); show("sub");
    sh4_dfloat(&h, &l, 0xffffffffu); show("float");
    printf("ftrc %08x %08x %08x %08x %08x %08x\n", (u32)sh4_dftrc(W(0x7ff80000u), 0), (u32)sh4_dftrc(W(0x41e00000u), 0),
           (u32)sh4_dftrc(W(0x41dfffffu), W(0xffe00000u)), (u32)sh4_dftrc(W(0xc1e00000u), W(0x00200000u)),
           (u32)sh4_dftrc(W(0xc1e00000u), 0), (u32)sh4_dftrc(W(0xc00e0000u), 0));
    printf("cmp %u %u %u %u\n", sh4_dcmpeq(W(0x7ff80000u), 0, W(0x7ff80000u), 0),
           sh4_dcmpgt(W(0x40000000u), 0, W(0x3ff00000u), 0), sh4_dcmpgt(W(0x3ff00000u), 0, W(0x40000000u), 0),
           sh4_dcmpeq(W(0x3ff00000u), W(1), W(0x3ff00000u), 0));
    csr(0x8040u); c0 = _mm_getcsr();                                                           /* DN = 1 */
    sh4_fcnvsd(&h, &l, 0x00000001u); show("cnvsd-denormal");
    printf("cnvds-denormal %08x csr %d\n", sh4_fcnvds(W(0x36a00000u), 0), _mm_getcsr() == c0);
    sh4_fcnvsd(&h, &l, 0xffffffffu); show("cnvsd-nan");
    put(0, 1); sh4_dadd(&h, &l, W(0), W(0)); show("dn-add");
    printf("dn-cmp %u\n", sh4_dcmpeq(W(0), W(1), W(0), 0));
    csr(0); put(0, 1); sh4_dadd(&h, &l, W(0), W(0)); show("add-denormal");
    put(0x3ff00000u, 0); sh4_dadd(&h, &l, W(0x3ca80000u), 0); show("rn-add");                  /* + 0.75 ulp */
    printf("rn-cnvds %08x\n", sh4_fcnvds(W(0x3ff00000u), W(0x30000000u)));                    /* a tie */
    csr(0x6000u);                                                                              /* RM = 1 */
    put(0x3ff00000u, 0); sh4_dadd(&h, &l, W(0x3ca80000u), 0); show("rz-add");
    printf("rz-cnvds %08x\n", sh4_fcnvds(W(0x3ff00000u), W(0x30000000u)));
    csr(0); g_pr = 0;                                                                          /* PR = 0 */
    put(0x3f800000u, 0x12345678u); sh4_dadd(&h, &l, W(0x40000000u), W(0x9abcdef0u)); show("pr0-add");
    put(0x41800000u, 0x12345678u); sh4_dsqrt(&h, &l); show("pr0-sqrt");
    sh4_dfloat(&h, &l, 0xfffffffeu); show("pr0-float");
    printf("pr0-ftrc %d cmp %u\n", sh4_dftrc(W(0x40400000u), W(0x7f800001u)), sh4_dcmpgt(W(0x40000000u), 0, W(0x3f800000u), 0));
    put(0x11111111u, 0x22222222u); sh4_fcnvsd(&h, &l, 0x3f800000u); v = sh4_fcnvds(h, l);
    show("pr0-cnv"); printf("pr0-undefined %d %08x\n", g_undef, v);
    return 0;
}
"""

WANT = ["add 3ff00000 00000001", "snan-low 3ff00000 7f800001", "sqrt 40000000 00000000", "div bff00000 00000000",
        "sub 3ff00000 00000000", "float bff00000 00000000",
        "ftrc 80000000 7fffffff 7fffffff 80000000 80000000 fffffffd", "cmp 0 1 0 0",
        "cnvsd-denormal 36a00000 00000000", "cnvds-denormal 00000001 csr 1", "cnvsd-nan ffffffff e0000000",
        "dn-add 00000000 00000000", "dn-cmp 1", "add-denormal 00000000 00000001",
        "rn-add 3ff00000 00000001", "rn-cnvds 3f800002", "rz-add 3ff00000 00000000", "rz-cnvds 3f800001",
        "pr0-add 40400000 12345678", "pr0-sqrt 41000000 12345678", "pr0-float c0000000 12345678",
        "pr0-ftrc 1003 cmp 1", "pr0-cnv 11111111 22222222", "pr0-undefined 2 00000000"]


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32() and vcvars(64), "no Visual Studio tools on this machine")
class Helpers(unittest.TestCase):
    def test_x64_and_port(self):
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        inc = str(ROOT / "targets" / "dreamcast_sh4" / "runtime")
        try:
            (d / "h.c").write_text(HELPERS, encoding="utf-8")
            for bits, flags in ((64, "/O2"), (32, "/arch:SSE2 /O1 /fp:strict")):
                vc = str(vcvars(bits)).replace("/", "\\")
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX {flags} /I"{inc}" '
                                   f'/Feh{bits}.exe h.c"', cwd=d, capture_output=True, text=True, errors="replace",
                                   timeout=300)
                self.assertEqual(r.returncode, 0, f"{bits}: " + r.stdout[-2000:])
                out = subprocess.run([str(d / f"h{bits}.exe")], capture_output=True, text=True, timeout=60)
                self.assertEqual(out.stdout.splitlines(), WANT, bits)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
