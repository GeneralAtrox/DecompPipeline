/* The hardware check's port half (tools/hw_check.py compares; tools/hw_emu.lua is the emulator's half). Each scene
 * the TA takes is written as the console's TA reads it, per display list, small enough for every frame of a run:
 *   --hw-out FILE        "R frame" per scene (frame: the paired emulator frame, lockstep_frame), then a line per list
 *                        that has parameters: "L list strips vertices sx sy sz seq..". A strip is a polygon strip (to
 *                        its end-of-strip vertex), a sprite (4 vertices) or a modifier volume (its triangles, as
 *                        Flycast counts them: one per volume header, empty ones left out); sx sy sz sum each polygon
 *                        strip's x, y and z extent ends (min + max). seq is each strip's vertex (or triangle) count,
 *                        after "h pcw isp tsp tcw" (hex) when its header differs from the previous strip's.
 *   --hw-tex FILE        "frame address size" lines, by frame: at the first scene of that emulator frame, those
 *                        texture memory bytes go to FILE.bin, each as u32 address, size, frame and the bytes (none
 *                        for a frame the port has no scene in: a movie it does not play).
 *   --hw-digest FILE     as --hw-out, with each list's vertex digest ("D" lines, ta_digest.c) and without the store
 *                        queue model below: the lists as the port plays them (tools/lockstep.py, tools/ta_digest.py).
 *                        --hw-out writes the D lines too; --hw-dump F:FILE the masked fields of one scene (ta_digest.c).
 *   --hw-strip L:S:V@F   logs the guest address of vertex V of strip S of list L in the scene of emulator frame F
 *                        (V -1: its header), for --hw-watch-ram.
 *   --hw-watch-ram A@F   logs each write to the guest word A from the scene's begin to its render, in emulator frame
 *                        F, with the stack (lockstep_watch.c).
 *   --hw-watch-vram A:N@F  logs each texture memory write that covers [A, A + N) through emulator frame F, with
 *                        the stack, in the watch's format (tools/lockstep_watch.py names the writers).
 * With any of them the store queues are modelled as the TA sees them (r14 defect class c, which the port's own path
 * does not model): the console's TA gets the vertex buffer through the store queues, and a pref of a block nothing
 * wrote sends the queue's last contents, the block 0x40 back (the queue is picked by address bit 5); the port reads
 * the buffer, so such a block is stale RAM. Each block the TA took is poisoned after the render; one still poisoned
 * at the next render gets the block 0x40 back first. "R frame resent" counts those blocks. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <intrin.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "../pvr/pvr.h"
#include "lockstep.h"
#include "lockstep_parts.h"

#define PARA_END 0u
#define PARA_POLY 4u
#define PARA_SPRITE 5u
#define PARA_VERTEX 7u
#define PCW_END_OF_STRIP (1u << 28)
#define PCW_VOLUME (1u << 6)
#define PCW_COL_SHIFT 4
#define PCW_TEXTURE (1u << 3)
#define PCW_OFFSET (1u << 2)
#define SLOT_WORDS 8u
#define LIST_MODVOL_OPAQUE 1u
#define LIST_MODVOL_TRANS 3u
#define SEQ_CAP (1u << 20)
#define WATCH_STACK 48u
#define WATCH_HITS 32u
#define SQ_POISON 0xfffffffdu
#define SQ_BACK 0x40u

typedef struct Tex {
    u32 frame, addr, size;
} Tex;

typedef struct Strips {
    u32 list, strips, vertices, n, hdr[4], last[4], have_hdr, have_last, kind;
    double sum[3], lo[3], hi[3];
    char *seq;
    size_t len;
} Strips;

static FILE *g_out, *g_bin;
static Tex *g_tex;
static u32 g_ntex, g_ti;
static u32 g_find[4], g_finding; /* --hw-strip: list, strip, vertex (+1), frame */
static u32 g_ram_at, g_ram_frame, g_ram_watching, g_ram_armed;
static u32 g_vram_at, g_vram_size, g_vram_frame, g_vram_watching, g_vram_hits;
static u32 g_sq;     /* the store queue model: on with any --hw option */
static u32 g_paired; /* a scene had an emulator frame: the run ends with them */
static u32 g_frame;  /* the scene's emulator frame, for ta_digest_strip */
static char g_seq[SEQ_CAP];

