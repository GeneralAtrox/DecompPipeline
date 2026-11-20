"""tx_rt.c's tc_call into a translated callee's glue: the callee's frame holds the call's 8 stack words, and a frame
past them the caller's words (split from tests/test_tx_port.py at its 400-line cap). Needs Visual Studio's 32-bit tools.
Run: python -W error tests/test_tx_stack.py.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402


STACK_DRIVER_C = r"""/* tx_rt.c's tc_call into a translated callee's glue: the callee's frame holds the call's 8 stack words. */
#include <stdarg.h>
#include <stdio.h>
#include <windows.h>
#include "port.h"
#include "port_dispatch.h"
#include "overlay.h"
#include "tx_rt.h"
#include "work_count.h"

u32 rd32(u32 addr);
void wr32(u32 addr, u32 value);
u32 tc_call(u32 target, const PortTcArgs *args);

/* the inbound glue of a judged body that takes 2 register arguments and reads its caller's stack word 1 (a va_list
 * pointer its prototype does not name), as tx_port.py writes it */
static u32 callee(u32 a0, u32 a1)
{
    u32 sp = port_tx_enter(16u, 32u), r = rd32(sp + 4u);
    (void)a0, (void)a1;
    port_tx_leave(sp, 32u);
    return r;
}
static void g_callee(const PortTcArgs *a, PortTcRet *o) { o->r0 = callee(a->r[0], a->r[1]); }
/* a judged body whose frame reaches past the 8 words, reading its caller's word at stack+32 through its entry r15 */
static void g_past(const PortTcArgs *a, PortTcRet *o)
{
    u32 sp = port_tx_enter(16u, 44u);
    (void)a;
    o->r0 = rd32(sp + 32u);
    port_tx_leave(sp, 44u);
}
static const PortTcRow rows[] = {{0x8c020000u, g_callee}, {0x8c020010u, g_past}};
static const int nrows = 2;
const PortTcChunk port_tx_chunks[] = {{rows, &nrows}};
const int port_tx_nchunks = 1;
const PortDispatchChunk port_dispatch_chunks[] = {{NULL, &nrows}};
const int port_dispatch_nchunks = 0;

const PortDispatch *overlay_find(u32 addr) { (void)addr; return NULL; }
const PortDispatch *overlay_glue(u32 addr) { (void)addr; return NULL; }
const PortDispatch *overlay_find_glue(u32 addr, const PortDispatch **glue) { (void)addr; *glue = NULL; return NULL; }
u32 overlay_delta(void) { return 0; }
u32 tx_ovl_delta, port_sh4_fr[16], port_sh4_ro[4];
int g_prof_on, g_work_on; u64 g_work_now[WC_N]; volatile int port_tx_spin_on; /* off: profiler, counters, spin */
void prof_call(u32 target) { (void)target; }  u32 port_tx_spin_read(u32 addr, u32 v) { (void)addr; return v; }
const PortDispatch *port_dispatch_find(u32 addr) { (void)addr; return NULL; }
u32 guest_fold(u32 a) { return (a >> 24) == 0x0cu ? a | 0x80000000u : a; }
int guest_is_ram(u32 a) { return guest_fold(a) - 0x8c000000u < 0x01000000u; }
u32 port_vblank_count(void) { return 0; }
void port_wait_vblank(void) {}
void sh4_set_r(int index, u32 value) { (void)index, (void)value; }
void sh4_set_fr(int index, f32 value) { (void)index, (void)value; }
void port_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); }
void port_exit(const char *why, int code) { printf("EXIT: %s\n", why); ExitProcess((UINT)code); }

int main(void)
{
    PortTcArgs a;
    if (!VirtualAlloc((void *)(UINT_PTR)0x8c000000u, 0x01000000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) return 3;
    memset(&a, 0, sizeof a);
    a.r[0] = 1;
    a.s[0] = 0x8c5f99b8u; /* a format */
    a.s[1] = 0x8c00f3f0u; /* the va_list pointer */
    printf("tc_call %08x\n", tc_call(0x0c020000u, &a));
    wr32(0x8c00f400u - 32u + 4u, 0x5eedu); /* a direct call (readable C): the frame keeps what it held */
    printf("direct %08x\n", callee(1, 0));
    {
        u32 r15 = port_tx_enter(64u, 32u) - 64u; /* a translated caller's frame, its r15 at the floor */
        memset(&a, 0, sizeof a);
        for (u32 i = 0; i < 8u; i++) {
            a.s[i] = 0xa000u + i;
            wr32(r15 + 4u * i, a.s[i]);
        }
        wr32(r15 + 32u, 0xb1u);
        printf("past %08x\n", tc_call(0x0c020010u, &a));
    }
    return 0;
}
"""


class StackWords(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "MSVC")
    def test_stack_words_reach_the_frame(self):
        """A callee reached by tc_call finds the call's stack words at its sp, also one its prototype does not name
        (the va_list pointer of a printf-like function, which the judged body reads at sp+4); a direct call from
        readable C leaves the frame as it was; a frame past the 8 words holds the caller's words there too."""
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "driver.c").write_text(STACK_DRIVER_C, encoding="utf-8")
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            rt = ROOT / "port" / "runtime"
            inc = [d, rt, rt / "tx", ROOT / "port" / "base", ROOT / "targets" / "dreamcast_sh4" / "runtime_rd"]
            cl = ("cl /nologo /TC /std:c11 /W4 /WX /O1 " + " ".join(f'/I"{p}"' for p in inc)
                  + f' driver.c "{rt / "tx" / "tx_rt.c"}" /Fe:drv.exe /link /LARGEADDRESSAWARE /DYNAMICBASE:NO '
                  "/BASE:0x5a000000")
            vc = str(game_config.vcvars32() or self.skipTest("no 32-bit Visual Studio (game_config.vcvars32)")).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            out = subprocess.run([str(d / "drv.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.split("\n")[:3], ["tc_call 8c00f3f0", "direct 00005eed", "past 000000b1"],
                             out.stdout)


if __name__ == "__main__":
    unittest.main()
