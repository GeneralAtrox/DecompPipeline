/* Texture packs: replacement images for guest textures, host-side only (guest memory, timing and game logic are
 * untouched). Off unless game.json "texture_pack" names a folder or --texture-pack DIR is given; --texture-pack off
 * turns it off, on turns it back on. A check run (--fixed-clock: regress goldens, lockstep, emulator comparisons; the
 * tools' headless runs have it) ignores the game's default pack, like widescreen, so it sees the original;
 * --texture-pack on, NAME or DIR asks for it there.
 *
 * A release carries its packs beside port.exe (tools/package_release.py): packs/<folder>. The game's default pack is
 * looked for there first, under the last part of its game.json path, since that path only exists on the machine that
 * built it. Every other folder there is an optional pack: --texture-pack NAME (the Display menu's Texture pack entry,
 * saved in settings.txt) shows it over the game's default, whose images still show for the keys it lacks.
 *
 * A pack is a folder: index.txt, one "KEY FILE" per line ('#' starts a comment), FILE a PNG beside it (any size: the
 * renderer samples by normalized coordinates, so a higher-resolution image just works). KEY, 16 hex digits, is the
 * FNV-1a 64 hash of what the texture unit reads for the texture: the TCW's format bits (tcw & 0xfe000000: mip-map,
 * VQ, pixel format, non-twiddled, stride), width, height and stride as little-endian u32s, then the texture memory
 * bytes the decoder reads (pvr_texture_bytes from the TCW address; a 16-bit twiddled mip chain from its 1x1 level
 * at +6, the bytes before it are not texels). The VQ codebook is part of those bytes, so a VQ texture with the same
 * indices and another codebook is another key. port/tools/texture_pack.py computes the same key from a texture's
 * bytes and writes packs (docs: port/README.md, Texture packs).
 *
 * pvr_tex.c asks when it decodes a texture, which is only after the guest wrote its memory: an unchanged texture is
 * never hashed again. A hit's image is read once and kept with its mip chain (box-filtered, for mip-mapped guests).
 * A palettized texture (pixel formats 5 and 6) has no pack image: its texels depend on palette RAM, not hashed.
 * A line's word over keeps the game's own texture under the image: where the image's alpha is 0 the game's texel shows
 * (the image is any whole multiple of the texture's size), and every other pixel is the image's. So a pack can change part
 * of a texture without holding the rest of the game's art; alpha 1 clears a pixel.
 * A line's rim=RRGGBB is for a model that draws its sides untextured right after the textured face: those vertices
 * take that colour, scaled by their strongest channel so the game's lighting stays (the button icons' lower edge).
 * Nearest existing: pvr_tex.c's decoded-texture cache (keyed by TCW, not by content; this sits under it). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "pvr_int.h"
#include "platform.h"

#ifdef PORT_TEXTURE_PACK
#define PACK_DEFAULT PORT_TEXTURE_PACK /* game.json texture_pack */
#else
#define PACK_DEFAULT ""
#endif
#define TCW_MIP (1u << 31)
#define TCW_VQ (1u << 30)
#define TCW_NON_TWIDDLED (1u << 26)
#define TCW_ADDR_MASK 0x1fffffu
#define TCW_FORMAT_BITS 0xfe000000u
#define MIP16_FIRST 6u
#define FNV_BASIS 0xcbf29ce484222325ull
#define FNV_PRIME 0x100000001b3ull
#define MAX_PATH_TEXT PVR_PACK_PATH
enum { LAYER_CHOSEN, LAYER_GAME, LAYERS };
#define MAX_RIMS 16u
#define RIM_SET 0x1000000u

typedef struct PackEntry {
    u64 key;
    char *file;
    u32 *texels; /* the image's levels, top first; NULL: not read yet */
    u32 w, h, levels, mipped, failed;
    u32 variant; /* the pad style the image is for (base/pad_style.c): 0 for every style, 1 xbox, 2 playstation */
    u32 layer;   /* whose image: LAYER_CHOSEN comes before LAYER_GAME */
    u32 rim;     /* RIM_SET | RRGGBB from the line's rim= word, or 0 */
    u32 over;    /* the line's over word: the image goes over the game's own texture */
} PackEntry;

/* The textures decoded with a rim= image. The scene parser asks each time an untextured polygon follows a textured
 * one, so this is a look-up, not a hash; a texture's first scene shows its rim as the game draws it. */
typedef struct RimRow {
    u32 tcw, w, h, rim;
} RimRow;

