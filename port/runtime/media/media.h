/* The kit's media decoders, written from the published format descriptions (see CLEANROOM.md): CRI's ADX ADPCM
 * (streamed music and movie sound), the MPEG-1 system stream that holds a Sofdec movie (.sfd), and MPEG-1 video
 * (ISO/IEC 11172-2). They know no game: the sdk modules (sdk/adx_mw.c) feed them a game's files and take their sound
 * (media_mix, mixed beside the AICA's voices by gfx/gfx_audio.c) and pictures (movie.c, drawn by gfx/gfx_movie.c). */
#ifndef MEDIA_H
#define MEDIA_H

#include "rd_rt.h"

/* adx.c: a whole ADX stream in memory (a file, or what has arrived of a movie's sound so far) */
typedef struct AdxInfo {
    u32 data;        /* the first frame's byte offset */
    u32 channels;    /* 1 or 2 */
    u32 rate;        /* samples a second */
    u32 samples;     /* per channel */
    u32 loop;        /* 1: the header loops loop_start..loop_end (samples) */
    u32 loop_start, loop_end;
    s32 c1, c2;      /* the prediction coefficients, 4.12 fixed point */
} AdxInfo;

typedef struct AdxStream {
    AdxInfo h;
    const u8 *buf;   /* the file's bytes from offset 0 */
    u32 size;        /* bytes there so far */
    u32 done;        /* 1: size is the whole stream (else more may come) */
    u32 pos;         /* the next sample to hand out */
    u32 loops;       /* 1: loop as the header says (ADXT's loop flag) */
    u32 loop_count;  /* loops taken */
    u32 decoded;     /* the frame whose samples pcm holds, +1 (0: none) */
    s32 hist[2][2];  /* per channel: the last two samples */
    s16 pcm[2][32];
} AdxStream;

#define ADX_FRAME_SAMPLES 32u
int adx_parse(const u8 *p, u32 n, AdxInfo *h); /* 1 if p starts an ADX header this decoder plays */
void adx_coefs(u32 cutoff, u32 rate, s32 *c1, s32 *c2);
void adx_frame(const u8 *in, s16 out[32], s32 hist[2], s32 c1, s32 c2); /* one channel's 18-byte frame */
void adx_open(AdxStream *s, const u8 *buf, u32 size, u32 done); /* after adx_parse(buf) filled s->h */
/* Up to n interleaved stereo frames (mono doubled) from the position; fewer at the end or where the bytes have not
 * arrived yet. */
u32 adx_read(AdxStream *s, s16 *out, u32 n);
int adx_ended(const AdxStream *s); /* every sample handed out (a looping stream never ends) */

/* media_mix.c: sound sources mixed into the kit's output at its 44,100 Hz, after the AICA's voices */
typedef u32 (*MediaPullFn)(void *ctx, s16 *stereo, u32 frames); /* frames given (the rest is silence) */
void media_add(MediaPullFn fn, void *ctx); /* once per ctx; media_remove(ctx) takes it out */
void media_remove(void *ctx);
void media_mix(s16 *stereo, u32 frames);   /* adds each source, saturating */
s32 media_gain(s32 centibels);             /* ADX/Sofdec volume (0.1 dB steps, 0 full, -960 silent) as 1.15 */

/* A talk: one ADX file played into the mix, as CRI's ADXT plays it. Its states are ADXT's numbers. */
#define MEDIA_TALK_STOP 0
#define MEDIA_TALK_PLAYING 3
#define MEDIA_TALK_PLAYEND 5
#define MEDIA_TALK_ERROR 6
typedef struct MediaTalk MediaTalk;
MediaTalk *media_talk_new(void);
void media_talk_free(MediaTalk *t);
u8 *media_talk_load(MediaTalk *t, u32 size); /* stops it; the buffer to read the file into (NULL: no memory) */
int media_talk_start(MediaTalk *t, u32 size); /* plays the file loaded; 0 if it is not ADX this decoder plays */
void media_talk_stop(MediaTalk *t);
void media_talk_volume(MediaTalk *t, s32 centibels);
void media_talk_pause(MediaTalk *t, int on);
void media_talk_loop(MediaTalk *t, int on); /* ADXT's loop flag (on by default) */
s32 media_talk_state(const MediaTalk *t);
const AdxInfo *media_talk_info(const MediaTalk *t);
u32 media_talk_pos(const MediaTalk *t);   /* samples played */
u32 media_talk_loops(const MediaTalk *t); /* loops taken */

/* movie.c: a Sofdec movie (read whole into host memory) shown and heard; states as a talk's */
typedef struct MediaMovie MediaMovie;
MediaMovie *media_movie_new(void);
void media_movie_free(MediaMovie *m);
u8 *media_movie_load(MediaMovie *m, u32 size); /* stops it; the buffer to read the file into (NULL: no memory) */
int media_movie_start(MediaMovie *m, u32 size); /* plays the file loaded; 0: none, or no room for another movie */
void media_movie_stop(MediaMovie *m); /* and takes its picture away */
void media_movie_fail(MediaMovie *m); /* a movie that could not start: it has ended */
void media_movie_volume(MediaMovie *m, s32 centibels);
void media_movie_pause(MediaMovie *m, int on);
s32 media_movie_state(const MediaMovie *m);
u32 media_movie_pictures(const MediaMovie *m); /* pictures shown so far */
void media_movie_frame(void); /* the renderer draws a frame: each movie's picture due now */

/* gfx/gfx_movie.c: a movie frame (planes Y, Cb, Cr; chroma at half size) drawn over the picture from now on */
void gfx_movie_show(const u8 *y, const u8 *cb, const u8 *cr, u32 y_stride, u32 c_stride, u32 w, u32 h);
void gfx_movie_hide(void);

#endif
