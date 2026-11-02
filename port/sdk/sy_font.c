/* Platform layer: the Dreamcast BIOS font, which the console keeps in its boot ROM and the port does not have. A game
 * draws its text boxes from it glyph by glyph; here it gets an open 12x24 face instead (sy_font_table.h, written by
 * tools/gen_sy_font.py). Fontset 1 is ASCII at 12x24. The other sets are Shift-JIS at 24x24: the full-width letters and
 * digits are drawn from the ASCII glyphs, and a code with no glyph is blank and logged once.
 * The lock never fails: nothing else uses this font.
 *
 * Needs from the game's headers: the function names below as the game's registry has them (Sdk_BiosFont* are registry
 * names for library functions with no SDK name). */
#include "game.h"
#include "platform.h"

#define ASCII_FIRST 0x20
#define ASCII_LAST 0x7f
#define ASCII_BYTES 36 /* 12 x 24 at 1 bit per pixel */
#define WIDE_BYTES 72  /* 24 x 24 */
#define ASCII_SET 1

static const u8 g_ascii[(ASCII_LAST - ASCII_FIRST + 1) * ASCII_BYTES] = {
#include "sy_font_table.h"
};

/* The ASCII glyphs, then one cell for the wide glyph last asked for: a game plots a glyph as soon as it has its address. */
static u8 g_font[sizeof g_ascii + WIDE_BYTES];
static s32 g_built;

/* The ASCII character a full-width Shift-JIS code stands for, or 0. */
static s32 wide_to_ascii(s32 code)
{
    if (code >= 0x824f && code <= 0x8258) return '0' + (code - 0x824f);
    if (code >= 0x8260 && code <= 0x8279) return 'A' + (code - 0x8260);
    if (code >= 0x8281 && code <= 0x829a) return 'a' + (code - 0x8281);
    switch (code) {
    case 0x8140: return ' ';
    case 0x8141: case 0x8143: return ',';
    case 0x8142: case 0x8144: return '.';
    case 0x8146: return ':';
    case 0x8147: return ';';
    case 0x8148: return '?';
    case 0x8149: return '!';
    case 0x815b: case 0x817c: return '-';
    case 0x815e: return '/';
    case 0x8169: return '(';
    case 0x816a: return ')';
    case 0x8175: case 0x8176: case 0x8167: case 0x8168: return '"';
    case 0x817b: return '+';
    default: return 0;
    }
}

/* A 12x24 glyph in the middle of a 24x24 cell. */
static void widen(u8 *wide, s32 ch)
{
    const u8 *from = g_ascii + (ch - ASCII_FIRST) * ASCII_BYTES;
    s32 y, x;

    memset(wide, 0, WIDE_BYTES);
    for (y = 0; y < 24; y++) {
        for (x = 0; x < 12; x++) {
            s32 src = y * 12 + x;
            s32 dst = y * 24 + x + 6;
            if (from[src >> 3] & (0x80 >> (src & 7))) wide[dst >> 3] |= (u8)(0x80 >> (dst & 7));
        }
    }
}

u8 *Sdk_BiosFontGetAddress(void)
{
    if (!g_built) {
        memcpy(g_font, g_ascii, sizeof g_ascii);
        g_built = 1;
    }
    return g_font;
}

s32 Sdk_BiosFontLock(void) { return 0; }

void Sdk_BiosFontUnlock(void) {}

void syBtFntGetInfo(s32 fontset, s32 code, u8 *info)
{
    (void)code;
    info[0] = fontset == ASCII_SET ? 12 : 24;
    info[1] = 24;
}

s32 syBtFntGetAddr(s32 fontset, s32 code)
{
    static u8 logged[0x10000 >> 3];
    u8 *cell = g_font + sizeof g_ascii;
    s32 ch;

    if (fontset == ASCII_SET) {
        if (code < ASCII_FIRST || code > ASCII_LAST) code = ASCII_FIRST;
        return (code - ASCII_FIRST) * ASCII_BYTES;
    }
    (void)Sdk_BiosFontGetAddress();
    ch = wide_to_ascii(code);
    if (ch) {
        widen(cell, ch);
        return (s32)sizeof g_ascii;
    }
    memset(cell, 0, WIDE_BYTES);
    if (!(logged[(code & 0xffff) >> 3] & (1 << (code & 7)))) {
        logged[(code & 0xffff) >> 3] |= (u8)(1 << (code & 7));
        port_log("sy_font: no glyph for Shift-JIS %04x, left blank\n", (unsigned)code);
    }
    return (s32)sizeof g_ascii;
}
