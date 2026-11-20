"""The game's setjmp and longjmp in the port form (port/games/<game>/jumps.json, docs/x360/EXCEPTIONS_AND_LONGJMP.md). Each blr
became a C return, so a translated longjmp went back to its own caller and nothing reached setjmp's frame again (Too Human's Lua
errors and its shader compiler's fatal errors). With the table, a call of setjmp is a host setjmp in the caller's own C frame on a
runtime slot for the guest jmp_buf (r3), and a call of longjmp is xb_longjmp with the buffer and the value (r3, r4). The locals of
such a function are volatile (C11 7.13.2.1), and the TcArgs fields written before the setjmp are written again after it. Without
the table the calls stay ordinary tc_calls, and the judge's form never has one. Where MSVC is installed (vswhere), the function's
C is built at the port's flags with the runtime's xb_jmp.h on stubs and run: a longjmp from two frames below the callee and one
from the function itself both land, return the function's own r31, call the callee once, and put back the call depth, tc_sp
and the vector file.
usage: python tests/test_x360_setjmp.py
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path[:0] = [str(X360 / "translator"), str(X360 / "port" / "tools"), str(pathlib.Path(__file__).resolve().parent)]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone: gen_xbox needs a project)
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, translate_function  # noqa: E402

BASE = 0x82010000
SETJMP, LONGJMP, WORK = BASE + 0x100, BASE + 0x200, BASE + 0x300
WORDS = [0x7C0802A6,   # mflr r0
         0x9001FFF8,   # stw r0,-8(r1)
         0x9421FF00,   # stwu r1,-0x100(r1)
         0x7C7F1B78,   # mr r31,r3
         0x38610050,   # addi r3,r1,0x50   (the jmp_buf, in this frame)
         0x480000ED,   # bl SETJMP
         0x2C030000,   # cmpwi r3,0
         0x40820018,   # bne -> mr r3,r31 (landed: return)
         0x7FE3FB78,   # mr r3,r31
         0x480002DD,   # bl WORK
         0x38610050,   # addi r3,r1,0x50
         0x38800007,   # li r4,7
         0x480001D1,   # bl LONGJMP
         0x7FE3FB78,   # mr r3,r31
         0x38210100,   # addi r1,r1,0x100
         0x8001FFF8,   # lwz r0,-8(r1)
         0x7C0803A6,   # mtlr r0
         0x4E800020]   # blr
ONLY_LONGJMP = [0x7C0802A6,   # mflr r0
                0x9001FFF8,   # stw r0,-8(r1)
                0x9421FF00,   # stwu r1,-0x100(r1)
                0x38610050,   # addi r3,r1,0x50
                0x38800007,   # li r4,7
                0x480001ED,   # bl LONGJMP (0x14 -> 0x200)
                0x38210100,   # addi r1,r1,0x100
                0x8001FFF8,   # lwz r0,-8(r1)
                0x7C0803A6,   # mtlr r0
                0x4E800020]   # blr

DRIVER = r"""#include <windows.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tc_rt.h"
u8 *tc_base;
__declspec(thread) u32 tc_sp, tc_r12, tc_r13;
__declspec(thread) const u64 *tc_nv;
__declspec(thread) u32 tc_vr[128][4];
__declspec(thread) u64 tc_gr[32];
__declspec(thread) f64 tc_fr[32];
__declspec(thread) u64 tc_resv_v;
__declspec(thread) u32 tc_resv_s;
static __declspec(thread) unsigned s_depth;
void xb_stop(const char *kind, const char *detail, u32 value) { printf("STOP %s %s %08x\n", kind, detail, value); exit(3); }
static __declspec(thread) u32 s_stack[256];
void port_log(const char *fmt, ...) { (void)fmt; }   /* the runtime logs a longjmp; the stub drops the line */
u32 xb_caller(unsigned up) { (void)up; return 0; }
unsigned xb_frame_number(void) { return 0; }
#include "xb_jmp.h"
u64 fn_82010000(u64 arg_r3);
static int mode, work;
static void deeper(u32 buf, u32 sp)   /* a frame below the callee's, as a translated function calling longjmp */
{
    TcArgs b;
    memset(&b, 0, sizeof b);
    b.r[0] = buf, b.r[1] = 9, b.sp[0] = sp - 0x80;
    xb_longjmp(&b);
}
u64 tc_call(u32 t, const TcArgs *a)
{
    u32 saved = tc_sp;
    s_depth++, tc_sp = a->sp[0];
    if (t == 0x82010300u) {
        work++;
        tc_vr[70][0] = 0xdeadu;
        if (mode == 1) { s_depth++; deeper(a->sp[0] + 0x50u, a->sp[0]); }
    } else {
        printf("unexpected call %08x\n", t);
        exit(4);
    }
    s_depth--, tc_sp = saved;
    return 0;
}
f64 tc_callf(u32 t, const TcArgs *a) { (void)tc_call(t, a); return 0.0; }
u64 tc_lastr(void) { return 0; }
f64 tc_lastf(void) { return 0.0; }
int main(void)
{
    tc_base = (u8 *)calloc(1, 0x200000);
    for (mode = 0; mode < 2; mode++) {
        u64 r;
        work = 0, tc_sp = 0x100000u, s_depth = 5, tc_vr[70][0] = 0x1111u;
        r = fn_82010000(0x1234);
        printf("mode %d r %llx work %d depth %u sp %x vr %x\n", mode, r, work, s_depth, tc_sp, tc_vr[70][0]);
    }
    return 0;
}
"""


def translate(jumps, words=None):
    words = words or WORDS
    code = bytearray(0x400)
    for i, w in enumerate(words):
        code[i * 4:i * 4 + 4] = w.to_bytes(4, "big")
    for at in (0x100, 0x200, 0x300):
        code[at:at + 4] = (0x4E800020).to_bytes(4, "big")
    facts = Facts(port=True)
    if jumps is not None:
        facts.jumps = jumps
    sig = Signature("fn_82010000", "u64 fn_82010000(u64 arg_r3)", [("arg_r3", "u64", "r3", -1)], "u64",
                    returns={SETJMP: "r", LONGJMP: "v", WORK: "v"})
    src, _ = translate_function(XexImage(bytes(code), BASE), BASE, sig, facts, [(BASE, BASE + 4 * len(words))])
    return src


def vcvars64():
    vw = pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vw.exists():
        return None
    r = subprocess.run([str(vw), "-latest", "-products", "*", "-property", "installationPath"], capture_output=True, text=True, timeout=60)
    p = pathlib.Path(r.stdout.strip() or ".") / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    return p if r.stdout.strip() and p.exists() else None


class SetjmpLongjmp(unittest.TestCase):
    def test_the_table_makes_a_host_setjmp_and_xb_longjmp(self):
        src = translate({SETJMP: "setjmp", LONGJMP: "longjmp"})
        self.assertIn("if (setjmp(*xb_setjmp_slot(&a))) r3 = xb_setjmp_landed(); else r3 = 0;", src, src)
        self.assertIn("xb_longjmp(&a);", src, src)
        self.assertNotIn(f"tc_call(0x{SETJMP:08x}u", src, src)
        self.assertNotIn(f"tc_call(0x{LONGJMP:08x}u", src, src)
        self.assertIn("#include <setjmp.h>", src)
        self.assertIn("jmp_buf *xb_setjmp_slot(const TcArgs *a);", src)
        # the guest jmp_buf (r1 + 0x50) reaches the slot; the buffer and the value reach xb_longjmp, written again after the setjmp
        before_sj = src[:src.index("setjmp(*xb_setjmp_slot")]
        self.assertTrue(re.search(r"a\.r\[0\] = [^;]*0x50", before_sj), src)
        before_lj = src[src.index("setjmp(*xb_setjmp_slot"):src.index("xb_longjmp(&a)")]
        self.assertTrue(re.search(r"a\.r\[1\] = 7", before_lj), src)
        self.assertTrue(re.search(r"a\.r\[0\] = ", before_lj), src)
        decl = [ln for ln in src.splitlines() if re.match(r"    (volatile )?(u64|u32|f64|s64|s32) \w+ = ", ln) and "= tc_" not in ln]
        self.assertTrue(decl and all(ln.startswith("    volatile ") for ln in decl), "\n".join(decl))

    def test_a_function_whose_only_call_is_longjmp_declares_its_args(self):
        # Too Human's 82b2af38: no other call, so nothing else declared TcArgs a and the build stopped on 'a': undeclared
        src = translate({SETJMP: "setjmp", LONGJMP: "longjmp"}, ONLY_LONGJMP)
        self.assertIn("xb_longjmp(&a);", src, src)
        self.assertNotIn("tc_call", src, src)
        self.assertIn("    TcArgs a = {0};", src, src)
        self.assertLess(src.index("TcArgs a = {0};"), src.index("a.r[0] = "), src)

    def test_without_the_table_they_stay_calls(self):
        src = translate(None)
        self.assertIn(f"tc_call(0x{SETJMP:08x}u", src)
        self.assertIn(f"tc_call(0x{LONGJMP:08x}u", src)
        self.assertNotIn("xb_setjmp_slot", src)
        self.assertNotIn("xb_longjmp", src)
        self.assertNotIn("volatile", src)

    @unittest.skipUnless(os.name == "nt" and vcvars64(), "no MSVC (vswhere) on this machine")
    def test_built_and_run_the_longjmp_lands(self):
        src = translate({SETJMP: "setjmp", LONGJMP: "longjmp"})
        rt = [X360 / "runtime", X360 / "port" / "runtime"]
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "fn.c").write_text(src, encoding="utf-8")
            (d / "drv.c").write_text(DRIVER, encoding="utf-8")
            cl = "cl /nologo /O1 /fp:strict /W3 /DTC_PORT " + " ".join(f'/I"{p}"' for p in rt) + " fn.c drv.c /Fe:drv.exe"
            r = subprocess.run(f'cmd /d /s /c ""{vcvars64()}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            out = subprocess.run([str(d / "drv.exe")], capture_output=True, text=True, timeout=60).stdout
        self.assertEqual(out.splitlines()[:2], ["mode 0 r 1234 work 1 depth 5 sp 100000 vr 1111",
                                               "mode 1 r 1234 work 1 depth 5 sp 100000 vr 1111"], out + "\n" + src)

    def test_too_human_table(self):
        import gen_xbox
        self.assertEqual(gen_xbox.jumps(X360 / "port" / "games" / "too_human"), {0x82ec5460: "setjmp", 0x82a566a0: "longjmp"})
        self.assertEqual(gen_xbox.jumps(X360 / "port" / "games" / "mega_man_10"), {})


if __name__ == "__main__":
    unittest.main()
