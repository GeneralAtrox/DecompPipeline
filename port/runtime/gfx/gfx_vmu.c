/* The VMU screen: each memory card's 48x32 LCD (sdk/maple_pad.c hands over the dots its last block lit, as the player
 * sees them in the controller) drawn beside the game's picture, with the VMU's look: dark blue dots on the grey-green
 * glass inside the white case. --vmu-screen off|side|over (the menu bar's Display > VMU screen):
 *   off   (default) not drawn: the console shows it on the controller, not the television, and a game's screen often
 *         holds one still picture (such as its boot picture), which would sit lit beside the play;
 *   side  in the bar beside a 4:3 picture (the left one), never over the picture; nothing where the bar is too narrow
 *         (a wide window with widescreen on fills it);
 *   over  in the picture's top-left corner, over the game.
 * A card shows once a block has reached it; several stack (A1 first). Only the back buffer gets it: frame dumps and
 * scene dumps are the game's picture alone. */
#include <string.h>
#include "gfx.h"
#include "platform.h"
#include "host.h"

#define CARDS 8u
#define DOTS_W 48u
#define DOTS_H 32u
#define CASE_PAD 3u     /* the case around the glass, in dots */
#define GLASS_PAD 1u    /* the glass around the dots, in dots */
#define MARGIN_DIV 60u  /* the gap to the picture's edge: the output's height over this */
#define SCALE_DIV 180u  /* the largest dot: the output's height over this (6 pixels at 1080) */
#define SCALE_MAX 12u   /* and never larger */
#define PANEL_W(s) ((DOTS_W + 2u * (CASE_PAD + GLASS_PAD)) * (s))
#define PANEL_H(s) ((DOTS_H + 2u * (CASE_PAD + GLASS_PAD)) * (s))

static const u32 kCase = 0xffd8dcd8u, kRim = 0xff8a9290u, kGlass = 0xffa3b8a0u, kDot = 0xff1f2c3eu,
                 kGrid = 0xff9aae97u; /* ARGB */

typedef struct Panel {
    int shown;                /* a block reached the card */
    int dirty;                /* the dots changed since the texture was filled */
    u8 dots[DOTS_W * DOTS_H];
    ID3D11Texture2D *tex;
    u32 scale;                /* the texture's dot size in pixels */
} Panel;

static Panel g_panel[CARDS];
static int g_mode; /* VMU_SCREEN_OFF, _SIDE, _OVER */

static const char *const kModes[] = {"off", "side", "over"};

int gfx_vmu_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--vmu-screen") || !value) return 0;
    for (int m = 0; m < 3; m++)
        if (!strcmp(value, kModes[m])) gfx_vmu_set_mode(m);
    return 2;
}

void gfx_vmu_set_mode(int mode) { g_mode = mode >= 0 && mode <= 2 ? mode : 0; }
int gfx_vmu_mode(void) { return g_mode; }
const char *gfx_vmu_mode_name(int mode) { return kModes[mode >= 0 && mode <= 2 ? mode : 0]; }

void host_vmu_lcd(u32 card, const u8 *dots)
{
    if (card >= CARDS) return;
    memcpy(g_panel[card].dots, dots, sizeof g_panel[card].dots);
    g_panel[card].shown = g_panel[card].dirty = 1;
}

static u32 abgr(u32 argb) { return (argb & 0xff00ff00u) | (argb >> 16 & 0xffu) | (argb & 0xffu) << 16; }

/* The panel's pixels at dot size s: the case with a rim, the glass, and each dot a cell whose last row and column
 * are the grid's (from 3 pixels up), as the LCD's dots have gaps. */
static void paint(const Panel *p, u32 s, u32 *px)
{
    u32 w = PANEL_W(s), h = PANEL_H(s), glass0 = CASE_PAD * s, dots0 = (CASE_PAD + GLASS_PAD) * s;
    for (u32 y = 0; y < h; y++)
        for (u32 x = 0; x < w; x++) {
            u32 c = kCase;
            if (x < 1u || y < 1u || x + 1u >= w || y + 1u >= h) c = kRim;
            else if (x >= glass0 && y >= glass0 && x < w - glass0 && y < h - glass0) {
                c = kGlass;
                if (x >= dots0 && y >= dots0 && x < w - dots0 && y < h - dots0) {
                    u32 dx = (x - dots0) / s, dy = (y - dots0) / s;
                    int gap = s >= 3u && ((x - dots0) % s == s - 1u || (y - dots0) % s == s - 1u);
                    c = gap ? kGlass : p->dots[dy * DOTS_W + dx] ? kDot : kGrid;
                }
            }
            px[y * w + x] = abgr(c);
        }
}

static int fill(Panel *p, u32 s)
{
    static u32 px[PANEL_W(SCALE_MAX) * PANEL_H(SCALE_MAX)];
    D3D11_TEXTURE2D_DESC d;
    if (p->tex && p->scale != s) {
        ID3D11Texture2D_Release(p->tex);
        p->tex = NULL;
    }
    if (!p->tex) {
        memset(&d, 0, sizeof d);
        d.Width = PANEL_W(s);
        d.Height = PANEL_H(s);
        d.MipLevels = d.ArraySize = 1;
        d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        d.SampleDesc.Count = 1;
        d.Usage = D3D11_USAGE_DEFAULT;
        if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &p->tex))) return 0;
        p->scale = s;
        p->dirty = 1;
    }
    if (p->dirty) {
        paint(p, s, px);
        ID3D11DeviceContext_UpdateSubresource(g_gfx.ctx, (ID3D11Resource *)p->tex, 0, NULL, px, PANEL_W(s) * 4u, 0);
        p->dirty = 0;
    }
    return 1;
}

/* The dot size and the first panel's place for the mode and the output now; 0 when it does not fit. */
static u32 place(u32 *x, u32 *y)
{
    u32 margin = g_gfx.out_h / MARGIN_DIV, s = g_gfx.out_h / SCALE_DIV;
    if (s < 1u) s = 1u;
    if (s > SCALE_MAX) s = SCALE_MAX;
    if (g_mode == 1) { /* side: inside the left bar */
        while (s > 1u && PANEL_W(s) + 2u * margin > g_gfx.frame_x) s--;
        if (s < 2u || PANEL_W(s) + 2u * margin > g_gfx.frame_x) return 0;
        *x = (g_gfx.frame_x - PANEL_W(s)) / 2u;
        *y = g_gfx.frame_y + margin;
        return s;
    }
    *x = g_gfx.frame_x + margin;
    *y = g_gfx.frame_y + margin;
    return s;
}

void gfx_vmu_draw(void *back_buffer)
{
    u32 x, y, s;
    if (!g_mode || !g_gfx.dev || !back_buffer) return;
    s = place(&x, &y);
    if (!s) return;
    for (u32 c = 0; c < CARDS; c++) {
        Panel *p = &g_panel[c];
        if (!p->shown) continue;
        if (y + PANEL_H(s) > g_gfx.out_h || !fill(p, s)) return;
        ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, (ID3D11Resource *)back_buffer, 0, x, y, 0,
                                                  (ID3D11Resource *)p->tex, 0, NULL);
        y += PANEL_H(s) + g_gfx.out_h / MARGIN_DIV;
    }
}

void gfx_vmu_release(void)
{
    for (u32 c = 0; c < CARDS; c++)
        if (g_panel[c].tex) {
            ID3D11Texture2D_Release(g_panel[c].tex);
            g_panel[c].tex = NULL;
        }
}
