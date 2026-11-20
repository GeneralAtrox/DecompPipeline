/* The console's on-chip render memory (EDRAM), as the host keeps it: included by xb_d3d.c. See targets/xbox360_ppc/DESIGN_RESOLVE.md, "On-chip
 * memory (EDRAM)".
 *
 * On the console a render target header only describes a window onto EDRAM: a base and a layout (pitch, multisampling, format) over tiles of
 * 80x16 samples. Two headers whose windows cover the same tiles see the same contents, and a resolve copies whatever those tiles hold. The kit
 * gave each header its own host texture, so a header that only reads tiles another header wrote read its own empty texture. Too Human at the
 * in-game opening (build box A bk5, bk6, 2026-11-08): the scene target 4046fa70 (640 wide, 2x, base 0, 736 tiles) is resolved with a clear (flags
 * 0x100); then 400fe910 (1280 wide, 1x, base 0, 720 tiles), never drawn into, is resolved over the same texture memory and drawn on by the fog
 * pass. On the console that second copy reads the cleared tiles; on the host it read a texture nothing had written, and the frames went black.
 *
 * XB_EDRAM=1 (off by default until proven): each colour target header has a record of its base and extent in tiles. Headers at the same base whose
 * host textures have the same size share one host texture. A header bound over tiles that another header at the same base wrote more recently
 * first takes that header's picture (a copy), so the newest writer owns the tiles; a resolve reads the owner. Overlaps at another base are logged,
 * not copied: the host keeps each surface as a picture, not as tile memory, so a window that starts part-way into another has no picture to give.
 * Draws apply the colour write masks (render states 212..224, xb_gpu_draw.h blend_for_draw), so a draw that writes no channel keeps what the
 * tiles hold. Depth surfaces are not in the model */

typedef struct { u32 base, extent; XbHostTex *host; unsigned stamp; int cleared; float clear[4]; } EdramRec;   /* cleared: its newest write was a
                                                                                                                  * whole clear to clear[] */
static EdramRec s_edram[64];
static unsigned s_edram_n, s_edram_clock;

int xb_edram_on(void)
{
    static int on = -1;
    if (on < 0) {
        const char *e = getenv("XB_EDRAM");
        on = e && e[0] == '1';
        if (on) port_log("d3d EDRAM model on (XB_EDRAM=1): colour targets keyed by EDRAM base and extent, the newest writer of the tiles owns them\n");
    }
    return on;
}

static unsigned s_edram_said;
#define EDRAM_LOG(...) do { if (s_edram_said < 60) s_edram_said++, port_log(__VA_ARGS__); } while (0)

/* The tiles a colour header covers: 80x16 samples a tile, 2x stacks samples vertically, 4x doubles them across too, 64-bit formats take two
 * columns of samples a pixel (16_16_16_16, 16_16_16_16_FLOAT, 32_32_FLOAT) */
static u32 edram_extent(u32 ci, u32 si, u32 h)
{
    u32 fmt = (ci >> 16) & 15, pitch = si & 0x3fff, msaa = (si >> 16) & 3;
    u32 sx = msaa >= 2 ? 2 : 1, sy = msaa >= 1 ? 2 : 1, wide = (fmt == 5 || fmt == 7 || fmt == 15) ? 2 : 1;
    return ((pitch * sx * wide + 79) / 80) * ((h * sy + 15) / 16);
}

static EdramRec *edram_rec_of(const XbHostTex *t)
{
    unsigned i;
    for (i = 0; i < s_edram_n; i++)
        if (s_edram[i].host == t) return &s_edram[i];
    return 0;
}

/* A host texture was drawn into or cleared (xb_gpu.c): it is now the newest writer of its tiles */
void xb_edram_written(XbHostTex *t)
{
    EdramRec *r = xb_edram_on() && t ? edram_rec_of(t) : 0;
    if (r) r->stamp = ++s_edram_clock, r->cleared = 0;
}

/* A host texture was cleared whole to rgba (xb_gpu.c): it is the newest writer, and its tiles hold one value, which reads the same through any
 * header over them, whatever its pitch, multisampling or size */
void xb_edram_cleared(XbHostTex *t, const float *rgba)
{
    EdramRec *r = xb_edram_on() && t ? edram_rec_of(t) : 0;
    if (r) r->stamp = ++s_edram_clock, r->cleared = 1, memcpy(r->clear, rgba, sizeof r->clear);
}

