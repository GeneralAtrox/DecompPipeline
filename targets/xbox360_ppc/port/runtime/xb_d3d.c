/* The host side of the XDK Direct3D library (port/games/<game>/boundary.json): the library is not translated, the game's calls into it land
 * here. Written entry by entry, from the stops: each case names the call that first needed it. The entries are anonymous in the
 * image (no symbols); a name below is the D3D9 call whose arguments the caller passes. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>   /* SRWLOCK: the game makes resources from more than one thread */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>      /* ldexpf: the half floats of made shaders' fetches (xb_d3d_th.h) */
#include "xb.h"
#include "xb_game.h"
#include "host.h"              /* host_data_folder: where made shaders with no converted copy are kept (xb_d3d_th.h) */
#include "xb_xenos_gamma.h"    /* gamma textures and the display's gamma ramp (xb_d3d_tex.h, xb_d3d_draw.h Swap) */
#include "xb_xenos_colour.h"   /* a render target's own exponent bias (xb_d3d_th.h th_target_bias) */
#include "frame_capture.h"     /* g_fcap_on: the frame capture records stream 0 and the fetch constants (xb_d3d_th.h stream0_at) */

/* What the game asked the device to be (the D3DPRESENT_PARAMETERS it passed). The D3D11 device and swap chain are made from the
 * window the shared base provides, at the size the base's aspect option gives; this record is what the game believes. */
typedef struct { u32 width, height, format, back_buffers, depth_format, interval; } XbGpuMode;
static XbGpuMode s_mode;
static struct { u32 x, y, w, h; float zmin, zmax; } s_view;

/* A GPU resource the game made: the guest-side header it holds the pointer to, and the arguments it asked with. */
/* The first lines show the game's order of calls; after them a run at full speed writes no line per draw */
static int s_log_left = 3000;
#define D3D_LOG(...) do { if (s_log_left > 0 && s_log_left--) port_log(__VA_ARGS__); } while (0)

typedef struct { u32 guest; const char *kind; u32 arg[6]; u32 data, pitch; int dirty, dying; } XbRes;   /* dying: released to zero, given back by res_drain */
static XbRes s_res[512];
static unsigned s_res_n;
static SRWLOCK s_res_lock = SRWLOCK_INIT;   /* a slot taken (make_res) or given back (res_drain); find_res reads without it, guest written last */

static XbRes *find_res(u32 guest)
{
    unsigned i;
    if (!guest) return 0;   /* a slot given back holds 0 */
    for (i = 0; i < s_res_n; i++)
        if (s_res[i].guest == guest) return &s_res[i];
    return 0;
}
#define CB_RT_MARK 0x52544831u   /* in a kept SetRenderTarget's r5 (xb_d3d_cb.h): r6 and r7 hold the header's size and type as recorded */
static XbHostTex *s_target_host[4];   /* the host surface bound to each slot, as SetRenderTarget chose it (a replay may find the header re-described) */
static u32 s_target[4], s_depth, s_texture[16], s_bound_decl, s_bound_vs, s_bound_ps, s_indices;      /* the guest header bound to each render target slot */
static u32 s_resolved_ring[16];   /* the last 16 Resolve destinations (not depth), newest at s_resolved_at - 1: Swap shows the one over the front buffer's memory */
static unsigned s_resolved_at;
static u32 s_last_resolved;                                                                 /* the texture the last Resolve copied into */
static u32 s_vs_container, s_ps_container;                                                  /* the compiled containers the bound shaders were made from */
static struct { u32 buffer, offset, stride; } s_stream[16];                                 /* the vertex buffer on each stream */

/* The viewport in s_view to the host and to the device. The library keeps it in the device as six floats at +0x3168 (x, y, width, height, min z,
 * max z), and game code reads it there: Too Human's full-screen passes (82990808, into 8298ffa8) scale their corners by 2 / width and 2 / height
 * from it. Left at zero, every corner was infinite, the post passes drew nothing and the title, menu and game showed black (build box A, 2026-11-06) */
static void view_to_device(u32 dev, const float *f)
{
    u32 k, w;
    if (dev)
        for (k = 0; k < 6; k++) memcpy(&w, &f[k], 4), wr32(dev + 0x3168u + 4u * k, w);
}

static void view_apply(u32 dev)
{
    float f[6] = {(float)s_view.x, (float)s_view.y, (float)s_view.w, (float)s_view.h, s_view.zmin, s_view.zmax};
    view_to_device(dev, f);
    xb_gpu_viewport(f[0], f[1], f[2], f[3], f[4], f[5]);
}

/* The host texture standing for a guest header the game made (xb_gpu.c makes it on first use). 10 is the host's 16-bit float RGBA: the console's
 * targets hold colours over 1 (Too Human draws its scene bright and scales it down by the resolve's exponent bias), which 8-bit RGBA clipped to white */
/* A header no create of the kit's made: Too Human builds its device's own back buffer and depth surface (device +0x5fa0 on) and its front
 * buffer texture itself. The type is the low four bits of the first word (GetType, 827447d0): a texture (3) has its size in its fetch
 * constant's +0x24, width - 1 in bits 0..12 and height - 1 in 13..25, as Resolve (8275e9e0) reads it; a surface (4) at +0x24 too, width - 1
 * from bit 18 and height - 1 from bit 3, as Clear's 82765bc0 reads it */
