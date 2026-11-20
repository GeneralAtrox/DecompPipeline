/* Too Human's own XMA driver (xb_game.h, XB_XMA_WINDOW) drives the console's XMA decoder through registers, which the kit keeps as plain memory at
 * 0x7FEA0000. This is a decoder behind them: XMACreateContext hands out contexts from an array whose physical address is in register 0x1800, as on the
 * console, and a host thread consumes each context's input buffers and fills its output ring, a frame (512 samples) for each frame the packets hold: decoded
 * through xma_decoder.dll (decoded(), below), mono or stereo, or silence without the library (or, for a stereo voice, with a build too old for one). The game's voices then play out and end as they would. XB_XMA_SILENT=1 leaves every voice silent; XB_XMA_NONE=1 refuses every context.
 *
 * Nearest existing: xb_xma_dll.c, which decodes whole XACT waves for Mega Man 10; Too Human feeds the hardware's contexts itself, packet by packet.
 *
 * A context is 64 bytes, big-endian words (the layout Xenia documents, which the driver's set-up at 82a06534 matches: index = (physical - array) / 64):
 *   +0  input buffer 0 packets (bits 0..11), buffer 0 valid (20), buffer 1 valid (21), output blocks (22..26), output write offset (27..31)
 *   +4  input buffer 1 packets (0..11), sample rate (27..28), stereo (29), output valid (31)
 *   +8  input read offset in bits (0..25)          +16 current input buffer (31)
 *   +20 input buffer 0, +24 input buffer 1, +28 output buffer: physical addresses
 *   +36 output read offset (0..4)
 * The registers are not watched: two kicks written to one word before a look would leave one, so a context works whenever its output is valid and an
 * input buffer is, as the decoder does for an enabled one. The driver's busy test (82a06940 reads 0x1818) sees 0, never one of its contexts. */
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "xb_game.h"
#include "xb_xma_dll.h"

#ifdef XB_XMA_WINDOW
#define XMA_REGS 0x7FEA0000u
#define XMA_CONTEXTS 320u
#define PACKET_BYTES 2048u
#define BLOCK_BYTES 256u

static u32 s_array;                       /* guest address of context 0 */
static volatile LONG s_used[XMA_CONTEXTS];
static struct { u32 off, frames_left; } s_at[XMA_CONTEXTS];
static volatile LONG64 s_frames, s_buffers;
static volatile LONG s_kinds[2][4];       /* voices that made a frame, by stereo and sample rate (24, 32, 44.1, 48 kHz), for the report */
static volatile LONG s_counted[XMA_CONTEXTS];

static u32 guest_of(u32 phys) { return phys + 0x20000000u; }   /* MmGetPhysicalAddress (import 190) is the address less the base */

/* A word of the context written back only if the driver has not written it since it was read: the driver's own change wins, and the next look sees it */
static int put(u32 at, u32 was, u32 now)
{
    return was == now || (u32)InterlockedCompareExchange((volatile LONG *)(tc_base + at), (LONG)_byteswap_ulong(now), (LONG)_byteswap_ulong(was)) ==
                             _byteswap_ulong(was);
}

/* A voice decoded (xma_decoder.dll's packet-fed functions): the stream's packets are copied in order into a window of a few, across the two input
 * buffers, and each frame comes out as 512 big-endian 16-bit samples a channel, four blocks of the output ring for a mono voice and eight for a stereo one,
 * its two channels' samples taking turns (left first), as the console's decoder writes them (and Xenia's). A packet used up leaves the window; a buffer used up is
 * given back (its valid bit cleared) and the other one is current. The read offset written back is the next frame's bit in the current buffer; any other value
 * there next time means the driver set it (a new voice), and the voice starts again. */
#define WINDOW 4u
typedef struct {
    void *dec;                            /* the decoder's stream */
    u32 n, pos;                           /* packets in the window, and the next frame's bit in it */
    u32 buf, pkt, addr;                   /* the current input buffer, its packet that window packet 0 is, and its address */
    u32 ch;                               /* channels: 1, or 2 for a stereo voice */
    u32 off;                              /* the read offset last written */
    int sync;                             /* the next frame starts at window packet 0's own first frame */
    u8 win[WINDOW * PACKET_BYTES];
} Voice;
static Voice *s_voice;                    /* NULL with XB_XMA_SILENT: every voice silent, as before the decoder */
static volatile LONG64 s_decoded, s_bad;