static char g_dir[MAX_PATH_TEXT] = PACK_DEFAULT; /* the game's pack, or --texture-pack DIR */
static int g_dir_given;                          /* it is --texture-pack DIR: not looked for beside port.exe */
static char g_chosen[PVR_PACK_NAME];             /* --texture-pack NAME: a folder of packs/ beside port.exe */
static char g_layer_dir[LAYERS][MAX_PATH_TEXT];
static int g_wanted = -1;           /* -1: the game's default (not in a check run); off: 0; on, NAME or DIR: 1 */
static volatile LONG g_enabled = -1; /* -1: not loaded yet */
static volatile LONG g_generation;   /* bumped by a switch: pvr_tex.c then decodes everything again */
static PackEntry *g_entry;
static u32 g_count, g_cap, g_hits, g_rim_lines, g_rim_rows;
static RimRow g_rim[MAX_RIMS];

static u64 fnv(u64 h, const u8 *p, u32 n)
{
    while (n--) h = (h ^ *p++) * FNV_PRIME;
    return h;
}

static u64 fnv32(u64 h, u32 v)
{
    u8 b[4] = {(u8)v, (u8)(v >> 8), (u8)(v >> 16), (u8)(v >> 24)};
    return fnv(h, b, 4);
}

u64 pvr_pack_key(const u8 *vram, u32 tcw, u32 w, u32 h, u32 stride)
{
    u32 at = (tcw & TCW_ADDR_MASK) << 3, n = pvr_texture_bytes(tcw, w, h, stride), skip = 0;
    u64 k = fnv32(fnv32(fnv32(fnv32(FNV_BASIS, tcw & TCW_FORMAT_BITS), w), h), stride);
    if ((tcw & TCW_MIP) && !(tcw & (TCW_VQ | TCW_NON_TWIDDLED))) skip = MIP16_FIRST;
    for (u32 i = skip; i < n; i++) k = (k ^ vram[(at + i) & (PVRX_VRAM_SIZE - 1u)]) * FNV_PRIME;
    return k;
}

static int by_key(const void *a, const void *b)
{
    const PackEntry *x = a, *y = b;
    if (x->key != y->key) return x->key < y->key ? -1 : 1;
    if (x->layer != y->layer) return x->layer < y->layer ? -1 : 1;
    return x->variant < y->variant ? -1 : x->variant > y->variant;
}

/* An index line's variant word: "" is every style (0), xbox 1, playstation 2; -1 for any other word. */
static int variant_of(const char *s)
{
    s += strspn(s, " \t");
    if (!*s || *s == '\r' || *s == '\n') return 0;
    if (!strncmp(s, "xbox", 4) && !s[4 + strspn(s + 4, " \t\r\n")]) return 1;
    if (!strncmp(s, "playstation", 11) && !s[11 + strspn(s + 11, " \t\r\n")]) return 2;
    return -1;
}

/* The entry for the key: the one for the style in use if the first pack holding the key has it, else the one for
 * every style. */
static PackEntry *find_entry(u64 key)
{
    u32 lo = 0, hi = g_count, style = (u32)pad_art_style(), layer;
    PackEntry *any = NULL;
    while (lo < hi) {
        u32 mid = (lo + hi) / 2u;
        if (g_entry[mid].key < key) lo = mid + 1u;
        else hi = mid;
    }
    layer = lo < g_count ? g_entry[lo].layer : 0u;
    for (; lo < g_count && g_entry[lo].key == key && g_entry[lo].layer == layer; lo++) {
        if (g_entry[lo].variant == style && style) return &g_entry[lo];
        if (!g_entry[lo].variant) any = &g_entry[lo];
    }
    return any;
}

/* The last part of the game's pack path: its folder name beside port.exe. */
static const char *game_pack_name(void)
{
    const char *slash = strrchr(g_dir, '/');
    return slash ? slash + 1 : g_dir;
}

/* The folder of the game's pack: beside port.exe if it is there, else where game.json said. */
static const char *game_pack_dir(char *out, size_t cap)
{
    if (g_dir_given) return g_dir;
    pack_bundled_dir(game_pack_name(), out, cap);
    return pack_has_index(out) ? out : g_dir;
}

