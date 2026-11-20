/* Clearer text (a game's Display > Clearer text, its option "clear-text"; on by default in a play run): lettering the
 * Saturn draws as VDP1 sprites, shown sharp at the window's resolution.
 *
 *  1. Before a VDP1 list is drawn, one walk of it (sat_vdp1.c's scan) hands each textured sprite to the game's `text`
 *     hook (sat_hooks.h), which says which are lettering: its fingerprint (texture, colour, size).
 *  2. The list is drawn as ever, into the framebuffer the game sees. Around each such sprite its box's dots are kept
 *     before and after it is drawn (each band keeps its own rows).
 *  3. A hidden copy of that framebuffer is made with those sprites taken out (a dot the sprite set, and nothing set
 *     since, goes back to what was under it), with a mask of the dots taken out. The composer (sat_vdp2.c) shows the
 *     copy; at a masked dot it also works out the colour the dot has with the sprite, so priority, colour calculation,
 *     colour offset (fades) and windows apply to the lettering exactly as on the console.
 *  4. Each sprite's lit dots and colours make an image k times the native size (A3: sat_text_a3.c, or the game's own
 *     picture, `text_image`), cached by its lit dots, which is posted with the picture (present_cpu.h cpu_present_overlay)
 *     and drawn over it at the window's resolution.
 *  5. In a play run the images, and their textures, are made by a thread of their own, so no shown frame pays for one
 *     (an image made on first sight cost a frame up to 10 ms). Until an item's image is ready the picture shows the
 *     same item in an older colour if one is made (a fade), else the original lettering's own dots, for that frame. A
 *     game may ask for images ahead of time (sat_text_warm, sat_text_job). Shots (--clear-text-shot) make them at once.
 *
 * Off (or no hook) nothing here runs: the list is drawn and composed exactly as before, and the guest framebuffer, VRAM
 * and work RAM are never written by any of this. Checks and scripted runs have it off unless --clear-text-shot K is
 * given (then each --dump-frames shot also writes frame_N_clear.png, k times the size, with the images drawn in).
 *
 * Nearest existing: sat_panel.c (the menu panel: marked dots the presenter draws over) and sat_wide.c's twin framebuffer;
 * neither replaces sprites or draws above the native resolution. */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_text.h"
#include "console.h"
#include "present_cpu.h"

enum { MAXIT = 1024, POOL = 1 << 19, MAXDOT = 64 * 256, OVN = 2048, CACHE = 8192, KEEP_POSTS = 8 };
typedef struct { int x0, y0, x1, y1, w, tag, visit; u16 *bef, *aft; } Item;
typedef struct { const u8 *guest, *src; u8 *shown; u16 *mask; int valid, sh, ox, n; u32 bytes; Item it[MAXIT]; } Slot;
typedef struct { PresentImage im; u64 key, skey; u32 last; volatile LONG ready; } Img; /* the pixels follow it in the same block */
typedef struct { Img *m; int x, y, w, h, tag, k; u32 *dots; u8 *on; void (*fn)(void *); void *arg; } Job;
enum { JOBS = 2048, SHAPES = 4096 };

int sat_text_on;
u32 sat_text_with[SAT_MAX_W * SAT_MAX_H];
static int g_shot_k, g_log, g_play = -1, g_visit, g_nit, g_pool_n, g_ox, g_sh, g_logged;
static Item g_it[MAXIT];
static u16 *g_pool;
static u8 *g_guest, *g_target;
static Slot g_sl[3], *g_slot[2] = {&g_sl[0], &g_sl[1]}, *g_spare = &g_sl[2], *g_used;
static PresentOverlay g_cur[OVN], g_held[OVN];
static unsigned g_cur_n, g_held_n, g_hide_n; /* g_hide_n: images below it wholly inside g_hide are left out (sat_text_hide) */
static float g_hide[4];
static const u32 *g_held_px;
static Img *g_cache[CACHE];
static u32 g_ncache, g_posts, g_next_id = 1;
static size_t g_bytes;
static u64 g_seen[4096];
static int g_cpu[2]; /* each framebuffer: the CPU wrote into it since its list */
static u32 g_tex_out, g_log_from; /* --clear-text-log hex@N: from vertical blank N */
/* the maker thread: jobs g_jh..g_jt (a job's place is free once it is done); the newest ready image of each shape */
static Job g_job[JOBS];
static u32 g_jh, g_jt, g_n_made, g_n_warm, g_n_frames, g_n_stand, g_n_older;
static int g_async = -1;
static CRITICAL_SECTION g_jcs;
static CONDITION_VARIABLE g_jcv, g_jdone;
static Img *g_shape[SHAPES];