static int decoded(u32 i, u32 c, u32 d0, u32 d1, u32 d2, u32 d4)
{
    static const u32 rates[4] = {24000u, 32000u, 44100u, 48000u};
    Voice *v = &s_voice[i];
    u32 n0 = d0, n2 = d2, n4 = d4, blocks = d0 >> 22 & 31u, out = guest_of(rd32(c + 28u)), cur = d4 >> 31, ch = (d1 >> 29 & 1u) + 1u, need = 4u * ch;
    float pcm[512 * 2];
    if (!(n0 >> (20u + cur) & 1u)) {
        if (!(n0 >> (21u - cur) & 1u)) return 1;                  /* neither buffer: the voice waits for more */
        n4 ^= 1u << 31, cur ^= 1u, n2 = (n2 & ~0x3ffffffu) | 32u;
    }
    if (v->off != (n2 & 0x3ffffffu) || v->buf != cur || v->addr != rd32(c + (cur ? 24u : 20u)) || v->ch != ch) {
        u32 off = n2 & 0x3ffffffu;
        void *dec = xb_xma_packets_open(v->dec, rates[d1 >> 27 & 3u], ch);
        if (!dec) return 0;
        v->dec = dec, v->ch = ch, v->n = 0, v->buf = cur, v->addr = rd32(c + (cur ? 24u : 20u)), v->pkt = off / (PACKET_BYTES * 8u), v->pos = off % (PACKET_BYTES * 8u);
        v->sync = v->pos <= 32u;
    }
    for (unsigned steps = 0; steps < 16u; steps++) {
        u32 count = cur ? d1 & 0xfffu : d0 & 0xfffu, other = cur ^ 1u, wr, rd, r, final;
        while (v->n < WINDOW) {                                   /* the window filled from this buffer, then the other one if it is valid */
            u32 k = v->pkt + v->n, from;
            if (k < count) from = guest_of(v->addr) + k * PACKET_BYTES;
            else if (n0 >> (20u + other) & 1u && k - count < (other ? d1 & 0xfffu : d0 & 0xfffu)) from = guest_of(rd32(c + (other ? 24u : 20u))) + (k - count) * PACKET_BYTES;
            else break;
            memcpy(v->win + v->n * PACKET_BYTES, tc_base + from, PACKET_BYTES);
            v->n++;
        }
        if (!v->n) break;
        if (v->sync) {
            u32 first = (u32)v->win[0] << 24 | (u32)v->win[1] << 16 | (u32)v->win[2] << 8 | v->win[3];
            first = first >> 11 & 0x7fffu;
            v->sync = 0;
            v->pos = first == 0x7fffu ? PACKET_BYTES * 8u : 32u + first;   /* no frame starts in it: the next packet */
        }
        final = v->n < WINDOW && !(n0 >> (20u + other) & 1u);          /* no more packets until the driver gives some */
        wr = n0 >> 27, rd = rd32(c + 36u) & 31u;
        if (v->pos < PACKET_BYTES * 8u) {
            if ((rd + blocks - wr - 1u) % blocks < need) break;        /* the game's mixer has not read the ring yet */
            r = (u32)xb_xma_packets_frame(v->dec, v->win, v->n * PACKET_BYTES * 8u, &v->pos, (int)final, pcm);
            if (r == 0u) break;
            if (r == 1u) {
                for (u32 k = 0; k < 512u * ch; k++) {                  /* k: the k-th sample written, channel k % ch of sample k / ch */
                    float f = pcm[k % ch * 512u + k / ch];
                    s32 q = f >= 32767.0f ? 32767 : f <= -32768.0f ? -32768 : (s32)f;
                    u8 *o = tc_base + out + (wr + k / 128u) % blocks * BLOCK_BYTES + k % 128u * 2u;
                    o[0] = (u8)((u32)q >> 8), o[1] = (u8)q;
                }
                n0 = (n0 & 0x07ffffffu) | ((wr + need) % blocks << 27);
                InterlockedIncrement64(&s_decoded);
                if (!InterlockedExchange(&s_counted[i], 1)) InterlockedIncrement(&s_kinds[ch - 1u][d1 >> 27 & 3u]);
            } else {                                                   /* ended (2), or a frame that cannot be read (-1): the packets are used up */
                if (r != 2u) InterlockedIncrement64(&s_bad), v->sync = 1, v->pos = PACKET_BYTES * 8u;
                else v->pos = v->n * PACKET_BYTES * 8u;
            }
        }
        while (v->pos >= PACKET_BYTES * 8u && v->n) {                  /* packets used up leave the window; a buffer used up is given back */
            memmove(v->win, v->win + PACKET_BYTES, (v->n - 1u) * PACKET_BYTES);
            v->n--, v->pos -= PACKET_BYTES * 8u, v->pkt++;
            if (v->pkt >= count) {
                n0 &= ~(1u << (20u + cur)), n4 ^= 1u << 31, cur ^= 1u;
                v->pkt = 0, v->buf = cur, v->addr = rd32(c + (cur ? 24u : 20u)), count = cur ? d1 & 0xfffu : d0 & 0xfffu;
                InterlockedIncrement64(&s_buffers);
                if (!(n0 >> (20u + cur) & 1u)) v->n = 0;                /* nothing after it: the next valid buffer starts afresh */
            }
        }
    }
    v->off = v->pkt * PACKET_BYTES * 8u + (v->pos < PACKET_BYTES * 8u ? v->pos : 0u);
    n2 = (n2 & ~0x3ffffffu) | v->off;
    if (put(c, d0, n0)) (void)(put(c + 8u, d2, n2) && put(c + 16u, d4, n4));
    else v->off = ~0u;                                                 /* the driver wrote the context meanwhile: start again from what it holds */
    return 1;
}

