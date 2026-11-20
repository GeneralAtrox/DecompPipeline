/* sat_vdp2_name.h: a cell plane's pattern name at a scroll-space dot, as the composer reads it (sat_vdp2.c nbg_dot) and as
 * clearer text's scan of the planes reads it (sat_text_v2.c): the character number, the palette, the flips and the special
 * function bits (SPR bit 0, SCC bit 1) of the cell that holds (px, py). 1- and 2-word names, 8 and 16 dot cells.
 *
 * Nearest existing: nbg_dot in sat_vdp2.c, which this was taken from (one copy for both). */
#ifndef SAT_VDP2_NAME_H
#define SAT_VDP2_NAME_H

#include "sat_vdp.h"

typedef struct { u32 charnum, pal, sf; int hf, vf; } SatV2Name;

static __inline u32 sat_v2n_w(u32 a) { return ((u32)sat_v2_vram[a & 0x7ffffu] << 8) | sat_v2_vram[(a + 1u) & 0x7ffffu]; }

static __inline void sat_v2_name_at(const SatPlane *g, u32 px, u32 py, SatV2Name *nm)
{
    u32 planew = 512u * (u32)g->plw, planeh = 512u * (u32)g->plh, cs = (u32)g->charpx, csh = cs == 16u ? 4u : 3u, x, y, pn, pnaddr, plane, page;
    px &= planew * (u32)g->maps - 1u;
    py &= planeh * (u32)g->maps - 1u;
    plane = (py >> (8 + g->plh)) * (u32)g->maps + (px >> (8 + g->plw));
    x = px & (planew - 1u);
    y = py & (planeh - 1u);
    page = (y >> 9) * (u32)g->plw + (x >> 9);
    x &= 511u;
    y &= 511u;
    pnaddr = g->map[plane] + page * (u32)(cs == 8u ? 4096 : 1024) * (g->pn1w ? 2u : 4u) + ((y >> csh) * (512u >> csh) + (x >> csh)) * (g->pn1w ? 2u : 4u);
    nm->hf = nm->vf = 0, nm->sf = (u32)g->pnsf;
    if (g->pn1w) {
        pn = sat_v2n_w(pnaddr);
        if (g->cnsm) {
            nm->charnum = (pn & 0xfffu) | (((u32)g->scn & 0x1cu) << 10);
        } else {
            nm->charnum = (pn & 0x3ffu) | (((u32)g->scn & 0x1fu) << 10);
            nm->vf = (int)((pn >> 11) & 1u);
            nm->hf = (int)((pn >> 10) & 1u);
        }
        if (cs == 16u) {
            nm->charnum = g->cnsm ? (((pn & 0xfffu) << 2) | ((u32)g->scn & 3u) | (((u32)g->scn & 0x10u) << 10))
                                  : (((pn & 0x3ffu) << 2) | ((u32)g->scn & 3u) | (((u32)g->scn & 0x1cu) << 10));
        }
        nm->pal = g->cc == 1 ? ((pn >> 12) & 7u) << 4 : ((pn >> 12) & 15u) | ((u32)g->splt << 4); /* 256 colours: pn bits 14-12 are the bank */
    } else {
        pn = (sat_v2n_w(pnaddr) << 16) | sat_v2n_w(pnaddr + 2u);
        nm->charnum = pn & 0x7fffu;
        nm->pal = (pn >> 16) & 0x7fu;
        nm->vf = (int)((pn >> 31) & 1u);
        nm->hf = (int)((pn >> 30) & 1u);
        nm->sf = ((pn >> 29) & 1u) | ((pn >> 27) & 2u); /* 2-word names carry SPR (bit 29) and SCC (bit 28) */
        if (cs == 16u) nm->charnum <<= 2;
    }
}

#endif