static u32 header_decode(u32 guest, u32 *w, u32 *h)
{
    u32 type = rd32(guest) & 15, v = rd32(guest + 0x24);
    if (type == 3) { *w = (v & 0x1FFF) + 1; *h = ((v >> 13) & 0x1FFF) + 1; }
    else if (type == 4) { *w = ((v >> 18) & 0x3FFF) + 1; *h = ((v >> 3) & 0x7FFF) + 1; }
    else {   /* the header as it stands, for the fault seen in 3 of 20 runs (type 9, 2026-11-09) */
        port_log("d3d_resource: header %08x holds %08x %08x %08x %08x %08x %08x %08x %08x, +20 %08x %08x %08x %08x; %u resources made, targets %08x %08x depth %08x\n", guest, rd32(guest), rd32(guest + 4),
                 rd32(guest + 8), rd32(guest + 12), rd32(guest + 16), rd32(guest + 20), rd32(guest + 24), rd32(guest + 28), rd32(guest + 32), v, rd32(guest + 40), rd32(guest + 44), s_res_n, s_target[0],
                 s_target[1], s_depth);
        xb_stop("d3d_resource", "a header the kit did not make, of a type it has no case for", type);
    }
    return type;
}

/* The host texture for a game-made header as it was described when a command buffer recorded the call naming it (type and size from
 * header_decode then): see cb_record, SetRenderTarget */
static XbHostTex *host_tex_as(u32 guest, int depth, u32 type, u32 w, u32 h)
{
    return xb_gpu_tex(guest, w, h, 10, depth ? 0 : (type == 4 ? 2 : 1), depth);
}

static XbHostTex *host_tex(u32 guest, int depth)
{
    XbRes *r = find_res(guest);
    u32 type, w, h;
    static u32 said[32];
    unsigned i;
    if (r) return xb_gpu_tex(guest, r->arg[0], r->arg[1], 10, depth ? 0 : strcmp(r->kind, "texture") ? 2 : 1, depth);
    if (!guest) return 0;
    type = header_decode(guest, &w, &h);
    for (i = 0; i < 32 && said[i] && said[i] != guest; i++) {}
    if (i < 32 && !said[i]) { said[i] = guest; port_log("d3d header %08x the game made: type %u, %ux%u%s\n", guest, type, w, h, depth ? ", depth" : ""); }
    return host_tex_as(guest, depth, type, w, h);
}

/* A texture the game fills through a lock. The data stays in guest memory in rows of the host's choosing (the lock tells the game the pitch), so it is linear whatever
 * the format's tiled bit says, and it becomes the host's RGBA when a draw samples it. The format word: bits 0..5 the layout, 6..7 the byte order, 18..29 three bits each
 * for which component red, green, blue and alpha read (4 is 0, 5 is 1). A component is numbered from the low end of the texel's word (82092cd0 fills an A4R4G4B4 with
 * format 0x1828004f: alpha, the top nibble, is component 3, read by the swizzle's alpha) */
static unsigned texel_bytes(u32 format)
{
    switch (format & 0x3F) {
    case 6: return 4;            /* 8_8_8_8 */
    case 15:                     /* 4_4_4_4 */
    case 3: return 2;            /* 1_5_5_5: first stop 821cbf30 under 820a5418, format 0x18280043 */
    case 2: return 1;            /* 8: first stop 821cbf30 under 82256c00 in the UI library, format 0x04900002: alpha only by the swizzle */
    }
    xb_stop("d3d_texture", "a texture format the host has no case for", format);
    return 0;
}

static void texture_upload(XbRes *r, XbHostTex *t)
{
    u32 format = r->arg[5], w = r->arg[0], h = r->arg[1], x, y, swz = format >> 18;
    unsigned layout = format & 0x3F, order = (format >> 6) & 3, k;
    u32 *rgba = (u32 *)malloc((size_t)w * h * 4);
    if ((layout == 6 && order != 2 && order != 0) || ((layout == 15 || layout == 3) && order != 1)) xb_stop("d3d_texture", "a byte order the host has no case for", format);
    for (y = 0; y < h; y++) {
        u32 row = r->data + y * r->pitch, *o = rgba + (size_t)y * w;
        for (x = 0; x < w; x++) {
            unsigned c[6], px = 0;
            if (layout == 6) {
                u32 v = order == 2 ? rd32(row + x * 4) : _byteswap_ulong(rd32(row + x * 4));
                for (k = 0; k < 4; k++) c[k] = (v >> (8 * k)) & 255;
            } else if (layout == 2) {
                for (k = 0; k < 4; k++) c[k] = rd8(row + x);
            } else if (layout == 15) {
                u32 v = rd16(row + x * 2);
                for (k = 0; k < 4; k++) c[k] = ((v >> (4 * k)) & 15) * 17;
            } else {
                u32 v = rd16(row + x * 2);
                for (k = 0; k < 3; k++) c[k] = ((v >> (5 * k)) & 31) * 255 / 31;
                c[3] = v >> 15 ? 255 : 0;
            }
            c[4] = 0;
            c[5] = 255;
            for (k = 0; k < 4; k++) {
                unsigned sel = (swz >> (3 * k)) & 7;
                px |= c[sel > 5 ? 4 : sel] << (8 * k);
            }
            o[x] = px;
        }
    }
    xb_gpu_upload(t, rgba, w * 4);
    free(rgba);
}

/* Before a draw: what each sampler reads. A texture the game filled comes from guest memory at its own size; any other is the host surface a resolve copied into. */
static unsigned s_frame;
unsigned xb_frame_number(void) { return s_frame; }

/* XB_WATCH: guest byte addresses in hex, comma apart; each change at a frame goes in the log. How a test run sees a state machine move. An address can
 * follow pointers: 833332e0>573 is the byte at 0x573 past the pointer stored at 833332e0, and each further >N follows the word there the same way
 * (a field of an object the game allocated). A null pointer on the way logs as null */
