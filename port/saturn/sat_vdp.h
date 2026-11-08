/* Saturn port kit: video memories shared by the VDP files (sat_vdp.c) and the renderer. */
#ifndef SAT_VDP_H
#define SAT_VDP_H

#include "sat.h"

extern u8 sat_v1_vram[0x80000], sat_v1_fb[2][0x40000], sat_v2_vram[0x80000], sat_v2_cram[0x1000];
extern u8 sat_v1_reg[0x20], sat_v2_reg[0x200];

#define SAT_MAX_W 1024 /* 704 dots of high resolution, or a widened 352 (sat_wide.c) */
#define SAT_MAX_H 256

extern u32 sat_frame[SAT_MAX_W * SAT_MAX_H]; /* the composed picture, 0x00RRGGBB (sat_vdp2.c) */
extern u32 sat_frame_w, sat_frame_h;

void sat_v1_draw(void);         /* VDP1: run the command list in VRAM into the drawing framebuffer (sat_vdp1.c) */
extern u32 sat_v1_dump_every; /* sat_vdp1_list.c */
extern int sat_v1_ground_shadows; /* sat_vdp1_list.c: --ground-shadows (1 under, 0 the game's order, -1 not set) */
void sat_v1_begin(u8 *fb);      /* sat_vdp1.c: the list's state at its top */
void sat_v1_cmd(u32 cmd, u32 ctrl); /* sat_vdp1.c: one command */
int sat_v1_first_band(void);       /* sat_vdp1.c: the band walking the list is the first (the one that logs) */
void sat_v1_bands_set(int on);     /* --v1-bands on|off: VDP1 drawn in the bands VDP2 uses (on by default) */
extern u32 sat_peek_addr[16];
extern int sat_peek_n; /* sat_vdp.c */
extern u32 sat_vwatch_lo, sat_vwatch_hi, sat_vwatch_vb; /* sat_vdp.c */
extern u32 sat_layer_mask; /* sat_vdp2.c */
int sat_v1_run(u8 *fb);         /* sat_vdp1_list.c: the same into fb; returns the number of commands */
u8 *sat_v1_draw_fb(void);       /* sat_vdp.c: the framebuffer VDP1 draws into / the CPU sees */
const u8 *sat_v1_display_fb(void); /* the one VDP2 shows */
void sat_vdp_report(void);      /* sat_vdp.c: the chips' registers and the first VDP1 commands, to the log */
void sat_v2_hist_report(void);  /* sat_vdp2.c: the colour indexes NBG1 drew, to the log (a test aid) */
void sat_v2_dump_regs(void);    /* sat_vdp.c: --vwatch 25f80000..: the non-zero VDP2 registers, to the log (a test aid) */
void sat_compose(void);
/* sat_inb.c: in-between frames (--in-between on|off) */
extern int sat_inb_on;
int sat_inb_live(void);
int sat_inb_drawing(void);        /* 1 inside one: nothing is recorded or logged there */
void sat_inb_drawn(const u8 *fb); /* sat_vdp.c: VDP1 drew its list into fb */
void sat_inb_vblank(void);        /* sat_video.c: before a picture is composed */
void sat_inb_post(void);          /* sat_video.c: the picture composed, to be shown one blank later */
void sat_inb_report(void);
void sat_inb_shot_option(const char *v); /* --in-between-shot VB,PREFIX */
void sat_v1_fb_flip(void);        /* sat_vdp.c: the framebuffers trade places (an in-between frame draws into the shown one) */
void sat_v1_erase_draw(void);     /* sat_vdp.c: the erase over the framebuffer VDP1 draws into */        /* sat_vdp2.c: VDP2's picture into sat_frame */
int sat_png_write(const char *path, const u32 *rgb, u32 w, u32 h); /* sat_shot.c */
/* sat_wide.c: the picture wider than the screen (game.json "widescreen"); ox 0 leaves every path as the Saturn's */
extern int sat_wide_ox, sat_wide_now; /* the blank's dots added each side; the picture being composed's */
extern u32 sat_wide_sw;               /* the screen width they widen */
void sat_wide_frame(void);            /* sat_video.c: before each blank's picture */
u8 *sat_wide_fb(const u8 *guest);     /* a VDP1 framebuffer's wide twin, NULL when not widened */
int sat_wide_fb_ox(const u8 *fb);     /* fb is a twin: its x offset, else 0 */
void sat_wide_back(u8 *guest);        /* the twin's middle back into the guest's framebuffer */
void sat_wide_erase(const u8 *guest, u32 data, u32 x0, u32 y0, u32 x1, u32 y1);
void sat_wide_cpu(const u8 *guest, u32 o, u32 size, u32 v);
void sat_wide_save(const u8 *guest, u8 *save);
void sat_wide_restore(u8 *guest, const u8 *save);
u32 sat_wide_width(u32 w);            /* sat_vdp2.c: the picture's width for a screen w wide */
const u8 *sat_wide_spr(const u8 *guest, u32 *shift, u32 *ox, u32 *mask);
void sat_wide_post(const u32 *px, u32 w, u32 h, u32 vb); /* to the presenter, with its screen's width */
void sat_wide_seen(int black);        /* sat_video.c: the picture just composed, for the log's "shown" lines */

/* one VDP2 scroll plane's registers as the composer reads them (sat_vdp2.c). An NBG's map is 2x2 planes (maps 2); RBG0's
 * is 4x4 (maps 4, map[] holds 16), filled by sat_vdp2_rot.c */
typedef struct {
    int on, bitmap, charpx, pn1w, cnsm, cc, tpon, prio, ccen, ccratio, plw, plh, bmw, bmh, craoff, splt, scn, bmpal, maps;
    u32 map[16], bmbase, scx, scy, zx, zy;
} SatPlane;
typedef struct {
    long long xsp, ysp, xp, yp, dxr, dyr; /* the line's: each band of the compose keeps its own */
    u32 line;
} SatRotLine;
void sat_rbg0_setup(SatPlane *g); /* sat_vdp2_rot.c: RBG0's registers and rotation parameter A, once a frame */
int sat_rbg0_row(SatRotLine *l, u32 y, u32 x0, u32 n, u32 *px, u32 *py, u8 *ok); /* the map dots RBG0 shows on line y from screen x x0 */

/* one picture line's windows and scroll (sat_vdp2_win.c): each window's dots on the line (x - xs <= xw, unsigned; none:
 * xs 2^31, which no dot reaches), per layer (sprite, NBG0..NBG3, RBG0) whether it is hidden for (in W0) | (in W1) << 1, and each plane's scroll
 * and X step there, which line scroll may set. Each band of the compose keeps its own. */
typedef struct {
    u32 xs[2], xw[2], scx[5], scy[5], zx[5];
    u8 tab[6][4], on[6];
} SatWinLine;
void sat_win_setup(void); /* the frame's window and line scroll registers, once a frame */
void sat_win_line(SatWinLine *l, u32 y, const SatPlane *planes);
int sat_win_span(const SatWinLine *l, int layer, u32 x0, u32 n, int *each); /* hidden at all n dots from x0; *each: hidden at some */
static __inline int sat_win_hide(const SatWinLine *l, int layer, u32 x)
{
    return l->on[layer] && l->tab[layer][(x - l->xs[0] <= l->xw[0]) | ((x - l->xs[1] <= l->xw[1]) << 1)];
}

#endif
