/* sat_screen.c: a kit screen drawn at twice the Saturn's dot size over the game's board (see its header). Places are
 * half dots of the 4:3 screen: 0..sat_screen_w()-1 across, 0..sat_screen_h()-1 down, sat_screen_side() more each side. */
#ifndef SAT_SCREEN_H
#define SAT_SCREEN_H
#include "sat.h"

void sat_screen_open(void);  /* the screen is up from the next vertical blank: the game's board, then its list */
void sat_screen_close(void); /* the game's picture again */
int sat_screen_on(void);
int sat_screen_ready(void);  /* the board is taken: what VDP1 draws now is not in it */
void sat_screen_clear(void); /* a new list: what the next pictures draw */
int sat_screen_w(void);
int sat_screen_h(void);
int sat_screen_side(void);
void sat_screen_rect(int x, int y, int w, int h, u32 rgb);
void sat_screen_panel(int x, int y, int w, int h, u32 border, u32 inner); /* the menus' dark panel with its two frames */
void sat_screen_cell(int x, int y, u32 word, int scale);                  /* one cell of the game's text plane, as named */
int sat_screen_text(int x, int y, const char *s, u32 slot, int scale);    /* the game's small type (slot | 0x100: red); its width */
int sat_screen_wrap(const char *s, int n, char out[4][32]); /* s in lines of at most n letters (at most 4) */
void sat_screen_image(int x, int y, int w, int h, const u32 *argb, int iw, int ih); /* a picture (top byte 0: none) */
int sat_screen_make(const u32 **px, u32 *w, u32 *h); /* sat_video.c: 1 this blank's picture, -1 not yet, 0 no screen */
void sat_screen_post(u32 vb);                        /* to the presenter */
#endif