static u32 watch_at(const char *p, char **end)
{
    u32 at = (u32)strtoul(p, end, 16);
    while (**end == '>' && at) {
        u32 ptr = rd32(at);
        at = ptr < 0x10000u ? 0 : ptr + (u32)strtoul(*end + 1, end, 16);
        if (!at) while (**end && **end != ',' && **end != '/') (*end)++;
    }
    return at;
}

u32 xb_watch_resolve(const char *p)
{
    char *end;
    return watch_at(p, &end);
}

/* XB_PEEK: the same addresses as XB_WATCH, each with /<bytes in hex>, shown as words at every XB_SAMPLE line: the records a waiting state machine reads */
void xb_peek_dump(void)
{
    const char *p = getenv("XB_PEEK");
    while (p && *p) {
        char *end, line[600];
        const char *name = p;
        u32 at = watch_at(p, &end), n = 0x20, i;
        int k;
        if (*end == '/') n = (u32)strtoul(end + 1, &end, 16);
        if (n > 0x200) n = 0x200;
        k = sprintf_s(line, sizeof line, "peek %.*s = %08x:", (int)(end - name), name, at);
        for (i = 0; at && i < n && k > 0 && k < (int)sizeof line - 12; i += 4) k += sprintf_s(line + k, sizeof line - k, "%s%08x", i % 32 ? " " : (i ? " | " : " "), rd32(at + i));
        port_log("%s\n", line);
        p = *end ? end + 1 : end;
    }
}

/* XB_POKE: diagnostic only, chain=value pairs comma apart (chains as XB_WATCH). At every frame a word that resolves and differs is set to the value and
 * the change logged once per address: a test that a stuck flag is the only thing holding a state machine. Never set in play runs */
static void poke_poll(void)
{
    static u32 done[16];
    const char *p = getenv("XB_POKE");
    unsigned i = 0;
    while (p && *p && i < 16) {
        char *end;
        u32 at = watch_at(p, &end), v;
        while (*end && *end != '=' && *end != ',') end++;
        v = *end == '=' ? (u32)strtoul(end + 1, &end, 16) : 0;
        if (at && rd32(at) != v) {
            if (done[i] != at) port_log("poke frame %u %08x: %08x -> %08x\n", s_frame, at, rd32(at), v), done[i] = at;
            wr32(at, v);
        }
        while (*end && *end != ',') end++;
        p = *end ? end + 1 : end;
        i++;
    }
}

static void watch_poll(void)
{
    static int last[32];
    const char *p = getenv("XB_WATCH");
    int i = 0;
    poke_poll();
    if (!p) return;
    while (*p && i < 32) {
        char *end;
        const char *name = p;
        u32 at = watch_at(p, &end);
        int now;
        while (*end && *end != ',') end++;
        now = at ? rd8(at) : -1;
        if (s_frame == 0 || last[i] != now) {
            if (now < 0) port_log("watch frame %u %.*s: null\n", s_frame, (int)(end - name), name);
            else port_log("watch frame %u %.*s: %02x\n", s_frame, (int)(end - name), name, now);
        }
        last[i++] = now;
        p = *end ? end + 1 : end;
    }
}
/* Each thread's own: a BeginVertices is drawn at its thread's next call (begin_flush). Shared until 2026-11-13, the next call of any thread drew it,
 * on that thread with that thread's device copy and words, while the thread that began it went on setting state */
static __declspec(thread) u32 s_begin_data;
static __declspec(thread) unsigned s_begin_prim, s_begin_n, s_begin_stride;
static __declspec(thread) u32 s_begin_index;   /* BeginIndexedVertices (829fc6f0): its 16-bit indices, 0 for BeginVertices */
static __declspec(thread) unsigned s_begin_icount;
static __declspec(thread) int s_begin_pending;   /* a BeginVertices with no EndVertices call: drawn at the next Direct3D or state call (see 829fc210) */
static __declspec(thread) u32 s_begin_decl, s_draw_decl;   /* the device's declaration slot when it began, which the game clears before that call (829a6424); given to the draw */

static void stage_dump(const char *name, u32 guest, int plain)
{
    /* the environment read once: getenv on every stage of every draw cost 2.4 ms a gameplay frame in strlen (PERF_CALLERS, 2026-11-14) */
    static const char *s_dir, *s_at, *s_from, *s_every;
    static volatile long s_read;
    const char *dir, *at, *from;
    static unsigned serial;
    char path[260];
    if (!s_read) s_dir = getenv("XB_DUMP"), s_at = getenv("XB_DUMP_STAGES"), s_from = getenv("XB_DUMP_STAGES_FROM"), s_every = getenv("XB_DUMP_STAGES_EVERY"), s_read = 1;
    dir = s_dir, at = s_at;
    from = s_from;   /* every stage from this one on, and every Resolve: the end of a frame, where its passes meet */
    if (!dir || !at) return;
    for (; at && (unsigned)atoi(at) != s_frame; at = strchr(at, ',') ? strchr(at, ',') + 1 : NULL) {}   /* XB_DUMP_STAGES: a frame or a list of them */
    if (!at) return;
    if (serial++ % (s_every ? atoi(s_every) | 1 : 1) && strncmp(name, "after_resolve", 13) &&
        (!from || serial - 1 < (unsigned)atoi(from))) return;   /* every Nth: a menu frame has 1,200 draws */
    sprintf_s(path, sizeof path, "%s/stage_%03u_%s_%08x.bmp", dir, serial - 1, name, guest);
    xb_gpu_dump(plain ? xb_gpu_tex_plain(guest, 0, 0) : host_tex(guest, 0), path);
}

#include "xb_d3d_tex.h"

