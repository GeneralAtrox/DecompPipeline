/* MPEG-1 video's sequence header and pictures (ISO/IEC 11172-2 2.4.2, 2.4.3). The sequence header gives the size,
 * the picture rate and any quantiser matrices (sent in scan order); the decoder keeps three frames. A picture header
 * gives its type (I, P or B) and, for P and B, the motion vectors' f codes; its slices follow, each starting at a
 * macroblock row with its quantiser scale. I and P pictures are references: each one decoded shows the reference
 * before it, so pictures come out in display order; a B picture shows at once. */
#include <stdlib.h>
#include <string.h>
#include "mpv.h"

static const u32 kRate[16][2] = {{0, 0}, {24000, 1001}, {24, 1}, {25, 1}, {30000, 1001}, {30, 1}, {50, 1},
                                 {60000, 1001}, {60, 1}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}};

static int start_code(const u8 *p, u32 n, u32 code)
{
    return n >= 4 && p[0] == 0 && p[1] == 0 && p[2] == 1 && p[3] == code;
}

void mpv_free(Mpv *m)
{
    free(m->mem);
    memset(m, 0, sizeof *m);
}

int mpv_sequence(Mpv *m, const u8 *p, u32 n)
{
    MpvBits b = {p, 32, n};
    u32 w, h, rate;
    if (!start_code(p, n, 0xb3) || n < 12) return 0;
    w = mpv_get(&b, 12);
    h = mpv_get(&b, 12);
    mpv_skip(&b, 4); /* pel aspect ratio */
    rate = mpv_get(&b, 4);
    mpv_skip(&b, 18 + 1 + 10 + 1); /* bit rate, marker, VBV buffer size, constrained parameters */
    if (!w || !h || w > 4095 || h > 4095) return 0;
    mpv_tables_init();
    mpv_idct_init();
    if (w != m->width || h != m->height || !m->mem) {
        u32 mbw = (w + 15) / 16, mbh = (h + 15) / 16, luma = mbw * mbh * 256u;
        free(m->mem);
        memset(m, 0, sizeof *m);
        m->mem = malloc(3u * (luma + luma / 2u) + MPV_PAD);
        if (!m->mem) return 0;
        for (int i = 0; i < 3; i++) {
            m->frame[i].y = m->mem + i * (luma + luma / 2u);
            m->frame[i].cb = m->frame[i].y + luma;
            m->frame[i].cr = m->frame[i].cb + luma / 4u;
            memset(m->frame[i].y, 16, luma);
            memset(m->frame[i].cb, 128, luma / 2u);
        }
        m->width = w, m->height = h, m->mb_w = mbw, m->mb_h = mbh;
        m->fwd = m->bwd = -1;
    }
    m->rate_num = kRate[rate][0], m->rate_den = kRate[rate][1];
    if (mpv_get(&b, 1))
        for (int i = 0; i < 64; i++) m->intra_q[kMpvZigzag[i]] = (u8)mpv_get(&b, 8);
    else
        memcpy(m->intra_q, kMpvIntraQ, 64);
    if (mpv_get(&b, 1))
        for (int i = 0; i < 64; i++) m->inter_q[kMpvZigzag[i]] = (u8)mpv_get(&b, 8);
    else
        memset(m->inter_q, 16, 64);
    return !mpv_over(&b);
}

/* the next start code at or after byte i: its offset, or n */
static u32 next_start(const u8 *p, u32 n, u32 i)
{
    for (; i + 3 < n; i++)
        if (p[i] == 0 && p[i + 1] == 0 && p[i + 2] == 1) return i;
    return n;
}

const MpvFrame *mpv_decode(Mpv *m, const u8 *p, u32 n)
{
    MpvBits b = {p, 32, n};
    int cur = 0;
    const MpvFrame *show = NULL;
    if (!m->mem || !start_code(p, n, 0x00)) return NULL;
    mpv_skip(&b, 10); /* temporal reference */
    m->type = mpv_get(&b, 3);
    mpv_skip(&b, 16); /* VBV delay */
    if (m->type < 1 || m->type > 3) return NULL; /* D pictures are not decoded */
    if (m->type == 2 && m->bwd < 0) return NULL; /* no reference yet (a stream cut into) */
    if (m->type == 3 && (m->fwd < 0 || m->bwd < 0)) return NULL;
    if (m->type >= 2) {
        m->full_f = mpv_get(&b, 1);
        m->f_code_f = mpv_get(&b, 3);
    }
    if (m->type == 3) {
        m->full_b = mpv_get(&b, 1);
        m->f_code_b = mpv_get(&b, 3);
    }
    /* fwd is the earlier reference, bwd the later. B predicts from both; P from the later; I or P replaces the
     * earlier, which no picture needs any more. */
    if (m->type == 3) {
        while (cur == m->fwd || cur == m->bwd) cur++;
        m->pf = &m->frame[m->fwd], m->pb = &m->frame[m->bwd];
    } else {
        while (cur == m->bwd) cur++;
        m->pf = m->bwd >= 0 ? &m->frame[m->bwd] : NULL, m->pb = NULL;
    }
    for (u32 i = next_start(p, n, 4); i < n;) {
        u32 code = p[i + 3], end = next_start(p, n, i + 4);
        if (code < 0x01 || code > 0xaf) break;
        if (code <= m->mb_h) {
            MpvBits s = {p + i, 32, end - i};
            m->qscale = mpv_get(&s, 5);
            while (mpv_get(&s, 1)) mpv_skip(&s, 8); /* extra information */
            if (!mpv_slice(m, &s, code - 1, &m->frame[cur])) m->errors++;
        }
        i = end;
    }
    m->pictures++;
    if (m->type == 3) return &m->frame[cur];
    if (m->bwd >= 0) show = &m->frame[m->bwd]; /* the previous reference shows now */
    m->fwd = m->bwd;
    m->bwd = cur;
    m->flushed = 0;
    return show;
}

const MpvFrame *mpv_flush(Mpv *m)
{
    if (m->bwd < 0 || m->flushed) return NULL;
    m->flushed = 1;
    return &m->frame[m->bwd];
}