int sat_text_wanted(void)
{
    if (!SAT_G(text) && !SAT_G(text_cells)) return 0;
    if (g_shot_k) return 1;
    if (g_play < 0) g_play = !sat_video_headless() && !host_fixed_clock();
    return g_play && port_option("clear-text") == 1;
}

static u64 fnv(u64 h, const void *p, size_t n)
{
    const u8 *b = (const u8 *)p;
    for (size_t i = 0; i < n; i++) h = (h ^ b[i]) * 1099511628211ull;
    return h;
}

void sat_text_begin(u8 *guest, u8 *target)
{
    g_nit = g_pool_n = g_visit = 0, g_guest = guest, g_target = target, g_cpu[guest == sat_v1_fb[1]] = 0;
    sat_text_on = sat_text_wanted();
    if (!sat_text_on) return;
    if (!g_pool && !(g_pool = (u16 *)malloc(sizeof(u16) * POOL))) sat_text_on = 0;
    g_ox = sat_wide_fb_ox(target), g_sh = g_ox ? 10 : 9;
}

/* the game's hook on one textured sprite (the scan); --clear-text-log: each new texture once, with its pens */
void sat_text_seen(const SatTextSprite *s)
{
    int v = g_visit++, tag, x0, y0, x1, y1, w, h, lim = g_ox ? 1023 - g_ox : 511;
    Item *t;
    if (g_nit >= MAXIT) return;
    tag = SAT_G(text)(s);
    if (g_log && g_logged < 3000 && s->tw * s->th <= 64 * 256 && sat_vblank_count() >= g_log_from) {
        u32 cm = (s->pmod >> 3) & 7u, bytes = (u32)(s->tw * s->th) / (cm <= 1u ? 2u : cm == 5u ? 1u : 1u) * (cm == 5u ? 2u : 1u), pens = 0;
        u64 k = fnv(fnv(14695981039346656037ull, s->tex, bytes), &s->colr, sizeof s->colr), slot = k % 4096u;
        if (g_seen[slot] != k) {
            g_seen[slot] = k, g_logged++;
            for (u32 i = 0; cm <= 1u && i < bytes; i++) pens |= 1u << (s->tex[i] >> 4) | 1u << (s->tex[i] & 15u);
            port_log("clear text: vb %u %s comm %u pmod %04x colr %04x srca %04x %dx%d at %d,%d pens %04x key %016llx\n", sat_vblank_count(),
                     tag ? "TEXT" : "----", s->comm, s->pmod, s->colr, s->srca, s->tw, s->th, s->x0, s->y0, pens, (unsigned long long)k);
            if (g_log == 2 && g_tex_out < (4u << 20)) { /* --clear-text-log hex: the texels too, to clear_text_tex.bin (a game's tables) */
                FILE *f = fopen("clear_text_tex.bin", "ab");
                int hd[12] = {(int)sat_vblank_count(), (int)s->comm, (int)s->pmod, (int)s->colr, (int)s->srca, s->tw, s->th, s->x0, s->y0, s->x1, s->y1, (int)bytes};
                if (f) fwrite("CTX1", 1, 4, f), fwrite(&k, 8, 1, f), fwrite(hd, 4, 12, f), fwrite(s->tex, 1, bytes, f), fclose(f), g_tex_out += bytes + 60u;
            }
        }
    }
    if (!tag) return;
    x0 = s->x0 < -g_ox ? -g_ox : s->x0, y0 = s->y0 < 0 ? 0 : s->y0, x1 = s->x1 + 1 > lim ? lim : s->x1 + 1, y1 = s->y1 + 1 > 255 ? 255 : s->y1 + 1;
    w = x1 - x0 + 1, h = y1 - y0 + 1;
    if (x1 < x0 || y1 < y0 || w * h > MAXDOT || g_pool_n + 2 * w * h > POOL) return;
    t = &g_it[g_nit++];
    t->x0 = x0, t->y0 = y0, t->x1 = x1, t->y1 = y1, t->w = w, t->tag = tag, t->visit = v;
    t->bef = g_pool + g_pool_n, t->aft = t->bef + w * h, g_pool_n += 2 * w * h;
}

