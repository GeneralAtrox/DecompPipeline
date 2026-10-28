/* The 8x8 inverse DCT of ISO/IEC 11172-2 annex A: f(x, y) = 1/4 sum over u, v of C(u) C(v) F(u, v)
 * cos((2x + 1) u pi / 16) cos((2y + 1) v pi / 16), C(0) = 1 / sqrt 2, else 1. Computed separably in single precision
 * (rows, then columns), which keeps well inside the accuracy rule's limits (IEEE 1180), and rounded to the nearest
 * integer, clipped to -256..255. A block whose only coefficient is the DC term is flat. */
#include <math.h>
#include "mpv.h"

static f32 g_cos[8][8]; /* [u][x]: C(u) / 2 cos((2x + 1) u pi / 16) */

void mpv_idct_init(void)
{
    for (int u = 0; u < 8; u++)
        for (int x = 0; x < 8; x++)
            g_cos[u][x] = (f32)((u ? 0.5 : 0.5 / sqrt(2.0)) * cos((2 * x + 1) * u * 3.14159265358979323846 / 16.0));
}

static s16 clip(f32 v)
{
    /* to the nearest, halves up: inside the range v + 256.5 is positive, so truncating it floors it */
    if (v <= -256.0f) return -256;
    if (v >= 255.0f) return 255;
    return (s16)((s32)((double)v + 256.5) - 256);
}

void mpv_idct(s16 block[64])
{
    f32 t[64];
    int dc_only = 1;
    for (int i = 1; i < 64 && dc_only; i++) dc_only = block[i] == 0;
    if (dc_only) {
        s16 v = clip(block[0] / 8.0f);
        for (int i = 0; i < 64; i++) block[i] = v;
        return;
    }
    for (int r = 0; r < 8; r++) { /* each row: v along it */
        const s16 *in = block + 8 * r;
        int any = 0;
        for (int v = 0; v < 8; v++) any |= in[v];
        for (int x = 0; x < 8; x++) {
            f32 s = 0.0f;
            if (any)
                for (int v = 0; v < 8; v++) s += g_cos[v][x] * in[v];
            t[8 * r + x] = s;
        }
    }
    for (int c = 0; c < 8; c++) /* each column: u down it */
        for (int y = 0; y < 8; y++) {
            f32 s = 0.0f;
            for (int u = 0; u < 8; u++) s += g_cos[u][y] * t[8 * u + c];
            block[8 * y + c] = clip(s);
        }
}