/* One context: as many frames as there are packets for and room in the output ring, one block always left free (equal offsets are an empty ring) */
static void work(u32 i)
{
    u32 c = s_array + i * 64u, d0 = rd32(c), d1 = rd32(c + 4u), d2 = rd32(c + 8u), d4 = rd32(c + 16u), n0 = d0, n2 = d2, n4 = d4;
    u32 blocks = d0 >> 22 & 31u, need = d1 >> 29 & 1u ? 8u : 4u, out = guest_of(rd32(c + 28u));
    unsigned steps;
    if (!(d1 >> 31) || !blocks) return;
    if (s_voice && decoded(i, c, d0, d1, d2, d4)) return;
    for (steps = 0; steps < 64u; steps++) {
        u32 cur = n4 >> 31, valid = n0 >> (20u + cur) & 1u, packets = cur ? d1 & 0xfffu : n0 & 0xfffu;
        u32 buf = guest_of(rd32(c + (cur ? 24u : 20u))), off = n2 & 0x3ffffffu, packet, wr, rd, room;
        if (!valid) {
            if (!(n0 >> (21u - cur) & 1u)) break;                 /* neither buffer: the voice waits for more */
            n4 ^= 1u << 31, n2 = (n2 & ~0x3ffffffu) | 32u;
            continue;
        }
        if (off < 32u) off = 32u;
        packet = off / (PACKET_BYTES * 8u);
        if (packet >= packets) {                                  /* the buffer is used up: given back, and the other one next */
            n0 &= ~(1u << (20u + cur)), n4 ^= 1u << 31, n2 = (n2 & ~0x3ffffffu) | 32u;
            InterlockedIncrement64(&s_buffers);
            continue;
        }
        if (s_at[i].off != off) s_at[i].off = off, s_at[i].frames_left = rd32(buf + packet * PACKET_BYTES) >> 26;   /* the frames that start in it */
        if (!s_at[i].frames_left) {
            n2 = (n2 & ~0x3ffffffu) | ((packet + 1u) * PACKET_BYTES * 8u + 32u);
            continue;
        }
        wr = n0 >> 27, rd = rd32(c + 36u) & 31u;
        room = (rd + blocks - wr - 1u) % blocks;
        if (room < need) break;                                   /* the game's mixer has not read the ring yet */
        for (u32 b = 0; b < need; b++) memset(tc_base + out + (wr + b) % blocks * BLOCK_BYTES, 0, BLOCK_BYTES);
        n0 = (n0 & 0x07ffffffu) | ((wr + need) % blocks << 27);
        s_at[i].frames_left--;
        if (!InterlockedExchange(&s_counted[i], 1)) InterlockedIncrement(&s_kinds[d1 >> 29 & 1u][d1 >> 27 & 3u]);
        InterlockedIncrement64(&s_frames);
    }
    if (put(c, d0, n0)) (void)(put(c + 8u, d2, n2) && put(c + 16u, d4, n4));
}