int sat_text_item(int visit) /* the items are in visit order */
{
    int lo = 0, hi = g_nit - 1;
    while (lo <= hi) {
        int m = (lo + hi) / 2;
        if (g_it[m].visit == visit) return m;
        if (g_it[m].visit < visit) lo = m + 1;
        else hi = m - 1;
    }
    return -1;
}

u16 *sat_text_keep(int i, int after, int *x0, int *y0, int *x1, int *y1)
{
    *x0 = g_it[i].x0, *y0 = g_it[i].y0, *x1 = g_it[i].x1, *y1 = g_it[i].y1;
    return after ? g_it[i].aft : g_it[i].bef;
}

static Slot *slot_of(const u8 *guest) { return g_slot[guest == sat_v1_fb[1]]; }

/* after the list: the copy without the lettering, newest sprite first, each dot back to what was under it while the
 * sprite's own dot is still there; the mask says which item a dot was taken from */
void sat_text_end(void)
{
    Slot *sl = slot_of(g_guest);
    sl->valid = 0;
    if (!sat_text_on || !g_nit) return;
    if (!sl->shown) sl->shown = (u8 *)malloc(0x80000), sl->mask = (u16 *)malloc(0x80000);
    if (!sl->shown || !sl->mask) return;
    sl->guest = g_guest, sl->src = g_target, sl->ox = g_ox, sl->sh = g_sh, sl->bytes = g_ox ? 0x80000u : 0x40000u;
    memcpy(sl->shown, g_target, sl->bytes);
    memset(sl->mask, 0, sl->bytes);
    for (int i = g_nit - 1; i >= 0; i--) {
        const Item *t = &g_it[i];
        for (int y = t->y0; y <= t->y1; y++)
            for (int x = t->x0; x <= t->x1; x++) {
                u32 o = (((u32)y << sl->sh) + (u32)(x + sl->ox)) * 2u & (sl->bytes - 1u), j = (u32)((y - t->y0) * t->w + x - t->x0);
                u16 cur = (u16)(sl->shown[o] << 8 | sl->shown[o + 1]), a = t->aft[j], b = t->bef[j];
                if (a != b && cur == a) sl->shown[o] = (u8)(b >> 8), sl->shown[o + 1] = (u8)b, sl->mask[o >> 1] = (u16)(i + 1);
            }
        sl->it[i] = *t;
    }
    sl->n = g_nit, sl->valid = 1;
}

void sat_text_dirty(const u8 *guest, int cpu)
{
    for (int i = 0; i < 2; i++)
        if (g_slot[i]->guest == guest) g_slot[i]->valid = 0;
    if (cpu) g_cpu[guest == sat_v1_fb[1]] = 1;
}

int sat_text_cpu_shown(void) { return g_cpu[sat_v1_display_fb() == sat_v1_fb[1]]; }

void sat_text_save(const u8 *guest)
{
    int i = guest == sat_v1_fb[1];
    Slot *t = g_slot[i];
    g_slot[i] = g_spare, g_spare = t, g_slot[i]->valid = 0;
}

void sat_text_restore(const u8 *guest)
{
    int i = guest == sat_v1_fb[1];
    Slot *t = g_slot[i];
    g_slot[i] = g_spare, g_spare = t;
}

const u8 *sat_text_spr(const u8 *fb, const u16 **mask, u32 frame_w)
{
    g_used = NULL, g_cur_n = g_hide_n = 0, *mask = NULL;
    if (frame_w > 512u || !sat_text_wanted()) return fb;
    for (int i = 0; i < 2; i++)
        if (g_slot[i]->valid && g_slot[i]->src == fb) {
            g_used = g_slot[i], *mask = g_used->mask;
            return g_used->shown;
        }
    return fb;
}

int sat_text_scale(void)
{
    PortView v;
    int k;
    if (g_shot_k) return g_shot_k;
    memset(&v, 0, sizeof v);
    cpu_present_view(&v);
    k = v.out_h && sat_frame_h ? (int)((v.out_h + sat_frame_h - 1u) / sat_frame_h) : 6;
    return k < 2 ? 2 : k > 10 ? 10 : k;
}

