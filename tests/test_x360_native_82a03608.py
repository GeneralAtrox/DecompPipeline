"""Too Human's native fn_82a03608 (port/games/too_human/xb_native_th.c) writes the same bytes as the translated function, call for call.

The translated function is the build's own gen/fn_82a03608.c (translated C is not kept in git): XB_GEN_DIR names the folder that holds it,
and without it the comparison is skipped. Each case fills the 1 KB destination, the 1 KB source and the gain and step vectors, runs the
translated body on one copy of guest memory and the native route (XB_NATIVE_82A03608 on) on another, and compares the destination, the
stack word, the 17 vector registers the body leaves and the result. The cases: random normals over the whole exponent range, denormals,
signed zeros, infinities, quiet and signalling NaNs with payloads, sums that land exactly on and just beside a rounding tie (where one
rounding and two differ), results at the edge of the normal range (flushed or not), overflow, and source rows overlapping the destination.
NaN and overlap cases must go to the translated body; the rest must be native, so both routes are exercised.

Nearest existing: tests/test_vmx.py - the translator's per-helper SSE against its scalar helper, not a hand-written whole function."""
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import vcvars64  # noqa: E402

NATIVE = X360 / "port" / "games" / "too_human" / "xb_native_th.c"
GEN = pathlib.Path(os.environ["XB_GEN_DIR"]) / "fn_82a03608.c" if os.environ.get("XB_GEN_DIR") else None

HARNESS = r'''
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include "tc_rt.h"
u8 *tc_base;
__declspec(thread) u32 tc_sp;
__declspec(thread) u32 tc_vr[128][4];
u32 ppc_single_bits(f64 x) { union { f32 f; u32 u; } v; v.f = (f32)x; return v.u; }
u64 tc_timebase(void) { return 0; }
void port_log(const char *f, ...) { va_list a; va_start(a, f); printf("LOG "); vprintf(f, a); va_end(a); }
unsigned xb_frame_number(void) { return 0; }
u64 fn_82a03608(u64, u64, u64, u64);
u64 fn_82a03608_gen(u64, u64, u64, u64);
static long s_tx;
u64 fn_82a03608_tx(u64 a, u64 b, u64 c, u64 d) { s_tx++; return fn_82a03608_gen(a, b, c, d); }

#define MEM 0x100000u
static unsigned long long s_rng = 0x9e3779b97f4a7c15ull;
static u32 rnd(void) { s_rng ^= s_rng << 13; s_rng ^= s_rng >> 7; s_rng ^= s_rng << 17; return (u32)(s_rng >> 16); }
static u32 fbits(float f) { union { float f; u32 u; } v; v.f = f; return v.u; }
static float bitsf(u32 u) { union { float f; u32 u; } v; v.u = u; return v.f; }

/* one lane value of a kind: 0 normal anywhere, 1 denormal, 2 zero, 3 infinity, 4 NaN, 5 normal near 1, 6 normal near the bottom of the range */
static u32 value(int kind)
{
    u32 s = rnd() & 0x80000000u;
    switch (kind) {
    case 1: return s | (rnd() & 0x7fffffu | 1u);
    case 2: return s;
    case 3: return s | 0x7f800000u;
    case 4: return s | 0x7f800000u | (rnd() & 0x7fffffu | 1u);
    case 5: return s | ((127u - 4u + rnd() % 9u) << 23) | (rnd() & 0x7fffffu);
    case 6: return s | ((1u + rnd() % 6u) << 23) | (rnd() & 0x7fffffu);
    default: return s | ((1u + rnd() % 253u) << 23) | (rnd() & 0x7fffffu);
    }
}

static void put(u8 *m, u32 at, u32 v) { *(u32 *)(m + at) = _byteswap_ulong(v); }

/* fill guest memory for case c; returns the r3 / r4 to use */
static void fill(u8 *m, int c, u32 *r3, u32 *r4)
{
    u32 a = 0x10000u, b = 0x20000u, i, mode = (u32)c % 8u;
    for (i = 0; i < 256; i++) {
        int ka = mode == 0 ? 0 : mode == 1 ? (rnd() % 8 ? 5 : 1) : mode == 2 ? (int)(rnd() % 7) : mode == 3 ? 6 : 5;
        int kb = mode == 2 ? (int)(rnd() % 7) : mode == 3 ? 6 : 5;
        u32 av = value(ka), bv = value(kb);
        if (mode == 2 && rnd() % 4) { if (((av >> 23) & 255) == 255) av &= ~0x7f800000u; if (((bv >> 23) & 255) == 255) bv &= ~0x7f800000u; }   /* mostly finite */
        if (mode == 4) {   /* ties: a + b with b half an ulp of a (gain 1, step 0), exactly or one bit to either side */
            u32 e = (av >> 23) & 255;
            av = (av & 0x80000000u) | (e << 23) | (rnd() & 0x7fffffu);
            bv = ((av ^ (rnd() % 2 ? 0x80000000u : 0)) & 0x80000000u) | ((e - 24u) << 23) | (rnd() % 3 == 0 ? 0 : rnd() % 2 ? 1u : 0x7fffffu);
        }
        if (mode == 5) bv = (rnd() & 0x80000000u) | ((200u + rnd() % 54u) << 23) | (rnd() & 0x7fffffu), av = value(0);   /* overflow */
        put(m, a + 4 * i, av), put(m, b + 4 * i, bv);
    }
    for (i = 0; i < 4; i++) {
        u32 g = mode == 4 ? fbits(1.0f) : mode == 3 ? fbits(bitsf(value(5)) * (1.0f / 1024.0f)) : value(5);
        u32 s = mode == 4 ? 0u : mode == 6 ? value((int)(rnd() % 7)) : fbits(bitsf(value(5)) * (1.0f / 64.0f));
        if (mode == 7 && i == 1) g = value(1);   /* a denormal gain */
        put(m, 0x30000u, 0), put(m, 0x30000u + 4 * i, g), put(m, 0x30010u + 4 * i, s);
    }
    *r3 = a | (rnd() & 15u), *r4 = b | (rnd() & 15u);
    if (c % 97 == 0) *r4 = a + 0x200u;   /* the source inside the destination */
}

int main(void)
{
    static u8 m0[MEM];
    u8 *m1 = (u8 *)malloc(MEM), *m2 = (u8 *)malloc(MEM);
    int c, n = 40000, differ = 0, said = 0;
    long native = 0, back = 0;
    for (c = 0; c < n; c++) {
        u32 r3, r4, k;
        u64 x1, x2;
        u32 vr1[17][4], vr2[17][4];
        static const int set[17] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 29, 30, 31};
        long before;
        memset(m0 + 0x10000u, 0, 0x30000u);
        fill(m0, c, &r3, &r4);
        memcpy(m1, m0, MEM), memcpy(m2, m0, MEM);
        tc_sp = 0x80000u;
        memset(tc_vr, 0xcd, sizeof tc_vr), tc_base = m1;
        x1 = fn_82a03608_gen(r3, r4, 0x30000u, 0x30010u);
        for (k = 0; k < 17; k++) memcpy(vr1[k], tc_vr[set[k]], 16);
        memset(tc_vr, 0xcd, sizeof tc_vr), tc_base = m2;
        before = s_tx;
        x2 = fn_82a03608(r3, r4, 0x30000u, 0x30010u);
        if (s_tx != before) back++; else native++;
        for (k = 0; k < 17; k++) memcpy(vr2[k], tc_vr[set[k]], 16);
        if (x1 != x2 || memcmp(m1, m2, MEM) || memcmp(vr1, vr2, sizeof vr1)) {
            differ++;
            if (said++ < 5) {
                u32 j;
                for (j = 0; j < MEM && m1[j] == m2[j]; j++) {}
                printf("DIFF case %d (kind %d) r3 %08x r4 %08x: first byte %05x translated %02x native %02x; registers %s\n", c, c % 8, r3, r4, j, j < MEM ? m1[j] : 0, j < MEM ? m2[j] : 0,
                       memcmp(vr1, vr2, sizeof vr1) ? "differ" : "same");
            }
        }
    }
    printf("RESULT cases %d differ %d native %ld translated %ld\n", n, differ, native, back);
    return 0;
}
'''