static DWORD WINAPI decoder(LPVOID p)
{
    (void)p;
    for (;;) {
        for (u32 i = 0; i < XMA_CONTEXTS; i++)
            if (s_used[i]) work(i);
        Sleep(1);
    }
}

/* XMACreateContext(u32 *context out): 0, or E_OUTOFMEMORY (0x8007000E) with every context in use */
u32 xb_xma_create(u32 out_p)
{
    static SRWLOCK lock = SRWLOCK_INIT;
    u32 i;
    if (getenv("XB_XMA_NONE")) return 0x8007000Eu;   /* E_OUTOFMEMORY, the kernel's answer with every context in use */
    AcquireSRWLockExclusive(&lock);
    if (!s_array) {
        s_array = xb_alloc_block(XMA_CONTEXTS * 64u, 0x1000u);
        *(u32 *)(tc_base + XMA_REGS + 0x1800u) = s_array & 0x1fffffffu;   /* little-endian, as the driver reads it (lwbrx at 829fdbdc) */
        if (!getenv("XB_XMA_SILENT") && (s_voice = calloc(XMA_CONTEXTS, sizeof *s_voice)) != NULL)
            for (u32 k = 0; k < XMA_CONTEXTS; k++) s_voice[k].off = ~0u;
        CreateThread(NULL, 1u << 20, decoder, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
        port_log("xma: %u contexts at %08x (register 0x1800 = %08x): %s\n", XMA_CONTEXTS, s_array, s_array & 0x1fffffffu,
                 s_voice ? "voices decoded when xma_decoder.dll can (stereo ones need its xma_packets_open_channels)" : "every voice silent (XB_XMA_SILENT)");
    }
    for (i = 0; i < XMA_CONTEXTS && s_used[i]; i++) {}
    if (i < XMA_CONTEXTS) {
        memset(tc_base + s_array + i * 64u, 0, 64u);
        s_at[i].off = ~0u;
        InterlockedExchange(&s_counted[i], 0);
        if (s_voice) s_voice[i].off = ~0u;
        InterlockedExchange(&s_used[i], 1);
        wr32(out_p, s_array + i * 64u);
    }
    ReleaseSRWLockExclusive(&lock);
    return i < XMA_CONTEXTS ? 0u : 0x8007000Eu;
}

/* XMAReleaseContext(context) */
void xb_xma_release(u32 context)
{
    u32 i = (context - s_array) / 64u;
    if (s_array && context >= s_array && i < XMA_CONTEXTS) InterlockedExchange(&s_used[i], 0);
}

/* For the run's last lines */
void xb_xma_report(void)
{
    if (!s_array) return;
    port_log("xma: %lld frames decoded, %lld silent frames made, %lld input buffers given back, %lld frames that could not be read\n", (long long)s_decoded,
             (long long)s_frames, (long long)s_buffers, (long long)s_bad);
    port_log("xma: voices by kind (24/32/44.1/48 kHz): mono %ld/%ld/%ld/%ld, stereo %ld/%ld/%ld/%ld\n", s_kinds[0][0], s_kinds[0][1], s_kinds[0][2],
             s_kinds[0][3], s_kinds[1][0], s_kinds[1][1], s_kinds[1][2], s_kinds[1][3]);
}
#endif
