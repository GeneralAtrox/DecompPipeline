/* The float math functions the game calls (the SHC C library's) that the 32-bit host C runtime only has as inline
 * wrappers in <math.h>, so there is no symbol to link: defined here on the double functions. <math.h> is not
 * included (its inline sinf etc. would clash with these definitions). sqrtf is the SSE root, as fsqrt.
 * PORT_OWN_CRT_name (port_config.h, gen_port.py OWN_CRT): the game's own judged C defines that one, rounding as the
 * console's library does, so it is left out here.
 * Also rand and srand: the SHC library's generator (state * 1103515245 + 12345, bits 16..30; the Shinobi SDK's rand),
 * with its state here, so an in-between frame (frame_extra.c) can put it back and the lockstep check
 * (lockstep.c) can read it. <stdlib.h> is not included either. */
#include <xmmintrin.h>
#include "platform.h"

double sin(double x);
double cos(double x);
double tan(double x);
double acos(double x);
double atan2(double y, double x);
double log10(double x);
double pow(double x, double y);
double floor(double x);
double ceil(double x);
double ldexp(double x, int exp);

f32 sinf(f32 x);
f32 cosf(f32 x);
f32 tanf(f32 x);
f32 acosf(f32 x);
f32 atan2f(f32 y, f32 x);
f32 log10f(f32 x);
f32 powf(f32 x, f32 y);
f32 floorf(f32 x);
f32 ceilf(f32 x);
f32 sqrtf(f32 x);
f32 ldexpf(f32 x, s32 exp);

#ifndef PORT_OWN_CRT_sinf
f32 sinf(f32 x) { return (f32)sin(x); }
#endif
#ifndef PORT_OWN_CRT_cosf
f32 cosf(f32 x) { return (f32)cos(x); }
#endif
#ifndef PORT_OWN_CRT_tanf
f32 tanf(f32 x) { return (f32)tan(x); }
#endif
#ifndef PORT_OWN_CRT_acosf
f32 acosf(f32 x) { return (f32)acos(x); }
#endif
#ifndef PORT_OWN_CRT_atan2f
f32 atan2f(f32 y, f32 x) { return (f32)atan2(y, x); }
#endif
#ifndef PORT_OWN_CRT_log10f
f32 log10f(f32 x) { return (f32)log10(x); }
#endif
#ifndef PORT_OWN_CRT_powf
f32 powf(f32 x, f32 y) { return (f32)pow(x, y); }
#endif
#ifndef PORT_OWN_CRT_floorf
f32 floorf(f32 x) { return (f32)floor(x); }
#endif
#ifndef PORT_OWN_CRT_ceilf
f32 ceilf(f32 x) { return (f32)ceil(x); }
#endif
f32 sqrtf(f32 x) { return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x))); }
#ifndef PORT_OWN_CRT_ldexpf
f32 ldexpf(f32 x, s32 exp) { return (f32)ldexp(x, exp); }
#endif

s32 rand(void);
void srand(u32 seed);
void port_libm_extra_state(void);

static u32 g_rand_state = 1u; /* the library's seed until srand */

s32 rand(void)
{
    g_rand_state = g_rand_state * 1103515245u + 12345u;
    return (s32)((g_rand_state >> 16) & 0x7fffu);
}

void srand(u32 seed) { g_rand_state = seed; }

void port_libm_extra_state(void) { port_extra_state(&g_rand_state, sizeof g_rand_state); }

u32 port_rand_state(void) { return g_rand_state; }

volatile u32 *port_rand_word(void) { return &g_rand_state; }