static FILE *open_or_exit(const char *path, const char *mode)
{
    FILE *f = NULL;
    if (fopen_s(&f, path, mode) || !f) {
        port_log("hw check: cannot open %s\n", path);
        exit(2);
    }
    return f;
}

static void load_tex(const char *path)
{
    char line[128], bin[512];
    u32 cap = 0;
    FILE *f = open_or_exit(path, "r");
    Tex t;
    while (fgets(line, sizeof line, f))
        if (sscanf_s(line, "%u %x %x", &t.frame, &t.addr, &t.size) == 3 && t.addr < PVRX_VRAM_SIZE
            && t.size <= PVRX_VRAM_SIZE - t.addr) {
            if (g_ntex == cap) {
                Tex *p = (Tex *)realloc(g_tex, (cap = cap ? cap * 2u : 1024u) * sizeof *g_tex);
                if (!p) exit(2);
                g_tex = p;
            }
            g_tex[g_ntex++] = t;
        }
    fclose(f);
    snprintf(bin, sizeof bin, "%s.bin", path);
    g_bin = open_or_exit(bin, "wb");
    port_log("hw check: %u textures to dump\n", g_ntex);
}

int hw_check_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (ta_digest_option(arg, value)) return 2;
    if (!strcmp(arg, "--hw-digest")) {
        g_out = open_or_exit(value, "w");
        setvbuf(g_out, NULL, _IOFBF, 1u << 20);
        return 2;
    }
    if (!strcmp(arg, "--hw-out")) {
        g_out = open_or_exit(value, "w");
        setvbuf(g_out, NULL, _IOFBF, 1u << 20);
    } else if (!strcmp(arg, "--hw-tex")) {
        load_tex(value);
    } else if (!strcmp(arg, "--hw-strip")) {
        s32 v = 0;
        if (sscanf_s(value, "%u:%u:%d@%u", &g_find[0], &g_find[1], &v, &g_find[3]) != 4) exit(2);
        g_find[2] = (u32)(v + 1);
        g_finding = 1;
    } else if (!strcmp(arg, "--hw-watch-ram")) {
        if (sscanf_s(value, "%x@%u", &g_ram_at, &g_ram_frame) != 2) exit(2);
        g_ram_watching = 1;
    } else if (!strcmp(arg, "--hw-watch-vram")) {
        if (sscanf_s(value, "%x:%x@%u", &g_vram_at, &g_vram_size, &g_vram_frame) != 3) exit(2);
        g_vram_watching = 1;
    } else {
        return 0;
    }
    g_sq = 1;
    return 2;
}

static double word_f(u32 w)
{
    union {
        u32 u;
        f32 f;
    } b;
    b.u = w;
    return (double)b.f;
}

/* The words of a polygon header (8, or 16 for intensity colours with a second face) and of a vertex (ta_parse.c). */
static u32 header_words(u32 pcw)
{
    u32 col = (pcw >> PCW_COL_SHIFT) & 3u;
    return col == 2u && ((pcw & PCW_VOLUME) || (pcw & PCW_OFFSET)) ? 16u : 8u;
}

static u32 vertex_words(u32 pcw)
{
    u32 col = (pcw >> PCW_COL_SHIFT) & 3u, tex = (pcw & PCW_TEXTURE) != 0;
    if (pcw & PCW_VOLUME) return tex ? 16u : 8u;
    return tex && col == 1u ? 16u : 8u;
}

static void put(Strips *s, const char *fmt, u32 a, u32 b, u32 c, u32 d)
{
    if (s->len + 64u >= SEQ_CAP) return;
    int n = snprintf(s->seq + s->len, SEQ_CAP - s->len, fmt, a, b, c, d);
    if (n > 0) s->len += (size_t)n;
}

/* A strip ends: its header (if new) and vertex count into seq, a polygon strip's extent into the sums. */
static void end_strip(Strips *s)
{
    if (!s->n) return;
    if (!s->have_last || memcmp(s->hdr, s->last, sizeof s->hdr)) {
        put(s, " h %x %x %x %x", s->hdr[0], s->hdr[1], s->hdr[2], s->hdr[3]);
        memcpy(s->last, s->hdr, sizeof s->hdr);
        s->have_last = 1;
    }
    put(s, " %u", s->n, 0, 0, 0);
    if (s->kind != 2u) ta_digest_strip(g_frame);
    if (s->kind == 0)
        for (u32 i = 0; i < 3u; i++) s->sum[i] += s->lo[i] + s->hi[i];
    s->strips++;
    s->vertices += s->n;
    s->n = 0;
}

