"""The Xbox 360 kit's command buffers keep only what changed and give back what the game abandons (runtime/xb_d3d_cb.h).

- cb_delta: a recorded draw keeps the 16-byte vectors of its constant copy that differ from the draw before, not a full ~9 KB copy (cb_snap). A run
  rebuilds each draw's copy in order from the buffer's baseline (cb_delta_start, cb_delta_apply). The check: for a recording with constant writes of
  every kind between draws (single words, runs of vectors, fetch constants, vectors put back to the baseline, no change at all), the rebuilt copy of
  every draw is byte for byte the copy cb_snap kept, with the device changed again before the run, and with XB_SNAP_MASK=diff too.
  XB_CB_DELTA=0 gives the full copies.
- cb_reclaim: a buffer neither recorded nor run for CB_IDLE_SWAPS whose guest word changed since BeginCommandBuffer is retired; an untouched idle one,
  a running one and one run every frame are kept; a run of a reclaimed one is counted; a buffer begun inside a kept one's header retires it.
  XB_CB_RECLAIM=0 keeps all, =idle reclaims on idleness alone.

No game data: the functions are cut from the kit's header and compiled with a stand-in guest memory, as tests/test_x360_kit_pool.py does.
Nearest existing: tests/test_x360_kit_pool.py - checks where the chunks come from and go, not the delta store or the reclaim of abandoned buffers."""
import os
import re
import shutil
import subprocess
import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402
from test_x360_kit_pool import COMMON, RT, between, body  # noqa: E402

DECLS = r'''
static volatile LONG s_cb_end_n, s_cb_run_done, s_cb_recording;
static unsigned s_defer_n;
static u32 s_kit_out, s_kit_pooled;
static unsigned res_live(void) { return 0; }
static int guest_readable(u32 a, u32 n) { return a + n <= 0x8000000u; }
CUT
#define DEV 0x10000u
static u32 seed = 1;
static u32 rnd(void) { seed = seed * 1103515245u + 12345u; return seed >> 8; }
'''

DELTA_MAIN = r'''
static void poke(u32 base)   /* what the game does to the device between draws: inline words, a setter's run of vectors, fetch constants, put back */
{
    unsigned kind = rnd() % 5, r = rnd() % 512, n, k;
    u32 v = rnd();
    switch (kind) {
    case 0: memcpy(tc_base + DEV + 0x780u + 16u * r + 4u * (rnd() % 4), &v, 4); break;
    case 1: memcpy(tc_base + DEV + 0x780u + 16u * r, tc_base + base + 0x300u + 16u * r, 16); break;
    case 2: memcpy(tc_base + DEV + 0x480u + 4u * (rnd() % 192), &v, 4); break;
    case 3:
        n = 1 + rnd() % 8;
        if (r + n > 512) n = 512 - r;
        for (k = 0; k < n * 4; k++) v = rnd(), memcpy(tc_base + DEV + 0x780u + 16u * r + 4u * k, &v, 4);
        break;
    default: break;   /* nothing: this draw's copy is the one before */
    }
}
int main(void)
{
    static u32 full[300], mark[300];
    unsigned rec, d, i, bad, draws = 300;
    u32 scratch;
    tc_base = (unsigned char *)calloc(1, 0x8000000u);
    for (i = 0; i < CB_SNAP_SIZE; i += 4) { u32 v = rnd() % 3 ? 0 : rnd(); memcpy(tc_base + DEV + CB_SNAP_AT + i, &v, 4); }
    scratch = xb_alloc(0x10000u);
    for (rec = 0; rec < 3; rec++) {   /* three recordings of the same two buffers: the second and third reuse the chunks */
        CbList *A = cb_begin(0x7000040u, DEV), *B = cb_begin(0x7100080u, DEV);   /* A full copies (cb_snap), B deltas (cb_delta), same device */
        for (d = 0; d < draws; d++) {
            unsigned np = rnd() % 6;
            for (i = 0; i < np; i++) poke(B->baseg);
            full[d] = cb_snap(A, DEV);
            mark[d] = cb_delta(B, DEV);
        }
        cb_end(B), cb_end(A);
        for (i = 0; i < 50; i++) poke(B->baseg);   /* the recorder goes on with the next frame before the run: the run must not read the device */
        cb_delta_start(B, scratch);
        for (d = bad = 0; d < draws; d++) {
            cb_delta_apply(B, mark[d], scratch);
            bad += mark[d] == 0 || memcmp(tc_base + scratch, tc_base + full[d] + CB_SNAP_AT, CB_SNAP_SIZE + 64u) != 0;
        }
        printf("recording %u: draws %u differing %u full %u KB delta %u KB chunks %u\n", rec, draws, bad, draws * (CB_SNAP_SIZE + 64u) >> 10, B->dbytes >> 10, B->dnchunk);
    }
    return 0;
}
'''

