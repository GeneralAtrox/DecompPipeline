/* In-between frames over a framebuffer the CPU wrote into (sat_inb.c with sat_vdp.c, both included whole for their static
 * functions). Dragon Force's NOW LOADING: the game writes the text into VDP1's framebuffer itself and its list draws
 * nothing, so an in-between frame, which erases and draws the list again, showed black twice in every vertical blank.
 * Programs FBCR and writes the framebuffer as the game does, with a stand-in for each kit piece around the two files.
 * Run by tests/test_sat_inb.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include "sat_vdp.c"
#include "sat_inb.c"

static u32 g_draws, g_vb;
static int g_fail;

/* a stand-in for each kit function the two files call */
void sat_v1_draw(void) { g_draws++; put(sat_v1_draw_fb(), 2u, 2, 0x8000u | g_draws); }
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
void sat_wide_save(const u8 *guest, u8 *save) { (void)guest, (void)save; }
void sat_wide_restore(u8 *guest, const u8 *save) { (void)guest, (void)save; }
void sat_wide_post(const u32 *px, u32 w, u32 h, u32 vb) { (void)px, (void)w, (void)h, (void)vb; }
void sat_text_dirty(const u8 *guest, int cpu) { (void)guest, (void)cpu; } /* clearer text (sat_text.c) is not built */
void sat_text_held(const u32 *held) { (void)held; }
void sat_text_save(const u8 *guest) { (void)guest; }
void sat_text_restore(const u8 *guest) { (void)guest; }
int sat_png_write(const char *path, const u32 *rgb, u32 w, u32 h) { (void)path, (void)rgb, (void)w, (void)h; return 0; }
u32 sat_frame[SAT_MAX_W * SAT_MAX_H];
u32 sat_frame_w = 320, sat_frame_h = 240;
void sat_compose(void) {}
unsigned cpu_present_fps(void) { return 144u; }
int cpu_present_threaded(void) { return 1; }
int sat_video_headless(void) { return 0; }
int host_fixed_clock(void) { return 0; }
int sat_net_on(void) { return 0; }
int sat_sys_fast(void) { return 0; }
int sat_sys_det(void) { return 0; }
u32 sat_sys_hz(void) { return 60u; }
int sat_screen_on(void) { return 0; }
u32 sat_sys_speed(void) { return 1u; }
long long sat_sys_now(void) { LARGE_INTEGER t; QueryPerformanceCounter(&t); return t.QuadPart; }
void sat_perf_wait_begin(void) {}
void sat_perf_wait_end(void) {}

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

/* one vertical blank as the kit runs it: the picture on show is composed (sat_video.c), then the frame change */
static void blank(u32 fbcr)
{
    g_vb++;
    if (fbcr) v1reg_w(0x05d00002u, 2, fbcr);
    sat_inb_vblank();
    sat_inb_post();
    v1_frame_change();
}

int main(void)
{
    put(sat_v1_reg, 6, 2, 0x8000u);  /* EWDR: black */
    put(sat_v1_reg, 10, 2, 0x50efu); /* EWRR: the whole 320x240 picture */
    v1reg_w(0x05d00004u, 2, 2u);     /* PTMR 2: the list is drawn at a change */
    for (int i = 0; i < 4; i++) blank(3u);
    check(sat_inb_due() != 0, "pictures VDP1 drew from the list: in-between frames are due");

    /* NOW LOADING: the text written into the framebuffer being drawn into, which the next change puts on show */
    for (u32 o = 0x1000u; o < 0x1100u; o += 2u) v1fb_w(0x05c80000u + o, 2, 0x7fffu);
    blank(3u);
    blank(0u); /* the game is busy loading: no change asked, the written picture stays on show */
    blank(0u);
    check(sat_inb_due() == 0, "a picture the CPU wrote into: no in-between frame redraws it from its list alone");
    check(get(sat_v1_display_fb(), 0x1000u, 2) == 0x7fffu, "the written picture is the one on show");

    /* the next pictures VDP1 draws from the list alone get in-betweens again */
    for (int i = 0; i < 3; i++) blank(3u);
    check(sat_inb_due() != 0, "list-drawn pictures after it: in-between frames again");

    puts(g_fail ? "failed" : "all ok");
    return g_fail;
}
