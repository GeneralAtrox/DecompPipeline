/* Texture packs' loader: a pack image's PNG read, its mip chain (stored beside it, else built) and its D3D11 texture made
 * on a thread of its own, so the frame that first draws the image only makes a view of it. The device is free-threaded
 * (base/present_device.c makes it without D3D11_CREATE_DEVICE_SINGLETHREADED), so making a texture there is safe.
 *
 * A kick comes as soon as the image's key can be known: kmLoadTexture (sdk/kamui1_tex.c, kamui2_tex.c) calls
 * pvr_pack_hint on the game's thread as the texture's bytes land in texture memory, usually in the game's own loading
 * frames, well before it is drawn. A game whose own library copies the bytes (raw C of the SDK's loader, writing through
 * port_texture_written) is hinted by that write: kmCreateTextureSurface tells pvr_pack_surface each surface, and the
 * write that reaches a surface's last texel byte hints it. A texture with no such word is kicked by its first draw.
 * A draw whose image is not ready yet waits for it, so every run shows the same pixels, fixed clock or real (the game's
 * own texture is never shown in its place); each wait is logged with its ms, and each image with the blanks of its kick,
 * ready and first draw.
 * An over line's image is loaded here too: its kick decodes the game's texels it goes over on the kicker's thread (the
 * game's at kmLoadTexture, while texture memory holds the bytes its key was made from), and the loader merges them
 * (pvr_pack_put_over) before it builds the chain. A job is its image and, for an over line, its texture's key.
 *
 * Bounds: at most PENDING images kicked and not yet drawn (a kick past that is dropped and logged; the draw kicks it), and
 * the thread holds one image's decoded pixels at a time, freed once its texture is made; the texture is kept, so a texture
 * decoded again (its memory rewritten with the same bytes) costs no read. With no device (the tests' driver) the pixels
 * are kept instead. The thread starts with the first kick: a run with the pack off starts none. A switch of pack drops
 * every image (pvr_pack_load_reset). A --work-counts run with a pack on counts the loader's texture creations when the
 * loader makes them (counts are off-thread there); checks run with the pack off, where nothing changes.
 *
 * Nearest existing: pvr_pack.c's read at first draw (on the drawing thread, in the frame that first draws the image; this
 * moves it off that thread and ahead of that frame). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <emmintrin.h>
#include <windows.h>
#include "pvr_int.h"
#include "platform.h"

#define PENDING 16u
#define PATH_CAP (PVR_PACK_PATH * 2 + 2)
#define JOB_QUEUED 0u
#define JOB_WORKING 1u
#define JOB_READY 2u
#define JOB_FAILED 3u
#define TCW_ADDR 0x1fffffu
#define SURFACES 4096u /* the SDK's texture blocks (sdk/kamui*_tex.c TEX_BLOCKS) */

typedef struct Job {
    char path[PATH_CAP];
    u32 state, urgent, early, taken, w, h, levels, kick_blank, ready_blank, over, gw, gh;
    u64 kick_us, ready_us, key; /* key: an over line's texture key, else 0 */
    f32 read_ms, chain_ms, split[3], upload_ms; /* split: the stored chain's file read, unpack and unfilter */
    const char *how;
    u32 *texels; /* no device: the image's levels, top first */
    u32 *game;   /* an over line's: its texture's texels (gw x gh), until the loader merges them */
    ID3D11Texture2D *tex;
} Job;

static SRWLOCK g_lock = SRWLOCK_INIT;
static CONDITION_VARIABLE g_wake = CONDITION_VARIABLE_INIT, g_done = CONDITION_VARIABLE_INIT;
static Job *g_job;
static u32 g_jobs, g_cap, g_pending, g_epoch;
static HANDLE g_thread;
static struct {
    u32 tcw, w, h;
} g_surf[SURFACES]; /* pvr_pack_surface's, the game's thread only */
static u32 g_surfs;
static struct {
    u32 kicks, early, dropped, waits, hints;
    u64 wait_us, wait_max_us, hint_us;
} g_st;

static u64 now_us(void)
{
    static LARGE_INTEGER f;
    LARGE_INTEGER c;
    if (!f.QuadPart) QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (u64)(c.QuadPart / f.QuadPart * 1000000 + c.QuadPart % f.QuadPart * 1000000 / f.QuadPart);
}

static f32 ms(u64 us) { return (f32)us / 1000.0f; }

