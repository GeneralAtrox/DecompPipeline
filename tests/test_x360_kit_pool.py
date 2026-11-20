"""The Xbox 360 kit's own Direct3D memory is given back and used again (runtime/xb_d3d_cb.h, xb_d3d_res.h): xb_alloc never frees, so what the kit
takes for command buffers and resources must come back to the kit's pools.

- A command buffer whose guest block the game frees is retired: its chunks go to the pool and the next buffer takes them, so the chunks made stay
  flat. A buffer the render thread is running is retired only when that run ends. A recording that needs fewer chunks than its buffer holds gives
  the rest back at EndCommandBuffer. XB_CB_POOL=0 keeps the old way (nothing given back).
- A kit vertex buffer released to zero is given back (header and data) only after two Swaps and after the run of every recording that may point at
  it; its header is 0x40 bytes from the pool, not 64 KB.

No game data: the functions are cut from the kit's headers and compiled with a stand-in guest memory (as tests/test_x360_replay_race.py does).
Nearest existing: tests/test_x360_replay_race.py - checks cb_snap's masks and the replayed state, not where the chunks come from or go."""
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

RT = X360 / "port" / "runtime"

COMMON = r'''
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef struct { u64 r[8]; } TcArgs;
static unsigned char *tc_base;
static u32 s_brk = 0x100000u, s_allocs;
static u32 xb_alloc(u32 n) { u32 a = s_brk; s_brk += (n + 0xffffu) & ~0xffffu; s_allocs++; return a; }
static void xb_stop(const char *a, const char *b, u32 c) { printf("stop %s %s %x\n", a, b, c); exit(3); }
static void port_log(const char *f, ...) { (void)f; }
static u32 rd32(u32 at) { u32 v; memcpy(&v, tc_base + at, 4); return v; }
static void wr32(u32 at, u32 v) { memcpy(tc_base + at, &v, 4); }
'''

CB_MAIN = r'''
static volatile LONG s_cb_end_n, s_cb_run_done, s_cb_recording;
static unsigned s_defer_n;
static u32 s_kit_out, s_kit_pooled;
static unsigned res_live(void) { return 0; }
CUT
#define DEV 0x10000u
static void record(u32 cb, unsigned draws)   /* BeginCommandBuffer, draws with differing constants, one copied argument, EndCommandBuffer: as cb_entry */
{
    CbList *l = cb_begin(cb, DEV);
    CbCall c;
    unsigned i;
    for (i = 0; i < draws; i++) {
        tc_base[DEV + 0x780u] = (u8)i, tc_base[DEV + 0x781u] = (u8)(i >> 8);   /* vertex c0 differs at each draw: no copy is shared */
        cb_snap(l, DEV);
    }
    memset(&c, 0, sizeof c);
    c.a.r[1] = 0x60000u;
    cb_copy(l, &c, 1, 24);
    cb_end(l);
}
static unsigned live(void) { unsigned i, n = 0; for (i = 0; i < CB_LISTS; i++) n += s_cb[i].cb != 0; return n; }
static void show(const char *what) { printf("%s: made %u+%u pool %u+%u lists %u\n", what, s_cb_made[0], s_cb_made[1], s_cb_pool_n[0], s_cb_pool_n[1], live()); }
int main(void)
{
    CbList *a;
    tc_base = (unsigned char *)calloc(1, 0x8000000u);
    record(0x7000040u, 300);  show("A 300");            /* 3 megabyte chunks of copies, a 64 KB chunk and the baseline */
    record(0x7000040u, 50);   show("A 50");             /* the same buffer again: 2 chunks back to the pool */
    record(0x7000040u, 300);  show("A 300 again");      /* taken back from the pool, none made */
    a = cb_list(0x7000040u, 2);                         /* the render thread starts a run of it */
    xb_d3d_guest_freed(0x7000000u, 0x10000u);  show("freed while running");
    cb_run_end(a);            show("run ended");
    record(0x7100080u, 300);  show("B 300");            /* a new buffer: its chunks from A's */
    record(0x7100080u, 300);  show("B 300 again");
    printf("run done %ld of %ld\n", (long)s_cb_run_done, (long)s_cb_end_n);
    return 0;
}
'''

RES_MAIN = r'''
static unsigned s_frame;
typedef struct { u32 guest; const char *kind; u32 arg[6]; u32 data, pitch; int dirty, dying; } XbRes;
static XbRes s_res[512];
static unsigned s_res_n;
static SRWLOCK s_res_lock = SRWLOCK_INIT;
FIND
CUT
static XbRes *vb(u32 len)   /* make_res and the vertex buffer create, in short */
{
    XbRes *r = &s_res[s_res_n++];
    r->kind = "vertex buffer", r->arg[0] = len, r->guest = kit_get(0x40);
    wr32(r->guest + 24, kit_get(len) | 3u);
    return r;
}
int main(void)
{
    XbRes *r1, *r2;
    u32 h1, d1, before;
    tc_base = (unsigned char *)calloc(1, 0x8000000u);
    r1 = vb(0x70), r2 = vb(0x70);
    h1 = r1->guest, d1 = rd32(h1 + 24) & ~3u;
    printf("headers %x apart, %u xb_allocs\n", r2->guest - r1->guest, s_allocs);
    s_cb_end_n = 5, s_cb_recording = 1, s_cb_run_done = 4;   /* recording 6 under way, 5 not run yet */
    res_defer(r1, d1, 0x70);
    s_frame += 3, res_drain();
    printf("after 3 Swaps, recordings 5 and 6 not run: kept %d\n", find_res(h1) != 0);
    s_cb_run_done = 5, res_drain();
    printf("recording 5 run: kept %d\n", find_res(h1) != 0);
    s_cb_end_n = 6, s_cb_recording = 0, s_cb_run_done = 6, res_drain();
    printf("recording 6 run: kept %d waiting %u\n", find_res(h1) != 0, s_defer_n);
    before = s_allocs;
    printf("reused header %d data %d, %u new xb_allocs\n", kit_get(0x40) == h1, kit_get(0x70) == d1, s_allocs - before);
    s_frame = 100, s_cb_end_n = 7, s_cb_run_done = 7;
    res_defer(r2, rd32(r2->guest + 24) & ~3u, 0x70);
    res_drain();
    printf("same Swap: kept %d\n", find_res(r2->guest) != 0);
    s_frame += 2, res_drain();
    printf("two Swaps later: kept %d\n", find_res(r2->guest) != 0);
    return 0;
}
'''


