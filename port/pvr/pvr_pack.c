/* Texture packs: replacement images for guest textures, host-side only (guest memory, timing and game logic are
 * untouched). Off unless game.json "texture_pack" names a folder or --texture-pack DIR is given; --texture-pack off
 * turns it off, on turns it back on. A check run (--fixed-clock: regress goldens, lockstep, emulator comparisons; the
 * tools' headless runs have it) ignores the game's default pack, like widescreen, so it sees the original;
 * --texture-pack on, NAME or DIR asks for it there.
 *
 * A release carries its packs in packs/<folder> beside port.exe; the game's default is looked for there first, under its
 * game.json folder's name. Every other folder is an optional pack (--texture-pack NAME, the Display menu, settings.txt),
 * shown over the game's default; game.json "texture_pack_choice" names the one a play run starts with until one is chosen.
 * A default pack the build could not make leaves packs/<folder>.skipped (tools/pack_builds.py): its reason is logged.
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
 * pvr_tex.c asks when it decodes a texture (after the guest wrote its memory). A hit's image, with its whole mip chain
 * (FILE.mips, else built) whatever the guest's mip-map bit, is loaded on the loader's thread (pvr_pack_load.c), kicked
 * by kmLoadTexture or else the draw; an over line's too, its game texels decoded where it is kicked.
 * Palettized textures (formats 5, 6) have none: their texels depend on palette RAM.
 * A line's words: over keeps the game's own texture under the image where its alpha is 0; rim=RRGGBB colours the
 * untextured sides a model draws right after the textured face; wide=W marks the image's middle W columns as the texture
 * and the rest as art for a frame wider than 4:3 (pvr_wide.c). port/TEXTURE_PACKS.md has them in full.
 * if=game.NAME switches a line with that setting (port_menu_check_on: a game.json menu_checks entry, maybe on by
 * default); off, the line is skipped, and a change reloads the pack at once.
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
#ifdef PORT_TEXTURE_PACK_CHOICE
#define PACK_CHOICE PORT_TEXTURE_PACK_CHOICE /* game.json texture_pack_choice: the optional pack until one is chosen */
#else
#define PACK_CHOICE ""
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
#define RIM_SET PVR_PACK_RIM_SET

typedef struct PackEntry {
    u64 key;
    char *file;
    u32 w, h, failed;
    u32 variant; /* the pad style the image is for (base/pad_style.c): 0 for every style, 1 xbox, 2 playstation */
    u32 layer;   /* whose image: LAYER_CHOSEN comes before LAYER_GAME */
    u32 rim;     /* RIM_SET | RRGGBB from the line's rim= word, or 0 */
    u32 over;    /* the line's over word: the image goes over the game's own texture */
    u32 wide;    /* the line's wide= word: the columns of the image that are the texture, or 0 */
} PackEntry;

/* The textures decoded with a rim= or wide= image. The scene parser asks each time an untextured polygon follows a
 * textured one, so this is a look-up, not a hash; a texture's first scene shows its rim as the game draws it. */
typedef struct RimRow {
    u32 tcw, w, h, rim;
    f32 wide; /* the image's width over the columns that are the texture; 0: no wide= word */
} RimRow;

