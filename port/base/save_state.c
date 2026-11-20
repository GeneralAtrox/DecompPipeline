/* Save states: the file (save_state.h). The kit writes and applies its own blocks through its provider; this file
 * never knows a device. The game's own save is never in a state. The slots, the menu and the keys: save_state_slots.c. */
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <bcrypt.h>
#include "save_state.h"
#pragma comment(lib, "bcrypt.lib")

enum { HDR = 280, TRAILER = 32, TAG = 8 };
static const char k_magic[8] = "PGSTATE";

struct SaveStateOut {
    u8 *p;
    u32 n, cap, blocks, open_at; /* open_at: the open block's length field + 4, or 0 */
    int failed;
};
typedef struct {
    char tag[TAG + 1];
    u32 version, len;
    const u8 *p;
} Block;
struct SaveStateIn {
    u8 *data;
    u32 size, n;
    Block *b;
};

static const SaveStateProvider *g_p;

void save_state_provider(const SaveStateProvider *p) { g_p = p; }
int save_state_has_provider(void) { return g_p != NULL; }

/* ---- writing ---- */
static int grow(SaveStateOut *o, u32 more)
{
    if (o->failed) return 0;
    if (o->n + more > o->cap) {
        u32 cap = o->cap ? o->cap : 1u << 20;
        while (cap < o->n + more) cap *= 2;
        u8 *p = (u8 *)realloc(o->p, cap);
        if (!p) return o->failed = 1, 0;
        o->p = p, o->cap = cap;
    }
    return 1;
}
static int raw(SaveStateOut *o, const void *data, u32 len)
{
    if (!grow(o, len)) return 0;
    if (len) memcpy(o->p + o->n, data, len);
    o->n += len;
    return 1;
}
static void put32_at(u8 *p, u32 v) { p[0] = (u8)v, p[1] = (u8)(v >> 8), p[2] = (u8)(v >> 16), p[3] = (u8)(v >> 24); }
static u32 get32(const u8 *p) { return p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24; }
static int raw32(SaveStateOut *o, u32 v)
{
    u8 b[4];
    put32_at(b, v);
    return raw(o, b, 4);
}
static int text(SaveStateOut *o, const char *s, u32 width) /* a fixed field, cut to width - 1, zero-filled */
{
    char f[128] = {0};
    if (s) strncpy_s(f, sizeof f, s, width - 1);
    return raw(o, f, width);
}

int save_state_begin(SaveStateOut *o, const char *tag, u32 version)
{
    char t[TAG] = {0};
    if (o->open_at) save_state_end(o);
    memcpy(t, tag, strlen(tag) < TAG ? strlen(tag) : TAG);
    if (!raw(o, t, TAG) || !raw32(o, version) || !raw32(o, 0)) return 0;
    o->open_at = o->n;
    return 1;
}
int save_state_put(SaveStateOut *o, const void *data, u32 len) { return o->open_at ? raw(o, data, len) : 0; }
int save_state_end(SaveStateOut *o)
{
    static const u8 zero[4] = {0};
    if (!o->open_at || o->failed) return 0;
    put32_at(o->p + o->open_at - 4, o->n - o->open_at);
    o->open_at = 0, o->blocks++;
    return raw(o, zero, (4 - (o->n & 3)) & 3);
}
int save_state_block(SaveStateOut *o, const char *tag, u32 version, const void *data, u32 len)
{
    return save_state_begin(o, tag, version) && save_state_put(o, data, len) && save_state_end(o);
}

static int sha256(const u8 *data, u32 size, u8 out[32])
{
    BCRYPT_ALG_HANDLE alg = NULL;
    BCRYPT_HASH_HANDLE h = NULL;
    int ok = 0;
    if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL, 0))) return 0;
    if (BCRYPT_SUCCESS(BCryptCreateHash(alg, &h, NULL, 0, NULL, 0, 0))) {
        ok = BCRYPT_SUCCESS(BCryptHashData(h, (PUCHAR)data, size, 0)) && BCRYPT_SUCCESS(BCryptFinishHash(h, out, 32, 0));
        BCryptDestroyHash(h);
    }
    BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

static const char *identity(void) { return g_p && g_p->identity ? g_p->identity() : ""; }

