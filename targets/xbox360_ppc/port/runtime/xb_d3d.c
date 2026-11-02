/* The host side of the XDK Direct3D library (port/boundary.json): the library is not translated, the game's calls into it land
 * here. Written entry by entry, from the stops: each case names the call that first needed it. The entries are anonymous in the
 * image (no symbols); a name below is the D3D9 call whose arguments the caller passes. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"

/* What the game asked the device to be (the D3DPRESENT_PARAMETERS it passed). The D3D11 device and swap chain are made from the
 * window the shared base provides, at the size the base's aspect option gives; this record is what the game believes. */
typedef struct { u32 width, height, format, back_buffers, depth_format, interval; } XbGpuMode;
static XbGpuMode s_mode;
static struct { u32 x, y, w, h; float zmin, zmax; } s_view;

/* A GPU resource the game made: the guest-side header it holds the pointer to, and the arguments it asked with. */
/* The first lines show the game's order of calls; after them a run at full speed writes no line per draw */
static int s_log_left = 3000;
#define D3D_LOG(...) do { if (s_log_left > 0 && s_log_left--) port_log(__VA_ARGS__); } while (0)

typedef struct { u32 guest; const char *kind; u32 arg[6]; u32 data, pitch; int dirty; } XbRes;
static XbRes s_res[512];
static unsigned s_res_n;

static XbRes *find_res(u32 guest)
{
    unsigned i;
    for (i = 0; i < s_res_n; i++)
        if (s_res[i].guest == guest) return &s_res[i];
    return 0;
}
static u32 s_target[4], s_depth, s_texture[16], s_bound_decl, s_bound_vs, s_bound_ps, s_indices;      /* the guest header bound to each render target slot */
static u32 s_vs_container, s_ps_container;                                                  /* the compiled containers the bound shaders were made from */
static struct { u32 buffer, offset, stride; } s_stream[16];                                 /* the vertex buffer on each stream */