/* ---- the images, cached by their dots (an id each; freed once no slot can show them) ---- */
static void evict(void)
{
    Img *keep[CACHE];
    u32 n = 0;
    for (u32 i = 0; i < CACHE; i++) {
        Img *m = g_cache[i];
        if (!m) continue;
        if (m->ready && m->last + KEEP_POSTS < g_posts) { /* (an image being made is kept) */
            if (g_shape[m->skey % SHAPES] == m) g_shape[m->skey % SHAPES] = NULL;
            cpu_present_unprepare(&m->im);
            g_bytes -= sizeof *m + (size_t)m->im.w * m->im.h * 4u, free(m);
        } else keep[n++] = m;
        g_cache[i] = NULL;
    }
    g_ncache = n;
    for (u32 i = 0; i < n; i++) {
        u32 s = (u32)(keep[i]->key % CACHE);
        while (g_cache[s]) s = (s + 1) % CACHE;
        g_cache[s] = keep[i];
    }
}

static Img *find(u64 key)
{
    for (u32 s = (u32)(key % CACHE);; s = (s + 1) % CACHE)
        if (!g_cache[s] || g_cache[s]->key == key) return g_cache[s];
}

static Img *make(u64 key, int kw, int kh)
{
    Img *m;
    u32 s;
    if (g_ncache > CACHE * 3 / 4 || g_bytes > (size_t)160 << 20) evict();
    if (g_ncache > CACHE * 3 / 4 || !(m = (Img *)malloc(sizeof *m + (size_t)kw * (size_t)kh * 4u))) return NULL;
    m->key = key, m->skey = 0, m->ready = 0, m->last = g_posts, m->im.id = g_next_id++, m->im.w = (u32)kw, m->im.h = (u32)kh, m->im.px = (const u32 *)(m + 1), m->im.srv = NULL;
    for (s = (u32)(key % CACHE); g_cache[s]; s = (s + 1) % CACHE) {}
    g_cache[s] = m, g_ncache++, g_bytes += sizeof *m + (size_t)kw * (size_t)kh * 4u;
    return m;
}

static void add(float x, float y, float w, float h, Img *m)
{
    if (!m || g_cur_n >= OVN) return;
    m->last = g_posts;
    g_cur[g_cur_n].x = x, g_cur[g_cur_n].y = y, g_cur[g_cur_n].w = w, g_cur[g_cur_n].h = h, g_cur[g_cur_n++].img = &m->im;
}

void sat_text_put(float x, float y, float w, float h, const u32 *argb, int kw, int kh, u64 key)
{
    Img *m = find(key);
    if (!m && (m = make(key, kw, kh)) != NULL) memcpy((u32 *)(m + 1), argb, (size_t)kw * (size_t)kh * 4u), m->ready = 1;
    add(x, y, w, h, m);
}

/* ---- the maker thread (a play run): images and their textures made off the game's and the presenter's threads ---- */
static void make_pixels(Img *m, const SatTextItem *it, int k)
{
    u32 *out = (u32 *)(m + 1);
    if (!(SAT_G(text_image) && SAT_G(text_image)(it, k, out)) && !sat_a3_picture(it->dots, it->on, it->w, it->h, k, out))
        memset(out, 0, (size_t)(it->w * k) * (size_t)(it->h * k) * 4u);
}

static DWORD WINAPI maker(LPVOID unused)
{
    (void)unused;
    for (;;) {
        Job j;
        EnterCriticalSection(&g_jcs);
        while (g_jh == g_jt) SleepConditionVariableCS(&g_jcv, &g_jcs, INFINITE);
        j = g_job[g_jh % JOBS];
        LeaveCriticalSection(&g_jcs);
        if (j.fn) j.fn(j.arg);
        else {
            SatTextItem it = {j.x, j.y, j.w, j.h, j.tag, j.dots, j.on};
            make_pixels(j.m, &it, j.k);
            cpu_present_prepare(&j.m->im);
            free(j.dots);
            InterlockedExchange(&j.m->ready, 1);
        }
        EnterCriticalSection(&g_jcs);
        g_jh++;
        WakeAllConditionVariable(&g_jdone);
        LeaveCriticalSection(&g_jcs);
    }
}

static int async(void)
{
    HANDLE t;
    if (g_async >= 0) return g_async;
    g_async = !g_shot_k && cpu_present_threaded();
    if (g_async) {
        InitializeCriticalSection(&g_jcs), InitializeConditionVariable(&g_jcv), InitializeConditionVariable(&g_jdone);
        if ((t = CreateThread(NULL, 0, maker, NULL, 0, NULL)) == NULL) g_async = 0;
        else CloseHandle(t), port_log("clear text: images made by a thread of their own\n");
    }
    return g_async;
}