int save_state_encode(int slot, u8 **data, u32 *size)
{
    SaveStateOut o = {0};
    u8 hash[32];
    *data = NULL, *size = 0;
    if (!g_p || !g_p->save) return 0;
    raw(&o, k_magic, 8), raw32(&o, SAVE_STATE_FORMAT), raw32(&o, HDR);
    text(&o, port_console.id, 16), text(&o, port_console.game, 64), text(&o, port_console.version, 64);
    text(&o, identity(), 96);
    raw32(&o, (u32)slot), raw32(&o, port_vblank_count());
    u64 now = (u64)time(NULL);
    raw32(&o, (u32)now), raw32(&o, (u32)(now >> 32)), raw32(&o, 0), raw32(&o, 0);
    if (!g_p->save(&o) || o.failed || (o.open_at && !save_state_end(&o)) || o.n < HDR) {
        free(o.p);
        return 0;
    }
    put32_at(o.p + HDR - 8, o.blocks);
    if (!sha256(o.p, o.n, hash) || !raw(&o, hash, 32)) {
        free(o.p);
        return 0;
    }
    *data = o.p, *size = o.n;
    return 1;
}

/* ---- reading: whole, checked, before anything is applied ---- */
static int same_field(const u8 *f, const char *want, u32 width)
{
    char w[128] = {0};
    if (want) strncpy_s(w, sizeof w, want, width - 1);
    return memcmp(f, w, width) == 0;
}

SaveStateIn *save_state_decode(const u8 *data, u32 size, const char **why)
{
    u8 hash[32];
    const char *dummy;
    if (!why) why = &dummy;
    if (size < HDR + TRAILER) return *why = "too short to be a state", NULL;
    if (!sha256(data, size - TRAILER, hash) || memcmp(hash, data + size - TRAILER, 32)) return *why = "damaged (its hash differs)", NULL;
    if (memcmp(data, k_magic, 8)) return *why = "not a save state", NULL;
    if (get32(data + 8) != SAVE_STATE_FORMAT || get32(data + 12) != HDR) return *why = "made by another version of the state format", NULL;
    if (!same_field(data + 16, port_console.id, 16) || !same_field(data + 32, port_console.game, 64)) return *why = "made by another game", NULL;
    if (!same_field(data + 96, port_console.version, 64) || !same_field(data + 160, identity(), 96))
        return *why = "made by another build of the game (its code or device layout differ)", NULL;
    u32 want = get32(data + HDR - 8), n = 0, at = HDR, end = size - TRAILER;
    if (want > 4096) return *why = "damaged (block count)", NULL;
    SaveStateIn *in = (SaveStateIn *)calloc(1, sizeof *in);
    if (!in || !(in->data = (u8 *)malloc(size)) || !(in->b = (Block *)calloc(want ? want : 1, sizeof(Block)))) {
        save_state_free(in);
        return *why = "out of memory", NULL;
    }
    memcpy(in->data, data, size), in->size = size;
    while (at < end) {
        if (n == want || end - at < TAG + 8) break;
        Block *b = &in->b[n];
        memcpy(b->tag, in->data + at, TAG), b->tag[TAG] = 0;
        b->version = get32(in->data + at + TAG), b->len = get32(in->data + at + TAG + 4);
        at += TAG + 8;
        if (b->len > end - at) break;
        b->p = in->data + at;
        at += b->len + ((4 - (b->len & 3)) & 3);
        n++;
    }
    if (n != want || at != end) {
        save_state_free(in);
        return *why = "damaged (its blocks do not fit the file)", NULL;
    }
    in->n = n;
    return in;
}

void save_state_free(SaveStateIn *in)
{
    if (!in) return;
    free(in->data), free(in->b), free(in);
}

const u8 *save_state_find(const SaveStateIn *in, const char *tag, u32 version, u32 *len)
{
    for (u32 i = 0; in && i < in->n; i++)
        if (!strncmp(in->b[i].tag, tag, TAG) && in->b[i].version == version) {
            if (len) *len = in->b[i].len;
            return in->b[i].p;
        }
    if (len) *len = 0;
    return NULL;
}

int save_state_apply(const SaveStateIn *in) { return g_p && g_p->load && g_p->load(in); }
u32 save_state_vblank(const SaveStateIn *in) { return get32(in->data + HDR - 20); }