/* Before a header's host texture is used: if another record at the same base wrote the tiles after it, it takes that picture */
static void edram_sync(EdramRec *r)
{
    EdramRec *best = 0;
    unsigned i;
    for (i = 0; i < s_edram_n; i++) {
        EdramRec *o = &s_edram[i];
        if (o == r || o->host == r->host || o->stamp <= r->stamp) continue;
        if (o->base >= r->base + r->extent || r->base >= o->base + o->extent) continue;
        if (o->base != r->base && !(o->cleared && o->base <= r->base && o->base + o->extent >= r->base + r->extent)) {
            static int said;
            if (!said++) EDRAM_LOG("d3d EDRAM: tiles %u..%u, written last at base %u (tiles %u..%u): an overlap at another base, not copied\n", r->base,
                                   r->base + r->extent - 1, o->base, o->base, o->base + o->extent - 1);
            continue;
        }
        if (!best || o->stamp > best->stamp) best = o;
    }
    if (!best) return;
    if (edram_diag())
        port_log("diag edram frame %u sync: tiles %u..%u (host %ux%u, written at %u) take %s of tiles %u..%u (host %ux%u, written at %u)\n", s_frame, r->base,
                 r->base + r->extent - 1, xb_gpu_tex_w(r->host), xb_gpu_tex_h(r->host), r->stamp, best->cleared ? "the clear value" : "the picture", best->base,
                 best->base + best->extent - 1, xb_gpu_tex_w(best->host), xb_gpu_tex_h(best->host), best->stamp);
    if (best->cleared) {   /* cleared tiles read as the clear value in any layout */
        if (edram_diag()) port_log("diag edram   clear value %g %g %g %g\n", best->clear[0], best->clear[1], best->clear[2], best->clear[3]);
        xb_gpu_clear_tex(r->host, best->clear);
        r->stamp = best->stamp, r->cleared = 1, memcpy(r->clear, best->clear, sizeof r->clear);
        return;
    }
    EDRAM_LOG("d3d EDRAM frame %u: tiles %u..%u (host %ux%u) take the picture of tiles %u..%u (host %ux%u), their newest writer\n", s_frame, r->base,
              r->base + r->extent - 1, xb_gpu_tex_w(r->host), xb_gpu_tex_h(r->host), best->base, best->base + best->extent - 1, xb_gpu_tex_w(best->host),
              xb_gpu_tex_h(best->host));
    xb_gpu_resolve(best->host, r->host, 0);   /* a plain copy of the part both have */
    r->stamp = best->stamp, r->cleared = 0;
}

void xb_edram_sync_host(XbHostTex *t)
{
    EdramRec *r = xb_edram_on() && t ? edram_rec_of(t) : 0;
    if (r) edram_sync(r);
}

/* SetRenderTarget: the host texture a game-made colour header draws into under the model. ci and si are its +0x1c and +0x18 words, h its height,
 * as described when the call was made (a replayed command buffer gives them as recorded) */
static XbHostTex *edram_bind(u32 surf, XbHostTex *host, u32 ci, u32 si, u32 h)
{
    u32 base = ci & 0xfff, extent;
    EdramRec *r = 0;
    unsigned i;
    if (!xb_edram_on() || !surf || !host || find_res(surf) || !(si & 0x3fff)) return host;   /* kit-made headers keep no EDRAM words */
    extent = edram_extent(ci, si, h);
    for (i = 0; i < s_edram_n && !r; i++)
        if (s_edram[i].base == base && xb_gpu_tex_w(s_edram[i].host) == xb_gpu_tex_w(host) && xb_gpu_tex_h(s_edram[i].host) == xb_gpu_tex_h(host)) r = &s_edram[i];
    if (r) {
        if (r->host != host) {
            static u32 said[16];
            unsigned k;
            for (k = 0; k < 16 && said[k] && said[k] != surf; k++) {}
            if (k < 16 && !said[k]) {
                said[k] = surf;
                EDRAM_LOG("d3d EDRAM: header %08x (base %u, %u tiles) shares the host surface of tiles %u..%u\n", surf, base, extent, r->base,
                          r->base + r->extent - 1);
            }
        }
        if (extent > r->extent) r->extent = extent;
    } else {
        if (s_edram_n == 64) {   /* full: the record written longest ago gives way */
            unsigned old = 0;
            for (i = 1; i < 64; i++)
                if (s_edram[i].stamp < s_edram[old].stamp) old = i;
            r = &s_edram[old];
        } else
            r = &s_edram[s_edram_n++];
        r->base = base, r->extent = extent, r->host = host, r->stamp = 0, r->cleared = 0;
        EDRAM_LOG("d3d EDRAM: header %08x covers tiles %u..%u (pitch %u, msaa %u, format %u, height %u), host %ux%u\n", surf, base, base + extent - 1, si & 0x3fff,
                  (si >> 16) & 3, (ci >> 16) & 15, h, xb_gpu_tex_w(host), xb_gpu_tex_h(host));
    }
    edram_sync(r);
    return r->host;
}

/* What a clear leaves in the tiles. The game gives Resolve and EndTiling a clear colour as four floats; the library packs it into the target's format
 * for the clear register, so the tiles hold the colour as that format can: 2_10_10_10_FLOAT (3) keeps red, green and blue as 7e3 floats, 0 to 31.875,
 * and alpha in two bits; 16_16_16_16_FLOAT (7) as half floats. The host clears its 16-bit float texture with the floats as given. XB_EDRAM=1 clears
 * with the value the format holds (a NaN as 0), so a copy of cleared tiles reads what the console's would */