/* XB_DIAG_UNBOUND=frame (bring-up): in that frame, each draw's fetch slots 0..3 that get no texture are logged with the draw's shaders, the fetch
 * constant's words and fields, and whether its memory is a Resolve's destination the kit holds a copy of (and whether that memory was written since):
 * what a post pass's empty slot names (the intro's depth-of-field composite f625a666 and fog pass f9fd4dd1 read slot 0 as depth). At most 400 lines */
static void unbound_diag(unsigned i)
{
    static long at = -2;
    static unsigned n;
    unsigned k;
    u32 f0, f1, f2, f3, f5, base;
    char st[160];
    if (at == -2) { const char *e = getenv("XB_DIAG_UNBOUND"); at = e ? atol(e) : -1; }
    if (at < 0 || xb_frame_number() != (unsigned)at || n >= 400u || i >= 4u || !xb_slot_fetch_w05[i][2]) return;
    f0 = xb_slot_fetch_w05[i][0], f1 = xb_slot_fetch[i][0], f2 = xb_slot_fetch[i][1], f3 = xb_slot_fetch[i][2], f5 = xb_slot_fetch_w05[i][1];
    base = f1 & 0x1ffff000u;
    for (k = 0; k < 128u; k++)
        if (base && s_resolved_mem[k].base == base) break;
    if (k < 128u)
        sprintf_s(st, sizeof st, "a Resolve's destination (into %08x, %s copy, %s since)", s_resolved_mem[k].header, s_resolved_mem[k].depth ? "depth" : "colour",
                  s_resolved_mem[k].sum != resolved_sum(base) ? "written" : "not written");
    else
        sprintf_s(st, sizeof st, "no Resolve's destination the kit holds");
    n++;
    port_log("diag unbound frame %u: vs %08x ps %08x slot %u: words %08x %08x %08x %08x %08x; type %u format %u base %08x %ux%u tiled %u pitch %u dim %u; %s\n",
             xb_frame_number(), s_vs_container, s_ps_container, i, f0, f1, f2, f3, f5, f0 & 3u, f1 & 63u, base, (f2 & 0x1fffu) + 1u, ((f2 >> 13) & 0x1fffu) + 1u,
             f0 >> 31, ((f0 >> 22) & 0x1ffu) << 5, (f5 >> 9) & 3u, st);
}

static void textures_for_draw(void)
{
    unsigned i;
    for (i = 0; i < 16; i++) {
        XbRes *r = s_texture[i] ? find_res(s_texture[i]) : 0;
        XbHostTex *t = 0;
        xb_tex_gamma[i] = 0;
        xb_slot_fetch_w05[i][2] = 0;   /* set again by fetch_texture: the sampler then takes its address modes from the fetch constant */
        if (!r && s_fetch_dev) t = fetch_texture(i);
        else if (r && r->data) {
            t = xb_gpu_tex_plain(r->guest, r->arg[0], r->arg[1]);
            if (r->dirty) {
                texture_upload(r, t);
                r->dirty = 0;
            }
            stage_dump("tex", r->guest, 1);
        } else if (r) {
            t = host_tex(r->guest, 0);
        }
        if (!t) unbound_diag(i);
        xb_gpu_bind_texture(i, t, s_texture[i]);
    }
}

/* A vertex declaration: the elements the game described (D3DVERTEXELEMENT9, 12 bytes each on this platform: stream and offset, a
 * format word, then method, usage and usage index), kept for the host input layout made when a draw pairs it with a shader. */
typedef struct { u32 guest, n; struct { u16 stream, offset; u32 type; u8 usage, index; } el[32]; } XbDecl;
static XbDecl s_decl[4096];   /* Too Human made more than 64 by frame 6 */
static unsigned s_decl_n;

static u32 kit_get(u32 size);          /* xb_d3d_res.h: the kit's own guest memory, pooled */
static void res_drain(void);            /* xb_d3d_res.h: released resources given back once no recorded call can reach them */
static void kit_census(unsigned frame); /* xb_d3d_cb.h: XB_MEMLOG's line for the kit's Direct3D memory */
static void cb_reclaim(void);            /* xb_d3d_cb.h: command buffers the game abandoned give their lists back */

/* The header is 0x40 bytes from the kit's pool (kit_get). It was a whole xb_alloc, 64 KB for 0x40 bytes: xb_alloc's grain, not anything the header
 * needs (the library's own headers are small objects in the game's heap, word aligned; the kit's sit 0x40 apart). A slot given back is used again */
static u32 make_res(const char *kind, const TcArgs *a, u32 target)
{
    XbRes *r = 0;
    unsigned k;
    int i;
    AcquireSRWLockExclusive(&s_res_lock);
    for (k = 0; k < s_res_n && !r; k++)
        if (!s_res[k].kind) r = &s_res[k];
    if (!r) {
        if (s_res_n >= sizeof s_res / sizeof *s_res) xb_stop("d3d_resources", "more than 512 resources", target);
        r = &s_res[s_res_n++];
    }
    r->kind = kind;
    for (i = 0; i < 6; i++) r->arg[i] = (u32)a->r[i];
    r->data = r->pitch = 0, r->dirty = r->dying = 0;
    MemoryBarrier();
    r->guest = kit_get(0x40);   /* last: find_res finds it once it is whole */
    ReleaseSRWLockExclusive(&s_res_lock);
    D3D_LOG("d3d %s %u at %08x: args %x %x %x %x %x %x\n", kind, (unsigned)(r - s_res), r->guest, r->arg[0], r->arg[1], r->arg[2], r->arg[3],
             r->arg[4], r->arg[5]);
    return r->guest;
}

