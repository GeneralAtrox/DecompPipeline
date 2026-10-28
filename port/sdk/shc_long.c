/* Platform layer: the SHC compiler's register-ABI division and block-move helpers (the long-division and move
 * routines an SHC-built game calls for /, % and struct copies), written from their disassembly. Needs from the game's
 * headers: the registry names below, g_errno and SHC_ERRNO_DIV0. Each is bit-exact with the original machine code
 * over its contract, including the overlap order of the moves (checked against xemu running the original bytes).
 *
 * Division: the dividend is r1, the divisor r0 (the prototypes list the divisor first). A zero divisor stores
 * SHC_ERRNO_DIV0 in g_errno and returns 0. The quotient is truncated toward zero; INT_MIN / -1 is INT_MIN and
 * INT_MIN % -1 is 0 (the div1 chain wraps; the host's idiv would trap).
 *
 * Moves: size bytes (r0) from src (r2) to dst (r1). The word moves need src and dst word aligned. */
#include "game.h"
#include "platform.h"

s32 Shc_DivLs(s32 divisor, s32 dividend)
{
    if (divisor == 0) {
        g_errno = SHC_ERRNO_DIV0;
        return 0;
    }
    if (divisor == -1) return (s32)(0u - (u32)dividend);
    return dividend / divisor;
}

u32 Shc_DivLu(u32 divisor, u32 dividend)
{
    if (divisor == 0u) {
        g_errno = SHC_ERRNO_DIV0;
        return 0u;
    }
    return dividend / divisor;
}

s32 Shc_ModLs(s32 divisor, s32 dividend)
{
    if (divisor == 0) {
        g_errno = SHC_ERRNO_DIV0;
        return 0;
    }
    if (divisor == -1) return 0;
    return dividend % divisor;
}

u32 Shc_ModLu(u32 divisor, u32 dividend)
{
    if (divisor == 0u) {
        g_errno = SHC_ERRNO_DIV0;
        return 0u;
    }
    return dividend % divisor;
}

/* SH-4 shld by a register: a count >= 0 shifts left by count & 31; a negative one shifts right (logical) by
 * 32 - (count & 31), or clears the word when count & 31 is 0. */
static u32 shld(u32 value, s32 count)
{
    if (count >= 0) return value << (count & 31);
    return (count & 31) == 0 ? 0u : value >> (32 - (count & 31));
}

/* Shc_BitfieldStore: a bit field write. spec (r1) = shift << 8 | width, shift counted from the word's most
 * significant bit (SHC allocates bit fields from the top); value (r0), word (r2). The field's bits of *word become
 * value's low width bits through an xor of the old and new field; returns value cut to width. Each step is the
 * original's shld, so out-of-range specs give its results too. */
u32 Shc_BitfieldStore(u32 value, u32 spec, u32 *word)
{
    u32 old = *word, width = spec & 0xffu, shift = (spec >> 8) & 0xffu;
    s32 up = (s32)(32u - width);
    u32 field = shld(value, up);
    u32 diff = shld(shld(old, (s32)shift) ^ field, -up);
    field = shld(field, -up);
    *word = shld(diff, up - (s32)shift) ^ old;
    return field;
}

/* Shc_QuickMove: size <= 64 goes through a jump table into an unrolled word copy, highest word first, each word read
 * then written (size a multiple of 4: the table is indexed by size itself); a larger size copies size / 4 words
 * lowest first (the bytes past the last whole word are not copied). */
void Shc_QuickMove(u32 size, void *dst, const void *src)
{
    u32 *d = (u32 *)dst;
    const u32 *s = (const u32 *)src;
    if (size <= 64u) {
        for (u32 i = size / 4u; i-- > 0u;) d[i] = s[i];
    } else {
        for (u32 i = 0; i < size / 4u; i++) d[i] = s[i];
    }
}

/* Shc_QuickMoveWords / Shc_QuickMoveWordsR3: an unrolled word copy for size <= 64 (a multiple of 4), highest word
 * first, each word read before the word above it is written (the chain loads one word ahead). The chain keeps
 * alternate words in r0 and r3, so the compiler calls QuickMoveWords for an even word count and QuickMoveWordsR3 for
 * an odd one (the other entry would store a register's leftover value in the last word). */
static void move_words_down(u32 size, u32 *d, const u32 *s)
{
    u32 n = size / 4u;
    if (n == 0u) return;
    u32 next = s[n - 1u];
    for (u32 i = n; i-- > 0u;) {
        u32 cur = next;
        if (i > 0u) next = s[i - 1u];
        d[i] = cur;
    }
}

void Shc_QuickMoveWords(u32 size, void *dst, const void *src, u32 unusedR3)
{
    (void)unusedR3;
    move_words_down(size, (u32 *)dst, (const u32 *)src);
}

void Shc_QuickMoveWordsR3(u32 size, void *dst, const void *src, u32 unusedR3)
{
    (void)unusedR3;
    move_words_down(size, (u32 *)dst, (const u32 *)src);
}

/* Shc_SlowMove: a byte copy, lowest byte first (any alignment; size 0 copies nothing). */
void Shc_SlowMove(u32 size, void *dst, const void *src)
{
    u8 *d = (u8 *)dst;
    const u8 *s = (const u8 *)src;
    for (u32 i = 0; i < size; i++) d[i] = s[i];
}

/* Shc_SlowStrCpy: strcpy (r1 to r0). Word aligned on both sides it copies whole words while a word has no zero byte,
 * then bytes up to the terminator; otherwise bytes. Either way the bytes written are the string and its terminator. */
void Shc_SlowStrCpy(char *dst, const char *src)
{
    u32 i = 0;
    while ((dst[i] = src[i]) != 0) i++;
}

/* Sh4_CopyWords: an SDK block copy with no library name yet (dst r4, src r5, size r6; a game binds its address with a
 * game.json hardware row). With dst, src and size all multiples of 8 it moves 8 bytes at a time through the FPU's
 * pair moves (fschg), otherwise one word at a time; either way size / 4 words, lowest first. The bytes past the last
 * whole word are not copied; a size under 4 (the dt loop on the console would run 2^32 times) copies nothing. */
void Sh4_CopyWords(u32 *dst, const u32 *src, u32 size)
{
    for (u32 i = 0; i < size / 4u; i++) dst[i] = src[i];
}

/* Sh4_CopyAligned: an SDK block copy with no library name yet (dst r4, src r5, size r6; bound as Sh4_CopyWords is).
 * It moves size bytes, lowest first, in the widest unit that dst, src and size are all multiples of: 8 bytes through
 * the FPU's pair moves (fschg), else 4, 2 or 1 (each unit read whole before it is written). A size of 0 (the dt
 * loop on the console would run 2^32 times) copies nothing. */
void Sh4_CopyAligned(void *dst, const void *src, u32 size)
{
    u32 bits = (u32)(uintptr_t)dst | (u32)(uintptr_t)src | size;
    u32 unit = !(bits & 7u) ? 8u : !(bits & 3u) ? 4u : !(bits & 1u) ? 2u : 1u;
    u8 *vram = port_texture_window((u32)(uintptr_t)dst, size), *d = vram ? vram : dst, t[8];
    const u8 *s = src;
    for (u32 i = 0; i < size; i += unit) {
        for (u32 k = 0; k < unit; k++) t[k] = s[i + k];
        for (u32 k = 0; k < unit; k++) d[i + k] = t[k];
    }
    if (vram) port_texture_written((u32)(uintptr_t)dst, size);
}