static float edram_7e3(float v)
{
    if (!(v > 0)) return 0;   /* NaN and negatives */
    return v > 31.875f ? 31.875f : v;
}

static float edram_alpha2(float a)
{
    return a > 0 ? (a < 1 ? (float)(int)(a * 3 + 0.5f) / 3 : 1) : 0;
}

static void edram_clear_value(u32 hdr, float *rgba)
{
    u32 fmt;
    unsigned c;
    if (!xb_edram_on() || !hdr || find_res(hdr)) return;
    fmt = (rd32(hdr + 0x1c) >> 16) & 15;
    if (fmt == 3 || fmt == 12) {
        for (c = 0; c < 3; c++) rgba[c] = edram_7e3(rgba[c]);
        rgba[3] = edram_alpha2(rgba[3]);
    } else if (fmt == 7)
        for (c = 0; c < 4; c++)
            if (rgba[c] != rgba[c]) rgba[c] = 0;
            else if (rgba[c] > 65504.0f) rgba[c] = 65504.0f;
            else if (rgba[c] < -65504.0f) rgba[c] = -65504.0f;
}

/* XB_DIAG_EDRAM=<frame>: from 600 frames before that frame (from frame 2000 for a scene-marker offset), each distinct clear of each target once, at most 40 lines: the raw words of the game's colour, the floats, any NaN,
 * infinity or value outside the format's range, the value the target's format holds, and the depth and stencil */
static void clear_diag(const char *who, u32 fl, u32 hdr, u32 cc, const float *rgba, float z, u32 stencil)
{
    static struct { u32 h, w[6]; } seen[40];
    static unsigned n, from;
    static int on = -1;
    u32 w[6], ci, fmt;
    int bias;
    unsigned i, c;
    char flag[160] = "", held[96] = "";
    float v[4];
    if (on < 0) {
        const char *e = getenv("XB_DIAG_EDRAM");
        on = e != 0, from = e && atoi(e) > 600 ? (unsigned)atoi(e) - 600 : e && !(*e >= '0' && *e <= '9') ? 2000u : 0;   /* fog+1: from frame 2000 */
    }
    if (!on || n >= 40 || s_frame < from) return;
    for (c = 0; c < 4; c++) memcpy(&w[c], &rgba[c], 4);
    memcpy(&w[4], &z, 4), w[5] = fl << 16 | (stencil & 0xffff);
    for (i = 0; i < n; i++)
        if (seen[i].h == hdr && !memcmp(seen[i].w, w, sizeof w)) return;
    seen[n].h = hdr, memcpy(seen[n].w, w, sizeof w), n++;
    ci = hdr && !find_res(hdr) ? rd32(hdr + 0x1c) : 0;
    fmt = (ci >> 16) & 15;
    bias = (int)((ci >> 20) & 63);
    if (bias >= 32) bias -= 64;
    for (c = 0; c < 4; c++) {
        float f = rgba[c];
        int k = (int)strlen(flag);
        if (f != f) sprintf_s(flag + k, sizeof flag - k, " %c NaN", "rgba"[c]);
        else if (f > 3.0e38f || f < -3.0e38f) sprintf_s(flag + k, sizeof flag - k, " %c infinite", "rgba"[c]);
        else if ((fmt == 3 || fmt == 12) && (f < 0 || (c < 3 && f > 31.875f) || (c == 3 && f > 1))) sprintf_s(flag + k, sizeof flag - k, " %c outside 7e3", "rgba"[c]);
        else if (fmt == 7 && (f > 65504.0f || f < -65504.0f)) sprintf_s(flag + k, sizeof flag - k, " %c outside half", "rgba"[c]);
        v[c] = f;
    }
    if (fmt == 3 || fmt == 12) {
        for (c = 0; c < 3; c++) v[c] = edram_7e3(v[c]);
        v[3] = edram_alpha2(v[3]);
        sprintf_s(held, sizeof held, "7e3 %g %g %g, alpha %g", v[0], v[1], v[2], v[3]);
    } else if (fmt == 7)
        sprintf_s(held, sizeof held, "half %g %g %g %g", v[0], v[1], v[2], v[3]);
    else
        sprintf_s(held, sizeof held, "format %u not decoded", fmt);
    port_log("d3d clear (%s, flags %x) frame %u of %08x, format %u bias %d: colour block %08x words %08x %08x %08x %08x = %g %g %g %g%s%s; held as %s; depth %g "
             "stencil %x%s\n", who, fl, s_frame, hdr, fmt, bias, cc, w[0], w[1], w[2], w[3], rgba[0], rgba[1], rgba[2], rgba[3], flag[0] ? ";" : "", flag, held,
             z, stencil, xb_edram_on() ? " (XB_EDRAM: cleared with the held value)" : "");
}
