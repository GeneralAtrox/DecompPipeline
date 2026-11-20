/* xb_xenos_gamma.h: the Xenos GPU's piecewise-linear gamma, the kit's own C (not copied from COGBox or Xenia; the behaviour is noted in
 * REFERENCES.md, "Xenos gamma").
 *
 * Nearest existing: xb_xenos_colour.h - it holds COGBox's resolve rules under COGBox's licence; these rules are the kit's own, kept apart so
 * the two provenances don't mix.
 *
 * Two halves of one curve:
 *  - A texture fetch constant marks a channel gamma (word 0, the channel's two sign bits 3). The texture unit turns such a channel to linear
 *    through the piecewise-linear curve before the shader sees it. The converted shader does the turning (tools/shader_convert.py xb_tg); the
 *    kit gives it, per fetch slot, a 4-bit mask of the shader's channels to turn (xenos_gamma_mask).
 *  - The front buffer (2_10_10_10) goes out through the display's gamma ramp, which the game sets with SetGammaRamp. The XDK keeps the ramp in
 *    the piecewise-linear form at device +0x3a9c: 128 entries a channel, red, then green, then blue, each a big-endian u16 base then u16 delta,
 *    10-bit values shifted left 6. A 10-bit value x reads entry x >> 3: base + delta * (x & 7) / 8 (xenos_ramp_eval).
 *
 * Switches: XB_TEX_GAMMA=0 samples gamma textures raw, as before 2026-11-13; XB_GAMMA_RAMP=0 shows the front buffer without the ramp. */
#ifndef XB_XENOS_GAMMA_H
#define XB_XENOS_GAMMA_H
#include <stdlib.h>

/* The shader's channels (bit m for x, y, z, w) a fetch constant turns to linear. f0 is word 0 (sign of component c at bits 2 + 2c), swz is
 * word 3's swizzle field (bits 1..12: 3 bits an output channel, 0..3 a component, 4 zero, 5 one); the kit's decode applies the swizzle, so
 * the mask is in the shader's channel order. A constant 0 or 1 is never turned */
static __inline unsigned xenos_gamma_mask(unsigned f0, unsigned swz)
{
    unsigned m, mask = 0;
    for (m = 0; m < 4; m++) {
        unsigned sel = (swz >> (3u * m)) & 7u;
        if (sel < 4u && ((f0 >> (2u + 2u * sel)) & 3u) == 3u) mask |= 1u << m;
    }
    return mask;
}

/* An 8-bit gamma value to linear, 0..1016/1024: the curve's slope doubles at 64, 96 and 192 (the same as shader_convert.py xb_pwl) */
static __inline float xenos_pwl_to_linear(unsigned v)
{
    int g = (int)(v & 255u), l = g < 64 ? g : g < 96 ? 2 * g - 64 : g < 192 ? 4 * g - 256 : 8 * g - 1024;
    return (float)l / 1024.0f;
}

/* The ramp: pairs[ch * 128 + i] is entry i of channel ch as the guest stores it, base in the high 16 bits and delta in the low (one big-endian
 * u32). x is a 10-bit value; the result is 10-bit */
static __inline unsigned xenos_ramp_eval(const unsigned *pairs, unsigned ch, unsigned x)
{
    unsigned p = pairs[ch * 128u + ((x & 1023u) >> 3)], base = (p >> 22) & 1023u, delta = (p >> 6) & 1023u;
    return base + ((delta * (x & 7u)) >> 3);
}

static __inline int xb_tex_gamma_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_TEX_GAMMA"); on = !(e && *e == '0'); }
    return on;
}

static __inline int xb_gamma_ramp_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_GAMMA_RAMP"); on = !(e && *e == '0'); }
    return on;
}
#endif
