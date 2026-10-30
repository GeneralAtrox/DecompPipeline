/* Palette RAM: the 1024 entries palettized textures (pixel formats 5 and 6) index, and PAL_RAM_CTRL, their format
 * (0 ARGB1555, 1 RGB565, 2 ARGB4444, 3 ARGB8888; a 16-bit format uses an entry's low half). The SDK layers write
 * them (Kamui 1 and 2's kmSetPaletteMode / kmSetPaletteData). Each 16-entry bank keeps a stamp of its last change,
 * so the texture cache (pvr_tex.c) decodes a palettized texture again when the entries it reads change. */
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

#define PAL_ENTRIES 1024u
#define PAL_BANK 16u
#define PAL_BANKS (PAL_ENTRIES / PAL_BANK)

static struct {
    u32 entry[PAL_ENTRIES];
    u32 format;
} g_pal;
static u32 g_bank_stamp[PAL_BANKS], g_format_stamp, g_stamp;

/* An in-between frame's palette writes are put back after it (runtime/frame_extra.c): every entry may differ. */
static void pal_undone(void *p, u32 size)
{
    (void)p;
    (void)size;
    g_format_stamp = ++g_stamp;
}

void pvr_set_palette_format(u32 format)
{
    if ((format & 3u) == g_pal.format) return;
    port_extra_undo(&g_pal, sizeof g_pal, pal_undone);
    g_pal.format = format & 3u;
    g_format_stamp = ++g_stamp;
}

/* Only the banks whose entries change get a new stamp: a game that uploads all 1024 entries each time (Kamui's
 * kmSetPaletteData) keeps the textures of the banks it left alone. */
void pvr_set_palette(u32 first, const u32 *entries, u32 count)
{
    int saved = 0;
    if (first >= PAL_ENTRIES) return;
    if (count > PAL_ENTRIES - first) count = PAL_ENTRIES - first;
    for (u32 at = first; at < first + count;) {
        u32 n = PAL_BANK - at % PAL_BANK;
        if (n > first + count - at) n = first + count - at;
        if (memcmp(g_pal.entry + at, entries + (at - first), n * sizeof *entries)) {
            if (!saved) port_extra_undo(&g_pal, sizeof g_pal, pal_undone);
            saved = 1;
            memmove(g_pal.entry + at, entries + (at - first), n * sizeof *entries);
            g_bank_stamp[at / PAL_BANK] = ++g_stamp;
        }
        at += n;
    }
}

u32 pvr_palette_entry(u32 index) { return g_pal.entry[index & (PAL_ENTRIES - 1u)]; }

u32 pvr_palette_format(void) { return g_pal.format; }

u32 pvr_palette_stamp(u32 first, u32 count)
{
    u32 s = g_format_stamp;
    for (u32 b = first / PAL_BANK; b < PAL_BANKS && b * PAL_BANK < first + count; b++)
        if (g_bank_stamp[b] > s) s = g_bank_stamp[b];
    return s;
}
