"""The fast store-queue helpers (port/tools/tx_sqfast.py) against the judged ones (sqmodel.SQ_HELPERS, and
sq_callee_helpers for a function whose mocked callees store into the queues): the same random stores, callee marks and
sends through both, compiled with MSVC, must leave the same queues and RAM. Planted faults must be caught.

Nearest existing: tests/test_host_pace.py - the same compile-and-print harness; it covers the pacing and the spin filter.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL / "port" / "tools"))
sys.path.insert(0, str(TOOL / "targets" / "dreamcast_sh4" / "translator"))
import game_config  # noqa: E402
import tx_sqfast  # noqa: E402
from sh4translate.sqmodel import SQ_HELPERS, sq_callee_helpers  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

MODEL_C = r"""#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef float f32;
#define RAM_SIZE 0x01000000u
static u8 *ram;
#define IN_RAM(a) ((a) - 0x8c000000u < RAM_SIZE)
static u8 rd8(u32 a) { return IN_RAM(a) ? ram[a - 0x8c000000u] : 0; }
static void wr8(u32 a, u8 v) { if (IN_RAM(a)) ram[a - 0x8c000000u] = v; }
static void wr16(u32 a, u16 v) { wr8(a, (u8)v); wr8(a + 1u, (u8)(v >> 8)); }
static void wr32(u32 a, u32 v) { wr16(a, (u16)v); wr16(a + 2u, (u16)(v >> 16)); }
static void wrf32(u32 a, f32 v) { union { u32 u; f32 f; } b; b.f = v; wr32(a, b.u); }
#define SQ_HOST(a) ((u32 *)(ram + ((a) - 0x8c000000u)))
@HELPERS@
static SqQueues queues;
u8 *@NAME@_ram(void) { return ram; }
SqQueues *@NAME@_q(void) { return &queues; }
size_t @NAME@_qsize(void) { return sizeof queues; }
void @NAME@_op(u32 op, u32 a, u32 v)
{
    union { u32 u; f32 f; } b;
    if (!ram) { ram = calloc(RAM_SIZE, 1); sq_start(&queues, 0u); }
    switch (op) {
    case 0: sq_wr8(&queues, a, (u8)v); break;
    case 1: sq_wr16(&queues, a, (u16)v); break;
    case 2: sq_wr32(&queues, a, v); break;
    case 3: b.u = v & 0x7f7fffffu; sq_wrf32(&queues, a, b.f); break;
    case 4: sq_pref(&queues, a); break;
    case 5: sq_start(&queues, a); break;
#ifdef CALLEE
    case 7: { unsigned long long lo = (unsigned long long)v * 0x9e3779b97f4a7c15ull; sq_mark(&queues, a, lo & (lo >> 3), lo >> 1); } break;
#endif
    default: sq_moved(&queues, a, a + (v & 0x1ffu)); break;
    }
}
"""

DRIVER_C = r"""#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef uint8_t u8; typedef uint32_t u32;
u8 *judged_ram(void); u8 *fast_ram(void); void *judged_q(void); void *fast_q(void); size_t judged_qsize(void);
void judged_op(u32, u32, u32); void fast_op(u32, u32, u32);
static u32 state = 0x12345678u;
static u32 rnd(void) { state ^= state << 13; state ^= state >> 17; state ^= state << 5; return state; }
/* near the two ends of the window, in the middle, a few blocks so a queue often holds another block; and outside it */
static u32 address(void)
{
    u32 pick = rnd() % 16u, off = rnd() % 0x200u;
    if (pick < 11u) return 0xe0001000u + off;
    if (pick == 11u) return 0xe0000000u + off;
    if (pick == 12u) return 0xe4000000u - 0x100u + off;
    if (pick == 13u) return 0xe0000000u - 0x10u + off % 32u;
    if (pick == 14u) return 0x8c001000u + off;
    return 0xe4000000u - 4u + off % 8u;
}
int main(void)
{
#ifdef CALLEE
    static const u32 mix[16] = {2, 2, 2, 2, 3, 3, 3, 7, 4, 4, 4, 0, 1, 7, 5, 6}; /* 7: a mocked callee's bytes */
#else
    static const u32 mix[16] = {2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 0, 1, 2, 5, 6};
#endif
    u32 i;
    for (i = 0; i < 400000u; i++) {
        u32 op = mix[rnd() % 16u], a = address(), v = rnd();
        if (op == 2 || op == 3) a &= rnd() % 8u ? ~3u : ~0u; /* mostly whole words, some across a boundary */
        if (op == 5 && rnd() % 64u) op = 2;
        judged_op(op, a, v);
        fast_op(op, a, v);
        if (memcmp(judged_q(), fast_q(), judged_qsize())) { printf("queues differ at op %u (%u %08x)\n", i, op, a); return 1; }
        if ((i & 1023u) == 0 && memcmp(judged_ram(), fast_ram(), 0x01000000u)) { printf("RAM differs by op %u\n", i); return 1; }
    }
    if (memcmp(judged_ram(), fast_ram(), 0x01000000u)) { printf("RAM differs at the end\n"); return 1; }
    { /* the run must have sent blocks: an idle model would pass */
        u32 n = 0, b;
        for (b = 0; b < 0x01000000u; b++) n += judged_ram()[b] != 0;
        printf("same %u\n", n > 300u);
    }
    return 0;
}
"""


def judged_text(callee=False):
    return "\n".join(sq_callee_helpers() if callee else SQ_HELPERS)


def fast_text(plant=None, callee=False):
    text = tx_sqfast.rewrite(judged_text(callee))
    if plant:
        assert plant[0] in text, plant[0]
        text = text.replace(plant[0], plant[1])
    return text


@unittest.skipUnless(HAVE_VC, NO_VC)
class SqFast(unittest.TestCase):
    def run_pair(self, plant=None, callee=False, fast=None):
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "judged.c").write_text(MODEL_C.replace("@NAME@", "judged").replace("@HELPERS@", judged_text(callee)))
            (d / "fast.c").write_text(MODEL_C.replace("@NAME@", "fast").replace("@HELPERS@", fast or fast_text(plant, callee)))
            (d / "driver.c").write_text(DRIVER_C)
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O2 /W3 {"/DCALLEE " if callee else ""}driver.c judged.c '
                               'fast.c /Fet.exe"', cwd=d, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=120).stdout

    def test_same_as_judged(self):
        self.assertEqual(self.run_pair().strip(), "same 1")

    def test_planted_faults_are_caught(self):
        for plant in (("((1u << n) - 1u) << o", "((1u << n) - 1u)"),  # the wrong bytes marked
                      ("if (nib == 15u) d[w] = g[w];", "if (nib) d[w] = g[w];"),  # a part-filled word sent whole
                      ("else if (from != to) {", "else if (0) {"),  # the rest of the block not carried over
                      ("o + n <= 32u", "1"),  # a store across two blocks taken the short way
                      ("q->marked[k] = 0;\n}", "}")):  # the queue not emptied by a send
            self.assertNotIn("same", self.run_pair(plant), plant)

    def test_callee_marks_same_as_judged(self):
        self.assertEqual(self.run_pair(callee=True).strip(), "same 1")

    def test_callee_planted_faults_are_caught(self):
        plain = judged_text(True)  # the helpers without src, as the tool wrote them before 2026-11-03: Spawn lost its menu bar
        for name, fast in tx_sqfast.FAST.items():
            s, e = tx_sqfast.helper_span(plain, name)
            plain = plain[:s] + fast + plain[e:]
        self.assertNotIn("same", self.run_pair(callee=True, fast=plain))
        for plant in (("for (i = 0; i < n; i++) q->src[k][o + i] = 0;", ""),  # a store leaves a callee's byte in place
                      ("q->src[k][j] != to + j :", "1 :"),  # a send writes over the callee's own byte
                      ("for (j = 0; j < 32u; j++) q->src[k][j] = 0;", "")):  # a send leaves the callee's bytes queued
            self.assertNotIn("same", self.run_pair(plant, callee=True), plant)

    def test_a_file_without_all_helpers_is_not_rewritten(self):
        self.assertIsNone(tx_sqfast.rewrite("static void sq_put(SqQueues *q, u32 a, u32 n, u32 v) { }"))


if __name__ == "__main__":
    unittest.main()