static void add_vertex(Strips *s, const u32 *w)
{
    for (u32 i = 0; i < 3u; i++) {
        double v = word_f(w[1 + i]);
        if (!s->n || v < s->lo[i]) s->lo[i] = v;
        if (!s->n || v > s->hi[i]) s->hi[i] = v;
    }
    s->n++;
}

static void found(const Strips *s, u32 frame, const u32 *at)
{
    if (!g_finding || frame != g_find[3] || s->list != g_find[0] || s->strips != g_find[1]) return;
    port_log("hw check: frame %u list %u strip %u vertex %d at %08x\n", frame, s->list, s->strips,
             (s32)g_find[2] - 1, (u32)(uintptr_t)at);
    g_finding = 0;
}

/* One list's parameters from start to end, as ta_parse.c reads them. */
static void walk(Strips *s, u32 start, u32 end, u32 frame)
{
    const u32 *w = (const u32 *)(uintptr_t)start, *stop = (const u32 *)(uintptr_t)end, *header = NULL;
    int modvol = s->list == LIST_MODVOL_OPAQUE || s->list == LIST_MODVOL_TRANS;
    end_strip(s);
    s->have_hdr = 0; /* each pass's list starts afresh */
    while (w + SLOT_WORDS <= stop) {
        u32 type = w[0] >> 29, words = SLOT_WORDS;
        if (type == PARA_END) {
            end_strip(s);
            s->have_hdr = 0;
        } else if (type == PARA_POLY || type == PARA_SPRITE) {
            end_strip(s);
            words = modvol ? SLOT_WORDS : header_words(w[0]);
            if (w + words > stop) return;
            memcpy(s->hdr, w, sizeof s->hdr);
            if (!modvol) ta_digest_header(w, words, type == PARA_SPRITE);
            s->hdr[0] &= ~PCW_END_OF_STRIP;
            s->kind = modvol ? 2u : (u32)(type == PARA_SPRITE);
            s->have_hdr = 1;
            header = w;
        } else if (type == PARA_VERTEX && s->have_hdr) {
            words = s->kind ? SLOT_WORDS * 2u : vertex_words(s->hdr[0]); /* a sprite or volume triangle: 16 */
            if (w + words > stop) return;
            if (g_find[2] == 0u) found(s, frame, header);
            else if (s->n + 1u == g_find[2]) found(s, frame, w);
            if (s->kind != 2u) ta_digest_vertex(w);
            if (s->kind == 1u) {
                s->n = 4u;
                end_strip(s);
            } else if (s->kind == 2u) {
                s->n++; /* a volume's triangles count up to its next header */
            } else {
                add_vertex(s, w);
                if (w[0] & PCW_END_OF_STRIP) end_strip(s);
            }
        } else if (type != PARA_VERTEX && type > 2u) {
            return; /* an unknown parameter: the TA's reading stops (ta_parse.c logs it) */
        }
        w += words;
    }
    end_strip(s);
}

static void dump_textures(u32 frame)
{
    for (; g_ti < g_ntex && g_tex[g_ti].frame <= frame; g_ti++) {
        const Tex *t = &g_tex[g_ti];
        if (t->frame != frame) continue;
        u32 head[3] = {t->addr, t->size, frame};
        fwrite(head, sizeof head, 1, g_bin);
        fwrite(pvr_vram() + t->addr, 1, t->size, g_bin);
    }
}

void hw_check_begin(void)
{
    s32 frame = lockstep_frame();
    if (!g_ram_watching || g_ram_armed || frame != (s32)g_ram_frame) return;
    lockstep_watch((volatile u32 *)(uintptr_t)g_ram_at, g_ram_frame);
    g_ram_armed = 1;
}