static void read_mode(u32 pp, const char *who)
{
    s_mode.width = rd32(pp + 0);
    s_mode.height = rd32(pp + 4);
    s_mode.format = rd32(pp + 8);
    s_mode.back_buffers = rd32(pp + 12);
    s_mode.depth_format = rd32(pp + 40);
    s_mode.interval = rd32(pp + 52);
    D3D_LOG("d3d %s %ux%u format %x, %u back buffers, depth %x, interval %u\n", who, s_mode.width, s_mode.height, s_mode.format,
             s_mode.back_buffers, s_mode.depth_format, s_mode.interval);
}

/* An entry whose only effect in the library is to write GPU state the host has no use for yet: answered with 0 and logged once with
 * the arguments, so the table of what the port does stays honest. */
static int state_only(u32 target, const TcArgs *a, u64 *r3, const char *what)
{
    static u32 seen[64];
    unsigned i;
    for (i = 0; i < sizeof seen / sizeof *seen && seen[i]; i++)
        if (seen[i] == target) goto done;
    if (i < sizeof seen / sizeof *seen) seen[i] = target;
    D3D_LOG("d3d %08x %s: args %x %x %x %x, answered 0, no effect\n", target, what, (u32)a->r[1], (u32)a->r[2], (u32)a->r[3], (u32)a->r[4]);
done:
    *r3 = 0;
    return 1;
}

#include "xb_d3d_th.h"

#include "xb_d3d_time.h"   /* XB_SAMPLE: the time of each entry and draw step */
#include "xb_d3d_vtx.h"    /* a draw's vertex layout, and its converted vertices kept */
#include "xb_d3d_vconv.h"  /* the vertex conversion: the old loop (XB_VTX_OLD=1), the per-draw one, and the byte check (XB_VTX_CHECK=1) */

/* The vertices at p go to the host: the game's vertex element k feeds the shader's fetch k (the library patches the fetch constant from the
 * declaration); each element is converted to the shader's float4 input, missing components 0,0,0,1 */
/* XB_DIAG_EDRAM=frame[,frame...], a diagnostic (a name and an offset, fog+1, counts from the scene marker's latest start, as XB_DUMP_AT): in those frames, each draw whose render targets, depth surface, pixel shader or colour write masks
 * (render states 212..224) differ from the draw before, every Resolve with its source, and each surface header's first twelve words once a frame (+0x1c is
 * RB_COLOR_INFO or RB_DEPTH_INFO: bits 0..11 the EDRAM base in tiles), at most 800 lines: which targets share on-chip memory, and what each pass writes */
static unsigned edram_diag_n;
static int edram_diag(void)
{
    static u32 fr[8];
    static char rel[8];
    static int nfr = -1;
    int i;
    if (nfr < 0) {
        const char *e = getenv("XB_DIAG_EDRAM");
        nfr = 0;
        while (e && *e && nfr < 8) {
            rel[nfr] = 0;
            while (*e && *e != ',' && *e != '+' && !(*e >= '0' && *e <= '9')) e++, rel[nfr] = 1;
            if (*e == '+') e++;
            fr[nfr++] = (u32)strtoul(e, (char **)&e, 10);
            if (*e == ',') e++; else break;
        }
    }
    for (i = 0; i < nfr; i++)
        if (rel[i] ? xb_mark_frame() != ~0u && s_frame == xb_mark_frame() + fr[i] : s_frame == fr[i]) return edram_diag_n < 800u;
    return 0;
}

static void edram_diag_header(u32 h)
{
    static u32 seen[32], seen_frame;
    unsigned i;
    if (!h) return;
    if (seen_frame != s_frame + 1u) memset(seen, 0, sizeof seen), seen_frame = s_frame + 1u;
    for (i = 0; i < 32 && seen[i] && seen[i] != h; i++) {}
    if (i == 32 || seen[i]) return;
    seen[i] = h, edram_diag_n++;
    port_log("diag edram frame %u header %08x: words %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x; +0x1c base %u tiles, format %u\n", s_frame, h,
             rd32(h), rd32(h + 4), rd32(h + 8), rd32(h + 12), rd32(h + 16), rd32(h + 20), rd32(h + 24), rd32(h + 28), rd32(h + 32), rd32(h + 36), rd32(h + 40),
             rd32(h + 44), rd32(h + 0x1c) & 0xfffu, (rd32(h + 0x1c) >> 16) & 15u);
}

/* The scene marker: the frame a chosen pixel shader starts to be drawn, so runs whose loading times differ (fixed-clock runs do: the same frame can be
 * the loading screen in one and the flight in another) and the kit and Xenia are compared at one scene, not one frame number. The game's XB_MARK_PS
 * (xb_game.h; Too Human's fog pass) or XB_MARK=key; XB_MARK=0 none. A start is a draw after 30 frames without one; each is logged, and XB_DUMP_AT
 * and XB_DIAG_PS count from the latest */
static unsigned s_mark_at, s_mark_last, s_mark_n;
unsigned xb_mark_frame(void) { return s_mark_n ? s_mark_at : ~0u; }
static void mark_draw(void)
{
    static int got;
    static u32 key;
    if (!got) {
        const char *e = getenv("XB_MARK");
        got = 1;
#ifdef XB_MARK_PS
        key = XB_MARK_PS;
#endif
        if (e) key = (u32)strtoul(e, 0, 16);
    }
    if (!key || s_ps_container != key) return;
    if (!s_mark_n || s_frame > s_mark_last + 30) {
        s_mark_at = s_frame, s_mark_n++;
        if (s_mark_n <= 50) port_log("scene marker %u: pixel shader %08x drawn from frame %u\n", s_mark_n, key, s_frame);
    }
    s_mark_last = s_frame;
}

