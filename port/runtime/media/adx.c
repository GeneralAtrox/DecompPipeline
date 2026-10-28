/* CRI ADX ADPCM, from the format's public descriptions (CLEANROOM.md). A file is a big-endian header, then frames
 * of 18 bytes per channel, interleaved: a 16-bit scale and 32 signed 4-bit codes, high nibble first. Each sample is
 * code * scale plus a second-order prediction from the channel's last two samples, with coefficients the header's
 * cut-off frequency gives; samples clip to 16 bits. A frame whose scale has the top bit set ends the stream.
 *
 * The header (offsets in bytes): 0 0x8000; 2 the copyright string's offset, the data starting 4 past it; 4 the type
 * (3: the coefficients from the cut-off); 5 the frame size (18); 6 bits a code (4); 7 channels; 8 the sample rate;
 * 12 the samples per channel; 16 the cut-off frequency; 18 the version (3 or 4); 19 flags (encryption: not played).
 * The loop, if any: version 3 at 24 (enabled, start sample, start byte, end sample, end byte), version 4 16 bytes
 * later. */
#include <math.h>
#include <string.h>
#include "media.h"

static u32 be16(const u8 *p) { return (u32)p[0] << 8 | p[1]; }
static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }

void adx_coefs(u32 cutoff, u32 rate, s32 *c1, s32 *c2)
{
    double z = cos(2.0 * 3.14159265358979323846 * cutoff / rate);
    double a = sqrt(2.0) - z, b = sqrt(2.0) - 1.0;
    double c = (a - sqrt((a + b) * (a - b))) / b;
    *c1 = (s32)floor(c * 8192.0 + 0.5); /* to the nearest */
    *c2 = (s32)floor(c * c * -4096.0 + 0.5);
}

int adx_parse(const u8 *p, u32 n, AdxInfo *h)
{
    u32 co, ver, lp;
    memset(h, 0, sizeof *h);
    if (n < 0x20 || be16(p) != 0x8000u) return 0;
    co = be16(p + 2);
    if (co < 0x1c || co + 4 > n || memcmp(p + co - 2, "(c)CRI", 6) != 0) return 0;
    if (p[4] != 3 || p[5] != 18 || p[6] != 4 || p[7] < 1 || p[7] > 2 || p[0x13] != 0) return 0;
    h->data = co + 4;
    h->channels = p[7];
    h->rate = be32(p + 8);
    h->samples = be32(p + 12);
    if (!h->rate) return 0;
    adx_coefs(be16(p + 16), h->rate, &h->c1, &h->c2);
    ver = p[0x12];
    lp = ver == 4 ? 0x28 : ver == 3 ? 0x18 : 0;
    if (lp && co >= lp + 16 && be32(p + lp) && be32(p + lp + 4) < be32(p + lp + 12) &&
        be32(p + lp + 12) <= h->samples) {
        h->loop = 1;
        h->loop_start = be32(p + lp + 4);
        h->loop_end = be32(p + lp + 12);
    }
    return 1;
}

void adx_frame(const u8 *in, s16 out[32], s32 hist[2], s32 c1, s32 c2)
{
    s32 scale = (s32)be16(in), h1 = hist[0], h2 = hist[1];
    for (u32 i = 0; i < 32u; i++) {
        s32 code = (in[2 + i / 2] >> (i & 1u ? 0 : 4)) & 15;
        s32 s = (code ^ 8) - 8;
        s = s * scale + ((c1 * h1 + c2 * h2) >> 12);
        s = s > 32767 ? 32767 : s < -32768 ? -32768 : s;
        out[i] = (s16)s;
        h2 = h1;
        h1 = s;
    }
    hist[0] = h1;
    hist[1] = h2;
}

void adx_open(AdxStream *s, const u8 *buf, u32 size, u32 done)
{
    AdxInfo h = s->h;
    memset(s, 0, sizeof *s);
    s->h = h;
    s->buf = buf;
    s->size = size;
    s->done = done;
    s->loops = 1;
}

/* Decodes frame f into pcm: 0 if its bytes have not arrived or it is the end marker. Frames decode in order; a jump
 * back (a loop) keeps the history, as the stream itself continues from the loop's end. */
static int decode(AdxStream *s, u32 f)
{
    u32 bytes = 18u * s->h.channels, at = s->h.data + f * bytes;
    if (s->decoded == f + 1) return 1;
    if (at + bytes > s->size || (s->buf[at] & 0x80u)) return 0;
    for (u32 c = 0; c < s->h.channels; c++) adx_frame(s->buf + at + 18u * c, s->pcm[c], s->hist[c], s->h.c1, s->h.c2);
    s->decoded = f + 1;
    return 1;
}

u32 adx_read(AdxStream *s, s16 *out, u32 n)
{
    u32 got = 0;
    while (got < n) {
        u32 end = s->loops && s->h.loop ? s->h.loop_end : s->h.samples;
        if (s->pos >= end) {
            if (!(s->loops && s->h.loop)) break;
            s->pos = s->h.loop_start;
            s->loop_count++;
        }
        if (!decode(s, s->pos / 32u)) break;
        u32 i = s->pos % 32u, take = 32u - i;
        if (take > end - s->pos) take = end - s->pos;
        if (take > n - got) take = n - got;
        for (u32 k = 0; k < take; k++) {
            out[2 * (got + k)] = s->pcm[0][i + k];
            out[2 * (got + k) + 1] = s->pcm[s->h.channels - 1][i + k];
        }
        got += take;
        s->pos += take;
    }
    return got;
}

int adx_ended(const AdxStream *s)
{
    if (s->loops && s->h.loop) return 0;
    if (s->pos >= s->h.samples) return 1;
    /* the end marker, or the last whole frame, before the header's count */
    u32 bytes = 18u * s->h.channels, at = s->h.data + (s->pos / 32u) * bytes;
    return s->done && (at + bytes > s->size || (s->buf[at] & 0x80u));
}