RECLAIM_MAIN = r'''
#define X 0x7200000u   /* abandoned, its memory used again */
#define Y 0x7300000u   /* abandoned, its memory untouched */
#define Z 0x7400000u   /* a run under way */
#define W 0x7500000u   /* recorded once, run every frame, its word changes */
static void rec(u32 cb) { CbList *l = cb_begin(cb, DEV); tc_base[DEV + 0x780u] ^= 1; cb_delta(l, DEV); cb_end(l); }
static int has(u32 cb) { unsigned i; for (i = 0; i < CB_LISTS; i++) if (s_cb[i].cb == cb) return 1; return 0; }
int main(void)
{
    CbList *z, *r;
    unsigned i;
    tc_base = (unsigned char *)calloc(1, 0x8000000u);
    wr32(X, 0x11110001u), wr32(Y, 0x11110001u), wr32(Z, 0x11110001u), wr32(W, 0x11110001u);
    rec(X), rec(Y), rec(Z), rec(W);
    z = cb_list(Z, 2);
    wr32(X, 0xdeadbeefu), wr32(W, 0xdeadbeefu);
    for (i = 0; i < 200; i++) { CbList *w = cb_list(W, 2); cb_run_end(w); cb_reclaim(); }
    printf("after 200 swaps: X %d Y %d Z %d W %d reclaimed %u kept %u\n", has(X), has(Y), has(Z), has(W), s_cb_reclaimed, s_cb_idle_kept);
    r = cb_list(X, 2), cb_run_end(r);
    printf("runs of reclaimed %u\n", s_cb_lost_runs);
    rec(Y + 8u);
    printf("overlap: Y %d new %d reclaimed %u\n", has(Y), has(Y + 8u), s_cb_reclaimed);
    cb_run_end(z);
    return 0;
}
'''


def cut():
    src = between("xb_d3d_cb.h", "typedef struct { u32 entry, snap", "/* The calls that only record into the buffer")
    for sig in ("static u32 cb_snap(CbList *l, u32 dev)", "static u32 cb_delta(CbList *l, u32 dev)", "static void cb_delta_start(const CbList *l, u32 scratch)",
                "static void cb_delta_apply(const CbList *l, u32 at, u32 scratch)", "static void cb_reclaim(void)"):
        src += body("xb_d3d_cb.h", sig)
    return src


def test_wired():
    """cb_record picks the delta store when on; a run rebuilds the copies; Swap sweeps (any machine)"""
    rec = body("xb_d3d_cb.h", "static int cb_record(")
    assert "cb_delta_on() && (target == 0x829fcfc0 || target == 0x821d18f0 || target == 0x821d12f0) ? cb_delta(l, (u32)a->r[0])" in rec
    run = body("xb_d3d_cb.h", "static int cb_entry(")
    assert "cb_delta_start(l, scratch);" in run and "cb_delta_apply(l, snap, scratch), snap = scratch - CB_SNAP_AT;" in run
    assert run.index("cb_delta_start(l, scratch);") < run.index("t_replay++;")
    swap = (RT / "xb_d3d_draw.h").read_text(encoding="utf-8")
    assert swap.index("kit_census(s_frame);") < swap.index("cb_reclaim();")
    census = body("xb_d3d_cb.h", "static void kit_census(unsigned frame)")
    assert "kit cb:" in census and "constant deltas" in census and "reclaimed" in census
    end = body("xb_d3d_cb.h", "static void cb_end(CbList *l)")
    assert "!l->running && l->dnchunk > keep" in end   # host chunks a run may be reading are never freed under it


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Compiled(unittest.TestCase):
    def run_c(self, main, env_extra):
        d = tmpdir("x360cd_")
        try:
            (d / "t.c").write_text(COMMON + DECLS.replace("CUT", cut()) + main, encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            env = dict(os.environ)
            for k in ("XB_CB_POOL", "XB_SNAP_MASK", "XB_CB_DELTA", "XB_CB_RECLAIM", "XB_MEMLOG"):
                env.pop(k, None)
            env.update(env_extra)
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def check_exact(self, got):
        for rec in range(3):
            m = re.match(r"recording (\d+): draws 300 differing (\d+) full (\d+) KB delta (\d+) KB chunks (\d+)$", got[rec])
            self.assertTrue(m, got)
            self.assertEqual(int(m.group(2)), 0, got[rec])               # every draw's rebuilt copy is cb_snap's, byte for byte
            self.assertLess(int(m.group(4)) * 10, int(m.group(3)), got[rec])   # and a tenth of the full copies or less

    def test_delta_matches_full_copies(self):
        self.check_exact(self.run_c(DELTA_MAIN, {}))

    def test_delta_matches_with_diff_mask(self):
        self.check_exact(self.run_c(DELTA_MAIN, {"XB_SNAP_MASK": "diff"}))

    def test_reclaim(self):
        got = self.run_c(RECLAIM_MAIN, {})
        self.assertEqual(got[0], "after 200 swaps: X 0 Y 1 Z 1 W 1 reclaimed 1 kept 1")
        self.assertEqual(got[1], "runs of reclaimed 1")
        self.assertEqual(got[2], "overlap: Y 0 new 1 reclaimed 2")

    def test_reclaim_off(self):
        got = self.run_c(RECLAIM_MAIN, {"XB_CB_RECLAIM": "0"})
        self.assertEqual(got[0], "after 200 swaps: X 1 Y 1 Z 1 W 1 reclaimed 0 kept 0")
        self.assertEqual(got[2], "overlap: Y 1 new 1 reclaimed 0")

    def test_reclaim_idle_only(self):
        got = self.run_c(RECLAIM_MAIN, {"XB_CB_RECLAIM": "idle"})
        self.assertEqual(got[0], "after 200 swaps: X 0 Y 0 Z 1 W 1 reclaimed 2 kept 0")
