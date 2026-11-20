/* xb_xenos_colour.h: the Xenos GPU's colour rules a resolve needs, copied from COGBox and put into C for the kit.
 *
 * Copied from COGBox (https://github.com/Shagg88/COGBox, commit a0557e9), Gears2-Native/src/layer0_xenos/include/xenos/colour_formats.h:
 * CopyDestExpBias, ExpBiasFactor, PreClampedFloat32To7e3, Float32To7e3, Float7e3To32, k7e3Max, Fixed16ResolveDecode (Canary range) and
 * Clamp7e3RenderTargetRange, renamed xenos_* and written in C. COGBox is MIT:
 *
 *   MIT License. Copyright (c) 2026 Shagg.
 *   Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the
 *   "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish,
 *   distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
 *   following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
 *   Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
 *   OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR
 *   ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
 *   SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 * COGBox's file says its behaviour follows Xenia Canary e6cd2e4bc (BSD-3-Clause, Copyright (c) Ben Vanik and Xenia project contributors):
 * gpu/dxbc_shader_translator_om.cc 3816-3889 (PreClampedFloat32To7e3 / UnclampedFloat32To7e3, DirectXTex-derived), gpu/xenos.cc 104-120
 * (Float7e3To32), gpu/shaders/resolve.xesli 148-150 (the exponent bias factor), gpu/shaders/pixel_formats.xesli 422-447 (16-bit fixed point).
 * Xenia's notice, which these parts keep:
 *
 *   Copyright (c) Ben Vanik and Xenia project contributors. All rights reserved. Redistribution and use in source and binary forms, with or
 *   without modification, are permitted provided that the following conditions are met: 1. Redistributions of source code must retain the
 *   above copyright notice, this list of conditions and the following disclaimer. 2. Redistributions in binary form must reproduce the above
 *   copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the
 *   distribution. 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products
 *   derived from this software without specific prior written permission. THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 *   "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
 *   PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 *   SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
 *   OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 *   (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 * In the kit this is a bring-up layer (emulated hardware: the console's render-target number formats), listed in DecompPipeline's
 * docs/RUNTIME_TARGET.md emulated-layers table. Its one switch, XB_COGBOX_7E3=1, clamps a scaled resolve's source to what a 7e3 target holds
 * (RGB 0..31.875, alpha 0..1) before the exponent bias; off (the default) the copy keeps the host's unclamped 16-bit floats, as Xenia's
 * default path does (COGBox notes/45). See the kit's THIRD_PARTY_NOTICES.md. */
#ifndef XB_XENOS_COLOUR_H
#define XB_XENOS_COLOUR_H
#include <stdlib.h>
#include <string.h>

#define XENOS_7E3_MAX 31.875f

static __inline unsigned xenos_f2u(float f) { unsigned u; memcpy(&u, &f, 4); return u; }
static __inline float xenos_u2f(unsigned u) { float f; memcpy(&f, &u, 4); return f; }
static __inline float xenos_max_gpu(float x, float lo) { return x > lo ? x : lo; }   /* NaN -> lo */
static __inline float xenos_min_gpu(float x, float hi) { return x < hi ? x : hi; }   /* NaN -> hi */

/* RB_COPY_DEST_INFO bits 16..21, signed */
static __inline int xenos_copy_dest_exp_bias(unsigned reg) { return (int)(reg << 10) >> 26; }
/* The XDK's Resolve flags carry the same bias in bits 26..31 (Too Human's ec000000 is -5); its library moves it into RB_COPY_DEST_INFO */
static __inline int xenos_resolve_flags_exp_bias(unsigned flags) { return (int)flags >> 26; }
/* RB_COLOR_INFO bits 20..25, signed: a render target's own bias, a factor on the pixel shader's colour (shadow_renderer.cpp 7848-7862, the
 * shader's epilogue in hlsl.cpp 1511-1512). The XDK keeps the register in a surface header's +0x1c; SetRenderTarget copies it to the device */
static __inline int xenos_color_info_exp_bias(unsigned reg) { return (int)(reg << 6) >> 26; }
/* 2^bias built as float bits, exact for -32..31 (resolve.xesli 148-150) */
static __inline float xenos_exp_bias_factor(int bias) { return xenos_u2f((unsigned)bias * (1u << 23) + 0x3F800000u); }

/* 7e3 (k_2_10_10_10_FLOAT): 3-bit exponent with bias 3, 7-bit mantissa, no sign, range 0..31.875. Below 0.25 a denormal; round to even */
static __inline unsigned xenos_preclamped_f32_to_7e3(float f)
{
    unsigned u = xenos_f2u(f), biased;
    if (u < 0x3E800000u) {
        unsigned shift = 125u - (u >> 23);
        biased = ((u & 0x7FFFFFu) | 0x800000u) >> (shift < 24u ? shift : 24u);
    } else
        biased = u + 0xC2000000u;   /* exponent - 124 */
    return ((biased + 0x7FFFu + ((biased >> 16) & 1u)) >> 16) & 0x3FFu;
}
static __inline unsigned xenos_f32_to_7e3(float f) { return xenos_preclamped_f32_to_7e3(xenos_min_gpu(xenos_max_gpu(f, 0.0f), XENOS_7E3_MAX)); }
static __inline float xenos_7e3_to_f32(unsigned f10)
{
    unsigned mantissa, exponent;
    f10 &= 0x3FFu;
    if (!f10) return 0.0f;
    mantissa = f10 & 0x7Fu;
    exponent = f10 >> 7;
    if (!exponent) {   /* a denormal: normalised */
        unsigned lz = 0;
        while (!(mantissa & (0x80u >> (lz + 1u)))) lz++;   /* the leading zeros of the 8-bit field past its top bit */
        lz += 1u;
        exponent = (unsigned)(1 - (int)lz);
        mantissa = (mantissa << lz) & 0x7Fu;
    }
    return xenos_u2f(((exponent + 124u) << 23) | (mantissa << 16));
}
/* What a 7e3 target holds of a colour: RGB clamped to 0..31.875, alpha saturated (the hardware's range; Xenia's ROV path) */
static __inline void xenos_clamp_7e3(float c[4])
{
    int i;
    for (i = 0; i < 3; i++) c[i] = xenos_min_gpu(xenos_max_gpu(c[i], 0.0f), XENOS_7E3_MAX);
    c[3] = xenos_min_gpu(xenos_max_gpu(c[3], 0.0f), 1.0f);
}

/* 16-bit fixed point (k_16_16, k_16_16_16_16): -32..32, 0x7FFF = 32, 0x8001 = -32 (Canary's decode) */
static __inline float xenos_fixed16_to_f32(unsigned bits16) { return xenos_max_gpu((float)(short)(bits16 & 0xFFFFu) * (32.0f / 32767.0f), -32.0f); }

/* The switch: XB_COGBOX_7E3=1 clamps a scaled resolve's source to the 7e3 range (xb_gpu_resolve) */
static __inline int xb_cogbox_7e3(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_COGBOX_7E3"); on = e && *e == '1'; }
    return on;
}

/* XB_COGBOX_RT_BIAS=1: each render target's own exponent bias applied to the pixel shader's colour, as the console does; off, the factor is 1 */
static __inline int xb_cogbox_rt_bias(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_COGBOX_RT_BIAS"); on = e && *e == '1'; }
    return on;
}
#endif