static int room(void) { return g_jt - *(volatile u32 *)&g_jh < JOBS; }

static void send(const Job *j)
{
    EnterCriticalSection(&g_jcs);
    g_job[g_jt % JOBS] = *j, g_jt++;
    WakeConditionVariable(&g_jcv);
    LeaveCriticalSection(&g_jcs);
}

void sat_text_job(void (*fn)(void *), void *arg)
{
    Job j;
    if (!async() || !room()) {
        fn(arg);
        return;
    }
    memset(&j, 0, sizeof j), j.fn = fn, j.arg = arg;
    send(&j);
}

void sat_text_wait(void)
{
    if (g_async <= 0) return;
    EnterCriticalSection(&g_jcs);
    while (g_jh != g_jt) SleepConditionVariableCS(&g_jdone, &g_jcs, INFINITE);
    LeaveCriticalSection(&g_jcs);
}

/* an item's key: its lit dots and their colours (the dots around them do not change its image); skey: its shape */
static u64 item_key(int tag, int k, int w, int h, const u8 *on, const u32 *dots, u64 *skey)
{
    u64 s = fnv(14695981039346656037ull, &tag, sizeof tag), c;
    s = fnv(fnv(fnv(s, &k, sizeof k), &w, sizeof w), &h, sizeof h), s = fnv(s, on, (size_t)(w * h));
    c = fnv(s, "c", 1);
    for (int j = 0; j < w * h; j++)
        if (on[j]) c = fnv(c, &dots[j], sizeof dots[j]);
    *skey = s;
    return c;
}

/* the item's image, found, made now (no maker thread) or asked for; NULL when it is not ready */
static Img *image(int x, int y, int w, int h, int tag, int k, const u32 *dots, const u8 *on, int warm)
{
    u64 skey, key = item_key(tag, k, w, h, on, dots, &skey);
    Img *m = find(key);
    if (m) return m->ready ? m : NULL;
    if (async() && !room()) return NULL;
    if ((m = make(key, w * k, h * k)) == NULL) return NULL;
    m->skey = skey;
    if (!async()) {
        SatTextItem it = {x, y, w, h, tag, dots, on};
        make_pixels(m, &it, k), m->ready = 1, g_n_made++;
        return m;
    } else {
        Job j;
        u32 *cp = (u32 *)malloc((size_t)(w * h) * 5u);
        if (!cp) return NULL; /* (stays not ready: its next sight asks no more; freed never - a rare leak of one entry) */
        memcpy(cp, dots, (size_t)(w * h) * 4u), memcpy((u8 *)(cp + w * h), on, (size_t)(w * h));
        memset(&j, 0, sizeof j);
        j.m = m, j.x = x, j.y = y, j.w = w, j.h = h, j.tag = tag, j.k = k, j.dots = cp, j.on = (u8 *)(cp + w * h);
        send(&j), g_n_made++, g_n_warm += warm != 0;
        return NULL;
    }
}

void sat_text_warm(int tag, int w, int h, const u32 *dots, const u8 *on)
{
    if (w > 0 && h > 0 && w * h <= MAXDOT && async()) image(0, 0, w, h, tag, sat_text_scale(), dots, on, 1);
}

/* one item's dots (g_on: lit where the lettering changed the picture) at picture dot X0, Y0: its image, else the same
 * lettering in an older colour, else the original lettering's own dots for this frame */
static u32 g_dots[MAXDOT];
static u8 g_on[MAXDOT];
static void show(u32 *px, u32 pw, u32 pox, int X0, int Y0, int w, int h, int tag, int k, int *stand, int *older)
{
    u64 key;
    Img *m;
    if ((m = image(X0 - (int)pox, Y0, w, h, tag, k, g_dots, g_on, 0)) != NULL) {
        g_shape[m->skey % SHAPES] = m;
        add((float)X0, (float)Y0, (float)w, (float)h, m);
        return;
    }
    item_key(tag, k, w, h, g_on, g_dots, &key);
    if ((m = g_shape[key % SHAPES]) != NULL && m->skey == key && m->ready) {
        add((float)X0, (float)Y0, (float)w, (float)h, m), *older = 1;
        return;
    }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            if (g_on[y * w + x]) px[(u32)(Y0 + y) * pw + (u32)(X0 + x)] = sat_text_with[(u32)(Y0 + y) * pw + (u32)(X0 + x)];
    *stand = 1;
}

