/* MPEG-1 video's macroblocks (ISO/IEC 11172-2 2.4.2.7, 2.4.4): the address increment (and the macroblocks it
 * skips), the type, the quantiser scale, the motion vectors, the coded block pattern, then the blocks. Intra blocks
 * code the DC term as a difference from the last block's of the same component (1024 at a slice's start and after
 * any non-intra or skipped macroblock); coefficients are dequantised by the matrix and the scale, made odd and
 * clipped to -2048..2047. Predictions use half-pel motion: two or four pels averaged, rounding up; a B macroblock
 * coded both ways averages its two predictions. */
#include <string.h>
#include "mpv.h"

static s32 clip8(s32 v) { return v < 0 ? 0 : v > 255 ? 255 : v; }

/* One prediction block, w x h, from ref (stride rs) at whole pel x, y plus half pels hx, hy into dst; avg: averaged
 * with what dst holds (the second of two directions). Pels outside the plane (pw x ph) repeat its edge. */
static void predict(u8 *dst, u32 ds, const u8 *ref, u32 rs, s32 x, s32 y, u32 hx, u32 hy, u32 w, u32 h, u32 pw,
                    u32 ph, int avg)
{
    u8 edge[17 * 17];
    if (x < 0 || y < 0 || x + (s32)(w + hx) > (s32)pw || y + (s32)(h + hy) > (s32)ph) {
        for (s32 j = 0; j <= (s32)h; j++)
            for (s32 i = 0; i <= (s32)w; i++) {
                s32 sx = x + i, sy = y + j;
                sx = sx < 0 ? 0 : sx >= (s32)pw ? (s32)pw - 1 : sx;
                sy = sy < 0 ? 0 : sy >= (s32)ph ? (s32)ph - 1 : sy;
                edge[j * 17 + i] = ref[(u32)sy * rs + (u32)sx];
            }
        ref = edge, rs = 17;
    } else
        ref += (u32)y * rs + (u32)x;
    for (u32 j = 0; j < h; j++, dst += ds, ref += rs)
        for (u32 i = 0; i < w; i++) {
            s32 p;
            if (hx && hy) p = (ref[i] + ref[i + 1] + ref[i + rs] + ref[i + rs + 1] + 2) >> 2;
            else if (hx) p = (ref[i] + ref[i + 1] + 1) >> 1;
            else if (hy) p = (ref[i] + ref[i + rs] + 1) >> 1;
            else p = ref[i];
            dst[i] = (u8)(avg ? (dst[i] + p + 1) >> 1 : p);
        }
}

/* The macroblock at mbx, mby predicted from ref with the vector mv (half pels) */
static void predict_mb(Mpv *m, MpvFrame *cur, const MpvFrame *ref, const s32 mv[2], u32 mbx, u32 mby, int avg)
{
    u32 ys = m->mb_w * 16u, cs = m->mb_w * 8u, yh = m->mb_h * 16u, ch = m->mb_h * 8u;
    s32 cx = mv[0] / 2, cy = mv[1] / 2; /* chroma: half the vector, toward zero */
    predict(cur->y + mby * 16u * ys + mbx * 16u, ys, ref->y, ys, (s32)mbx * 16 + (mv[0] >> 1),
            (s32)mby * 16 + (mv[1] >> 1), (u32)mv[0] & 1u, (u32)mv[1] & 1u, 16, 16, ys, yh, avg);
    predict(cur->cb + mby * 8u * cs + mbx * 8u, cs, ref->cb, cs, (s32)mbx * 8 + (cx >> 1), (s32)mby * 8 + (cy >> 1),
            (u32)cx & 1u, (u32)cy & 1u, 8, 8, cs, ch, avg);
    predict(cur->cr + mby * 8u * cs + mbx * 8u, cs, ref->cr, cs, (s32)mbx * 8 + (cx >> 1), (s32)mby * 8 + (cy >> 1),
            (u32)cx & 1u, (u32)cy & 1u, 8, 8, cs, ch, avg);
}

