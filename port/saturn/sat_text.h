/* sat_text.c: clearer text. A game names the VDP1 sprites that are lettering (sat_hooks.h `text`); the kit keeps them in
 * the game's framebuffer as ever, composes the shown picture from a copy without them, and draws each one sharp over the
 * picture at the window's resolution (A3 of its own dots, sat_text_a3.c, or the game's own picture). */
#ifndef SAT_TEXT_H
#define SAT_TEXT_H

#include "sat.h"
#include "sat_vdp.h" /* SatPlane (sat_vdp.h includes this file at its end: either order works) */

typedef struct SatTextSprite {
    u32 cmd, comm, pmod, colr, srca; /* the command's address in VDP1 VRAM, its kind (0 normal, 1 scaled, 2 distorted), words */
    int tw, th;                      /* the texture's size */
    int x0, y0, x1, y1;              /* the screen box it is drawn in */
    const u8 *tex;                   /* its texels in VDP1 VRAM (tw * th dots at the colour mode's bits) */
} SatTextSprite;

/* One item of the picture, for a game's own drawing (sat_hooks.h text_image): the dots it showed (0x00RRGGBB, the
 * composed colours) where on[] is set, w x h from the screen dot x, y; tag: what the game's text hook returned. */
typedef struct SatTextItem {
    int x, y, w, h, tag;
    const u32 *dots;
    const u8 *on;
} SatTextItem;

/* VDP2's lettering (sat_text_v2.c): cells of a scroll plane the game's text_cells hook is asked about. One cell (tag 0),
 * or a block of n x rows cells all given tag, side by side and under each other as on the screen. The texels are as shown
 * (flips applied), row-major, tw x th: 4 bits a dot two to a byte (high first) for colour mode 0, a byte for 1, two bytes
 * for 2 and 3, four for 4. The answer is kept for the same texels, form, tag and palette, so it must depend on those alone. */
typedef struct SatTextCells {
    int layer, cc, cs, n, rows, tag; /* NBG0..3, colour mode (0 16 colours .. 4 RGB888), cell size 8 or 16 dots */
    u32 charnum, pal;                /* the first cell's character number and palette (as the composer reads them); a bitmap's
                                      * tile: its top left dot, x | y << 16, and the bitmap's palette */
    int tw, th;
    const u8 *tex;
} SatTextCells;
enum { SAT_V2_ITEMS = 512 };
typedef struct { int x, y, w, h, tag, layer; } SatTextV2Item; /* a block's box on the picture (x from its left edge) */
extern SatTextV2Item sat_text_v2_it[SAT_V2_ITEMS];
extern int sat_text_v2_n;
extern u16 sat_text_v2_map[];  /* each picture dot: the item (1..n) whose box holds it, 0 none */
extern u8 sat_text_v2_layer[]; /* each item's plane */
extern u32 sat_text_v2_bm;     /* the bitmap planes with lettering this picture (bit n: NBGn) */
int sat_text_v2_scan(const SatPlane *planes, const int *act, int nact, u32 w, u32 h, u32 ww, u32 ox); /* sat_vdp2.c, before a
                                 compose: the planes' lettering (1: any) */
u32 sat_text_v2_tag(int layer, u32 charnum, u32 pal, int hf, int vf); /* the composer: this picture's tag for a cell's name */
int sat_text_v2_relog(void); /* --clear-text-log ...@N: once, from N: ask about every cell again */
void sat_text_v2_log(const SatTextCells *c, int tag, u64 key, int x, int y); /* --clear-text-log: a cell or block asked about */

extern int sat_text_on; /* the list being drawn: the game has a text hook and Clearer text is on */
void sat_text_begin(u8 *guest, u8 *target); /* sat_vdp1_list.c: before a list is drawn into target (guest's or its wide twin) */
void sat_text_seen(const SatTextSprite *s);  /* sat_vdp1.c, scanning: one textured sprite */
int sat_text_item(int visit);                /* sat_vdp1.c, drawing: the item for this textured sprite, -1 none */
u16 *sat_text_keep(int item, int after, int *x0, int *y0, int *x1, int *y1); /* its dots before or after it is drawn */
void sat_text_end(void);                     /* sat_vdp1_list.c: after the list: the shown copy */
void sat_text_dirty(const u8 *guest, int cpu); /* sat_vdp.c: the CPU wrote into a framebuffer (cpu 1), or it was erased */
int sat_text_cpu_shown(void);                /* the framebuffer shown now has the CPU's writes since its list (a game's frame hook) */
int sat_text_wanted(void);                   /* Clearer text is on in this run and the game has a text hook */
void sat_text_save(const u8 *guest);         /* sat_inb.c: an in-between frame is drawn into guest; and back */
void sat_text_restore(const u8 *guest);
const u8 *sat_text_spr(const u8 *fb, const u16 **mask, u32 frame_w); /* sat_vdp2.c: the sprite layer to compose */
extern u32 sat_text_with[]; /* sat_vdp2.c: at a masked dot, the picture's colour with the lettering */
void sat_text_frame(u32 *px, u32 w, u32 h, u32 ox); /* sat_vdp2.c: after a compose, this picture's items (an item whose
                                                    image is not made yet shows its original dots in px) */
void sat_text_warm(int tag, int w, int h, const u32 *dots, const u8 *on); /* a game: make this item's image ahead of time */
void sat_text_job(void (*fn)(void *), void *arg); /* a game: fn(arg) on the thread that makes the images (or now) */
void sat_text_wait(void);                          /* every image asked for is made (tests) */
void sat_text_put(float x, float y, float w, float h, const u32 *argb, int kw, int kh, u64 key); /* a game's own picture over this one */
void sat_text_held(const u32 *held);         /* sat_inb.c: the picture was kept back to be shown a blank later */
void sat_text_post(const u32 *px);           /* sat_wide.c: px is posted: its pictures to the presenter */
int sat_text_scale(void);                    /* the times the native size images are made at */
unsigned sat_text_mark(void);                /* a game's frame hook: the pictures put over this one so far */
void sat_text_rewind(unsigned n);            /* back to that many: a game redrawing its own over a picture it holds */
void sat_text_hide(unsigned below, float x, float y, float w, float h); /* until the next compose: images below `below` wholly in
                                             the box are not shown (a game's opaque box over them); w 0: none */
int sat_text_option(const char *arg, const char *value);
void sat_text_png(const char *path, const u32 *px, u32 w, u32 h); /* sat_video.c: a dump at the scale, with the items */
int sat_text_shots(void);
void sat_a3_cover(const u8 *m, int w, int h, int k, u8 *cov); /* sat_text_a3.c */
int sat_a3_picture(const u32 *dots, const u8 *on, int w, int h, int k, u32 *out);

#endif
