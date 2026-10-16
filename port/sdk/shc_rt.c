/* Platform layer: the SHC compiler's runtime helpers that have no readable C, written from their disassembly (any
 * SHC-built game links the same helpers). Needs from the game's headers: the registry names below (carried over to a
 * new game for byte-identical functions by xproject.py names), ShcDivResult {quot, rem}, g_errno and
 * SHC_ERRNO_DIV0.
 *
 * Shc_BlockCopy is the block move the compiler emits for a struct assignment: size bytes (r0) from src (r2) to dst
 * (r1); the original copies words, unrolled, and the sizes it gets are multiples of 4. */
#include "game.h"
#include "platform.h"

void Shc_BlockCopy(u32 size, void *dst, const void *src) { memmove(dst, src, size); }

/* SH-4 shld by a register: a count >= 0 shifts left by count & 31; a negative one shifts right (logical) by
 * 32 - (count & 31), or clears the word when count & 31 is 0. */
static u32 shld(u32 value, s32 count)
{
    if (count >= 0) {
        return value << (count & 31);
    }
    return (count & 31) == 0 ? 0u : value >> (32 - (count & 31));
}

/* Shc_BitFieldGetU32: a bit field read. spec (r1) = shift << 8 | width, shift counted from the word's most significant bit
 * (SHC allocates bit fields from the top): (word << shift) >> (32 - width). */
u32 Shc_BitFieldGetU32(u32 spec, const u32 *word)
{
    u32 value = shld(*word, (s32)((spec >> 8) & 0xffu));
    return shld(value, (s32)(spec & 0xffu) - 32);
}

/* Shc_BitFieldGetU16: the same over a halfword, loaded sign-extended and shifted 16 more. */
u32 Shc_BitFieldGetU16(u32 spec, const u16 *half)
{
    u32 value = shld((u32)(s32)(s16)*half, (s32)((spec >> 8) & 0xffu) + 16);
    return shld(value, (s32)(spec & 0xffu) - 32);
}

/* Shc_ModU32: dividend % divisor (r1 % r0, unsigned). A zero divisor sets errno and returns 0. */
u32 Shc_ModU32(u32 divisor, u32 dividend)
{
    if (divisor == 0u) {
        g_errno = SHC_ERRNO_DIV0;
        return 0u;
    }
    return dividend % divisor;
}

/* Shc_Div: the C library's div(): quotient (truncated) and remainder = numer - quot * denom, through the hidden
 * result pointer. A zero denominator sets errno and leaves *out alone. */
void Shc_Div(s32 numer, s32 denom, u32 unusedR6, u32 unusedR7, ShcDivResult *out)
{
    s32 quot;

    (void)unusedR6;
    (void)unusedR7;
    if (denom == 0) {
        g_errno = SHC_ERRNO_DIV0;
        return;
    }
    quot = (denom == -1) ? (s32)(0u - (u32)numer) : numer / denom; /* no host trap on INT_MIN / -1 */
    out->quot = quot;
    out->rem = (s32)((u32)numer - (u32)quot * (u32)denom);
}