f32 pvr_pack_ms(LARGE_INTEGER *t)
{
    LARGE_INTEGER now, rate;
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&rate);
    f32 v = (f32)((double)(now.QuadPart - t->QuadPart) * 1000.0 / (double)rate.QuadPart);
    return *t = now, v;
}

/* The stored chain's filter undone (pvr_pack_png.c's gradient, back): per row, each byte plus the one above minus the
 * one above-left (16 bytes at a time), then each texel plus the one to its left (a running sum, four texels at a time).
 * The same bytes as undoing it one byte at a time. */
void pvr_pack_unfilter(u8 *p, u32 w, u32 h)
{
    while (w > 1u || h > 1u) {
        u32 row, i;
        w = w > 1u ? w / 2u : 1u;
        h = h > 1u ? h / 2u : 1u;
        row = w * 4u;
        for (u32 y = 0; y < h; y++) {
            u8 *r = p + (size_t)y * row;
            const u8 *up = y ? r - row : NULL;
            __m128i carry = _mm_setzero_si128();
            if (up) {
                for (i = 0; i < 4u && i < row; i++) r[i] = (u8)(r[i] + up[i]);
                for (; i + 16u <= row; i += 16u) {
                    __m128i d = _mm_sub_epi8(_mm_loadu_si128((const __m128i *)(up + i)), _mm_loadu_si128((const __m128i *)(up + i - 4u)));
                    _mm_storeu_si128((__m128i *)(r + i), _mm_add_epi8(_mm_loadu_si128((const __m128i *)(r + i)), d));
                }
                for (; i < row; i++) r[i] = (u8)(r[i] + up[i] - up[i - 4u]);
            }
            for (i = 0; i + 16u <= row; i += 16u) {
                __m128i v = _mm_loadu_si128((const __m128i *)(r + i));
                v = _mm_add_epi8(v, _mm_slli_si128(v, 4));
                v = _mm_add_epi8(v, _mm_slli_si128(v, 8));
                v = _mm_add_epi8(v, carry);
                _mm_storeu_si128((__m128i *)(r + i), v);
                carry = _mm_shuffle_epi32(v, 0xff);
            }
            for (; i < row; i++) r[i] = (u8)(r[i] + (i >= 4u ? r[i - 4u] : 0u));
        }
        p += (size_t)row * h;
    }
}

/* The image read, its chain and (a device) its texture, off the lock. */
static void load(Job *j)
{
    u64 t0 = now_us(), t1;
    ID3D11Device *dev = g_gfx.dev;
    j->texels = pvr_pack_read_png(j->path, &j->w, &j->h);
    if (j->texels && j->over && !pvr_pack_put_over(j->texels, j->w, j->h, j->path, j->game, j->gw, j->gh)) {
        free(j->texels);
        j->texels = NULL;
    }
    free(j->game);
    j->game = NULL;
    j->read_ms = ms((t1 = now_us()) - t0);
    if (!j->texels) return;
    j->how = pvr_pack_chain(j->path, j->texels, j->w, j->h, (int)j->over, &j->levels, j->split);
    j->chain_ms = ms((t0 = now_us()) - t1);
    if (!dev) return;
    D3D11_SUBRESOURCE_DATA init[16];
    D3D11_TEXTURE2D_DESC d;
    u32 at = 0, levels = j->levels < 16u ? j->levels : 16u;
    for (u32 i = 0; i < levels; i++) {
        u32 lw = j->w >> i ? j->w >> i : 1u, lh = j->h >> i ? j->h >> i : 1u;
        init[i].pSysMem = j->texels + at;
        init[i].SysMemPitch = lw * 4u;
        init[i].SysMemSlicePitch = 0;
        at += lw * lh;
    }
    memset(&d, 0, sizeof d);
    d.Width = j->w;
    d.Height = j->h;
    d.MipLevels = j->levels = levels;
    d.ArraySize = 1;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_IMMUTABLE;
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(ID3D11Device_CreateTexture2D(dev, &d, init, &j->tex))) j->tex = NULL;
    j->upload_ms = ms(now_us() - t0);
    free(j->texels); /* uploaded: the pixels are not kept */
    j->texels = NULL;
}

