/* The frame after a scene dump, draw by draw (frame_capture.h). */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "frame_capture.h"

#define FCAP_CAP (64u << 20)   /* the records of one frame at most: a frame of 3,000 draws is about 3 MB */

int g_fcap_on;
static char g_fdir[520];
static char *g_buf;
static size_t g_n, g_cap;
static unsigned g_seq, g_pngs, g_blobs, g_truncated;

void fcap_arm(const char *dir)
{
    if (!(port_console.flags & PORT_CONSOLE_FCAP)) return;   /* a console without the capture never arms it */
    strcpy_s(g_fdir, sizeof g_fdir, dir);
    g_n = 0, g_seq = 0, g_pngs = 0, g_blobs = 0, g_truncated = 0;
    g_fcap_on = 1;
}

static void put(const char *s, size_t n)
{
    if (g_n + n + 1 > g_cap) {
        size_t cap = g_cap ? g_cap * 2 : 1u << 20;
        char *p;
        while (cap < g_n + n + 1) cap *= 2;
        if (cap > FCAP_CAP || !(p = (char *)realloc(g_buf, cap))) {
            g_truncated = 1;
            return;
        }
        g_buf = p, g_cap = cap;
    }
    memcpy(g_buf + g_n, s, n);
    g_n += n;
}

void fcap_rec(const char *fmt, ...)
{
    char line[8192];
    int k, m;
    va_list ap;
    if (!g_fcap_on || g_truncated) return;
    k = sprintf_s(line, sizeof line, "{\"n\":%u,", g_seq++);
    va_start(ap, fmt);
    m = vsnprintf(line + k, sizeof line - (size_t)k - 3, fmt, ap);
    va_end(ap);
    if (m < 0 || (size_t)m >= sizeof line - (size_t)k - 3) m = (int)strlen(line + k);   /* cut: still one object a line */
    k += m;
    line[k++] = '}', line[k++] = '\n', line[k] = 0;
    put(line, (size_t)k);
}

static u32 crc_table[256];