static void edram_diag_draw(void)
{
    static u32 last[10];
    static unsigned draws;
    u32 k[10];
    unsigned i;
    draws++;
    if (!edram_diag()) return;
    k[0] = s_target[0], k[1] = s_target[1], k[2] = s_target[2], k[3] = s_target[3], k[4] = s_depth, k[5] = s_ps_container;
    k[6] = xb_rs(212), k[7] = xb_rs(216), k[8] = xb_rs(220), k[9] = xb_rs(224);
    if (!memcmp(k, last, sizeof k)) return;
    memcpy(last, k, sizeof k), edram_diag_n++;
    port_log("diag edram frame %u draw %u: targets %08x %08x %08x %08x, depth %08x, ps %08x, write masks %x %x %x %x, blend %u (%u %u), z %u func %u write %u\n", s_frame, draws,
             k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7], k[8], k[9], xb_rs(60), xb_rs(72), xb_rs(76), xb_rs(40), xb_rs(44), xb_rs(48));
    for (i = 0; i < 5; i++) edram_diag_header(k[i]);
}

static void draw_vertices(unsigned prim, unsigned n, u32 p, u32 stride)
{
    LONG64 t0;
    XbDecl *d;
    const XbShader *vs = xb_shader_find(s_vs_container);
    float *v;
    unsigned k, nat;
    unsigned long long key;
    int kept, hit = 0;
    void **vb = 0, **ib = 0;   /* where the kept vertices' host buffer stays (vcache), and a unique draw's index buffer */
    if (xb_gpu_draw_nothing()) return;   /* no pixel shader, z off: before its vertices are read */
    if (!made_shaders_converted() || !(d = draw_decl(vs, p, stride))) return;
    nat = vs->n_inputs;
    if (nat > d->n) xb_stop("d3d_draw", "the shader fetches more vertex inputs than the declaration has", nat);
    if (prim != 8 && prim != 13 && prim != 4 && prim != 6 && prim != 5) xb_stop("d3d_draw", "a primitive type the host has no case for", prim);
    s_draw_n = n, s_draw_inputs = nat, s_draw_prim = prim, s_part_draws++, s_part_verts += n, t0 = PART_START(PART_CONVERT);
    s_vr_key = 0;
    key = vtx_old() ? vcache_key(d, n, nat, p, stride) : vcache_key_fast(d, n, nat, p, stride);
    if (key && s_vr_key) key = vrows_key(s_vr_key);   /* XB_VTX_REUSE: a palette draw by the rows it read (xb_d3d_vconv.h) */
    if (key && vtx_unique_on() && (prim == 4 || prim == 6)) key = vc_mix(key, 0x756e6971ull << 32 | prim) | 1u;   /* XB_VTX_UNIQUE: kept apart from a quad list's or fan's */
    s_vu.nu = 0, s_vu.at = 0;   /* one row an entry, unless vconv_planned or the cache says it has distinct rows */
    if ((kept = key && (v = vcache_find(key, n, nat, &vb, &ib, &s_vu.nu, &s_vu.at)) != 0)) { hit = 1; goto converted; }   /* made from the same before */
    v = vtx_out(n, nat, key != 0);   /* every byte is written by the conversion (xb_d3d_vconv.h vtx_out) */
    if (s_vr_key) vrows_begin(nat);
    if (vtx_old() || d != &s_from_fetch || !vtx_convert_fast(n, nat, p, stride, v)) {   /* the per-draw plan (xb_d3d_vconv.h), else the old loop */
        if (s_lazy.n) lazy_gather();   /* an indexed draw's vertices, read only now that they must be converted */
        vtx_convert_old(d, n, nat, p, stride, v);
    }
    if (s_vr_key && !vrows_note(nat)) key = 0;   /* rows not known before, or read outside them: not kept this time */
    s_vc_conv += n, kept = key && vcache_keep(key, n, nat, v, &vb, &ib, s_vu.nu, s_vu.at);
    if (!kept) *(key ? &s_vc_full : &s_vc_nokey) += n;
converted:
    if (vtx_check_on()) vtx_check_draw(d, n, nat, p, stride, v, hit);   /* XB_VTX_CHECK=1: these bytes against a fresh conversion by the old loop */
    if (vtx_stats_on()) vtx_stats_draw(d, n, nat, key, kept, hit);   /* XB_VTX_STATS=1: the cache's hits and misses every 50 frames */
    D3D_LOG("d3d draw vs %08x ps %08x, target %08x, viewport %ux%u, vte off %u, blend %u %u %u, attr 2 %g %g %g %g, first vertex %g %g %g %g | %g %g %g %g | %g %g %g %g\n", s_vs_container, s_ps_container, s_target[0], s_view.w, s_view.h, xb_rs(304), xb_rs(60), xb_rs(72), xb_rs(76),
            nat > 2 ? v[8] : 0, nat > 2 ? v[9] : 0, nat > 2 ? v[10] : 0, nat > 2 ? v[11] : 0, v[0], v[1], v[2], v[3], nat > 1 ? v[4] : 0, nat > 1 ? v[5] : 0, nat > 1 ? v[6] : 0, nat > 1 ? v[7] : 0,
            n > 1 ? v[nat * 4] : 0, n > 1 ? v[nat * 4 + 1] : 0, n > 2 ? v[nat * 8] : 0, n > 2 ? v[nat * 8 + 1] : 0);
    D3D_LOG("d3d DrawVerticesUP prim %u, %u vertices, stride %u, %u inputs\n", prim, n, stride, nat);
    {   /* XB_DIAG_VTX=<pixel shader key> (bring-up): the first 6 draws with that pixel shader log their vertices as converted, every input, up to 8 vertices,
         * and the guest words they came from (Too Human's post pass fea9caac, P1) */
        static int on = -1, said;
        static u32 key;
        if (on < 0) { const char *e = getenv("XB_DIAG_VTX"); on = e && *e; key = on ? (u32)strtoul(e, 0, 16) : 0; }
        if (on && s_ps_container == key && said < 6) {
            unsigned q, a;
            said++;
            port_log("diag vtx frame %u: vs %08x ps %08x prim %u, %u vertices, stride %u, %u inputs, from %08x:", s_frame, s_vs_container, s_ps_container, prim, n, stride, nat, p);
            for (q = 0; q < n && q < 8; q++) {
                port_log(" |");
                for (a = 0; a < nat; a++) port_log(" (%g %g %g %g)", v[(q * nat + a) * 4], v[(q * nat + a) * 4 + 1], v[(q * nat + a) * 4 + 2], v[(q * nat + a) * 4 + 3]);
            }
            port_log("\n   guest words:");
            for (q = 0; p && stride && q < (n < 3 ? n : 3) * stride / 4 && q < 24; q++) port_log(" %08x", rd32(p + 4 * q));
            port_log("\n");
        }
    }
    PART_END(PART_CONVERT, t0), t0 = PART_START(PART_TEXTURES);
    textures_for_draw();
    PART_END(PART_TEXTURES, t0), t0 = PART_START(PART_CONSTANTS);
    if (s_fetch_dev) constants_from_device();
    PART_END(PART_CONSTANTS, t0), t0 = PART_START(PART_HOST);
    edram_diag_draw();
    mark_draw();
    if (prim == 13) {   /* a quad list (first stop the UI library), two triangles each: the host has no quads */
        unsigned q, nq = n / 4, sz = nat * 4;
        float *t = (float *)malloc((size_t)nq * 6 * sz * sizeof(float));
        static const unsigned char corner[6] = {0, 1, 2, 0, 2, 3};
        for (q = 0; q < nq; q++)
            for (k = 0; k < 6; k++) memcpy(t + (q * 6 + k) * sz, v + (q * 4 + corner[k]) * sz, sz * sizeof(float));
        xb_gpu_draw(4, nq * 6, nat, t);
        free(t);
    } else if (prim == 5) {   /* a triangle fan (first stop at frame 635): triangles 0, i, i+1, the host has no fans */
        unsigned f, nf = n > 2 ? n - 2 : 0, sz = nat * 4;
        float *t = (float *)malloc(((size_t)nf * 3 + 1) * sz * sizeof(float));
        for (f = 0; f < nf; f++) {
            memcpy(t + (f * 3) * sz, v, sz * sizeof(float));
            memcpy(t + (f * 3 + 1) * sz, v + (f + 1) * sz, sz * sizeof(float));
            memcpy(t + (f * 3 + 2) * sz, v + (f + 2) * sz, sz * sizeof(float));
        }
        if (nf) xb_gpu_draw(4, nf * 3, nat, t);
        free(t);
    } else if (s_vu.nu)   /* XB_VTX_UNIQUE: the distinct rows, each entry by its row's index */
        xb_gpu_draw_indexed(prim, s_vu.nu, nat, v, n, s_vu.at, kept && xb_draw_fast() ? vb : 0, kept && xb_draw_fast() ? ib : 0);
    else
        xb_gpu_draw_kept(prim, n, nat, v, kept && xb_draw_fast() ? vb : 0);
    PART_END(PART_HOST, t0), s_part_now = 0;
    stage_dump("after_draw", s_target[0], 0);
    if (!kept) vtx_out_done(v);
}

