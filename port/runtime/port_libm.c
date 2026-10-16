/* The float math functions the game calls (the SHC C library's) that the 32-bit host C runtime only has as inline
 * wrappers in <math.h>, so there is no symbol to link: defined here on the double functions. <math.h> is not
 * included (its inline sinf etc. would clash with these definitions). sqrtf is the SSE root, as fsqrt. */
#include <xmmintrin.h>
#include "rd_rt.h"

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