/* Motion vector predictors pred updated by the next two motion codes; 0 on a bad code */
static int motion(MpvBits *b, s32 pred[2], u32 f_code)
{
    u32 r_size = f_code ? f_code - 1u : 0u;
    s32 f = 1 << r_size;
    for (int c = 0; c < 2; c++) {
        s32 code = mpv_vlc(b, g_mpv_motion, 10), delta;
        if (code < 0) return 0;
        if (code && mpv_get(b, 1)) code = -code;
        if (f == 1 || code == 0) delta = code;
        else {
            delta = ((code < 0 ? -code : code) - 1) * f + (s32)mpv_get(b, r_size) + 1;
            if (code < 0) delta = -delta;
        }
        delta += pred[c];
        if (delta > 16 * f - 1) delta -= 32 * f;
        else if (delta < -16 * f) delta += 32 * f;
        pred[c] = delta;
    }
    return 1;
}

static void vec(s32 out[2], const s32 pred[2], u32 full)
{
    out[0] = full ? pred[0] * 2 : pred[0];
    out[1] = full ? pred[1] * 2 : pred[1];
}

/* Block i (0..3 luma, 4 Cb, 5 Cr) into m->block, dequantised (not transformed); 0 on a bad code */
static int block(Mpv *m, MpvBits *b, u32 i, int intra)
{
    s16 *blk = m->block;
    const u8 *q = intra ? m->intra_q : m->inter_q;
    s32 n = 0, qs = (s32)m->qscale;
    memset(blk, 0, sizeof m->block);
    if (intra) {
        s32 size = mpv_vlc(b, i < 4 ? g_mpv_dc_y : g_mpv_dc_c, i < 4 ? 7 : 8), diff = 0;
        u32 comp = i < 4 ? 0 : i - 3;
        if (size < 0) return 0;
        if (size) {
            s32 v = (s32)mpv_get(b, (u32)size);
            diff = v < (1 << (size - 1)) ? v - (1 << size) + 1 : v;
        }
        m->dc[comp] += diff * 8;
        blk[0] = (s16)m->dc[comp];
        n = 1;
    } else if (mpv_peek(b, 1)) { /* the first coefficient: "1s" is run 0, level 1 */
        s32 v = (3 * qs * q[0]) / 16;
        mpv_skip(b, 1);
        if (!(v & 1)) v -= v > 0;
        blk[0] = (s16)(mpv_get(b, 1) ? -v : v);
        n = 1;
    }
    for (;;) {
        MpvVlc e = g_mpv_dct[mpv_peek(b, 16)];
        s32 run, level, v;
        if (!e.len) return 0;
        if (e.value == MPV_DCT_EOB) {
            mpv_skip(b, 2);
            return !mpv_over(b);
        }
        mpv_skip(b, e.len);
        if (e.value == MPV_DCT_ESCAPE) {
            run = (s32)mpv_get(b, 6);
            level = (s32)mpv_get(b, 8);
            if (level == 0) level = (s32)mpv_get(b, 8);
            else if (level == 128) level = (s32)mpv_get(b, 8) - 256;
            else if (level > 128) level -= 256;
        } else {
            run = e.value >> 8;
            level = e.value & 0xff;
            if (mpv_get(b, 1)) level = -level;
        }
        n += run;
        if (n > 63) return 0;
        u32 pos = kMpvZigzag[n++];
        if (intra) v = (2 * level * qs * q[pos]) / 16;
        else v = ((2 * level + (level > 0 ? 1 : -1)) * qs * q[pos]) / 16;
        if (v && !(v & 1)) v -= v > 0 ? 1 : -1;
        blk[pos] = (s16)(v > 2047 ? 2047 : v < -2048 ? -2048 : v);
    }
}

/* the transformed block put (intra) or added (else) at dst */
static void place(const s16 *blk, u8 *dst, u32 stride, int intra)
{
    for (u32 y = 0; y < 8; y++, dst += stride, blk += 8)
        for (u32 x = 0; x < 8; x++) dst[x] = (u8)clip8(intra ? blk[x] : dst[x] + blk[x]);
}

