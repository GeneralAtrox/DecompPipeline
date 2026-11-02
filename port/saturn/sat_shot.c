/* A frame as a PNG (stored deflate blocks, no compression): the picture the port shows, for logs, goldens and bug reports.
 *
 * Nearest existing: runtime/gfx/gfx_capture.c - writes the D3D frame target through WIC; this takes a plain RGB buffer
 * and needs nothing but the C library, so the headless Saturn port can dump a frame. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_vdp.h"

static u32 g_crc[256];

static void crc_init(void)
{
    for (u32 n = 0; n < 256; n++) {
        u32 c = n;
        for (int k = 0; k < 8; k++) c = (c & 1u) ? 0xedb88320u ^ (c >> 1) : c >> 1;
        g_crc[n] = c;
    }
}

static u32 crc(u32 c, const u8 *p, size_t n)
{
    for (size_t i = 0; i < n; i++) c = g_crc[(c ^ p[i]) & 255u] ^ (c >> 8);
    return c;
}

static void be32(u8 *p, u32 v)
{
    p[0] = (u8)(v >> 24);
    p[1] = (u8)(v >> 16);
    p[2] = (u8)(v >> 8);
    p[3] = (u8)v;
}

static void chunk(FILE *f, const char *type, const u8 *data, u32 n)
{
    u8 head[8], tail[4];
    u32 c;
    be32(head, n);
    memcpy(head + 4, type, 4);
    fwrite(head, 1, 8, f);
    if (n) fwrite(data, 1, n, f);
    c = crc(0xffffffffu, (const u8 *)type, 4);
    if (n) c = crc(c, data, n);
    be32(tail, c ^ 0xffffffffu);
    fwrite(tail, 1, 4, f);
}

int sat_png_write(const char *path, const u32 *rgb, u32 w, u32 h)
{
    FILE *f = fopen(path, "wb");
    size_t rowbytes = (size_t)w * 3u + 1u, raw = rowbytes * h, nblocks = (raw + 65534u) / 65535u, zn = 2u + raw + nblocks * 5u + 4u, zi = 0, ri = 0;
    u8 *rawbuf, *z, ihdr[13];
    u32 a = 1, b = 0;
    if (!f) return 0;
    crc_init();
    rawbuf = (u8 *)malloc(raw);
    z = (u8 *)malloc(zn);
    for (u32 y = 0; y < h; y++) {
        rawbuf[ri++] = 0;
        for (u32 x = 0; x < w; x++) {
            u32 c = rgb[y * w + x];
            rawbuf[ri++] = (u8)(c >> 16);
            rawbuf[ri++] = (u8)(c >> 8);
            rawbuf[ri++] = (u8)c;
        }
    }
    z[zi++] = 0x78;
    z[zi++] = 0x01;
    for (size_t o = 0; o < raw; o += 65535u) {
        size_t n = raw - o < 65535u ? raw - o : 65535u;
        z[zi++] = o + n >= raw ? 1 : 0;
        z[zi++] = (u8)n;
        z[zi++] = (u8)(n >> 8);
        z[zi++] = (u8)~n;
        z[zi++] = (u8)(~n >> 8);
        memcpy(z + zi, rawbuf + o, n);
        zi += n;
    }
    for (size_t i = 0; i < raw; i++) {
        a = (a + rawbuf[i]) % 65521u;
        b = (b + a) % 65521u;
    }
    be32(z + zi, (b << 16) | a);
    zi += 4;
    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
    be32(ihdr, w);
    be32(ihdr + 4, h);
    ihdr[8] = 8;
    ihdr[9] = 2;
    ihdr[10] = ihdr[11] = ihdr[12] = 0;
    chunk(f, "IHDR", ihdr, 13);
    chunk(f, "IDAT", z, (u32)zi);
    chunk(f, "IEND", NULL, 0);
    fclose(f);
    free(rawbuf);
    free(z);
    return 1;
}