/* after a compose: each item's dots to its image; the sprites' (the slot's mask) and the planes' (sat_text_v2.c's map) */
void sat_text_frame(u32 *px, u32 pw, u32 ph, u32 pox)
{
    const Slot *sl = g_used;
    int k, stand = 0, older = 0;
    if (!px) g_used = NULL, g_cur_n = 0; /* a blank picture */
    if ((!sl && !sat_text_v2_n) || !px) return;
    k = sat_text_scale();
    for (int i = 0; sl && i < sl->n; i++) {
        const Item *t = &sl->it[i];
        int X0 = t->x0 + (int)pox, Y0 = t->y0, w = t->w, h = t->y1 - t->y0 + 1, lit = 0;
        if (X0 < 0 || Y0 < 0 || X0 + w > (int)pw || Y0 + h > (int)ph) continue;
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) {
                u32 o = (((u32)(Y0 + y) << sl->sh) + (u32)(t->x0 + x + sl->ox)) * 2u & (sl->bytes - 1u), p = (u32)(Y0 + y) * pw + (u32)(X0 + x), j = (u32)(y * w + x);
                g_on[j] = sl->mask[o >> 1] == (u16)(i + 1) && sat_text_with[p] != px[p];
                g_dots[j] = g_on[j] ? sat_text_with[p] : px[p];
                lit += g_on[j];
            }
        if (lit) show(px, pw, pox, X0, Y0, w, h, t->tag, k, &stand, &older);
    }
    for (int i = 0; i < sat_text_v2_n; i++) {
        const SatTextV2Item *t = &sat_text_v2_it[i];
        int lit = 0;
        if (t->x + t->w > (int)pw || t->y + t->h > (int)ph) continue;
        for (int y = 0; y < t->h; y++)
            for (int x = 0; x < t->w; x++) {
                u32 p = (u32)(t->y + y) * pw + (u32)(t->x + x), j = (u32)(y * t->w + x);
                g_on[j] = sat_text_v2_map[p] == (u16)(i + 1) && sat_text_with[p] != px[p];
                g_dots[j] = g_on[j] ? sat_text_with[p] : px[p];
                lit += g_on[j];
            }
        if (lit) show(px, pw, pox, t->x, t->y, t->w, t->h, t->tag, k, &stand, &older);
    }
    g_n_frames++, g_n_stand += stand, g_n_older += older;
    if (g_async > 0 && g_n_frames % 1000u == 0)
        port_log("clear text: %u pictures: %u images made (%u ahead of time), %u pictures with an original standing in, %u with an older colour\n",
                 g_n_frames, g_n_made, g_n_warm, g_n_stand, g_n_older);
}

void sat_text_held(const u32 *held)
{
    g_held_px = held, g_held_n = g_cur_n;
    memcpy(g_held, g_cur, g_cur_n * sizeof g_cur[0]);
}

static int hidden(unsigned i, const PresentOverlay *o)
{
    /* wholly inside only: the original lettering is out of the picture, so an image that reaches past the box stays */
    return i < g_hide_n && o->x >= g_hide[0] && o->x + o->w <= g_hide[0] + g_hide[2] && o->y >= g_hide[1] && o->y + o->h <= g_hide[1] + g_hide[3];
}

void sat_text_post(const u32 *px)
{
    static PresentOverlay keep[OVN];
    const PresentOverlay *o = px == sat_frame ? g_cur : px == g_held_px ? g_held : NULL;
    unsigned n = px == sat_frame ? g_cur_n : px == g_held_px ? g_held_n : 0;
    g_posts++;
    for (unsigned i = 0; o && i < n; i++) ((Img *)o[i].img)->last = g_posts; /* PresentImage is an Img's first member */
    if (o == g_cur && g_hide_n) { /* a game's box over the picture: the clearer text under it left out */
        unsigned m = 0;
        for (unsigned i = 0; i < n; i++)
            if (!hidden(i, &o[i])) keep[m++] = o[i];
        o = keep, n = m;
    }
    cpu_present_overlay(o, n);
}

int sat_text_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--clear-text-shot")) g_shot_k = atoi(value) < 0 ? 0 : atoi(value) > 12 ? 12 : atoi(value);
    else if (!strcmp(arg, "--clear-text-log")) g_log = !strncmp(value, "hex", 3) ? 2 : !strcmp(value, "on"), g_log_from = strchr(value, '@') ? (u32)atoi(strchr(value, '@') + 1) : 0;
    else return 0;
    return 2;
}