static void reconstruct(Mpv *m, MpvFrame *cur, u32 addr, u32 type, const s32 vf[2], const s32 vb[2])
{
    u32 mbx = addr % m->mb_w, mby = addr / m->mb_w;
    if (type & MPV_MB_INTRA) return;
    if (m->type == 2 || (type & MPV_MB_FWD)) predict_mb(m, cur, m->pf, vf, mbx, mby, 0);
    if (type & MPV_MB_BWD) predict_mb(m, cur, m->pb, vb, mbx, mby, (type & MPV_MB_FWD) != 0);
}

int mpv_slice(Mpv *m, MpvBits *b, u32 row, MpvFrame *cur)
{
    s32 addr = (s32)(row * m->mb_w) - 1;
    u32 total = m->mb_w * m->mb_h, ys = m->mb_w * 16u, cs = m->mb_w * 8u;
    int first = 1;
    const MpvVlc *types = m->type == 1 ? g_mpv_mb_i : m->type == 2 ? g_mpv_mb_p : g_mpv_mb_b;
    u32 type_bits = m->type == 1 ? 2 : 6;
    static const s32 zero[2] = {0, 0};
    m->dc[0] = m->dc[1] = m->dc[2] = 1024;
    m->mv_f[0] = m->mv_f[1] = m->mv_b[0] = m->mv_b[1] = 0;
    m->last_type = 0;
    while (mpv_peek(b, 23) != 0 && !mpv_over(b)) {
        s32 inc = 0, v, type, vf[2], vb[2];
        while ((v = mpv_vlc(b, g_mpv_mbai, 11)) == MPV_MBAI_STUFFING || v == MPV_MBAI_ESCAPE)
            if (v == MPV_MBAI_ESCAPE) inc += 33;
        if (v < 0) return 0;
        inc += v;
        if (!first && inc > 1) { /* skipped macroblocks */
            m->dc[0] = m->dc[1] = m->dc[2] = 1024;
            if (m->type == 2) m->mv_f[0] = m->mv_f[1] = 0;
            vec(vf, m->mv_f, m->full_f);
            vec(vb, m->mv_b, m->full_b);
            for (s32 k = 1; k < inc; k++)
                if (addr + k < (s32)total)
                    reconstruct(m, cur, (u32)(addr + k), m->type == 2 ? 0 : m->last_type, vf, vb);
        }
        first = 0;
        addr += inc;
        if (addr >= (s32)total) return 0;
        type = mpv_vlc(b, types, type_bits);
        if (type < 0) return 0;
        if (type & MPV_MB_QUANT) m->qscale = mpv_get(b, 5);
        if ((type & MPV_MB_FWD) && !motion(b, m->mv_f, m->f_code_f)) return 0;
        if ((type & MPV_MB_BWD) && !motion(b, m->mv_b, m->f_code_b)) return 0;
        u32 cbp = type & MPV_MB_INTRA ? 63u : type & MPV_MB_PATTERN ? (u32)mpv_vlc(b, g_mpv_cbp, 9) : 0u;
        if (cbp > 63u) return 0;
        if (type & MPV_MB_INTRA) m->mv_f[0] = m->mv_f[1] = m->mv_b[0] = m->mv_b[1] = 0;
        else {
            m->dc[0] = m->dc[1] = m->dc[2] = 1024;
            if (m->type == 2 && !(type & MPV_MB_FWD)) m->mv_f[0] = m->mv_f[1] = 0;
        }
        vec(vf, m->mv_f, m->full_f);
        vec(vb, m->mv_b, m->full_b);
        reconstruct(m, cur, (u32)addr, (u32)type, m->type == 2 && !(type & MPV_MB_FWD) ? zero : vf, vb);
        u32 mbx = (u32)addr % m->mb_w, mby = (u32)addr / m->mb_w;
        for (u32 i = 0; i < 6; i++) {
            if (!(cbp & (32u >> i))) continue;
            if (!block(m, b, i, type & MPV_MB_INTRA)) return 0;
            mpv_idct(m->block);
            if (i < 4) place(m->block, cur->y + (mby * 16u + (i & 2u) * 4u) * ys + mbx * 16u + (i & 1u) * 8u, ys,
                             type & MPV_MB_INTRA);
            else place(m->block, (i == 4 ? cur->cb : cur->cr) + mby * 8u * cs + mbx * 8u, cs, type & MPV_MB_INTRA);
        }
        m->last_type = (u32)type;
    }
    return 1;
}