static u32 crc32_of(u32 c, const u8 *p, size_t n)
{
    if (!crc_table[1])
        for (u32 i = 0; i < 256; i++) {
            u32 v = i;
            for (int b = 0; b < 8; b++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1;
            crc_table[i] = v;
        }
    c = ~c;
    while (n--) c = crc_table[(c ^ *p++) & 255] ^ (c >> 8);
    return ~c;
}

static void be32(u8 *p, u32 v) { p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v; }

static void chunk(FILE *f, const char *type, const u8 *data, u32 n)
{
    u8 h[8];
    u32 c;
    be32(h, n);
    memcpy(h + 4, type, 4);
    c = crc32_of(0, h + 4, 4);
    c = crc32_of(c, data, n);
    fwrite(h, 1, 8, f);
    if (n) fwrite(data, 1, n, f);
    be32(h, c);
    fwrite(h, 1, 4, f);
}

int fcap_write_png(const char *path, unsigned w, unsigned h, const u8 *rgba, unsigned pitch)
{
    static const u8 sig[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    size_t row = (size_t)w * 4 + 1, raw = row * h, blocks = (raw + 65534) / 65535, n = 2 + raw + blocks * 5 + 4, at = 0, done = 0;
    u8 ihdr[13], *z;
    u32 a = 1, b = 0;
    FILE *f = NULL;
    if (!w || !h || n > 0xFFFFFFF0u || !(z = (u8 *)malloc(n))) return 0;
    z[at++] = 0x78, z[at++] = 0x01;   /* zlib, stored blocks: sizes in MB is enough for a few pictures a capture */
    for (unsigned y = 0; y < h; y++)
        for (size_t x = 0; x < row; x++) {
            u8 v = x ? rgba[(size_t)y * pitch + x - 1] : 0;   /* each row: filter 0, then its bytes */
            if (done % 65535 == 0) {
                size_t len = raw - done < 65535 ? raw - done : 65535;
                z[at++] = (u8)(done + len == raw), z[at++] = (u8)len, z[at++] = (u8)(len >> 8), z[at++] = (u8)~len, z[at++] = (u8)(~len >> 8);
            }
            z[at++] = v, done++;
            a = (a + v) % 65521u, b = (b + a) % 65521u;
        }
    be32(z + at, b << 16 | a), at += 4;
    be32(ihdr, w), be32(ihdr + 4, h);
    ihdr[8] = 8, ihdr[9] = 6, ihdr[10] = ihdr[11] = ihdr[12] = 0;
    if (fopen_s(&f, path, "wb") || !f) {
        free(z);
        return 0;
    }
    fwrite(sig, 1, 8, f);
    chunk(f, "IHDR", ihdr, 13);
    chunk(f, "IDAT", z, (u32)at);
    chunk(f, "IEND", NULL, 0);
    fclose(f);
    free(z);
    return 1;
}

int fcap_png(const char *name, unsigned w, unsigned h, const u8 *rgba, unsigned pitch)
{
    char path[600];
    static unsigned cap;
    if (!cap) {   /* FCAP_PNGS=n (bring-up): more pictures for a frame pictured draw by draw; 200 by default */
        const char *e = getenv("FCAP_PNGS");
        cap = e && atoi(e) > 0 ? (unsigned)atoi(e) : 200;
    }
    if (!g_fcap_on || g_pngs >= cap) return 0;   /* a frame resolving more than 200 times: the rest named in the records only */
    sprintf_s(path, sizeof path, "%s\\%s.png", g_fdir, name);
    if (!fcap_write_png(path, w, h, rgba, pitch)) return 0;
    g_pngs++;
    return 1;
}

unsigned fcap_next(void) { return g_seq; }

int fcap_blob(const char *name, const void *data, size_t n)
{
    char path[600];
    FILE *f = NULL;
    size_t w;
    if (!g_fcap_on) return 0;
    if (g_blobs >= 200) return 0;
    sprintf_s(path, sizeof path, "%s\\%s", g_fdir, name);
    if (fopen_s(&f, path, "wb") || !f) return 0;
    w = fwrite(data, 1, n, f);
    fclose(f);
    g_blobs++;
    return w == n;
}

void fcap_frame_end(u32 frame)
{
    char path[600], line[1024];
    FILE *f = NULL;
    int bmp;
    if (!g_fcap_on) return;   /* armed by the dump after the last frame's draws: what was recorded since is this frame */
    g_fcap_on = 0;
    sprintf_s(path, sizeof path, "%s\\final.bmp", g_fdir);
    bmp = console_capture_bmp(path);
    sprintf_s(path, sizeof path, "%s\\frame.jsonl", g_fdir);
    if (!fopen_s(&f, path, "wb") && f) {
        if (g_n) fwrite(g_buf, 1, g_n, f);
        fclose(f);
    }
    sprintf_s(line, sizeof line, "{\n  \"format\": \"port_frame_capture_v1\",\n  \"frame\": %u,\n  \"records\": %u,\n  \"pictures\": %u,\n  \"truncated\": %s,\n"
              "  \"final\": \"%s\",\n  \"records_file\": \"frame.jsonl\",\n  \"read_with\": \"python port/tools/frame_capture_view.py <this folder>\"\n}\n",
              frame, g_seq, g_pngs, g_truncated ? "true" : "false", bmp ? "final.bmp" : "");
    sprintf_s(path, sizeof path, "%s\\summary.json", g_fdir);
    f = NULL;
    if (!fopen_s(&f, path, "wb") && f) {
        fwrite(line, 1, strlen(line), f);
        fclose(f);
    }
    port_log("scene: frame %u captured draw by draw into %s (%u records, %u pictures%s)\n", frame, g_fdir, g_seq, g_pngs, g_truncated ? ", cut short" : "");
    free(g_buf), g_buf = NULL, g_n = g_cap = 0;
}
