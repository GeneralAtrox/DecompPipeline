/* VDP2's rotation scroll plane RBG0: its registers and rotation parameter A, for the composer (sat_vdp2.c), which draws
 * the plane's dots with the NBG cell and bitmap code. Per frame the parameter table is read from VRAM at RPTA; per line
 * the screen start and the rotation matrix give Xsp, Ysp, Xp, Yp; per dot the coefficient table (KTCTL) may replace the
 * scale or Xp, or make the dot transparent. Over modes 0, 2 and 3 (1, the screen-over name, repeats as 0 does). Fixed
 * point as the chip reads it, held as 16.16: the low 6 fraction bits of the start, delta, matrix, Mx/My and KA fields
 * are ignored. Not yet: rotation parameter B and its switching (RPMD), RBG1, the line colour screen.
 *
 * Nearest existing: sat_vdp2.c - draws the planes and is at the kit's file size limit; this adds only the coordinates. */
#include <stdint.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"

typedef int64_t s64;

static struct {
    int on, kte, kword, kmd, ovr;
    u32 kbase, w, h;
    s64 xst, yst, zst, dxst, dyst, dx, dy, a, b, c, d, e, f, mx, my, kx, ky, kast, dkast, dkax, px, py, pz, cx, cy, cz;
} g_r; /* the frame's; each line's are the caller's (SatRotLine), so bands of the picture can be composed at once */

static u32 R(u32 o) { return ((u32)sat_v2_reg[o] << 8) | sat_v2_reg[o + 1]; }
static u32 vw(u32 a) { return ((u32)sat_v2_vram[a & 0x7ffffu] << 8) | sat_v2_vram[(a + 1u) & 0x7ffffu]; }
static u32 vl(u32 a) { return (vw(a) << 16) | vw(a + 2u); }
static s64 sx(u32 v, int bits) { return (s64)((int32_t)(v << (32 - bits)) >> (32 - bits)); } /* bits wide, signed */
static s64 fx(u32 t, u32 o, int bits) { return sx(vl(t + o) & ~0x3fu, bits); }     /* a 16.16 field, bits 5-0 ignored */
static s64 iw(u32 t, u32 o) { return sx(vw(t + o), 14); }                          /* Px..Cz: 14-bit integers */

