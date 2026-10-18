/* The float math functions the game calls (the SHC C library's) that the 32-bit host C runtime only has as inline
 * wrappers in <math.h>, so there is no symbol to link: defined here on the double functions. <math.h> is not
 * included (its inline sinf etc. would clash with these definitions). sqrtf is the SSE root, as fsqrt.
 * Also rand and srand: the host C runtime's generator, the same sequence, but with its state here, so an in-between
 * frame (frame_extra.c) can put it back. <stdlib.h> is not included either. */
#include <xmmintrin.h>
#include "platform.h"

double sin(double x);
double cos(double x);
double tan(double x);
double acos(double x);
double atan2(double y, double x);
double log10(double x);
double floor(double x);
double ceil(double x);
double ldexp(double x, int exp);

f32 sinf(f32 x);
f32 cosf(f32 x);
f32 tanf(f32 x);
f32 acosf(f32 x);
f32 atan2f(f32 y, f32 x);
f32 log10f(f32 x);
f32 floorf(f32 x);
f32 ceilf(f32 x);
f32 sqrtf(f32 x);
f32 ldexpf(f32 x, s32 exp);

f32 sinf(f32 x) { return (f32)sin(x); }
f32 cosf(f32 x) { return (f32)cos(x); }
f32 tanf(f32 x) { return (f32)tan(x); }
f32 acosf(f32 x) { return (f32)acos(x); }
f32 atan2f(f32 y, f32 x) { return (f32)atan2(y, x); }
f32 log10f(f32 x) { return (f32)log10(x); }
f32 floorf(f32 x) { return (f32)floor(x); }
f32 ceilf(f32 x) { return (f32)ceil(x); }
f32 sqrtf(f32 x) { return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x))); }
f32 ldexpf(f32 x, s32 exp) { return (f32)ldexp(x, exp); }

s32 rand(void);
void srand(u32 seed);
void port_libm_extra_state(void);

static u32 g_rand_state = 1u; /* the C runtime's seed until srand */

s32 rand(void)
{
    g_rand_state = g_rand_state * 214013u + 2531011u;
    return (s32)((g_rand_state >> 16) & 0x7fffu);
}

void srand(u32 seed) { g_rand_state = seed; }

void port_libm_extra_state(void) { port_extra_state(&g_rand_state, sizeof g_rand_state); }
