/* Saturn port kit: video memories shared by the VDP files (sat_vdp.c) and the renderer. */
#ifndef SAT_VDP_H
#define SAT_VDP_H

#include "sat.h"

extern u8 sat_v1_vram[0x80000], sat_v1_fb[2][0x40000], sat_v2_vram[0x80000], sat_v2_cram[0x1000];
extern u8 sat_v1_reg[0x20], sat_v2_reg[0x200];

#define SAT_MAX_W 704
#define SAT_MAX_H 256

extern u32 sat_frame[SAT_MAX_W * SAT_MAX_H]; /* the composed picture, 0x00RRGGBB (sat_vdp2.c) */
extern u32 sat_frame_w, sat_frame_h;

void sat_v1_draw(void);         /* VDP1: run the command list in VRAM into the drawing framebuffer (sat_vdp1.c) */
extern u32 sat_v1_dump_every; /* sat_vdp1.c */
extern u32 sat_peek_addr[16];
extern int sat_peek_n; /* sat_vdp.c */
extern u32 sat_vwatch_lo, sat_vwatch_hi, sat_vwatch_vb; /* sat_vdp.c */
extern u32 sat_layer_mask; /* sat_vdp2.c */
int sat_v1_run(u8 *fb);         /* the same into fb; returns the number of commands */
u8 *sat_v1_draw_fb(void);       /* sat_vdp.c: the framebuffer VDP1 draws into / the CPU sees */
const u8 *sat_v1_display_fb(void); /* the one VDP2 shows */
void sat_vdp_report(void);      /* sat_vdp.c: the chips' registers and the first VDP1 commands, to the log */
void sat_v2_hist_report(void);  /* sat_vdp2.c: the colour indexes NBG1 drew, to the log (a test aid) */
void sat_compose(void);        /* sat_vdp2.c: VDP2's picture into sat_frame */
int sat_png_write(const char *path, const u32 *rgb, u32 w, u32 h); /* sat_shot.c */

#endif