void sat_rbg0_setup(SatPlane *g)
{
    u32 bgon = R(0x20), chctl = R(0x2a) >> 8, pnc = R(0x38), plsz = R(0x3a) >> 8, ramp = R(0x3e) & 7u, t, mp;
    memset(g, 0, sizeof *g);
    g_r.on = g->on = (int)((bgon >> 4) & 1u);
    if (!g->on) return;
    g->tpon = (int)((bgon >> 12) & 1u);
    g->prio = (int)(R(0xfc) & 7u);
    g->pn1w = (int)((pnc >> 15) & 1u);
    g->cnsm = (int)((pnc >> 14) & 1u);
    g->splt = (int)((pnc >> 5) & 7u);
    g->scn = (int)(pnc & 31u);
    g->plw = (plsz & 1u) ? 2 : 1;
    g->plh = (plsz & 2u) ? 2 : 1;
    g->maps = 4;
    g->charpx = (chctl & 1u) ? 16 : 8;
    g->bitmap = (int)((chctl >> 1) & 1u);
    g->bmw = 512;
    g->bmh = (chctl & 4u) ? 512 : 256;
    g->cc = (int)((chctl >> 4) & 7u);
    g->bmpal = (int)(R(0x2e) & 7u) << 4;
    g->bmbase = (ramp << 17) & 0x7ffffu;
    for (u32 i = 0; i < 16u; i++) { /* planes A..P; a 2x1 or 2x2 plane starts on a 2 or 4 page boundary */
        u32 pagebytes = (u32)(g->charpx == 8 ? 4096 : 1024) * (g->pn1w ? 2u : 4u);
        mp = (R(0x50u + (i & ~1u)) >> (8u * (i & 1u))) & 0x3fu & ~(u32)((g->plw - 1) | (g->plh - 1) << 1);
        g->map[i] = (((ramp << 6) | mp) * pagebytes) & 0x7ffffu;
    }
    g->craoff = (int)(R(0xe6) & 7u) << 8;
    g->ccen = (int)((R(0xec) >> 4) & 1u);
    g->ccratio = (int)(R(0x10c) & 31u);
    g->zx = g->zy = 0x100;
    g_r.ovr = (int)((plsz >> 2) & 3u);
    g_r.w = g->bitmap ? 512u : 2048u * (u32)g->plw;
    g_r.h = g->bitmap ? (u32)g->bmh : 2048u * (u32)g->plh;
    g_r.kte = (int)(R(0xb4) & 1u);
    g_r.kword = (int)((R(0xb4) >> 1) & 1u);
    g_r.kmd = (int)((R(0xb4) >> 2) & 3u);
    g_r.kbase = (R(0xb6) & 7u) << 16;
    t = (((R(0xbc) & 7u) << 16) | (R(0xbe) & 0xfffeu)) * 2u;
    g_r.xst = fx(t, 0x00, 29); g_r.yst = fx(t, 0x04, 29); g_r.zst = fx(t, 0x08, 29);
    g_r.dxst = fx(t, 0x0c, 19); g_r.dyst = fx(t, 0x10, 19); g_r.dx = fx(t, 0x14, 19); g_r.dy = fx(t, 0x18, 19);
    g_r.a = fx(t, 0x1c, 20); g_r.b = fx(t, 0x20, 20); g_r.c = fx(t, 0x24, 20);
    g_r.d = fx(t, 0x28, 20); g_r.e = fx(t, 0x2c, 20); g_r.f = fx(t, 0x30, 20);
    g_r.px = iw(t, 0x34); g_r.py = iw(t, 0x36); g_r.pz = iw(t, 0x38);
    g_r.cx = iw(t, 0x3c); g_r.cy = iw(t, 0x3e); g_r.cz = iw(t, 0x40);
    g_r.mx = fx(t, 0x44, 30); g_r.my = fx(t, 0x48, 30);
    g_r.kx = sx(vl(t + 0x4c), 24); g_r.ky = sx(vl(t + 0x50), 24);
    g_r.kast = (s64)(vl(t + 0x54) & ~0x3fu); g_r.dkast = fx(t, 0x58, 26); g_r.dkax = fx(t, 0x5c, 26);
    if (sat_vwatch_lo == 0x25f80000u && sat_vblank_count() == sat_vwatch_vb) /* with the register dump: the table it read */
        port_log("vdp2 RBG0 table at %05x: st %llx,%llx,%llx dst %llx,%llx d %llx,%llx abcdef %llx %llx %llx %llx %llx %llx"
                 " p %lld,%lld,%lld c %lld,%lld,%lld m %llx,%llx k %llx,%llx ka %llx %llx %llx\n", t, g_r.xst, g_r.yst,
                 g_r.zst, g_r.dxst, g_r.dyst, g_r.dx, g_r.dy, g_r.a, g_r.b, g_r.c, g_r.d, g_r.e, g_r.f, g_r.px, g_r.py,
                 g_r.pz, g_r.cx, g_r.cy, g_r.cz, g_r.mx, g_r.my, g_r.kx, g_r.ky, g_r.kast, g_r.dkast, g_r.dkax);
}