def compile_and_run(d, env):
    vc = str(vcvars64()).replace("/", "\\")
    inc = f'/I"{X360 / "runtime"}"'
    steps = [f"cl /nologo /c /O1 /fp:strict /W3 /DTC_PORT {inc} /Dfn_82a03608=fn_82a03608_gen fn_gen.c",
             f"cl /nologo /c /O1 /fp:strict /W4 /WX /DTC_PORT {inc} xb_native_th.c",
             f"cl /nologo /O1 /fp:strict /W3 /DTC_PORT {inc} /Fet.exe h.c fn_gen.obj xb_native_th.obj"]
    for s in steps:
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {s}"', cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
        if r.returncode:
            return None, s + "\n" + r.stdout[-3000:]
    r = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=300, env=env)
    return r.returncode, r.stdout


def test_wired():
    """the native function is listed, built from the game folder, and its translated twin renamed (any machine)"""
    import json
    assert "82a03608" in json.loads((NATIVE.parent / "native.json").read_text())
    g = (X360 / "port" / "tools" / "gen_xbox.py").read_text(encoding="utf-8")
    assert 'self.game.glob("*.c")' in g and '_tx"' in g
    s = NATIVE.read_text(encoding="utf-8")
    assert "u64 fn_82a03608(u64 arg_r3" in s and "fn_82a03608_tx(arg_r3" in s and "XB_NATIVE_82A03608" in s


@unittest.skipUnless(os.name == "nt" and vcvars64() and GEN and GEN.exists(), "needs Visual Studio and XB_GEN_DIR with the build's gen/fn_82a03608.c")
class Native(unittest.TestCase):
    def test_same_bytes_as_translated(self):
        d = tmpdir("x360n03608_")
        try:
            shutil.copy2(GEN, d / "fn_gen.c")
            shutil.copy2(NATIVE, d / "xb_native_th.c")
            (d / "h.c").write_text(HARNESS, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith("XB_NATIVE")}
            env["XB_NATIVE_82A03608"] = "1"
            rc, out = compile_and_run(d, env)
            self.assertEqual(rc, 0, out[-3000:])
            res = [ln for ln in out.splitlines() if ln.startswith("RESULT")]
            print("\n".join([ln for ln in out.splitlines() if ln.startswith(("DIFF", "LOG"))][:8] + res))
            self.assertTrue(res, out[-2000:])
            f = res[0].split()
            cases, differ, native, back = int(f[2]), int(f[4]), int(f[6]), int(f[8])
            self.assertEqual(differ, 0, out[-3000:])
            self.assertGreater(native, cases // 2)
            self.assertGreater(back, 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
