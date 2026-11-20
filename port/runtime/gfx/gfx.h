/* The Dreamcast's graphics, on the base's presenter (base/present.h: the device, the swap chain, the frame target). The
 * game-facing calls (gfx_clear, gfx_present) are in platform.h; this header is for the kit's own C files. */
#ifndef GFX_H
#define GFX_H

#include "present.h"
#include "port.h"

/* gfx_vmu.c: the VMU screen (host.h has the mode calls) */
int gfx_vmu_option(const char *arg, const char *value);
void gfx_vmu_draw(void *back_buffer); /* an ID3D11Resource: the panels over it, after the frame */
void gfx_vmu_release(void);

/* gfx_movie.c: a movie's frames over the picture (media.h has the calls that hand them over) */
int gfx_movie_on(void);   /* a movie is laid over the picture now */
void gfx_movie_draw(void); /* into the frame, after the renderer, before it is dumped or shown */
void gfx_movie_lay(void);  /* the current picture into the frame, without choosing a newer one */
void gfx_movie_check_shown(void); /* --movie-check on: a frame about to be shown while a movie is on, read back */
void gfx_movie_release(void);

/* gfx_present.c: --border-edges on|off (game.json border_edges): the bars beside the screen take its edge pixels
 * (base/present.h gfx_carry_edges) after a frame that drew nothing in them. With a movie on, the bars are black instead (base/present.h
 * gfx_black_bars) */
int gfx_edges_on(void);

/* pvr_draw.c: the scene drawn last drew nothing past the screen (in a wider or taller frame) */
extern int g_pvr_framed;

/* gfx_capture.c */
int gfx_capture_option(const char *arg, const char *value);
void gfx_capture_frame(u32 frame); /* writes the frame's PNGs if --dump-frames or --dump-every asks for it */
int gfx_capture_bmp(const char *path); /* the frame target as a 24-bit BMP; 0 on failure */

/* pvr/ta_parse.c: the renderer's own options (--ta-dump SCENE) */
int pvr_option(const char *arg, const char *value);

#endif