static void line(SatRotLine *l, u32 v)
{
    s64 xs = g_r.xst + g_r.dxst * v - g_r.px * 65536, ys = g_r.yst + g_r.dyst * v - g_r.py * 65536, zs = g_r.zst - g_r.pz * 65536;
    s64 ox = g_r.px - g_r.cx, oy = g_r.py - g_r.cy, oz = g_r.pz - g_r.cz;
    l->xsp = (g_r.a * xs + g_r.b * ys + g_r.c * zs) >> 16;
    l->ysp = (g_r.d * xs + g_r.e * ys + g_r.f * zs) >> 16;
    l->xp = g_r.a * ox + g_r.b * oy + g_r.c * oz + g_r.cx * 65536 + g_r.mx;
    l->yp = g_r.d * ox + g_r.e * oy + g_r.f * oz + g_r.cy * 65536 + g_r.my;
    l->dxr = (g_r.a * g_r.dx + g_r.b * g_r.dy) >> 16;
    l->dyr = (g_r.d * g_r.dx + g_r.e * g_r.dy) >> 16;
    l->line = v;
}

/* the coefficient at table address ka (16.16): 0 where it makes the dot transparent, else kx, ky and Xp as it sets them */
static int coef(s64 ka64, s64 *kx, s64 *ky, s64 *xp)
{
    u32 ka = (u32)(ka64 >> 16) & 0xffffu, k;
    s64 kv;
    *kx = g_r.kx, *ky = g_r.ky;
    if (!g_r.kte) return 1;
    if (g_r.kword) { /* bit 31 (1-word: 15) transparent, then signed 8.16 (1-word: 4.10) */
        k = vw((g_r.kbase + ka) * 2u);
        if (k & 0x8000u) return 0;
        kv = sx(k, 15) * 64;
    } else {
        k = vl((g_r.kbase + ka) * 4u);
        if (k & 0x80000000u) return 0;
        kv = sx(k, 24);
    }
    switch (g_r.kmd) {
    case 0: *kx = *ky = kv; break;
    case 1: *kx = kv; break;
    case 2: *ky = kv; break;
    default: *xp = kv; break; /* the coefficient as Xp */
    }
    return 1;
}

static u8 put(s64 X, s64 Y, u32 *px, u32 *py) /* the map dot (X, Y), 0 where the over mode makes it transparent */
{
    if (g_r.ovr == 2 && (X < 0 || Y < 0 || X >= (s64)g_r.w || Y >= (s64)g_r.h)) return 0;
    if (g_r.ovr == 3 && (X < 0 || Y < 0 || X >= 512 || Y >= 512)) return 0;
    *px = (u32)X;
    *py = (u32)Y;
    return 1;
}

/* RBG0's map dots for n dots of line y from screen x x0 (signed: a widened picture's left side is below 0); ok[i] 0
 * where the dot is transparent, and 0 returned when none is shown. X and Y step by the line's per-dot change, exact
 * integers as the chip adds them; the coefficient is read once for the line unless it changes along it (dKAx). */
int sat_rbg0_row(SatRotLine *l, u32 y, u32 x0, u32 n, u32 *px, u32 *py, u8 *ok)
{
    s64 x = (s64)(s32)x0, ka = g_r.kast + g_r.dkast * y, kx = 0, ky = 0, xp, bx, by;
    int any = 0, each = g_r.kte && g_r.dkax;
    if (y != l->line) line(l, y);
    xp = l->xp;
    if (!each && !coef(ka, &kx, &ky, &xp)) return 0;
    bx = l->xsp + l->dxr * x, by = l->ysp + l->dyr * x;
    if (!each) { /* one scale for the line: kx (Xsp + dX x) steps by kx dX */
        s64 ax = kx * bx, ay = ky * by, dax = kx * l->dxr, day = ky * l->dyr;
        for (u32 i = 0; i < n; i++, ax += dax, ay += day) any |= ok[i] = put(((ax >> 16) + xp) >> 16, ((ay >> 16) + l->yp) >> 16, px + i, py + i);
        return any;
    }
    for (u32 i = 0; i < n; i++, x++, bx += l->dxr, by += l->dyr) {
        xp = l->xp;
        ok[i] = (u8)(coef(ka + g_r.dkax * x, &kx, &ky, &xp) && put((((kx * bx) >> 16) + xp) >> 16, (((ky * by) >> 16) + l->yp) >> 16, px + i, py + i));
        any |= ok[i];
    }
    return any;
}