static char g_dir[MAX_PATH_TEXT] = PACK_DEFAULT; /* the game's pack, or --texture-pack DIR */
static int g_dir_given;                          /* it is --texture-pack DIR: not looked for beside port.exe */
static char g_chosen[PVR_PACK_NAME] = PACK_CHOICE; /* --texture-pack NAME: a folder of packs/ beside port.exe */
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
    u32 bad = 0, switched_off = 0, before = g_count;
    snprintf(g_layer_dir[layer], sizeof g_layer_dir[layer], "%s", dir);
    snprintf(path, sizeof path, "%s/index.txt", dir);
    FILE *f = NULL;
    if (!pack_widen(path, wide, MAX_PATH_TEXT + 16) || _wfopen_s(&f, wide, L"r") != 0 || !f) {
        port_log("pvr: texture pack: no %s, the pack is off\n", path);
        return 0;
    }
    while (fgets(line, sizeof line, f)) {
        PvrPackLine e;
        int got = pvr_pack_line(line, &e); /* pvr_pack_line.c */
        if (got < 1) {
            bad += got == -1;
            switched_off += got == -2;
            continue;
        }
        if (g_count == g_cap) {
            PackEntry *p = realloc(g_entry, (size_t)(g_cap ? g_cap * 2u : 256u) * sizeof *p);
            if (!p) break;
            g_entry = p;
            g_cap = g_cap ? g_cap * 2u : 256u;
        }
        memset(&g_entry[g_count], 0, sizeof *g_entry);
        g_entry[g_count].key = e.key;
        g_entry[g_count].variant = (u32)e.variant;
        g_entry[g_count].layer = layer;
        g_entry[g_count].rim = e.rim;
        g_entry[g_count].over = e.over;
        g_entry[g_count].wide = e.wide;
        g_rim_lines += e.rim != 0u || e.wide != 0u;
        g_entry[g_count++].file = _strdup(e.file);
    }
    fclose(f);
    port_log("pvr: texture pack: %u images in %s%s\n", g_count - before, dir, bad ? " (some lines unreadable)" : "");
    if (switched_off) port_log("pvr: texture pack: %u lines off by their if= setting\n", switched_off);
    return g_count - before;
}

static int wanted(void) { return g_wanted < 0 ? !host_fixed_clock() : g_wanted; }

/* The build's note beside the game's default pack it could not make (tools/pack_builds.py), logged; 0: no note */
static int log_skipped(const char *dir)
{
    char path[MAX_PATH_TEXT + 16], why[512] = "";
    WCHAR wide[MAX_PATH_TEXT + 16];
    FILE *f = NULL;
    snprintf(path, sizeof path, "%s.skipped", dir);
    if (!pack_widen(path, wide, MAX_PATH_TEXT + 16) || _wfopen_s(&f, wide, L"r") != 0 || !f) return 0;
    fgets(why, sizeof why, f);
    fclose(f);
    port_log("pvr: texture pack: the build skipped %s: %.*s; the original picture shows\n", dir, (int)strcspn(why, "\r\n"), why);
    return 1;
}

/* What is wanted now into g_entry, sorted by key, dropping what a switch left there. Runs where images are used, so
 * nothing else holds an image (pvr_tex.c copies one into the device at once). */
