/* A draw's vertices into the host's rows of four floats, one input at a time over every vertex (built /O2, /fp:strict: gen_xbox.py O2_FILES).
 * xb_d3d_vconv.h plans each input once a draw (its format, where it is read, its selects as one byte shuffle) and calls xb_vconv_run; the loop
 * per vertex is then a load, a byte swap and a shuffle. The values are the ones vtx_convert_fast gives (XB_VTX_CONV=old, the answer key):
 *  - kind 0 (32-bit floats) is the bytes as written, swapped and moved by the one shuffle;
 *  - half floats and the 8-bit formats come from the same tables, the 16-bit integers from the same expressions;
 *  - a component past the format's count reads as 0 (the fourth as 1); a select of 4 or 6 or 7 gives 0, of 5 gives 1;
 *  - an indexed fetch's index is the input it names of the same vertex, already converted when that input comes first (else 0, as the zeroed buffer
 *    gave), rounded or floored, read as absent when not 0 <= index < 2^24.
 * Every byte of v[0 .. n * nat * 4) is written. SSSE3 (pshufb) */
#include <tmmintrin.h>
#include <string.h>
#include "xb.h"

#define VC_RAW(a) _mm_loadu_si128((const __m128i *)(tc_base + (a)))
#define VC_W(a) _byteswap_ulong(*(const u32 *)(tc_base + (a)))
#define VC_OUT(x) _mm_or_si128(_mm_shuffle_epi8((x), mask), one)

static __forceinline __m128i vc_short(const XbVcEl *e, u32 at)   /* formats 25 and 26, as vx_decode */
{
    float c[4] = {0.0f, 0.0f, 0.0f, 1.0f};
    u32 i, x;
    for (i = 0; i < e->nc; i++) {
        x = VC_W(at + 4 * (i >> 1)) >> (16 * (i & 1)) & 0xffffu;
        c[i] = (e->t & 1u) ? (float)(short)x : (float)x;
        if (!(e->t & 2u)) c[i] = (e->t & 1u) ? (c[i] < -32767.0f ? -1.0f : c[i] / 32767.0f) : c[i] / 65535.0f;
    }
    return _mm_castps_si128(_mm_loadu_ps(c));
}

/* One input over all vertices: AT gives the element's guest address for vertex i (0: absent), the rest the shuffled row from it */
#define VC_LOOP(AT, ...)                                                        \
    for (i = 0; i < n; i++) {                                                   \
        u32 at = (AT);                                                          \
        __m128i r;                                                              \
        if (!at) r = absent;                                                    \
        else { __VA_ARGS__; }                                                     \
        _mm_storeu_si128((__m128i *)(o + (size_t)i * ostep), r);                \
    }

#define VC_DECODERS(AT)                                                                                                                       \
    switch (e->kind * 8u + e->nc) {                                                                                                           \
    case 4: VC_LOOP(AT, r = VC_OUT(VC_RAW(at))) break;                                                                                        \
    case 3: VC_LOOP(AT, r = VC_OUT(_mm_unpacklo_epi64(_mm_loadl_epi64((const __m128i *)(tc_base + at)), _mm_cvtsi32_si128(*(const int *)(tc_base + at + 8))))) break; \
    case 2: VC_LOOP(AT, r = VC_OUT(_mm_loadl_epi64((const __m128i *)(tc_base + at)))) break;                                                  \
    case 1: VC_LOOP(AT, r = VC_OUT(_mm_cvtsi32_si128(*(const int *)(tc_base + at)))) break;                                                   \
    case 10: VC_LOOP(AT, { u32 w = VC_W(at); r = VC_OUT(_mm_castps_si128(_mm_setr_ps(half[w & 0xffffu], half[w >> 16], 0.0f, 1.0f))); }) break; \
    case 12: VC_LOOP(AT, { u32 w = VC_W(at), w2 = VC_W(at + 4);                                                                              \
                           r = VC_OUT(_mm_castps_si128(_mm_setr_ps(half[w & 0xffffu], half[w >> 16], half[w2 & 0xffffu], half[w2 >> 16]))); }) break; \
    case 28: VC_LOOP(AT, { u32 w = VC_W(at); r = VC_OUT(_mm_castps_si128(_mm_setr_ps(b8[w & 255u], b8[(w >> 8) & 255u], b8[(w >> 16) & 255u], b8[w >> 24]))); }) break; \
    default: VC_LOOP(AT, r = VC_OUT(vc_short(e, at))) break;                                                                                \
    }

static __forceinline u32 vc_indexed(const XbVcEl *e, float x, u32 (*slow)(unsigned, u32))
{
    u32 ix;
    if (e->round) x = x + 0.5f;
    if (!(x >= 0.0f && x < 16777216.0f)) return 0;   /* floor(x) is in range exactly when x is */
    ix = (u32)x;   /* x >= 0: truncation is the floor */
    return ix < e->reach ? e->ibase + ix * e->isz : slow(e->k, ix);
}

void xb_vconv_run(const XbVcEl *el, unsigned nat, unsigned n, const u32 *vindex, float *v, const float *half, const float (*byte)[256], u32 (*slow)(unsigned k, u32 index))
{
    unsigned i, k;
    size_t ostep = (size_t)nat * 4u;
    for (k = 0; k < nat; k++) {
        const XbVcEl *e = &el[k];
        const __m128i mask = _mm_loadu_si128((const __m128i *)e->mask), one = _mm_loadu_si128((const __m128i *)e->one), absent = _mm_loadu_si128((const __m128i *)e->absent);
        const float *b8 = byte[e->t & 3u], *src = v + e->isrc * 4u + e->icomp;
        float *o = v + (size_t)k * 4u;
        const u32 base = e->base, step = e->step;
        if (e->mode == 0) {
            VC_DECODERS(base + i * step)
        } else if (e->mode == 1) {
            VC_DECODERS(base + vindex[i] * step)
        } else if (e->isrc < k) {
            VC_DECODERS(vc_indexed(e, src[(size_t)i * ostep], slow))
        } else {
            const u32 at0 = vc_indexed(e, e->ix, slow);   /* the same index for every vertex */
            VC_DECODERS(at0)
        }
    }
}

/* XB_VTX_UNIQUE (xb_d3d_vconv.h): an indexed draw's distinct vertex numbers, in the order the entries first name them, into uv; entry i's row (its vertex's
 * place in uv) into remap[i]. Every vindex[i] is in lo .. lo + span - 1; map has span words and is filled here. Returns the count of rows */
unsigned xb_vconv_unique(const u32 *vindex, unsigned n, u32 lo, u32 span, u32 *map, u32 *uv, u32 *remap)
{
    unsigned i, nu = 0;
    memset(map, 0xff, (size_t)span * 4u);
    for (i = 0; i < n; i++) {
        u32 j = vindex[i] - lo, u = map[j];
        if (u == ~0u) map[j] = u = nu, uv[nu++] = vindex[i];
        remap[i] = u;
    }
    return nu;
}