/* Holding the lock: the next queued image (one a draw waits for first) loaded; 0 if none is queued. */
static int run_next(void)
{
    u32 i, epoch = g_epoch, pick = g_jobs;
    for (i = 0; i < g_jobs; i++)
        if (g_job[i].state == JOB_QUEUED && (pick == g_jobs || (g_job[i].urgent && !g_job[pick].urgent))) pick = i;
    if (pick == g_jobs) return 0;
    Job j = g_job[pick];
    g_job[pick].state = JOB_WORKING;
    g_job[pick].game = NULL; /* load frees it */
    ReleaseSRWLockExclusive(&g_lock);
    load(&j);
    AcquireSRWLockExclusive(&g_lock);
    if (epoch != g_epoch) { /* the pack was switched meanwhile: dropped */
        free(j.texels);
        if (j.tex) ID3D11Texture2D_Release(j.tex);
    } else {
        j.state = j.tex || j.texels ? JOB_READY : JOB_FAILED;
        j.urgent = g_job[pick].urgent;
        j.ready_blank = port_vblank_count();
        j.ready_us = now_us();
        g_job[pick] = j;
    }
    WakeAllConditionVariable(&g_done);
    return 1;
}

static DWORD WINAPI worker(void *arg)
{
    (void)arg;
    AcquireSRWLockExclusive(&g_lock);
    for (;;)
        if (!run_next()) SleepConditionVariableSRW(&g_wake, &g_lock, INFINITE, 0);
}

/* Holding the lock: the image's job, queued if new (a hint's kick: dropped, ~0u, when PENDING are not yet drawn). A new
 * over line's job (over_key not 0) gets its texture's texels (tcw, w x h, stride) decoded here, on the kicker's thread. */
static u32 kick(const char *path, u64 over_key, u32 tcw, u32 w, u32 h, u32 stride, int urgent)
{
    u32 i;
    for (i = 0; i < g_jobs; i++)
        if (g_job[i].key == over_key && !strcmp(g_job[i].path, path)) {
            g_job[i].urgent |= (u32)urgent;
            return i;
        }
    if (!urgent && g_pending >= PENDING) {
        if (!g_st.dropped++) port_log("pvr: texture pack: %u images kicked and not drawn yet: later kicks wait for their draw\n", g_pending);
        return ~0u;
    }
    if (g_jobs == g_cap) {
        Job *p = realloc(g_job, (size_t)(g_cap ? g_cap * 2u : 32u) * sizeof *p);
        if (!p) return ~0u;
        g_job = p;
        g_cap = g_cap ? g_cap * 2u : 32u;
    }
    Job *j = &g_job[g_jobs];
    memset(j, 0, sizeof *j);
    snprintf(j->path, sizeof j->path, "%s", path);
    j->state = JOB_QUEUED;
    j->urgent = (u32)urgent;
    j->early = !urgent;
    j->kick_blank = port_vblank_count();
    j->kick_us = now_us();
    if ((j->key = over_key) != 0u) {
        u32 need = pvr_decode_texels(tcw, w, h);
        j->over = 1;
        j->gw = w;
        j->gh = h;
        j->game = malloc((size_t)need * sizeof *j->game);
        if (j->game && !pvr_decode(tcw, w, h, stride, j->game, need)) {
            free(j->game);
            j->game = NULL; /* pvr_pack_put_over logs it and the job fails: the game's own texture shows */
        }
    }
    g_pending++;
    g_st.kicks++;
    g_st.early += !urgent;
    if (!g_thread) g_thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
    WakeConditionVariable(&g_wake);
    return g_jobs++;
}

void pvr_pack_hint(u32 tcw, u32 w, u32 h, u32 stride)
{
    char path[PATH_CAP] = "?"; /* left so: the pack is off */
    u64 t = now_us(), over_key = 0;
    const char *image = pvr_pack_image(tcw, w, h, stride, path, sizeof path, &over_key);
    if (!image && path[0] == '?') return;
    AcquireSRWLockExclusive(&g_lock);
    if (image) kick(image, over_key, tcw, w, h, stride, 0);
    g_st.hints++;
    g_st.hint_us += now_us() - t;
    ReleaseSRWLockExclusive(&g_lock);
}