def between(path, start, end):
    src = (RT / path).read_text(encoding="utf-8")
    i = src.index(start)
    return src[i:src.index(end, i)]


def body(path, sig):
    src = (RT / path).read_text(encoding="utf-8")
    start = src.index(sig)
    return src[start:src.index("\n}\n", start) + 3]


def cb_source():
    cut = between("xb_d3d_cb.h", "typedef struct { u32 entry, snap", "/* The calls that only record into the buffer")
    cut += body("xb_d3d_cb.h", "static void cb_copy(CbList *l, CbCall *c, unsigned k, u32 size)")
    cut += body("xb_d3d_cb.h", "static u32 cb_snap(CbList *l, u32 dev)")
    return COMMON + CB_MAIN.replace("CUT", cut)


def res_source():
    cut = between("xb_d3d_res.h", "/* The kit's own blocks of guest memory for resources", "static int entry_res(")
    return COMMON + RES_MAIN.replace("FIND", body("xb_d3d.c", "static XbRes *find_res(u32 guest)")).replace("CUT", cut)


def test_sources_cut():
    """The cuts the compiled checks use are there (any machine)"""
    assert "void xb_d3d_guest_freed(u32 at, u32 size)" in cb_source() and "static void cb_end(CbList *l)" in cb_source()
    assert "static void res_drain(void)" in res_source()
    krnl = (RT / "xb_imports_krnl.h").read_text(encoding="utf-8") + (RT / "xb_imports_krnl2.h").read_text(encoding="utf-8")
    assert krnl.count("free_block_told(") == 4 and "xb_free_block((u32)" not in krnl.replace("u32 n = xb_free_block(at);", "")
    d3d = (RT / "xb_d3d.c").read_text(encoding="utf-8")
    assert "xb_alloc(0x40)" not in d3d and "r->guest = kit_get(0x40);" in d3d
    swap = (RT / "xb_d3d_draw.h").read_text(encoding="utf-8")
    assert "kit_census(s_frame);" in swap and "res_drain();" in swap
    assert "cb_run_end(l);" in body("xb_d3d_cb.h", "static int cb_entry(")


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Pools(unittest.TestCase):
    def run_c(self, source, env_extra):
        d = tmpdir("x360kp_")
        try:
            (d / "t.c").write_text(source, encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            env = dict(os.environ)
            for k in ("XB_CB_POOL", "XB_RES_FREE", "XB_SNAP_MASK"):
                env.pop(k, None)
            env.update(env_extra)
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_command_buffer_chunks_reused(self):
        got = self.run_c(cb_source(), {})
        self.assertEqual(got[0], "A 300: made 2+3 pool 0+0 lists 1")
        self.assertEqual(got[1], "A 50: made 2+3 pool 0+2 lists 1")
        self.assertEqual(got[2], "A 300 again: made 2+3 pool 0+0 lists 1")
        self.assertEqual(got[3], "freed while running: made 2+3 pool 0+0 lists 1")   # the run is under way: nothing given back yet
        self.assertEqual(got[4], "run ended: made 2+3 pool 2+3 lists 0")
        self.assertEqual(got[5], "B 300: made 2+3 pool 0+0 lists 1")                 # the chunks made stay flat
        self.assertEqual(got[6], "B 300 again: made 2+3 pool 0+0 lists 1")
        self.assertEqual(got[7], "run done 3 of 5")

    def test_pool_off(self):
        got = self.run_c(cb_source(), {"XB_CB_POOL": "0"})
        self.assertEqual(got[1], "A 50: made 2+3 pool 0+0 lists 1")      # kept by the buffer, as before
        self.assertEqual(got[4], "run ended: made 2+3 pool 0+0 lists 1")
        self.assertEqual(got[5], "B 300: made 4+6 pool 0+0 lists 2")

    def test_release_waits_for_runs(self):
        got = self.run_c(res_source(), {})
        self.assertEqual(got[0], "headers 40 apart, 2 xb_allocs")          # one 64 KB block for both headers, one for both small data blocks
        self.assertEqual(got[1], "after 3 Swaps, recordings 5 and 6 not run: kept 1")
        self.assertEqual(got[2], "recording 5 run: kept 1")
        self.assertEqual(got[3], "recording 6 run: kept 0 waiting 0")
        self.assertEqual(got[4], "reused header 1 data 1, 0 new xb_allocs")
        self.assertEqual(got[5], "same Swap: kept 1")
        self.assertEqual(got[6], "two Swaps later: kept 0")