/* XB_DIAG_RESOLVE=1, a diagnostic: a frame's copies in order (R Resolve, E EndTiling: flags/predication, the game's two callers), logged with their
 * source and destination headers whenever the order differs from the last one logged, at most 3000 lines. It lists, by frame, where a run's copy
 * order changes, so a stretch before the scene marker (which is only known once it is drawn) is covered too */
static u32 s_predication;   /* SetPredication's tile mask (xb_d3d_draw.h) */
static void resolve_sig(char kind, u32 fl, u32 src, u32 dst)
{
    static int on = -1;
    static char key[1024], last[1024], full[2048];
    static unsigned frame = ~0u, nk, nf, lines;
    if (on < 0) on = getenv("XB_DIAG_RESOLVE") != NULL;
    if (!on) return;
    if (s_frame != frame) {
        if (frame != ~0u && strcmp(key, last) && lines < 3000u) lines++, strcpy(last, key), port_log("diag resolve frame %u:%s\n", frame, full);
        frame = s_frame, nk = nf = 0, key[0] = full[0] = 0;
    }
    if (nk < sizeof key - 48u) nk += (unsigned)sprintf(key + nk, " %c%x/%x@%08x,%08x", kind, fl, s_predication & 0xffffu, lib_up(0), lib_up(1));
    if (nf < sizeof full - 80u) nf += (unsigned)sprintf(full + nf, " %c%x/%x %08x>%08x @%08x,%08x", kind, fl, s_predication & 0xffffu, src, dst, lib_up(0), lib_up(1));
}

#include "xb_d3d_edram.h"   /* XB_EDRAM: colour targets keyed by their EDRAM tiles */
#include "xb_d3d_res.h"
#include "xb_d3d_draw.h"
#include "xb_d3d_cb.h"

/* A BeginVertices of this thread with no EndVertices: its vertices are written now (829fc210) */
static void begin_flush(void)
{
    s_begin_pending = 0;
    s_draw_decl = s_begin_decl;
    if (s_begin_index) {   /* the vertices gathered in index order, as DrawIndexedVertices does; an index past the vertices reads the first */
        static __declspec(thread) u32 gather, room;
        unsigned i, k, n = s_begin_icount, st = s_begin_stride;
        if (n * st > room) {   /* at least doubled: each one outgrown stays in xb_alloc's memory for good, so 64 KB steps left a trail of them */
            u32 want = (n * st + 0xffffu) & ~0xffffu;
            room = want > 2u * room ? want : 2u * room, gather = xb_alloc(room);
        }
        for (i = 0; i < n; i++) {
            u32 idx = rd16(s_begin_index + 2u * i);
            if (idx >= s_begin_n) idx = 0;
            for (k = 0; k < st; k += 4) wr32(gather + i * st + k, rd32(s_begin_data + idx * st + k));
        }
        s_begin_index = 0;
        draw_vertices(s_begin_prim, n, gather, st);
    } else
        draw_vertices(s_begin_prim, s_begin_n, s_begin_data, s_begin_stride);
    s_draw_decl = 0;
}