/* A folder's index.txt added to g_entry for the layer; the number of entries (0: no pack, logged) */
static u32 load_index(const char *dir, u32 layer)
{
    char path[MAX_PATH_TEXT + 16], line[MAX_PATH_TEXT];
    WCHAR wide[MAX_PATH_TEXT + 16];
    u32 bad = 0, before = g_count;
    snprintf(g_layer_dir[layer], sizeof g_layer_dir[layer], "%s", dir);
    snprintf(path, sizeof path, "%s/index.txt", dir);
    FILE *f = NULL;
    if (!pack_widen(path, wide, MAX_PATH_TEXT + 16) || _wfopen_s(&f, wide, L"r") != 0 || !f) {
        port_log("pvr: texture pack: no %s, the pack is off\n", path);
        return 0;
    }
    while (fgets(line, sizeof line, f)) {
        char *hash = strchr(line, '#'), *end, *file, *rest;
        unsigned long long key;
        int variant = 0;
        u32 rim = 0, over = 0;
        if (hash) *hash = '\0';
        key = _strtoui64(line, &end, 16);
        file = end + strspn(end, " \t");
        rest = file + strcspn(file, " \t\r\n");
        if (*rest) {
            char *word = strstr(rest + 1, "rim="), *stop;
            if (word) {
                rim = RIM_SET | strtoul(word + 4, &stop, 16);
                if (stop != word + 10 || (rim & ~(RIM_SET | 0xffffffu))) variant = -1;
                *word = '\0';
            }
            word = strstr(rest + 1, "over");
            if (word && strspn(word + 4, " \t\r\n") == strlen(word + 4)) {
                over = 1;
                *word = '\0';
            }
            if (!variant) variant = variant_of(rest + 1);
        }
        *rest = '\0';
        if (variant < 0 || end == line || file == end || !*file) {
            bad += strspn(line, " \t\r\n") != strlen(line);
            continue;
        }
        if (g_count == g_cap) {
            PackEntry *p = realloc(g_entry, (size_t)(g_cap ? g_cap * 2u : 256u) * sizeof *p);
            if (!p) break;
            g_entry = p;
            g_cap = g_cap ? g_cap * 2u : 256u;
        }
        memset(&g_entry[g_count], 0, sizeof *g_entry);
        g_entry[g_count].key = key;
        g_entry[g_count].variant = (u32)variant;
        g_entry[g_count].layer = layer;
        g_entry[g_count].rim = rim;
        g_entry[g_count].over = over;
        g_rim_lines += rim != 0u;
        g_entry[g_count++].file = _strdup(file);
    }
    fclose(f);
    port_log("pvr: texture pack: %u images in %s%s\n", g_count - before, dir, bad ? " (some lines unreadable)" : "");
    return g_count - before;
}

static int wanted(void) { return g_wanted < 0 ? !host_fixed_clock() : g_wanted; }

/* What is wanted now into g_entry, sorted by key, dropping what a switch left there. Runs where images are used, so
 * nothing else holds an image (pvr_tex.c copies one into the device at once). */
static int load_packs(void)
{
    char dir[MAX_PATH_TEXT];
    for (u32 i = 0; i < g_count; i++) {
        free(g_entry[i].file);
        free(g_entry[i].texels);
    }
    g_count = g_hits = g_rim_lines = g_rim_rows = 0;
    if (!wanted()) return 0;
    if (g_chosen[0]) {
        pack_bundled_dir(g_chosen, dir, sizeof dir);
        load_index(dir, LAYER_CHOSEN);
    }
    if (g_dir[0]) load_index(game_pack_dir(dir, sizeof dir), LAYER_GAME);
    if (g_count) qsort(g_entry, g_count, sizeof *g_entry, by_key);
    return g_count != 0;
}

static int enabled(void)
{
    if (g_enabled < 0) {
        LONG generation = g_generation;
        int on = load_packs();
        if (generation == g_generation) InterlockedExchange(&g_enabled, on); /* else a switch came meanwhile: again */
    }
    return g_enabled > 0;
}

static void note_rim(u32 tcw, u32 w, u32 h, u32 rim)
{
    for (u32 i = 0; i < g_rim_rows; i++)
        if (g_rim[i].tcw == tcw && g_rim[i].w == w && g_rim[i].h == h) {
            g_rim[i].rim = rim;
            return;
        }
    if (rim && g_rim_rows < MAX_RIMS) g_rim[g_rim_rows++] = (RimRow){tcw, w, h, rim};
}

