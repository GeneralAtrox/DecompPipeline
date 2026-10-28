/* MPEG-1 video (ISO/IEC 11172-2), the kit's own decoder: its tables (mpv_tables.c), the inverse DCT (mpv_idct.c),
 * the sequence header and pictures (mpv_pic.c) and macroblocks (mpv_mb.c). A picture's bytes run from its picture
 * start code through its last slice, followed by at least MPV_PAD readable bytes (the bit reader looks ahead). */
#ifndef MPV_H
#define MPV_H

#include "rd_rt.h"

#define MPV_PAD 8u

/* a code table entry: the value, and the code's length in bits (0: no such code) */
typedef struct MpvVlc {
    s16 value;
    u8 len;
} MpvVlc;

#define MPV_MB_QUANT 1
#define MPV_MB_FWD 2
#define MPV_MB_BWD 4
#define MPV_MB_PATTERN 8
#define MPV_MB_INTRA 16
#define MPV_MBAI_STUFFING 34
#define MPV_MBAI_ESCAPE 35
#define MPV_DCT_EOB (-1)
#define MPV_DCT_ESCAPE (-2)

extern MpvVlc g_mpv_mbai[1u << 11], g_mpv_mb_i[1u << 2], g_mpv_mb_p[1u << 6], g_mpv_mb_b[1u << 6],
    g_mpv_cbp[1u << 9], g_mpv_motion[1u << 10], g_mpv_dc_y[1u << 7], g_mpv_dc_c[1u << 8], g_mpv_dct[1u << 16];
extern const u8 kMpvZigzag[64], kMpvIntraQ[64];
void mpv_tables_init(void);

/* the bit reader: big-endian, reads past the end as the padding's zeros */
typedef struct MpvBits {
    const u8 *buf;
    u32 pos;  /* bits read */
    u32 size; /* bytes */
} MpvBits;

static __inline u32 mpv_peek(const MpvBits *b, u32 n) /* n 1..32 */
{
    const u8 *q = b->buf + (b->pos >> 3);
    u64 w = (u64)q[0] << 32 | (u64)q[1] << 24 | (u64)q[2] << 16 | (u64)q[3] << 8 | q[4];
    return (u32)((w << (b->pos & 7u)) >> (40u - n)) & (u32)((1ull << n) - 1u);
}
static __inline void mpv_skip(MpvBits *b, u32 n) { b->pos += n; }
static __inline u32 mpv_get(MpvBits *b, u32 n)
{
    u32 v = mpv_peek(b, n);
    b->pos += n;
    return v;
}
static __inline int mpv_over(const MpvBits *b) { return b->pos > 8u * b->size; }
/* a code from table t (2^bits entries): its value, or -32768 for no code */
static __inline s32 mpv_vlc(MpvBits *b, const MpvVlc *t, u32 bits)
{
    MpvVlc e = t[mpv_peek(b, bits)];
    if (!e.len) return -32768;
    b->pos += e.len;
    return e.value;
}

/* mpv_idct.c: the 8x8 inverse DCT of block (row-major, in place), each output rounded to the nearest integer and
 * clipped to -256..255 (the standard's accuracy rule, IEEE 1180) */
void mpv_idct(s16 block[64]);
void mpv_idct_init(void);

typedef struct MpvFrame {
    u8 *y, *cb, *cr; /* stride mb_w * 16 and mb_w * 8 */
} MpvFrame;

typedef struct Mpv {
    u32 width, height, mb_w, mb_h;
    u32 rate_num, rate_den;  /* pictures a second, as a fraction (0 0: unknown) */
    u8 intra_q[64], inter_q[64]; /* row-major */
    u8 *mem;
    MpvFrame frame[3];
    int fwd, bwd;            /* the reference frames (-1: none); bwd is the later one */
    int flushed;             /* mpv_flush has shown bwd */
    MpvFrame *pf, *pb;       /* the picture's forward and backward prediction frames */
    /* the picture */
    u32 type;                /* 1 I, 2 P, 3 B */
    u32 full_f, f_code_f, full_b, f_code_b;
    /* the slice */
    u32 qscale;
    s32 dc[3];
    s32 mv_f[2], mv_b[2];    /* the motion vector predictors, half (or whole) pels */
    u32 last_type;           /* the previous macroblock's type, for B skips */
    u32 pictures, errors;
    s16 block[64];
} Mpv;

/* mpv_pic.c */
int mpv_sequence(Mpv *m, const u8 *p, u32 n); /* a sequence header (from its start code); 0 if not one we decode */
void mpv_free(Mpv *m);
/* A picture (from its start code): the frame to show now, in display order (NULL: none yet, or an error). */
const MpvFrame *mpv_decode(Mpv *m, const u8 *p, u32 n);
const MpvFrame *mpv_flush(Mpv *m); /* at the end of the stream: the last reference frame, once */
/* mpv_mb.c: one slice's macroblocks, b just past its quantiser scale and extra bits; cur the frame decoded into */
int mpv_slice(Mpv *m, MpvBits *b, u32 row, MpvFrame *cur);

#endif