/* The host texture standing for a guest header the game made (xb_gpu.c makes it on first use; 28 is the host's 8-bit RGBA). */
static XbHostTex *host_tex(u32 guest, int depth)
{
    XbRes *r = find_res(guest);
    if (!r) return 0;
    return xb_gpu_tex(guest, r->arg[0], r->arg[1], 28, !depth, depth);
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

/* XB_WATCH: guest byte addresses in hex, comma apart; each change at a frame goes in the log. How a test run sees a state machine move */
static void watch_poll(void)
{
    static int last[32];
    const char *p = getenv("XB_WATCH");
    int i = 0;
    if (!p) return;
    while (*p && i < 32) {
        char *end;
        u32 at = (u32)strtoul(p, &end, 16);
        int now = rd8(at);
        if (s_frame == 0 || last[i] != now) port_log("watch frame %u %08x: %02x\n", s_frame, at, now);
        last[i++] = now;
        p = *end ? end + 1 : end;
    }
}
static u32 s_begin_data;
static unsigned s_begin_prim, s_begin_n, s_begin_stride;

static void stage_dump(const char *name, u32 guest, int plain)
{
    const char *dir = getenv("XB_DUMP"), *at = getenv("XB_DUMP_STAGES");
    static unsigned serial;
    char path[260];
    if (!dir || !at || s_frame != (unsigned)atoi(at)) return;
    sprintf_s(path, sizeof path, "%s/stage_%03u_%s_%08x.bmp", dir, serial++, name, guest);
    xb_gpu_dump(plain ? xb_gpu_tex_plain(guest, 0, 0) : host_tex(guest, 0), path);
}

static void textures_for_draw(void)
{
    unsigned i;
    for (i = 0; i < 16; i++) {
        XbRes *r = s_texture[i] ? find_res(s_texture[i]) : 0;
        XbHostTex *t = 0;
        if (r && r->data) {
            t = xb_gpu_tex_plain(r->guest, r->arg[0], r->arg[1]);
            if (r->dirty) {
                texture_upload(r, t);
                r->dirty = 0;
            }
            stage_dump("tex", r->guest, 1);
        } else if (r) {
            t = host_tex(r->guest, 0);
        }
        xb_gpu_bind_texture(i, t, s_texture[i]);
    }
}

/* A vertex declaration: the elements the game described (D3DVERTEXELEMENT9, 12 bytes each on this platform: stream and offset, a
 * format word, then method, usage and usage index), kept for the host input layout made when a draw pairs it with a shader. */
typedef struct { u32 guest, n; struct { u16 stream, offset; u32 type; u8 usage, index; } el[16]; } XbDecl;
static XbDecl s_decl[64];
static unsigned s_decl_n;

static u32 make_res(const char *kind, const TcArgs *a, u32 target)
{
    XbRes *r;
    int i;
    if (s_res_n >= sizeof s_res / sizeof *s_res) xb_stop("d3d_resources", "more than 512 resources", target);
    r = &s_res[s_res_n++];
    r->guest = xb_alloc(0x40);
    r->kind = kind;
    for (i = 0; i < 6; i++) r->arg[i] = (u32)a->r[i];
    D3D_LOG("d3d %s %u at %08x: args %x %x %x %x %x %x\n", kind, s_res_n - 1, r->guest, r->arg[0], r->arg[1], r->arg[2], r->arg[3],
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

/* The vertices at p go to the host: the game's vertex element k feeds the shader's fetch k (the library patches the fetch constant from the
 * declaration); each element is converted to the shader's float4 input, missing components 0,0,0,1 */
static void draw_vertices(unsigned prim, unsigned n, u32 p, u32 stride)
{
    XbDecl *d = 0;
    const XbShader *vs = xb_shader_find(s_vs_container);
    float *v;
    unsigned i, k, nat;
    for (i = 0; i < s_decl_n; i++)
        if (s_decl[i].guest == s_bound_decl) d = &s_decl[i];
    if (!d) xb_stop("d3d_draw", "a draw with no vertex declaration bound", s_bound_decl);
    if (!vs) xb_stop("d3d_draw", "a draw with no converted vertex shader", s_vs_container);
    nat = vs->n_inputs;
    if (nat > d->n) xb_stop("d3d_draw", "the shader fetches more vertex inputs than the declaration has", nat);
    if (prim != 8 && prim != 13 && prim != 4 && prim != 6) xb_stop("d3d_draw", "a primitive type the host has no case for", prim);
    v = (float *)calloc((size_t)n * (nat ? nat : 1) * 4, sizeof(float));
    for (i = 0; i < n; i++) {
        for (k = 0; k < nat; k++) {
            float *o = v + ((size_t)i * nat + k) * 4;
            u32 at = p + i * stride + d->el[k].offset, fmt = d->el[k].type & 0x3F, c;
            o[3] = 1.0f;
            if (fmt >= 36 && fmt <= 38) {   /* 32-bit float, 1 to 4 of them */
                unsigned m, nc = fmt == 38 ? 4 : fmt - 35;   /* 36 one, 37 two, 38 four */
                for (m = 0; m < 4; m++) o[m] = m < nc ? 0.0f : (m == 3 ? 1.0f : 0.0f);
                for (m = 0; m < nc; m++) {
                    u32 w = rd32(at + 4 * m);
                    memcpy(&o[m], &w, 4);
                }
            } else if (fmt == 57) {          /* three 32-bit floats */
                unsigned m;
                for (m = 0; m < 3; m++) {
                    u32 w = rd32(at + 4 * m);
                    memcpy(&o[m], &w, 4);
                }
            } else if (fmt == 6) {           /* four 8-bit unsigned normalised; D3DCOLOR is ARGB in the word */
                c = rd32(at);
                o[0] = ((c >> 16) & 255) / 255.0f;
                o[1] = ((c >> 8) & 255) / 255.0f;
                o[2] = (c & 255) / 255.0f;
                o[3] = ((c >> 24) & 255) / 255.0f;
            } else {
                free(v);
                xb_stop("d3d_draw", "a vertex element format the host has no case for", d->el[k].type);
            }
        }
    }
    D3D_LOG("d3d draw vs %08x ps %08x, target %08x, blend %u %u %u, attr 2 %g %g %g %g, first vertex %g %g %g %g | %g %g %g %g | %g %g %g %g\n", s_vs_container, s_ps_container, s_target[0], xb_rs(60), xb_rs(72), xb_rs(76),
            nat > 2 ? v[8] : 0, nat > 2 ? v[9] : 0, nat > 2 ? v[10] : 0, nat > 2 ? v[11] : 0, v[0], v[1], v[2], v[3], nat > 1 ? v[4] : 0, nat > 1 ? v[5] : 0, nat > 1 ? v[6] : 0, nat > 1 ? v[7] : 0,
            n > 1 ? v[nat * 4] : 0, n > 1 ? v[nat * 4 + 1] : 0, n > 2 ? v[nat * 8] : 0, n > 2 ? v[nat * 8 + 1] : 0);
    D3D_LOG("d3d DrawVerticesUP prim %u, %u vertices, stride %u, %u inputs\n", prim, n, stride, nat);
    textures_for_draw();
    if (prim == 13) {   /* a quad list (first stop the UI library), two triangles each: the host has no quads */
        unsigned q, nq = n / 4, sz = nat * 4;
        float *t = (float *)malloc((size_t)nq * 6 * sz * sizeof(float));
        static const unsigned char corner[6] = {0, 1, 2, 0, 2, 3};
        for (q = 0; q < nq; q++)
            for (k = 0; k < 6; k++) memcpy(t + (q * 6 + k) * sz, v + (q * 4 + corner[k]) * sz, sz * sizeof(float));
        xb_gpu_draw(4, nq * 6, nat, t);
        free(t);
    } else
        xb_gpu_draw(prim, n, nat, v);
    stage_dump("after_draw", s_target[0], 0);
    free(v);
}

#include "xb_d3d_res.h"
#include "xb_d3d_draw.h"

int xb_lib_entry(u32 target, const TcArgs *a, u64 *r3, f64 *f1)
{
    (void)f1;
    if (target == 0x821e5990) return xb_xact_create(a, r3);
    if (target == 0x82264068) return xb_xaudio2_create(a, r3);
    if (target == 0x821b09f8) return xb_net_voice_start(a, r3);
    if (target == 0x82141248) return xb_net_stage_message(a, r3);
    if (target >= XB_XACT_BASE + (XB_CLASS_RS << 10) && target < XB_XACT_BASE + 0x2000) return xb_state_call(target, a, r3);
    if (target >= XB_XACT_BASE && target < XB_XACT_BASE + 0x2000) return xb_xact_call(target, a, r3);
    return entry_res(target, a, r3) || entry_draw(target, a, r3);
}