u32 pvr_pack_rim(u32 tcw, u32 tsp)
{
    u32 w = 8u << ((tsp >> 3) & 7u), h = 8u << (tsp & 7u);
    if (!g_rim_rows || !enabled()) return 0;
    for (u32 i = 0; i < g_rim_rows; i++)
        if (g_rim[i].tcw == tcw && g_rim[i].w == w && g_rim[i].h == h) return g_rim[i].rim;
    return 0;
}

u32 pvr_pack_levels(u32 tcw, u32 w, u32 h, u32 stride, const u32 **texels, u32 *pw, u32 *ph)
{
    PackEntry *e;
    u64 key;
    char path[MAX_PATH_TEXT * 2 + 2];
    u32 mipped = (tcw & TCW_MIP) && !(tcw & TCW_NON_TWIDDLED), pal_first, pal_count;
    if (!enabled() || pvr_palette_range(tcw, &pal_first, &pal_count)) return 0;
    key = pvr_pack_key(pvr_vram(), tcw, w, h, stride);
    e = find_entry(key);
    if (g_rim_lines) note_rim(tcw, w, h, e ? e->rim : 0u);
    if (!e || e->failed) return 0;
    if (!e->texels) {
        snprintf(path, sizeof path, "%s/%s", g_layer_dir[e->layer], e->file);
        e->texels = pvr_pack_read_png(path, &e->w, &e->h);
        if (!e->texels) {
            e->failed = 1;
            return 0;
        }
        if (e->over && !pvr_pack_put_over(e->texels, e->w, e->h, e->file, tcw, w, h, stride)) {
            e->failed = 1;
            return 0;
        }
        e->levels = 1;
    }
    if (mipped && !e->mipped) {
        e->levels = pvr_pack_mips(e->texels, e->w, e->h);
        e->mipped = 1;
    }
    if (!g_hits++) port_log("pvr: texture pack: first image used (%016llx)\n", (unsigned long long)key);
    *texels = e->texels;
    *pw = e->w;
    *ph = e->h;
    return mipped ? e->levels : 1u;
}

int pvr_pack_has_style(int style)
{
    if (!enabled()) return 0;
    for (u32 i = 0; i < g_count; i++)
        if (g_entry[i].variant == (u32)style) return 1;
    return 0;
}

u32 pvr_pack_generation(void) { return (u32)g_generation + pad_style_serial(); }

/* The menu's Texture pack entry (pvr_pack_bundled.c). */
u32 pvr_pack_choices(const char *const **labels, const char *const **values)
{
    return pack_read_choices(g_dir[0] != 0, g_dir_given ? "" : game_pack_name(), labels, values);
}

int pvr_pack_available(void)
{
    const char *const *labels, *const *values;
    return pvr_pack_choices(&labels, &values) > 1u;
}

/* The entry in use. A saved name whose folder has gone shows as the game's default, which is what the run then has. */
int pvr_pack_choice(void)
{
    const char *const *labels, *const *values;
    u32 count = pvr_pack_choices(&labels, &values);
    if (!wanted()) return 0;
    for (u32 i = 1; i < count; i++)
        if (!strcmp(values[i], g_chosen[0] ? g_chosen : "on")) return (int)i;
    return g_dir[0] ? 1 : 0;
}

void pvr_pack_set_choice(int choice)
{
    const char *const *labels, *const *values;
    if (choice < 0 || (u32)choice >= pvr_pack_choices(&labels, &values)) return;
    g_wanted = choice != 0;
    snprintf(g_chosen, sizeof g_chosen, "%s", choice && strcmp(values[choice], "on") ? values[choice] : "");
    InterlockedExchange(&g_enabled, -1);
    InterlockedIncrement(&g_generation);
}

/* --texture-pack NAME (a folder of packs/ beside port.exe) | DIR | on | off */
int pvr_pack_option(const char *arg, const char *value)
{
    if (pvr_keys_option(arg, value) || pad_style_option(arg, value) || pvr_tex_option(arg, value)) return 2;
    if (strcmp(arg, "--texture-pack") || !value) return 0;
    g_chosen[0] = '\0';
    if (!strcmp(value, "off") || !strcmp(value, "on")) {
        g_wanted = value[1] == 'n';
    } else if (!strpbrk(value, "/\\:") && !pack_has_index(value)) {
        snprintf(g_chosen, sizeof g_chosen, "%s", value); /* gone since it was saved: logged, and the default shows */
        g_wanted = 1;
    } else {
        snprintf(g_dir, sizeof g_dir, "%s", value);
        g_dir_given = 1;
        g_wanted = 1;
    }
    return 2;
}