int sat_text_shots(void) { return g_shot_k > 0; }

/* --clear-text-log ...@N: 1 once, at the first plane scan from vertical blank N (the scan then asks about every cell again,
 * so that picture's lettering is logged whole, where it stands) */
int sat_text_v2_relog(void)
{
    static int done;
    if (!g_log || done || sat_vblank_count() < g_log_from) return 0;
    return done = 1;
}

/* --clear-text-log: a plane's cell or block the game was asked about, once (hex: its texels to clear_text_tex.bin, "CTV2") */
void sat_text_v2_log(const SatTextCells *c, int tag, u64 key, int x, int y)
{
    int bytes = c->tw * c->th * (c->cc == 0 ? 4 : c->cc == 1 ? 8 : c->cc <= 3 ? 16 : 32) / 8;
    if (!g_log || g_logged >= 6000 || sat_vblank_count() < g_log_from) return;
    g_logged++;
    port_log("clear text: vb %u %s layer %d cc %d cell %d %dx%d char %05x pal %02x at %d,%d asked %d key %016llx\n", sat_vblank_count(),
             tag ? "TEXT" : "----", c->layer, c->cc, c->cs, c->n, c->rows, c->charnum, c->pal, x, y, c->tag, (unsigned long long)key);
    if (g_log == 2 && g_tex_out < (8u << 20)) {
        FILE *f = fopen("clear_text_tex.bin", "ab");
        int hd[13] = {(int)sat_vblank_count(), c->layer, c->cc, c->cs, c->n, c->rows, c->tag, tag, (int)c->charnum, (int)c->pal, x, y, bytes};
        if (f) fwrite("CTV2", 1, 4, f), fwrite(&key, 8, 1, f), fwrite(hd, 4, 13, f), fwrite(c->tex, 1, (size_t)bytes, f), fclose(f), g_tex_out += (u32)bytes + 64u;
    }
}

/* a dump at k times: the picture scaled up dot for dot, its images drawn over it (premultiplied) */
void sat_text_png(const char *path, const u32 *px, u32 w, u32 h)
{
    u32 k = (u32)g_shot_k, W = w * k, H = h * k, *big;
    if (!k || px != sat_frame || !(big = (u32 *)malloc((size_t)W * H * 4u))) return;
    for (u32 y = 0; y < H; y++)
        for (u32 x = 0; x < W; x++) big[y * W + x] = px[(y / k) * w + x / k];
    for (unsigned i = 0; i < g_cur_n; i++) {
        const PresentImage *im = g_cur[i].img;
        if (hidden(i, &g_cur[i])) continue;
        int X = (int)(g_cur[i].x * (float)k), Y = (int)(g_cur[i].y * (float)k);
        for (u32 y = 0; y < im->h; y++)
            for (u32 x = 0; x < im->w; x++) {
                u32 s = im->px[y * im->w + x], a = s >> 24, *d, out = 0;
                if (!a || X + (int)x < 0 || Y + (int)y < 0 || X + (int)x >= (int)W || Y + (int)y >= (int)H) continue;
                d = &big[(u32)(Y + (int)y) * W + (u32)(X + (int)x)];
                for (int c = 0; c < 24; c += 8) {
                    u32 v = (s >> c & 255u) + ((*d >> c & 255u) * (255u - a) + 127u) / 255u;
                    out |= (v > 255u ? 255u : v) << c;
                }
                *d = out;
            }
    }
    if (sat_png_write(path, big, W, H)) port_log("clear text: %s (%ux%u, %u images) at vertical blank %u\n", path, W, H, g_cur_n, sat_vblank_count());
    free(big);
}

/* A game that holds the picture (Dragon Force's Journal, shown while the game waits) posts it again with its own pictures
 * changed: it marks the list before putting them and rewinds to the mark before putting the next set. */
unsigned sat_text_mark(void) { return g_cur_n; }
void sat_text_hide(unsigned below, float x, float y, float w, float h)
{
    g_hide_n = w > 0.0f && h > 0.0f ? below : 0u;
    g_hide[0] = x, g_hide[1] = y, g_hide[2] = w, g_hide[3] = h;
}
void sat_text_rewind(unsigned n)
{
    if (n < g_cur_n) g_cur_n = n;
}