void pvr_pack_surface(u32 tcw, u32 w, u32 h)
{
    u32 i = 0;
    while (i < g_surfs && (g_surf[i].tcw & TCW_ADDR) != (tcw & TCW_ADDR)) i++;
    if (!w) { /* freed */
        if (i < g_surfs) g_surf[i] = g_surf[--g_surfs];
        return;
    }
    if (i == g_surfs && g_surfs == SURFACES) return; /* past the SDK's own bound: its draw kicks it */
    g_surfs += i == g_surfs;
    g_surf[i].tcw = tcw;
    g_surf[i].w = w;
    g_surf[i].h = h;
}

/* A write of texture memory by the game's own copy: each surface whose last texel byte it reaches is hinted (its key is
 * over the bytes now there, so a write that is not the texture's last one at worst kicks an image no draw takes). */
void pvr_pack_written(u32 offset, u32 size)
{
    for (u32 i = 0; i < g_surfs; i++) {
        u32 at = (g_surf[i].tcw & TCW_ADDR) << 3, end = at + pvr_texture_bytes(g_surf[i].tcw, g_surf[i].w, g_surf[i].h, 0);
        if (offset < end && size >= end - offset) pvr_pack_hint(g_surf[i].tcw, g_surf[i].w, g_surf[i].h, 0);
    }
}

/* The drawing thread's: the image at path, waiting for it if it is not ready (kicked now if it was not kicked). Its
 * texture (a device), else its levels; the level count, 0 if it cannot be read. */
u32 pvr_pack_take(const char *path, u64 over_key, u32 tcw, u32 tw, u32 th, u32 stride, const u32 **texels,
                  ID3D11Texture2D **tex, u32 *w, u32 *h)
{
    u32 i, levels = 0;
    u64 t = now_us(), waited;
    AcquireSRWLockExclusive(&g_lock);
    i = kick(path, over_key, tcw, tw, th, stride, 1);
    while (i != ~0u && g_job[i].state <= JOB_WORKING)
        if (!g_thread && !run_next()) break;
        else if (g_thread) SleepConditionVariableSRW(&g_done, &g_lock, INFINITE, 0);
    waited = now_us() - t;
    if (i != ~0u && g_job[i].state > JOB_WORKING) {
        Job *j = &g_job[i];
        const char *file = strrchr(j->path, '/');
        if (!j->taken) {
            j->taken = 1;
            g_pending--;
            g_st.waits += waited >= 100u;
            g_st.wait_us += waited;
            if (waited > g_st.wait_max_us) g_st.wait_max_us = waited;
            port_log("pvr: texture pack: %s %ux%u, %u levels (%s): kicked at blank %u (%s), ready at blank %u, first drawn at "
                     "blank %u, the draw waited %.1f ms; loader: read %.1f ms, chain %.1f ms (file %.1f, unpack %.1f, "
                     "unfilter %.1f), upload %.1f ms\n", file ? file + 1 : j->path, j->w, j->h, j->levels,
                     j->how ? j->how : "unreadable", j->kick_blank, j->early ? "load" : "first draw", j->ready_blank,
                     port_vblank_count(), ms(waited), j->read_ms, j->chain_ms, j->split[0], j->split[1], j->split[2], j->upload_ms);
        }
        *w = j->w;
        *h = j->h;
        if (j->tex && tex) *tex = j->tex, levels = j->levels;
        else if (j->texels) *texels = j->texels, levels = j->levels;
    }
    ReleaseSRWLockExclusive(&g_lock);
    return levels;
}

/* A switch of pack (pvr_pack.c's load_packs): every image dropped; one being loaded is dropped when it is done. */
void pvr_pack_load_reset(void)
{
    AcquireSRWLockExclusive(&g_lock);
    for (u32 i = 0; i < g_jobs; i++) {
        free(g_job[i].texels);
        free(g_job[i].game);
        if (g_job[i].tex) ID3D11Texture2D_Release(g_job[i].tex);
    }
    g_jobs = g_pending = 0;
    g_epoch++;
    ReleaseSRWLockExclusive(&g_lock);
}

void pvr_pack_load_report(void)
{
    if (!g_st.kicks) return;
    port_log("pvr: texture pack loader: %u images kicked, %u at their load, %u at their first draw; %u dropped (%u "
             "pending at most); %u first draws waited, %.1f ms in all, %.1f ms the longest; %u hints, %.1f ms on the game's "
             "thread\n", g_st.kicks, g_st.early, g_st.kicks - g_st.early, g_st.dropped, PENDING, g_st.waits, ms(g_st.wait_us),
             ms(g_st.wait_max_us), g_st.hints, ms(g_st.hint_us));
}
