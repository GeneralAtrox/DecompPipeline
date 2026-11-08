/* VDP1's framebuffer change (sat_vdp.c, included whole for its static functions): programs FBCR, PTMR and TVMR as a game
 * does, with a stand-in for the draw that adds one mark per list run. Run by tests/test_sat_vdp_fb.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include "sat_vdp.c"

static u32 g_draws, g_vb;
static int g_fail;

/* a stand-in for each kit function sat_vdp.c calls */
void sat_v1_draw(void) { g_draws++; put(sat_v1_draw_fb(), g_draws * 2u, 2, 0x8000u | g_draws); }
void sat_scu_raise(u32 n) { (void)n; }
void sat_perf_vblank(void) {}
void sat_sample(void) {}
void sat_vblank_counted(void) {}
void sat_frame_limit_check(void) {}
void sat_present(void) {}
void sat_cd_frame(void) {}
void sat_part_frame(void) {}
void sat_scsp_frame(void) {}
void sat_irq_poll(void) {}
void sat_where(void) {}
void sat_v2_hist_report(void) {}
u32 sat_vblank_count(void) { return g_vb; }
u32 sat_scanline(u32 total, u32 *along) { (void)total; *along = 0; return 0; }
u32 sat_peek32(u32 a) { (void)a; return 0; }
void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr) { (void)lo, (void)hi, (void)name, (void)rd, (void)wr; }
void port_log(const char *fmt, ...) { (void)fmt; }
void sat_wide_erase(const u8 *guest, u32 data, u32 x0, u32 y0, u32 x1, u32 y1) { (void)guest, (void)data, (void)x0, (void)y0, (void)x1, (void)y1; }
void sat_wide_cpu(const u8 *guest, u32 o, u32 size, u32 v) { (void)guest, (void)o, (void)size, (void)v; }

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

static void reset(u32 ptmr)
{
    memset(sat_v1_fb, 0, sizeof sat_v1_fb);
    memset(sat_v1_reg, 0, sizeof sat_v1_reg);
    g_fb_swapped = g_manual_pending = g_erase_armed = g_v1_draw_end = 0;
    g_draws = 0;
    put(sat_v1_reg, 10, 2, 0x58ffu); /* EWRR as Robo Pit: the whole picture; EWDR 0 */
    v1reg_w(0x05d00004u, 2, ptmr);
}

static u32 marks(const u8 *fb)
{
    u32 n = 0;
    for (u32 i = 1; i < 200u; i++) n += get(fb, i * 2u, 2) != 0;
    return n;
}

static void fbcr(u32 v) { v1reg_w(0x05d00002u, 2, v); }

int main(void)
{
    /* one-cycle mode: a new picture every frame, one list's marks in it */
    reset(2);
    for (int i = 0; i < 8; i++) {
        fbcr(0);
        v1_frame_change();
    }
    check(g_draws == 8, "FBCR 0: the list runs every frame");
    check(marks(sat_v1_display_fb()) == 1 && marks(sat_v1_draw_fb()) == 1, "FBCR 0: both framebuffers hold one list's marks");

    /* SGL's frame of two vertical blanks (Robo Pit's fights: fn_060811ea writes 2, then 3) */
    reset(2);
    {
        int shown_ok = 1, extra = 0;
        const u8 *was = NULL;
        for (int i = 0; i < 16; i++) {
            fbcr(i & 1 ? 3u : 2u);
            v1_frame_change();
            if (i & 1) was = sat_v1_display_fb();
            else if (was && sat_v1_display_fb() != was) extra = 1;
            if (i > 2 && marks(sat_v1_display_fb()) > 1) shown_ok = 0;
            if (i > 2 && marks(sat_v1_draw_fb()) > 1) shown_ok = 0;
        }
        check(g_draws == 8, "FBCR 2,3: the list runs once per pair, at the swap");
        check(!extra, "FBCR 2 does not swap");
        check(shown_ok, "FBCR 2,3: neither framebuffer holds more than one list's marks: nothing piles up");
    }

    /* an erase asked for by FBCR 2 keeps the picture on show until the picture after it */
    reset(2);
    fbcr(0);
    v1_frame_change();
    fbcr(0);
    v1_frame_change(); /* a picture on show */
    fbcr(2);
    v1_frame_change();
    check(marks(sat_v1_display_fb()) == 1, "FBCR 2: the picture stays for the picture after the write");
    v1_frame_change();
    check(marks(sat_v1_display_fb()) == 0, "FBCR 2: then it has been erased behind the beam");

    /* manual with nothing asked: no swap, no draw */
    reset(2);
    fbcr(3);
    v1_frame_change();
    {
        u32 d = g_draws;
        int s = g_fb_swapped;
        for (int i = 0; i < 4; i++) v1_frame_change();
        check(g_draws == d && g_fb_swapped == s, "FBCR 3 left in place: one swap per write");
    }

    /* VBE: the change itself erases the framebuffer on show */
    reset(2);
    v1reg_w(0x05d00000u, 2, TVMR_VBE);
    for (int i = 0; i < 6; i++) {
        fbcr(3);
        v1_frame_change();
    }
    check(marks(sat_v1_draw_fb()) == 1, "VBE with FBCR 3: the framebuffer drawn into starts clean");

    /* CEF stays set from the draw's end until the next draw: a frame with no draw does not clear it */
    reset(2);
    fbcr(3);
    v1_frame_change();
    fbcr(2);
    sat_vblank_in();
    check((v1reg_r(0x05d00010u, 2) & 2u) != 0, "EDSR CEF survives a vertical blank with no draw");

    puts(g_fail ? "failed" : "all ok");
    return g_fail;
}