/* The words of b nothing stored get the block 0x40 back's (the queue's last contents); 1 if there were any. */
static int resend(u32 *b)
{
    int any = 0;
    for (u32 i = 0; i < SLOT_WORDS; i++)
        if (b[i] == SQ_POISON) {
            b[i] = b[(s32)i - (s32)(SQ_BACK / 4u)];
            any = 1;
        }
    return any;
}

/* Each block of the scene's lists: poison 0 resends what nothing wrote (the store queue: a block's pref sends the
 * words stored, the rest from the queue's last contents) and returns how many blocks; poison 1 poisons them all. */
static u32 sq_blocks(const PvrPass *passes, u32 count, int poison)
{
    u32 resent = 0;
    for (u32 i = 0; i < count && i < PVRX_MAX_PASSES; i++)
        for (u32 l = 0; l < PVRX_LISTS; l++) {
            u32 *start = (u32 *)(uintptr_t)passes[i].start[l], *stop = (u32 *)(uintptr_t)passes[i].end[l];
            for (u32 *b = start; b + SLOT_WORDS <= stop; b += SLOT_WORDS)
                if (poison)
                    for (u32 k = 0; k < SLOT_WORDS; k++) b[k] = SQ_POISON;
                else if (b - SQ_BACK / 4u >= start)
                    resent += (u32)resend(b);
        }
    return resent;
}

void hw_check_rendered(const PvrPass *passes, u32 count)
{
    if (g_sq) sq_blocks(passes, count, 1);
}

void hw_check_scene(const PvrPass *passes, u32 count)
{
    s32 frame = lockstep_frame();
    Strips s;
    if (g_ram_armed) {
        lockstep_watch(NULL, g_ram_frame);
        g_ram_armed = 0;
        g_ram_watching = 0;
    }
    u32 resent = g_sq ? sq_blocks(passes, count, 0) : 0u;
    if (frame >= 0) g_paired = 1;
    else if (g_paired && g_sq) port_exit("hw check: the emulator frames ended", 0);
    if (frame < 0 || port_extra_drawing()) return;
    if (g_bin) dump_textures((u32)frame);
    if (!g_out && !g_finding && !ta_digest_dumping()) return;
    if (g_out) fprintf(g_out, "R %d %u\n", frame, resent);
    g_frame = (u32)frame;
    for (u32 l = 0; l < PVRX_LISTS; l++) {
        memset(&s, 0, sizeof s);
        ta_digest_list(l);
        s.list = l;
        s.seq = g_seq;
        g_seq[0] = '\0';
        for (u32 i = 0; i < count && i < PVRX_MAX_PASSES; i++)
            walk(&s, passes[i].start[l], passes[i].end[l], (u32)frame);
        if (g_out && s.strips)
            fprintf(g_out, "L %u %u %u %.9g %.9g %.9g%s\n", l, s.strips, s.vertices, s.sum[0], s.sum[1], s.sum[2],
                    g_seq);
        if (g_out && s.strips && l != LIST_MODVOL_OPAQUE && l != LIST_MODVOL_TRANS) ta_digest_write(g_out, l);
    }
    ta_digest_scene_done((u32)frame);
}

void hw_check_vram(u32 offset, u32 size)
{
    char line[96 + WATCH_STACK * 9];
    const u32 *sp = (const u32 *)_AddressOfReturnAddress();
    s32 frame = lockstep_frame();
    if (!g_vram_watching || frame > (s32)g_vram_frame || offset >= g_vram_at + g_vram_size
        || offset + size <= g_vram_at || g_vram_hits++ >= WATCH_HITS)
        return;
    if (g_vram_hits == 1u)
        port_log("lockstep watch: frame %u word %08x image %08x\n", g_vram_frame, g_vram_at,
                 (u32)(uintptr_t)GetModuleHandleW(NULL));
    int n = snprintf(line, sizeof line, "lockstep watch: frame %d write %08x after %08x stack", frame, offset,
                     (u32)(uintptr_t)_ReturnAddress());
    for (u32 i = 1; i <= WATCH_STACK && n > 0 && (size_t)n < sizeof line; i++)
        n += snprintf(line + n, sizeof line - (size_t)n, " %08x", sp[i]);
    port_log("%s\n", line);
}

void hw_check_close(void)
{
    if (g_out) fclose(g_out);
    if (g_bin) fclose(g_bin);
    g_out = g_bin = NULL;
}