/* The device fields a draw entry reads, on the thread that draws: its fetch constants and constants (a replayed draw's copy, t_snap, else the device) and
 * its depth-control, colour mask, colour control and mode control words (a replayed call's as recorded, t_dc / t_dw, else the device's now) */
static void draw_entry_state(u32 dev)
{
    s_fetch_dev = t_snap ? t_snap : dev, s_snap_mask = t_snap ? tc_base + t_snap + CB_SNAP_AT + CB_SNAP_SIZE : 0;   /* t_snap: a replayed draw's copy (cb_snap) */
#ifdef XB_DEV_DEPTHCONTROL
    xb_gpu_depth_word(t_dc_on ? t_dc : rd32(dev + XB_DEV_DEPTHCONTROL));
#endif
#ifdef XB_DEV_COLORMASK
    if (t_dc_on) xb_gpu_dev_words(t_dw[0], t_dw[1], t_dw[2]);
    else xb_gpu_dev_words(rd32(dev + XB_DEV_COLORMASK), rd32(dev + XB_DEV_COLORCONTROL), rd32(dev + XB_DEV_MODECONTROL));
#endif
}

/* A game's entry to the name its call goes by here (games/<game>/xb_game.h), or 0, which no case has, so the entry stops */
static u32 lib_call(u32 target)
{
#ifdef XB_LIB_CALLS_ARE_LABELS
    return target;
#else
    static const u32 calls[][2] = {XB_LIB_CALLS};
    size_t i;
    if (target >= XB_XACT_BASE && target < XB_XACT_BASE + 0x2000) return target;   /* the kit's own methods (a game's entries are above them, 0x82...) */
    for (i = 0; i < sizeof calls / sizeof calls[0]; i++)
        if (calls[i][0] == target) return calls[i][1];
    return 0;
#endif
}

static int lib_entry(u32 target, const TcArgs *a, u64 *r3, f64 *f1)
{
    u32 entry = target;
    (void)f1;
    target = lib_call(target);
    if (target == 0x821e5990) return xb_xact_create(a, r3);
    if (target == 0x82264068) return xb_xaudio2_create(a, r3);
    if (target == 0x821b09f8) return xb_net_voice_start(a, r3);
    if (target == 0x82141248) return xb_net_stage_message(a, r3);
    if (target >= XB_XACT_BASE && target < XB_XACT_BASE + (XB_CLASS_RS << 10)) return xb_xact_call(target, a, r3);
    if (s_begin_pending) begin_flush();   /* this thread's BeginVertices, before anything of this call changes what it draws with */
    if (target == 0x82743190 || target == 0x82743650 || target == 0x82743a40) return cb_entry(target, a, r3);
    /* Too Human sets stream 0 and the vertex declaration inline too (829a62cc), into the device fields its SetStreamSource 8274e658 and SetVertexDeclaration
     * 82758b08 store: the device is what holds them, so those calls write the same fields and each of its draws is first given them as calls, made or
     * recorded in order with it (a command buffer runs after the fields have changed) */
    if (entry == 0x8274e658u || entry == 0x82758b08u) th_state_store(entry, a, t_replay);   /* not at a command buffer's run (th_state_store) */
    /* not in a command buffer's run: its draws were given these calls when recorded, and the fields are the recorder's (frame 504 read a header at 0x900) */
    if ((entry == 0x829fcfc0u || entry == 0x829fcbd0u) && !t_replay) th_draw_state((u32)a->r[0]);
    /* Too Human's SetRenderTarget and SetDepthStencilSurface store the surface in the device's slots, +0x3098 + 4 * index and +0x30a8, and the game reads
     * them back itself (an inline GetRenderTarget): with the slots left empty it restored render target 0 to none and Resolve (8275e9e0) had no source.
     * Written when the call is made or recorded, as the library's setter writes them, never at a command buffer's run: the device's fields are the
     * recorder's (until 2026-11-13 they were written only at the run, on the render thread, while the game recorded the next buffer and read them) */
    if (entry == 0x8274e890u && !t_replay) wr32((u32)a->r[0] + 0x3098u + 4u * ((u32)a->r[1] & 3u), (u32)a->r[2]);
    if (entry == 0x8274ebf0u && !t_replay) wr32((u32)a->r[0] + 0x30a8u, (u32)a->r[1]);
    if (t_rec && cb_record(entry, target, a, r3)) return 1;
    /* From here the call draws or sets on this thread. A draw's device copy and words: a recorded draw's are taken when it is recorded (CbCall), so the
     * recording thread leaves the drawing thread's alone (until 2026-11-13 it set the host's words at each draw it recorded) */
    if (entry == 0x829fcfc0u || entry == 0x829fcbd0u || entry == 0x829fc210u || entry == 0x829fc6f0u) draw_entry_state((u32)a->r[0]);
    if (target >= XB_XACT_BASE + (XB_CLASS_RS << 10) && target < XB_XACT_BASE + 0x2000) return xb_state_call(target, a, r3);
    return entry_res(target, a, r3) || entry_draw(target, a, r3) || xb_state_row_call(entry, a, r3);
}