static int load_packs(void)
{
    char dir[MAX_PATH_TEXT];
    pvr_pack_load_reset();
    for (u32 i = 0; i < g_count; i++) free(g_entry[i].file);
    g_count = g_hits = g_rim_lines = g_rim_rows = 0;
    if (!wanted()) return 0;
    if (g_chosen[0]) {
        pack_bundled_dir(g_chosen, dir, sizeof dir);
        load_index(dir, LAYER_CHOSEN);
    }
    if (g_dir[0] && (g_dir_given || game_pack_dir(dir, sizeof dir) != g_dir || pack_has_index(g_dir) || !log_skipped(dir)))
        load_index(game_pack_dir(dir, sizeof dir), LAYER_GAME);
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

static void note_rim(u32 tcw, u32 w, u32 h, u32 rim, f32 wide)
{
    for (u32 i = 0; i < g_rim_rows; i++)
        if (g_rim[i].tcw == tcw && g_rim[i].w == w && g_rim[i].h == h) {
            g_rim[i].rim = rim;
            g_rim[i].wide = wide;
            return;
        }
    if ((rim || wide > 0.0f) && g_rim_rows < MAX_RIMS) g_rim[g_rim_rows++] = (RimRow){tcw, w, h, rim, wide};
}

u32 pvr_pack_rim(u32 tcw, u32 tsp)
{
    u32 w = 8u << ((tsp >> 3) & 7u), h = 8u << (tsp & 7u);
    if (!g_rim_rows || !enabled()) return 0;
    for (u32 i = 0; i < g_rim_rows; i++)
        if (g_rim[i].tcw == tcw && g_rim[i].w == w && g_rim[i].h == h) return g_rim[i].rim;
    return 0;
}

f32 pvr_pack_wide(u32 tcw, u32 tsp)
{
    u32 w = 8u << ((tsp >> 3) & 7u), h = 8u << (tsp & 7u);
    if (!g_rim_rows || !enabled()) return 0.0f;
    for (u32 i = 0; i < g_rim_rows; i++)
        if (g_rim[i].tcw == tcw && g_rim[i].w == w && g_rim[i].h == h) return g_rim[i].wide;
    return 0.0f;
}

/* pvr_pack_hint (pvr_pack_load.c), on the game's thread: the image file for the texture as now in texture memory if one
 * replaces it (into path; *over_key: its key if it is an over line's, else 0), else NULL; path is left as it was if the
 * pack is off. */
const char *pvr_pack_image(u32 tcw, u32 w, u32 h, u32 stride, char *path, size_t cap, u64 *over_key)
{
    PackEntry *e = NULL;
    u64 key = 0;
    if (!g_enabled || ((tcw >> 27) & 7u) >= 5u) return NULL; /* off, or palettized */
    gfx_lock(); /* the drawing threads' hold: a switch of pack is loaded under it */
    if (enabled()) e = find_entry(key = pvr_pack_key(pvr_vram(), tcw, w, h, stride));
    path[0] = '\0';
    if (e && !e->failed) snprintf(path, cap, "%s/%s", g_layer_dir[e->layer], e->file);
    *over_key = e && e->over ? key : 0u;
    gfx_unlock();
    return path[0] ? path : NULL;
}

u32 pvr_pack_levels(u32 tcw, u32 w, u32 h, u32 stride, const u32 **texels, ID3D11Texture2D **tex, u32 *pw, u32 *ph)
{
    PackEntry *e;
    u64 key;
    char path[MAX_PATH_TEXT * 2 + 2];
    u32 pal_first, pal_count, tw = w, th = h, levels;
    if (!enabled() || pvr_palette_range(tcw, &pal_first, &pal_count)) return 0;
    key = pvr_pack_key(pvr_vram(), tcw, w, h, stride);
    e = find_entry(key);
    if (g_rim_lines) note_rim(tcw, tw, th, e ? e->rim : 0u, 0.0f);
    if (!e || e->failed) return 0;
    snprintf(path, sizeof path, "%s/%s", g_layer_dir[e->layer], e->file);
    levels = pvr_pack_take(path, e->over ? key : 0u, tcw, w, h, stride, texels, tex, &e->w, &e->h); /* the loader's */
    if (!levels) return e->failed = 1, 0;
    if (e->wide) note_rim(tcw, tw, th, e->rim, (f32)e->w / (f32)e->wide);
    if (!g_hits++) port_log("pvr: texture pack: first image used (%016llx)\n", (unsigned long long)key);
    *pw = e->w;
    *ph = e->h;
    return levels;
}

const char *pvr_pack_entry(u32 i, char *path, size_t cap, u32 *over)
{
    if (!enabled() || i >= g_count) return NULL;
    return *over = g_entry[i].over, snprintf(path, cap, "%s/%s", g_layer_dir[g_entry[i].layer], g_entry[i].file), path;
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
    pvr_pack_refresh();
}

/* The packs read again at the next decode, and every texture decoded again: a choice or an if= line's setting changed. */
void pvr_pack_refresh(void)
{
    InterlockedExchange(&g_enabled, -1);
    InterlockedIncrement(&g_generation);
}

/* --texture-pack NAME (a folder of packs/ beside port.exe) | DIR | on | off; --pack-mips[-check] DIR (pvr_pack_png.c) */
int pvr_pack_option(const char *arg, const char *value)
{
    if (pvr_keys_option(arg, value) || pad_style_option(arg, value) || pvr_tex_option(arg, value)) return 2;
    if (value && (!strcmp(arg, "--pack-mips") || !strcmp(arg, "--pack-mips-check")))
        ExitProcess((UINT)pvr_pack_write_mips(value, arg[11] == '-')); /* a tool's run: it opens nothing */
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
