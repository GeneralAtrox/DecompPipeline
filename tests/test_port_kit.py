"""The Dreamcast port kit (port/): game.json loading, the generated per-game files, stub replacement by defined
names, the kit holding no game material, and (only where Visual Studio's 32-bit tools are found) the runtime
compiling at /W4 /WX and its lockstep check in behaviour (tests/test_lockstep.py). Synthetic data; no Ghidra, no
game workspace. The Visual Studio tools come from config.local.json "vcvars" or $VCVARS32; a git worktree has no
config.local.json, so set VCVARS32 there. Without them those tests skip and stderr says so (*** no 32-bit ...).
Run: python -W error tests/test_port_kit.py, or python -W error -m unittest tests.test_port_kit from the repo root
(also from tests/test_tooling.py)
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))  # its sibling test modules, also when run from the repo root

import game_config  # noqa: E402
import gen_port  # noqa: E402
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import readable  # noqa: E402  (pointer_adapter's prototypes)
from test_lockstep import HAVE_VC, NO_VC, LockstepCompare, LockstepCounter, LockstepTimer  # noqa: E402,F401
from test_overlays import OverlayBuilt, OverlayGroups, OverlayWindows  # noqa: E402,F401  (overlays)

GAME = {"name": "Fixture Game", "decomp": "../decomp", "entry": {"name": "Fx_Main", "addr": "8c011000"},
        "image_base": "8c010000", "sdk": ["sy_heap"], "sources": ["src/own.c"], "hardware": {"0x8C012000": "Fx_Hw"},
        "console": {"country": "0x32", "language": 1, "broadcast": "0x31"}}
# Game material the kit must never hold: guest addresses in RAM (P1 0x8c..., P0 0x0c...; only the bases are kept),
# the games' names and the entry functions they configure.
PA_C = r"""#include <stdint.h>
#include <stdio.h>
typedef unsigned char u8;
typedef unsigned int u32;
typedef int s32;
static u32 g_got[2];
static void Fx_Uv(u32 dst, u32 normal) { g_got[0] = dst; g_got[1] = normal; }
static u32 Fx_Swap(s32 a, u32 b) { return (u32)a * 10u + b; }
%s
%s
typedef void (*Ptr4)(u32, u32, u32, u32, float);
typedef u32 (*Ptr2)(u32, u32);
int main(void)
{
    ((Ptr4)pa_8c011100)(1u, 2u, 3u, 4u, 0.0f);
    printf("%%u %%u %%u\n", g_got[0], g_got[1], ((Ptr2)pa_8c011200)(5u, 6u));
    return 0;
}
"""
GUEST_CODE = re.compile(r"(?<![0-9a-fA-F])[08]c[0-9a-f]{6}(?![0-9a-fA-F])", re.I)
ALLOWED_ADDRS = {"8c000000", "0c000000"}  # guest RAM and its P0 mirror
GAME_WORDS = ("Toy Commander", "ToyCommander", "1GUTH", "Spawn", "Game_Main")
CODE = {".c", ".h", ".cpp", ".hpp", ".hlsl", ".py", ".txt"}
# test_guest_crt_links: readable C and a dispatch row calling the registry's malloc/free, and a host main on the CRT
FX_USE_C = """#include "game.h"
void *g_got[2];
void Fx_Use(FxPool *pool)
{
    g_got[0] = malloc(64);
    free(g_got[0]);
    g_got[1] = malloc(pool->free);
}
"""
FX_ROW_C = """#include "game.h"
typedef void (*FxFn)(void);
const FxFn g_row = (FxFn)malloc;
"""
FX_MAIN_C = r"""#include <stdlib.h>
#include <stdio.h>
typedef struct FxPool { unsigned int free; } FxPool;
typedef void (*FxFn)(void);
void syMallocInit(void *heap, unsigned int size);
void Fx_Use(FxPool *pool);
void *sy_guest_malloc(unsigned int size);
extern const FxFn g_row;
extern void *g_got[2];
static unsigned char g_guest[0x10000];
void port_log(const char *fmt, ...) { (void)fmt; }
static int in_guest(const void *p) { return (const unsigned char *)p >= g_guest && (const unsigned char *)p < g_guest + sizeof g_guest; }
int main(void)
{
    FxPool pool = {64}; /* the same size: the freed block comes back (the top of the merged block) */
    void *host;
    syMallocInit(g_guest, sizeof g_guest);
    Fx_Use(&pool);
    host = malloc(16);
    printf("%d %d %d %d %d\n", in_guest(g_got[0]), in_guest(g_got[1]), g_got[0] == g_got[1],
           g_row == (FxFn)sy_guest_malloc, host != NULL && !in_guest(host));
    free(host);
    return 0;
}
"""
# test_shc_long: sdk/shc_long.c on the edge cases, each result the original's (xemu ran the original helpers)
SHC_GAME_H = """#ifndef GAME_H
#define GAME_H
#include "rd_rt.h"
extern s32 g_errno;
#define SHC_ERRNO_DIV0 1102
s32 Shc_DivLs(s32 divisor, s32 dividend);
u32 Shc_DivLu(u32 divisor, u32 dividend);
s32 Shc_ModLs(s32 divisor, s32 dividend);
u32 Shc_ModLu(u32 divisor, u32 dividend);
void Shc_QuickMove(u32 size, void *dst, const void *src);
void Shc_QuickMoveWords(u32 size, void *dst, const void *src, u32 unusedR3);
void Shc_QuickMoveWordsR3(u32 size, void *dst, const void *src, u32 unusedR3);
void Shc_SlowMove(u32 size, void *dst, const void *src);
void Shc_SlowStrCpy(char *dst, const char *src);
u32 Shc_BitfieldStore(u32 value, u32 spec, u32 *word);
void Sh4_CopyWords(u32 *dst, const u32 *src, u32 size);
void Sh4_CopyAligned(void *dst, const void *src, u32 size);
#endif
"""
SHC_MAIN_C = r"""#include <stdio.h>
#include <string.h>
#include "game.h"
s32 g_errno;
static u32 w[24];
static void words(u32 n) { for (u32 i = 0; i < 24; i++) w[i] = i < n ? i + 1 : 0; }
/* platform.h's texture window, as pvr_state.c gives it: 0xa4000000.. is tex[], written ranges are noted */
static u8 tex[16];
static u32 tex_written;
u8 *port_texture_window(u32 addr, u32 size) { return addr >= 0xa4000000u && addr - 0xa4000000u + size <= 16u ? tex + (addr - 0xa4000000u) : NULL; }
void port_texture_written(u32 addr, u32 size) { tex_written = addr ^ size; }
int main(void)
{
    char s[16] = "abcdefg", d[16];
    u8 b[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    int ok = 1;
    g_errno = 0;
    ok &= Shc_DivLs(0, 7) == 0 && g_errno == 1102;                   /* zero divisor: errno, 0 */
    g_errno = 0;
    ok &= Shc_ModLu(0, 7) == 0 && g_errno == 1102;
    g_errno = 0;
    ok &= Shc_DivLs(-1, (s32)0x80000000u) == (s32)0x80000000u;      /* INT_MIN / -1 wraps */
    ok &= Shc_ModLs(-1, (s32)0x80000000u) == 0 && g_errno == 0;
    ok &= Shc_DivLs(2, -7) == -3 && Shc_ModLs(2, -7) == -1;           /* truncated toward zero */
    ok &= Shc_DivLu(2, 0xffffffffu) == 0x7fffffffu && Shc_ModLu(10, 0xffffffffu) == 5u;
    words(5);
    Shc_QuickMove(16, w, w + 1);                                      /* <= 64: highest word first */
    ok &= w[0] == 5 && w[1] == 5 && w[2] == 5 && w[3] == 5 && w[4] == 5;
    words(5);
    Shc_QuickMoveWords(16, w, w + 1, 0);                              /* one word read ahead */
    ok &= w[0] == 2 && w[1] == 3 && w[2] == 4 && w[3] == 5 && w[4] == 5;
    words(4);
    Shc_QuickMoveWordsR3(12, w + 1, w, 0);
    ok &= w[0] == 1 && w[1] == 1 && w[2] == 2 && w[3] == 3 && w[4] == 0;
    words(20);
    Shc_QuickMove(70, w + 1, w);                                      /* > 64: lowest word first, size / 4 words */
    ok &= w[1] == 1 && w[17] == 1 && w[18] == 19;
    Shc_SlowMove(4, b + 1, b);                                        /* bytes, lowest first */
    ok &= b[1] == 1 && b[4] == 1 && b[5] == 6;
    Shc_SlowMove(0, b, b + 4);
    ok &= b[0] == 1;
    memset(d, 'x', sizeof d);
    Shc_SlowStrCpy(d + 1, s);
    ok &= strcmp(d + 1, "abcdefg") == 0 && d[0] == 'x' && d[9] == 'x';
    w[0] = 0x12345678u;                                               /* bit field 8 bits wide, 4 below the top */
    ok &= Shc_BitfieldStore(0x1abu, 4u << 8 | 8u, w) == 0xabu && w[0] == 0x1ab45678u;
    w[0] = 0x12345678u;
    ok &= Shc_BitfieldStore(0u, 0u << 8 | 32u, w) == 0u && w[0] == 0u;  /* the whole word */
    words(6);
    Sh4_CopyWords(w + 1, w, 22);                                      /* size / 4 words, lowest first */
    ok &= w[1] == 1 && w[5] == 1 && w[6] == 0;
    words(3);
    Sh4_CopyWords(w, w + 1, 3);                                       /* under a word: nothing */
    ok &= w[0] == 1;
    words(6);
    Sh4_CopyAligned(w + 2, w, 16);                                    /* 8-byte units, each read before written */
    ok &= w[2] == 1 && w[3] == 2 && w[4] == 1 && w[5] == 2 && w[6] == 0;
    words(6);
    Sh4_CopyAligned(w + 1, w, 12);                                    /* 4-byte units, lowest first */
    ok &= w[1] == 1 && w[3] == 1 && w[4] == 5;
    memcpy(b, "\x01\x02\x03\x04\x05\x06\x07\x08", 8);
    Sh4_CopyAligned(b + 2, b, 6);                                     /* 2-byte units */
    ok &= b[2] == 1 && b[3] == 2 && b[4] == 1 && b[5] == 2 && b[7] == 2;
    memcpy(b, "\x01\x02\x03\x04\x05\x06\x07\x08", 8);
    Sh4_CopyAligned(b + 1, b, 3);                                     /* bytes: an odd size */
    ok &= b[1] == 1 && b[3] == 1 && b[4] == 5;
    Sh4_CopyAligned(b, b + 4, 0);                                     /* size 0: nothing */
    ok &= b[0] == 1;
    Sh4_CopyAligned((void *)(uintptr_t)0xa4000004u, b, 8);           /* into texture memory: through the window */
    ok &= tex[4] == 1 && tex[11] == 8 && tex[3] == 0 && tex_written == (0xa4000004u ^ 8u);
    printf("%d\n", ok);
    return 0;
}
"""
# test_lib_ctx: sdk/lib_ctx.c's contexts on host coroutines: fresh contexts as the library makes them, the switch order,
# an entry that returns starting over, SR per context, a switch between two contexts, an entry with no host function
LIB_GAME_H = """#ifndef GAME_H
#define GAME_H
#include "rd_rt.h"
typedef void (*SdkProc)(void);
typedef struct LibCpuContext {
    u32 r0;
    u32 unknown_04;
    u32 gpr[15];
    u32 pr;
    u32 sr;
} LibCpuContext;
void *memset(void *dst, s32 c, u32 n);
void Lib_InitContext(LibCpuContext *ctx, SdkProc entry, void *stackTop);
void Lib_SwapContext(LibCpuContext *load, LibCpuContext *save);
#endif
"""
LIB_MAIN_C = r"""#include <stdio.h>
#include <stdlib.h>
#include "game.h"
#include "platform.h"
typedef union {
    LibCpuContext c;
    u8 bytes[0x50];
} Ctx;
static Ctx g_main, g_a, g_b, g_x, g_never;
static char g_out[64];
static int g_n, g_logs;
static u32 g_sr = 0x400000f0u;
u32 sh4_stc_sr(void) { return g_sr; }
void sh4_ldc_sr(u32 v) { g_sr = v & 0x700083f3u; }
void port_log(const char *fmt, ...) { (void)fmt; g_logs++; }
void port_exit(const char *why, int code)
{
    printf("exit %d %s\n", code, why);
    exit(0);
}
PortCodeFn port_code_function(u32 addr) { return addr == 0x8c001000u ? NULL : (PortCodeFn)(uintptr_t)addr; }
static void put(char c) { g_out[g_n++] = c; }
static void a_entry(void)
{
    put('a');
    put(g_sr == 0x600000f0u ? '6' : '?');
    Lib_SwapContext(&g_main.c, &g_a.c);
    put('A');
    Lib_SwapContext(&g_main.c, &g_a.c);
}
static void b_entry(void)
{
    for (;;) {
        put('b');
        Lib_SwapContext(&g_a.c, &g_b.c);
    }
}
/* the library's fresh context: "$CTX", r15 = top, pr = entry, SR 0x600000f0, all else of the 0x50 bytes 0 */
static int fresh(Ctx *c, u32 entry, u32 top)
{
    for (u32 i = 0; i < 0x50u; i++) {
        u32 at = i & ~3u, w = at == 0u ? 0x58544324u : at == 0x40u ? top : at == 0x44u ? entry : at == 0x48u ? 0x600000f0u : 0u;
        if (c->bytes[i] != (u8)(w >> 8 * (i & 3u))) return 0;
    }
    return 1;
}
int main(int argc, char **argv)
{
    Ctx *all[3] = {&g_a, &g_b, &g_x};
    u32 entry[3] = {(u32)(uintptr_t)a_entry, (u32)(uintptr_t)b_entry, 0x8c001000u}, ok = 1;
    (void)argv;
    for (u32 i = 0; i < 3; i++) {
        memset(all[i], 0x5a, sizeof *all[i]);
        Lib_InitContext(&all[i]->c, (SdkProc)(uintptr_t)entry[i], (void *)(uintptr_t)(0x8c100000u + i));
        ok &= (u32)fresh(all[i], entry[i], 0x8c100000u + i);
    }
    if (argc > 1) Lib_SwapContext(&g_never.c, &g_main.c); /* never made: the run ends */
    for (int i = 0; i < 3; i++) {
        Lib_SwapContext(&g_a.c, &g_main.c);
        put('m');
    }
    Lib_SwapContext(&g_b.c, &g_main.c);
    put('m');
    Lib_SwapContext(&g_x.c, &g_main.c);
    Lib_SwapContext(&g_x.c, &g_main.c);
    put('x');
    printf("%s %d %u %u\n", g_out, g_logs, ok, (u32)(g_sr == 0x400000f0u && g_main.c.sr == 0x400000f0u));
    return 0;
}
"""
# test_fm_mng: sdk/fm_mng.c on a stock flash and on a --flash image, each result the original library's (xemu ran it)
FM_GAME_H = """#ifndef GAME_H
#define GAME_H
#include "rd_rt.h"
s32 fmMngInit(s32 partition, void *work);
s32 fmMngFormat(s32 mode);
s32 fmMngRead(s32 block, void *buffer);
s32 fmMngWrite(s32 block, const void *buffer);
s32 fmMngExecServer(void);
void Fm_Finish(void);
#endif
"""
FM_MAIN_C = r"""#include <stdio.h>
#include <string.h>
#include "game.h"
static u8 g_image[0x20000], g_work[0x4000], g_buf[64];
static int g_use_image;
void port_log(const char *fmt, ...) { (void)fmt; }
int host_flash_read(void *buf, u32 size)
{
    if (!g_use_image) return 0;
    memcpy(buf, g_image, size);
    return 1;
}
static u32 crc(const u8 *p)
{
    u32 n = 0xffffu;
    for (u32 i = 0; i < 62u; i++) {
        n ^= (u32)p[i] << 8;
        for (u32 b = 0; b < 8u; b++) n = (n & 0x8000u) ? ((n << 1) ^ 0x1021u) : (n << 1);
    }
    return ~n & 0xffffu;
}
static void block(u32 phys, u32 id, u8 fill)
{
    u8 *b = g_image + 0x1c000u + phys * 64u;
    memset(b, fill, 64);
    b[0] = (u8)id;
    b[1] = (u8)(id >> 8);
    u32 c = crc(b);
    b[62] = (u8)c;
    b[63] = (u8)(c >> 8);
}
static s32 run(void)
{
    s32 r, n = 0;
    while ((r = fmMngExecServer()) == 2) n++;
    return n ? r + 10 * n : r; /* 1 done, 3 failed; + 10 per busy step */
}
static int all(const u8 *p, u8 v, u32 n)
{
    for (u32 i = 0; i < n; i++)
        if (p[i] != v) return 0;
    return 1;
}
/* a stock console: the user partition formatted and empty; an unwritten block reads as 0xff and succeeds */
static int stock(const u8 *data)
{
    int ok = 1;
    ok &= fmMngExecServer() == 0 && fmMngRead(0, g_buf) == -11;
    ok &= fmMngInit(0, g_work) == -7 && fmMngInit(5, g_work) == -7;
    ok &= fmMngInit(3, g_work) == -8;                                       /* unformatted: the caller formats */
    ok &= fmMngInit(2, g_work) == 0 && !memcmp(g_work, "KATANA_FLASH____\2\0", 18) && all(g_work + 18, 0xff, 0x3fee);
    memset(g_buf, 0, sizeof g_buf);
    ok &= fmMngRead(192, g_buf) == 0 && fmMngRead(193, g_buf) == -9 && run() == 1;
    ok &= all(g_buf, 0xff, 60) && g_buf[60] == 0;                          /* 60 bytes, no more */
    ok &= fmMngRead(-1, g_buf) == -5 && fmMngRead(254, g_buf) == -5;
    ok &= fmMngWrite(192, data) == 0 && run() == 1 && fmMngRead(192, g_buf) == 0 && run() == 1;
    ok &= !memcmp(g_buf, data, 60) && g_work[193 * 64] == 192 && g_work[255 * 64] == 0x7f;    /* the bitmap: 1 block */
    g_work[193 * 64 + 10] ^= 1u;                                            /* a damaged copy fails its CRC */
    ok &= fmMngRead(192, g_buf) == 0 && run() == 3;
    Fm_Finish();
    ok &= fmMngExecServer() == 0 && fmMngRead(192, g_buf) == -11 && fmMngFormat(0) == -11;
    return ok;
}
/* a --flash image: copies in physical order, the newest winning, a bad CRC skipped, the scan ending at a free block;
   a full partition; mode 1 refused */
static int image(u8 *data)
{
    int ok = 1;
    memset(g_image, 0xff, sizeof g_image);
    memcpy(g_image + 0x1c000u, "KATANA_FLASH____\2\0", 18);
    block(1, 7, 0x11);
    block(2, 7, 0x22);
    block(3, 8, 0x33);
    g_image[0x1c000u + 3u * 64u + 5u] ^= 1u;
    block(4, 0xffffu, 0xff);
    block(5, 9, 0x55);
    memset(g_image + 0x1c000u + 255u * 64u, 0xff, 64);
    g_image[0x1c000u + 255u * 64u] = 0x07;                                   /* 5 allocated */
    ok &= fmMngInit(2, g_work) == 0;
    ok &= fmMngRead(7, g_buf) == 0 && run() == 1 && all(g_buf, 0x22, 60);
    ok &= fmMngRead(8, g_buf) == 0 && run() == 1 && all(g_buf, 0xff, 60);
    ok &= fmMngRead(9, g_buf) == 0 && run() == 1 && all(g_buf, 0xff, 60);
    /* a full partition: the write that finds no room takes two more steps (erase, write back) */
    for (u32 i = 0; i < 249u && ok; i++) ok &= fmMngWrite(0, data) == 0 && run() == 1;
    data[0] = 0x5a;
    ok &= fmMngWrite(100, data) == 0 && run() == 21 && fmMngRead(100, g_buf) == 0 && run() == 1 && g_buf[0] == 0x5a;
    ok &= fmMngRead(7, g_buf) == 0 && run() == 1 && all(g_buf, 0x22, 60);
    ok &= fmMngFormat(2) == -6 && fmMngFormat(1) == -6 && fmMngInit(2, g_work) == -6;
    ok &= fmMngFormat(0) == 0 && fmMngInit(2, g_work) == 0 && all(g_work + 64, 0xff, 0x3f80);
    return ok;
}
int main(int argc, char **argv)
{
    u8 data[60];
    (void)argv;
    for (u32 i = 0; i < 60; i++) data[i] = (u8)(i * 7u);
    g_use_image = argc > 1; /* the image is loaded once, at the first Init: a run each */
    printf("%d\n", g_use_image ? image(data) : stock(data));
    return 0;
}
"""


def kit_files(kit=KIT):
    return sorted(p for p in kit.rglob("*") if p.is_file() and (p.suffix in CODE or p.name == "CMakeLists.txt")
                  and "__pycache__" not in p.parts)


def game_material(files, kit=KIT):
    """[(relative path, line no, what)] for each guest code address, game word or runtime game.h include."""
    hits = []
    for p in files:
        rel = p.relative_to(kit).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in GUEST_CODE.finditer(line):
                if m.group(0).lower() not in ALLOWED_ADDRS:
                    hits.append((rel, i, m.group(0)))
            hits += [(rel, i, w) for w in GAME_WORDS if w in line]
            if rel.startswith("runtime/") and re.match(r'\s*#\s*include\s+"game\.h"', line):
                hits.append((rel, i, "game.h"))
    return hits


AICA_BLANKS_C = r"""#include <stdio.h>
#include "aica.h"

int main(void)
{
    u64 sum = 0, track = 0;
    u32 lo = ~0u, hi = 0;
    for (u64 n = 0; n < 60000u; n++) {
        u32 f = aica_vblank_frames(n);
        sum += f;
        lo = f < lo ? f : lo;
        hi = f > hi ? f : hi;
        if (!track && sum >= 10632u * 588u) track = n + 1u; /* a CD sector is 588 frames */
    }
    printf("%llu %u %u %llu\n", sum, lo, hi, track);
    return 0;
}
"""

HEAP_LAYOUT_C = r"""#include <stdio.h>
#include <string.h>
#include "platform.h"

void port_log(const char *fmt, ...) { (void)fmt; }

static __declspec(align(32)) u8 g_region[256 * 32];

int main(void)
{
    Heap h;
    u8 *base = g_region, *x, *q, *all;
    u32 w[2];
    memset(g_region, 0, sizeof g_region);
    heap_init(&h, "test", g_region, sizeof g_region);
    x = heap_alloc(&h, 1000);                /* 33 units, the region's top */
    q = heap_alloc(&h, 64);                  /* 3 units, just below: an aligned allocator's 32-byte buffer at q + 32 */
    memcpy(w, q + 64, sizeof w);             /* the two words past that buffer: x's header, next stale (0), 33 units */
    printf("%d %d %u %u %u", (int)((x - base) / 32), (int)((q - base) / 32), ((u32 *)(q - 32))[1], w[0], w[1]);
    printf(" %d", heap_free(&h, q));
    printf(" %d", heap_free(&h, q));
    printf(" %d", heap_free(&h, base + 8));
    printf(" %d", heap_free(&h, x));
    all = heap_alloc(&h, 255 * 32);          /* merged back into one block: all 256 units */
    printf(" %d %d\n", all ? (int)((all - base) / 32) : -1, heap_alloc(&h, 1) != NULL);
    return 0;
}
"""
# test_kamui2_line_mode: sdk/kamui2_misc.c's kmSetDisplayMode calls the line-mode callback (slot 11) only for a mode
# with bits 0x42 both set, as the library tests them (DP 5768938); each line: the callback's calls, the mode it got and
# the frame buffer's lines, for a callback that always picks line mode 4 (Toy Commander's picks a PAL count)
K2_GAME_H = """#ifndef GAME_H
#define GAME_H
#include <string.h>
#include "rd_rt.h"
typedef void (*SdkProc)(void);
typedef void (*KmCallbackFunc)(void *);
typedef struct { u8 b[12]; } KmVersionInfo;
#endif
"""
K2_MAIN_C = r"""#include <stdio.h>
#include "game.h"
#include "platform.h"
#include "kamui2.h"

u32 k2_frame_lines(void);
s32 Km_SetCallback11(KmCallbackFunc func);
s32 kmSetDisplayMode(u32 mode, u32 bpp, u32 dither, u32 antiAlias);
void port_log(const char *fmt, ...) { (void)fmt; }
void pvr_todo(const char *fmt, ...) { (void)fmt; }
void port_set_vblank_hook(PortVblankFn fn, void *arg) { (void)fn, (void)arg; }
void pvr_set_fog_table(const u16 entries[128]) { (void)entries; }
void pvr_set_fog_color(u32 argb) { (void)argb; }
void pvr_set_fog_density(u32 reg) { (void)reg; }
void pvr_set_background(const f32 xyz[3][3], const u32 argb[3]) { (void)xyz, (void)argb; }
void pvr_set_border(u32 argb) { (void)argb; }
void pvr_set_cheap_shadow(s32 intensity) { (void)intensity; }
void pvr_set_palette_format(u32 format) { (void)format; }
void pvr_set_palette(u32 first, const u32 *entries, u32 count) { (void)first, (void)entries, (void)count; }

static u32 g_calls, g_mode, g_pick;
static void lines(void *arg)
{
    u32 *a = (u32 *)arg;
    g_calls++;
    g_mode = a[0];
    a[1] = g_pick;
}

int main(void)
{
    static const u32 modes[] = {0x00u, 0x02u, 0x40u, 0x42u, 0x43u, 0x00u};
    Km_SetCallback11(lines);
    g_pick = 4u;
    for (u32 i = 0; i < sizeof modes / sizeof modes[0]; i++) {
        g_calls = g_mode = 0;
        kmSetDisplayMode(modes[i], 0, 0, 0);
        printf("%u %x %u\n", g_calls, g_mode, k2_frame_lines());
    }
    g_pick = 7u; /* no such line mode: 480 lines */
    kmSetDisplayMode(0x42u, 0, 0, 0);
    printf("%u\n", k2_frame_lines());
    return 0;
}
"""


class GameConfig(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "src").mkdir()
        (self.dir / "src" / "own.c").write_text("void Fx_Hw(void) {}\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, **over):
        (self.dir / "game.json").write_text(json.dumps(dict(GAME, **over)), encoding="utf-8")
        return game_config.load(self.dir)

    def test_resolved(self):
        cfg = self.load()
        self.assertEqual(cfg["entry"], {"name": "Fx_Main", "addr": "8c011000"})
        self.assertEqual(cfg["hardware"], {"8c012000": "Fx_Hw"})
        self.assertEqual(cfg["decomp"], (self.dir.parent / "decomp").resolve())
        self.assertEqual([p.name for p in cfg["sdk"]], ["sy_heap.c"])
        self.assertEqual(cfg["console"], {"country": 0x32, "language": 1, "broadcast": 0x31})
        self.assertEqual((cfg["boot_file"], cfg["disc"], cfg["must_work"]), ("", None, None))
        self.assertEqual(cfg["tx_first"], [])
        self.assertEqual(self.load(tx_first=["0x8C058560"])["tx_first"], ["8c058560"])
        self.assertEqual(cfg["not_code"], {})
        self.assertEqual(self.load(not_code={"0x8C0B0242": "inside a table"})["not_code"], {"8c0b0242": "inside a table"})
        self.assertIsNone(cfg["texture_pack"])  # pvr/pvr_pack.c: no pack unless the game names one
        pack = self.load(texture_pack="artifacts/texture_pack")["texture_pack"]
        self.assertEqual(pack, (self.dir / "artifacts" / "texture_pack").resolve())  # need not exist yet
        self.assertIn(f'#define PORT_TEXTURE_PACK "{pack.as_posix()}"', game_config.port_config_h(dict(cfg, texture_pack=pack)))
        self.assertNotIn("PORT_TEXTURE_PACK", game_config.port_config_h(cfg))

    def test_errors(self):
        for over in ({"sdk": ["no_such_module"]}, {"image_base": "8c01"}, {"sources": ["src/missing.c"]},
                     {"console": {"country": 1}}, {"surprise": 1}, {"entry": {"name": "1bad", "addr": "8c011000"}},
                     {"boot_file": "../X.BIN"}, {"tx_first": "8c058560"}, {"tx_first": ["8c0585"]},
                     {"not_code": ["8c0b0242"]}, {"not_code": {"8c0b0242": ""}}, {"not_code": {"8c0b02": "x"}}, {"texture_pack": True}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)
        (self.dir / "game.json").write_text(json.dumps({k: v for k, v in GAME.items() if k != "entry"}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            game_config.load(self.dir)

    def test_generated(self):
        cfg = self.load(boot_file="1ST_READ.BIN")
        h = game_config.port_config_h(cfg)
        for line in ('#define PORT_ENTRY_NAME "Fx_Main"', "#define PORT_ENTRY_ADDR 0x8c011000u",
                     "#define PORT_IMAGE_BASE 0x8c010000u", '#define PORT_BOOT_FILE "1ST_READ.BIN"',
                     "#define PORT_CONSOLE_COUNTRY 0x32"):
            self.assertIn(line, h)
        self.assertIn("void port_game_entry(void) { Fx_Main(); }", game_config.port_entry_c(cfg))
        cm = game_config.port_files_cmake(cfg, pathlib.Path("W:/r/src"), ["8c011000"], pathlib.Path("W:/r/include"),
                                          pathlib.Path("W:/rt"))
        self.assertIn('  "W:/r/src/8c011000.c"', cm)
        self.assertIn(f'  "{(KIT / "sdk" / "sy_heap.c").as_posix()}"', cm)
        self.assertIn(f'  "{(self.dir / "src" / "own.c").resolve().as_posix()}"', cm)
        cm = game_config.port_files_cmake(cfg, pathlib.Path("W:/r/src"), ["8c011000"], pathlib.Path("W:/r/include"),
                                          pathlib.Path("W:/rt"), {"8c011000": ["Fx_Draw=Fx_DrawWrap"]}, ["8c011000"])
        self.assertIn('set(PORT_READABLE_NO_PCH\n  "W:/r/src/8c011000.c"\n)', cm)
        self.assertIn('set_source_files_properties("W:/r/src/8c011000.c" PROPERTIES COMPILE_DEFINITIONS '
                      '"Fx_Draw=Fx_DrawWrap")', cm)

    def test_kamui1(self):
        self.assertNotIn("PORT_KAMUI1", game_config.port_config_h(self.load()))
        cfg = self.load(kamui1={"list_word": "0x8C380E74"})
        self.assertEqual(cfg["kamui1"], {"list_word": "8c380e74"})
        self.assertIn("#define PORT_KAMUI1_LIST_WORD 0x8c380e74u", game_config.port_config_h(cfg))
        for over in ({"kamui1": {"list_word": "8c38"}}, {"kamui1": {"other": 1}}, {"kamui1": 5}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)

    def test_lockstep_sample_pcs(self):
        self.assertEqual(self.load()["lockstep_sample_pcs"], [])
        self.assertEqual(self.load(lockstep_sample_pcs=["0x8C06CC48", "8c06c090"])["lockstep_sample_pcs"], ["8c06cc48", "8c06c090"])
        for over in ({"lockstep_sample_pcs": "8c06cc48"}, {"lockstep_sample_pcs": ["8c06"]}, {"lockstep_sample_pcs": ["8c06cc48"] * 9}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)

    def test_lockstep_rtc_pcs(self):
        self.assertEqual(self.load()["lockstep_rtc_pcs"], [])
        self.assertEqual(self.load(lockstep_rtc_pcs=["0x8C0B42A6"])["lockstep_rtc_pcs"], ["8c0b42a6"])
        self.assertEqual(self.load(lockstep_scene_pcs=["8C06C278"])["lockstep_scene_pcs"], ["8c06c278"])
        for over in ({"lockstep_rtc_pcs": "8c0b42a6"}, {"lockstep_rtc_pcs": ["8c0b"]},
                     {"lockstep_rtc_pcs": ["8c0b42a6"], "lockstep_sample_pcs": ["8c06cc48"] * 8},
                     {"lockstep_scene_pcs": ["8c06c278"] * 7, "lockstep_sample_pcs": ["8c06cc48"] * 2}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)

    def test_wrap(self):
        self.assertEqual(self.load(wrap={"0x8C011800": "Fx_DrawWrap"})["wrap"], {"8c011800": "Fx_DrawWrap"})
        for over in ({"wrap": {"8c011800": "1bad"}}, {"wrap": {"8c012000": "Fx_Hw2"}}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)
        src = self.dir / "rsrc"
        src.mkdir()
        (src / "8c011000.c").write_text("void Fx_Main(void) { Fx_Draw(); }\n", encoding="utf-8")
        (src / "8c011800.c").write_text("void Fx_Draw(void) { Fx_Draw2(); }\n", encoding="utf-8")
        (src / "8c011900.c").write_text("void Fx_Other(void) { Fx_Drawn(); }\n", encoding="utf-8")
        names = {"8c011000": "Fx_Main", "8c011800": "Fx_Draw", "8c011900": "Fx_Other"}
        files = sorted(names)
        got = gen_port.wrap_defines(src, files, {"8c011800": "Fx_DrawWrap"}, names, {"Fx_DrawWrap"})
        self.assertEqual(got, {"8c011000": ["Fx_Draw=Fx_DrawWrap"]})  # not its own file, not Fx_Drawn
        with self.assertRaises(SystemExit):  # the game's sources must define the wrapper
            gen_port.wrap_defines(src, files, {"8c011800": "Fx_DrawWrap"}, names, set())
        self.assertEqual(gen_port.wrapper_proto("void Fx_Draw(void);", "Fx_Draw", "Fx_DrawWrap"), "void Fx_DrawWrap(void);")
        (src / "8c011a00.c").write_text('#include "game.h"\nvoid Fx_Four(void) {}\n', encoding="utf-8")
        self.assertEqual(gen_port.no_pch(src, files + ["8c011a00"], got), files)  # wrap defines, or not game.h first

    def test_hardware_names(self):
        """A hardware function must have the registry's name at its address; at an address the registry does not
        name (unnamed library code) the game's sources name it, and the dispatch row is checked with the catalog."""
        cfg = {"entry": {"name": "Fx_Main", "addr": "8c011000"}, "hardware": {"8c012000": "Fx_Hw", "8c0d0000": "Fx_Lib"}}
        protos = {"8c011000": "void Fx_Main(void);"}
        gen_port.check_names(cfg, {"8c011000": "Fx_Main", "8c012000": "Fx_Hw"}, protos)
        with self.assertRaises(SystemExit):
            gen_port.check_names(cfg, {"8c011000": "Fx_Main", "8c012000": "Fx_Other"}, protos)

    def test_guest_crt(self):
        """The guest C library's heap names map onto the kit's (game.h #defines), apart from the host CRT's."""
        self.assertEqual(gen_port.guest_crt_lines(["Fx_Main", "strlen"]), [])
        lines = gen_port.guest_crt_lines(["malloc", "Fx_Main", "free", "free"])
        self.assertEqual(lines[1:], ["#define free sy_guest_free", "#define malloc sy_guest_malloc"])
        self.assertFalse(set(gen_port.GUEST_CRT) & gen_port.CRT)
        heap = gen_port.defined_names([KIT / "sdk" / "sy_heap.c"])
        self.assertTrue({gen_port.GUEST_CRT["malloc"], gen_port.GUEST_CRT["free"]} <= heap)

    def test_defined_names(self):
        f = self.dir / "src" / "defs.c"
        f.write_text("s32 Fx_Open(const char *name)\n{\n    return 0;\n}\nstatic void helper(void) {}\n"
                     "void *Fx_Alloc(u32 size) { return 0; }\n    s32 indented(void);\nu32 proto_only(void);\n"
                     "s32 Fx_Long(u32 a,\n            u32 b)\n{\n    return 0;\n}\nu32 long_proto(u32 a,\n"
                     "               u32 b);\n", encoding="utf-8")
        # not the prototypes; a parameter list may run over lines
        self.assertEqual(gen_port.defined_names([f]), {"Fx_Open", "Fx_Alloc", "Fx_Long"})


class KitIsGameFree(unittest.TestCase):
    def test_no_game_material(self):
        files = kit_files()
        self.assertTrue(any(p.parent.name == "runtime" for p in files))
        self.assertEqual(game_material(files), [])

    def test_material_is_found(self):
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            (d / "runtime").mkdir()
            (d / "runtime" / "x.c").write_text('#include "game.h"\nu32 a = 0x8c02796au, b = 0x8c000000u;\n'
                                               "/* Toy Commander */\n", encoding="utf-8")
            hits = game_material(kit_files(d), d)
            self.assertEqual(sorted(h[2] for h in hits), sorted(["game.h", "8c02796a", "Toy Commander"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_sizes(self):
        self.assertEqual([p.name for p in kit_files() if len(p.read_text(encoding="utf-8").splitlines()) > 400], [])


class RegressUnstableFrames(unittest.TestCase):
    """A pass past the renderer's autosort pool drops the translucent fragments the GPU appended last, which differ
    from run to run: tools/regress.py fails the script, since its frames cannot repeat."""
    def test_overflow_fails(self):
        # by path: the pipeline's own tools/regress.py has the same module name, and in a full suite run it can be
        # imported first
        import importlib.util
        spec = importlib.util.spec_from_file_location("kit_regress_t", KIT / "tools" / "regress.py")
        regress = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(regress)
        log = ("ta: list 2: polygon vertex type 0, isp e4000000 tsp 949800c0 (frame 3)\n"
               "pvr: a pass drew 1245066 translucent fragments, past the autosort pool of 1228800 (frame 6862)\n"
               "pvr: autosort pool of 1556332 fragments; the largest pass drew 1245066; 1 pass(es) overflowed\n")
        self.assertEqual(regress.unstable(log), [
            "pvr: a pass drew 1245066 translucent fragments, past the autosort pool of 1228800 (frame 6862)"])
        self.assertEqual(regress.unstable(log.splitlines()[2]), [])


class PointerAdapters(unittest.TestCase):
    """A call through a guest pointer (dispatch.c's thunk) passes r4..r7 in order. A function whose own parameters skip
    or reorder them (Toy Commander's fn_8c075224 takes r4 and r6: its env-map UVs came out of r5, a NULL UV pointer)
    gets a dispatch adapter (gen_port.pointer_adapter); a positional one, or one with a float, keeps its row."""
    class Reg:
        kind = staticmethod(lambda t: "i" if "*" in t else readable.BASE_KINDS[t.replace("const ", "").strip()])

    @staticmethod
    def adapter(proto, storages, fn=None):
        abi = type("Abi", (), {"own_storages": staticmethod(lambda reg, addr, proto, meta: storages)})
        return gen_port.pointer_adapter(PointerAdapters.Reg, readable, abi, "8c011100", proto, {}, fn or "Fx_Fn")

    def test_skipped_register(self):
        self.assertEqual(self.adapter("void Fx_Fn(u32 param_1, u32 param_2)", ["r4", "r6"], "Fx_Wrap"),
                         ("pa_8c011100", "static void pa_8c011100(u32 r4, u32 r5, u32 r6) { (void)r5; "
                                         "Fx_Wrap((u32)r4, (u32)r6); }"))

    def test_reordered_with_a_result(self):
        self.assertEqual(self.adapter("u32 Fx_Fn(s32 a, const u8 *b)", ["r5", "r4"])[1],
                         "static u32 pa_8c011100(u32 r4, u32 r5) { return Fx_Fn((s32)r5, (const u8 *)(uintptr_t)r4); }")

    def test_kept(self):
        self.assertIsNone(self.adapter("void Fx_Fn(u32 a, u32 b)", ["r4", "r5"]))  # positional
        self.assertIsNone(self.adapter("void Fx_Fn(u32 a, f32 b)", ["r4", "fr4"]))  # a float
        self.assertIsNone(self.adapter("void Fx_Fn(u32 a, u32 b)", ["r4", "stack+0"]))  # the stack


@unittest.skipUnless(HAVE_VC, NO_VC)
class RuntimeCompiles(unittest.TestCase):
    def test_runtime_w4_wx(self):
        """Every runtime file and the generated fsca_table.c compile at the port's flags with zero warnings (a synthetic
        port_config.h)."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            sys.path.insert(0, str(TOOL))
            from core import fsca  # gen_port.py's fsca_table.c, which sh4_rt.c reads
            (d / "fsca_table.c").write_text(fsca.table_c(), encoding="utf-8")
            srcs = " ".join(f'"{p}"' for p in sorted((KIT / "runtime").glob("*.c")) + [d / "fsca_table.c"])
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /c /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "host"}" /I"{rd}" {srcs}')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_guest_crt_links(self):
        """A registry naming malloc/free: readable calls and a dispatch row reach sy_heap's guest-RAM allocator, and
        the program links against the static host CRT (whose own malloc still works) with zero warnings."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            game_h = (["#ifndef GAME_H", "#define GAME_H", '#include "rd_rt.h"']
                      + gen_port.guest_crt_lines(["malloc", "free", "Fx_Use"])
                      + ["typedef struct FxPool { u32 free; } FxPool;", "void *malloc(u32 size);",
                         "void free(void *ptr);", "void syMallocInit(void *heap, u32 size);",
                         "void Fx_Use(FxPool *pool);", "#endif", ""])
            (d / "game.h").write_text("\n".join(game_h), encoding="utf-8")
            (d / "fx_use.c").write_text(FX_USE_C, encoding="utf-8")    # readable C: the registry's names
            (d / "fx_row.c").write_text(FX_ROW_C, encoding="utf-8")    # a dispatch row names the registry function
            (d / "fx_main.c").write_text(FX_MAIN_C, encoding="utf-8")  # the host side: no game.h, the CRT's heap
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "sy_heap.c", KIT / "runtime" / "heap.c",
                                                d / "fx_use.c", d / "fx_row.c", d / "fx_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /MT /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{rd}" {srcs} /Fe:fx.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "fx.exe")], capture_output=True, text=True, timeout=60)
            # both guest blocks in guest RAM (the freed one reused), the row is the kit's, the host heap separate
            self.assertEqual((run.returncode, run.stdout.split()), (0, ["1", "1", "1", "1", "1"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_aica_vblank_frames(self):
        """The AICA advances 44,100 Hz over the console's NTSC blank (60000/1001 Hz): 735 or 736 frames a blank, exact
        over 1001 s, so a CD track ends at the blank it does on the console (a 10,632-sector track 8,497.1 blanks after its
        start, not 8,505.6 as at 735 a blank)."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "blanks.c").write_text(AICA_BLANKS_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{rd}" "{d / "blanks.c"}" /Fe:blanks.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            run = subprocess.run([str(d / "blanks.exe")], capture_output=True, text=True, timeout=60)
            # frames over 60,000 blanks (1001 s), the fewest and most in a blank, blanks a 10,632-sector track takes
            self.assertEqual((run.returncode, run.stdout.split()), (0, ["44144100", "735", "736", "8498"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_heap_console_layout(self):
        """runtime/heap.c lays blocks out as the console's syMalloc: whole 32-byte units with a {next, units} header,
        taken from the top of a free block, so the two words past an aligned allocator's 32-byte buffer are the block
        above's {stale next, units} (the console's 0, 0x1678 that a sprite clip test reads); frees merge back into
        one block, and a second or a stray free is refused."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "layout.c").write_text(HEAP_LAYOUT_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{rd}" "{d / "layout.c"}" "{KIT / "runtime" / "heap.c"}" /Fe:layout.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            run = subprocess.run([str(d / "layout.exe")], capture_output=True, text=True, timeout=60)
            # x, q (units from the base), q's units, the words past q's buffer, three frees, x's free, the whole block
            self.assertEqual((run.returncode, run.stdout.split()), (0, ["224", "221", "3", "0", "33", "1", "0", "0", "1", "1", "0"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_shc_long(self):
        """sdk/shc_long.c compiles at the port's flags with zero warnings and gives the original helpers' results on
        the edge cases: a zero divisor, INT_MIN / -1, overlapping moves in each helper's order, size 0."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(SHC_GAME_H, encoding="utf-8")
            (d / "shc_main.c").write_text(SHC_MAIN_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "shc_long.c", d / "shc_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{rd}" {srcs} /Fe:shc.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "shc.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual((run.returncode, run.stdout.split()), (0, ["1"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_kamui2_line_mode(self):
        """sdk/kamui2_misc.c compiles at the port's flags with zero warnings; kmSetDisplayMode calls the line-mode
        callback with {mode, 0} only when mode bits 0x42 are both set, so an NTSC mode keeps 480 lines whatever the
        callback picks; line mode 4 is 496 + 4 x 16 = 560 lines; a later NTSC mode goes back to 480."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(K2_GAME_H, encoding="utf-8")
            (d / "k2_main.c").write_text(K2_MAIN_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "kamui2_misc.c", d / "k2_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "sdk"}" /I"{KIT / "pvr"}" /I"{rd}" {srcs} /Fe:k2.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "k2.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stdout.split("\n")[:7], ["0 0 480", "0 0 480", "0 0 480", "1 42 560", "1 43 560", "0 0 480", "480"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fm_mng(self):
        """sdk/fm_mng.c compiles at the port's flags with zero warnings and gives the flash library's results: a stock
        console's empty user partition (an unwritten block reads as 0xff), a --flash image's copies, queued requests,
        a damaged copy, a full partition's write back, Format and the mode checks."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(FM_GAME_H, encoding="utf-8")
            (d / "fm_main.c").write_text(FM_MAIN_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "fm_mng.c", d / "fm_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{rd}" {srcs} /Fe:fm.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            for args in ([], ["image"]):
                run = subprocess.run([str(d / "fm.exe")] + args, capture_output=True, text=True, timeout=60)
                self.assertEqual((run.returncode, run.stdout.split()), (0, ["1"]), args)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_lib_ctx(self):
        """sdk/lib_ctx.c (with runtime/coro.c) compiles at the port's flags with zero warnings and runs contexts as the
        library does: fresh ones as Lib_InitContext makes them, switches in program order, an entry that returns
        starting over, each context's own SR, a switch from one context straight to another, an entry the port has
        no function for returning at once (logged once), and a context never made ending the run."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(LIB_GAME_H, encoding="utf-8")
            (d / "lib_main.c").write_text(LIB_MAIN_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "lib_ctx.c", KIT / "runtime" / "coro.c", d / "lib_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{rd}" {srcs} /Fe:lib.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "lib.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual((run.returncode, run.stdout.split()), (0, ["a6mAma6mbAmx", "1", "1", "1"]))
            run = subprocess.run([str(d / "lib.exe"), "never"], capture_output=True, text=True, timeout=60)
            self.assertEqual((run.returncode, run.stdout.split()[:2]), (0, ["exit", "1"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_host_w4_wx(self):
        """The C++ host files compile at the port's C++ flags with zero warnings. Those that include SDL3 need the
        game build's vcpkg headers (manifest mode), so the build checks them, as it does runtime/gfx."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            files = [p for p in sorted((KIT / "host").glob("*.cpp")) if "<SDL3/" not in p.read_text(encoding="utf-8")]
            self.assertTrue(files)
            srcs = " ".join(f'"{p}"' for p in files)
            cl = (f'cl /nologo /c /std:c++20 /W4 /WX /permissive- /utf-8 /EHsc /arch:SSE2 /DNOMINMAX '
                  f'/DWIN32_LEAN_AND_MEAN /O2 /I"{KIT / "host"}" {srcs}')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_pointer_adapter_runs(self):
        """Two adapters compile at the port's flags with zero warnings, and a positional pointer call through each
        reaches the function with its own registers: (r4, r6) of 1, 2, 3, 4 and (r5, r4) of 5, 6."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            skip = PointerAdapters.adapter("void Fx_Uv(u32 dst, u32 normal)", ["r4", "r6"], "Fx_Uv")[1]
            swap = PointerAdapters.adapter("u32 Fx_Swap(s32 a, u32 b)", ["r5", "r4"], "Fx_Swap")[1].replace(
                "pa_8c011100", "pa_8c011200")
            (d / "pa.c").write_text(PA_C % (skip, swap), encoding="utf-8")
            cl = f'cl /nologo /TC /std:c11 /W4 /WX /O1 /arch:SSE2 "{d / "pa.c"}" /Fe:pa.exe'
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            run = subprocess.run([str(d / "pa.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual((run.returncode, run.stdout.split()), (0, ["1", "3", "65"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class BuFileImageGate(unittest.TestCase):
    """sdk/bu_vmu.c's file image functions take the game's BUS_BACKUPFILEHEADER: port_config.h turns them on only
    when the game's header declares it (a game without it, which never calls them, still builds)."""
    CFG = {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
           "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}

    def test_gate(self):
        self.assertIn("#define PORT_BU_FILE_IMAGE 1", game_config.port_config_h(self.CFG, (), True))
        self.assertNotIn("PORT_BU_FILE_IMAGE", game_config.port_config_h(self.CFG))
        src = (KIT / "sdk" / "bu_vmu.c").read_text(encoding="utf-8")
        self.assertIn('#include "port_config.h"', src)
        gated = src[src.index("#ifdef PORT_BU_FILE_IMAGE"):]
        self.assertIn("buMakeBackupFileImage", gated)
        self.assertIn("buAnalyzeBackupFileImage", gated)


if __name__ == "__main__":
    unittest.main()
