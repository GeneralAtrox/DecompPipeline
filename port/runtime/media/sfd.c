/* A Sofdec movie (.sfd) is an MPEG-1 system stream (ISO/IEC 11172-1): packs, a system header, and packets, each a
 * start code, a 16-bit length and, for the elementary streams, stuffing, an optional buffer size and time stamps
 * before the payload. Stream 0xe0 is the MPEG-1 video, 0xc0 the sound (ADX, not MPEG audio); padding and the private
 * streams are passed over. The demuxer takes the file in pieces: whole packets are parsed and their payloads appended
 * to the video and sound buffers; a packet cut by a piece's end waits for the next piece. The video buffer is then
 * cut into units for the decoder: a sequence header, or a picture through its last slice (complete once the next
 * non-slice start code has arrived, or at the end of the file). */
#include <stdlib.h>
#include <string.h>
#include "sfd.h"

static int grow(u8 **buf, u32 *cap, u32 need)
{
    if (need + MPV_PAD <= *cap) return 1;
    u32 c = *cap ? *cap : 65536u;
    while (c < need + MPV_PAD) c *= 2;
    u8 *b = realloc(*buf, c);
    if (!b) return 0;
    *buf = b;
    *cap = c;
    return 1;
}

static void append(u8 **buf, u32 *len, u32 *cap, const u8 *p, u32 n)
{
    if (!grow(buf, cap, *len + n)) return;
    memcpy(*buf + *len, p, n);
    *len += n;
    memset(*buf + *len, 0, MPV_PAD);
}

void sfd_free(SfdDemux *d)
{
    free(d->video);
    free(d->audio);
    memset(d, 0, sizeof *d);
}

u32 sfd_parse(SfdDemux *d, const u8 *p, u32 n, int eof)
{
    u32 i = 0;
    while (i + 4 <= n) {
        u32 code, len, h;
        if (p[i] || p[i + 1] || p[i + 2] != 1) { /* not at a start code: find the next */
            i++;
            continue;
        }
        code = p[i + 3];
        if (code == 0xb9) { /* the end of the stream */
            i += 4;
            d->ended = 1;
            continue;
        }
        if (code == 0xba) { /* a pack: MPEG-1's 12 bytes, or MPEG-2's 14 plus stuffing */
            if (i + 14 > n) break;
            len = (p[i + 4] & 0xc0u) == 0x40u ? 14u + (p[i + 13] & 7u) : 12u;
            if (i + len > n) break;
            i += len;
            continue;
        }
        if (code < 0xbb) { /* a video start code in the system layer: resync */
            i++;
            continue;
        }
        if (i + 6 > n) break;
        len = 6u + ((u32)p[i + 4] << 8 | p[i + 5]);
        if (i + len > n) break;
        h = i + 6;
        if (code >= 0xc0 && code <= 0xef) { /* an elementary stream: its packet header */
            while (h < i + len && p[h] == 0xff) h++;
            if (h < i + len && (p[h] & 0xc0u) == 0x40u) h += 2;
            if (h < i + len && (p[h] & 0xf0u) == 0x20u) h += 5;
            else if (h < i + len && (p[h] & 0xf0u) == 0x30u) h += 10;
            else if (h < i + len && p[h] == 0x0f) h += 1;
            else if (h < i + len && (p[h] & 0xc0u) == 0x80u) h += 3u + p[h + 2]; /* MPEG-2's packet header */
            if (h < i + len) {
                if (code == SFD_VIDEO) append(&d->video, &d->vlen, &d->vcap, p + h, i + len - h);
                else if (code == SFD_AUDIO) append(&d->audio, &d->alen, &d->acap, p + h, i + len - h);
            }
        }
        i += len;
    }
    if (eof) d->ended = 1, i = n;
    return i;
}

/* the next start code at or after i in the video buffer: its offset, or vlen */
static u32 next_start(const SfdDemux *d, u32 i)
{
    const u8 *v = d->video;
    for (; i + 3 < d->vlen; i++)
        if (v[i] == 0 && v[i + 1] == 0 && v[i + 2] == 1) return i;
    return d->vlen;
}

int sfd_unit(SfdDemux *d, u32 *start, u32 *size)
{
    u32 a = next_start(d, d->vpos), e, code;
    if (a >= d->vlen) return 0;
    code = d->video[a + 3];
    e = next_start(d, a + 4);
    if (code == 0x00) /* a picture: through its slices */
        while (e < d->vlen && d->video[e + 3] >= 0x01 && d->video[e + 3] <= 0xaf) e = next_start(d, e + 4);
    if (e >= d->vlen && !d->ended) return 0; /* may run on into bytes not here yet */
    *start = a;
    *size = e - a;
    d->vpos = e;
    return 1;
}

void sfd_compact(SfdDemux *d)
{
    if (d->vpos < 65536u) return;
    memmove(d->video, d->video + d->vpos, d->vlen - d->vpos + MPV_PAD);
    d->vlen -= d->vpos;
    d->vpos = 0;
}
