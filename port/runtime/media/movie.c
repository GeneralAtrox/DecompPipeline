/* A movie played as a middleware player plays it: the file (a Sofdec MPEG-1 system stream, in host memory) is
 * demultiplexed a little ahead of the picture, its ADX sound goes into the kit's mix (media_mix) and the sound's
 * clock picks each picture: at each vertical blank the video is decoded up to the frame whose time the sound has
 * reached, and that frame is drawn over the game's picture (gfx/gfx_movie.c). A movie without sound runs on the
 * vertical blanks (60 a second). The movie ends when its last picture has been shown and its sound has played out. */
#include <stdlib.h>
#include <string.h>
#include "platform.h" /* port_add_vblank_task, port_log */
#include "media.h"
#include "sfd.h"

#define CHUNK (256u * 1024u)    /* file bytes demultiplexed at a time */
#define AHEAD_FRAMES (44100u / 2u) /* sound kept demultiplexed ahead of the clock: half a second */
#define MAX_DECODES 4u          /* pictures decoded in one vertical blank at most (catching up) */

struct MediaMovie {
    u8 *file;         /* the whole file, read in by the caller (media_movie_load) */
    u32 size, fpos;
    SfdDemux d;
    Mpv v;
    int have_seq;
    AdxStream a;
    int have_sound, sound_done;
    u32 played;       /* sound frames handed to the mix */
    u32 vblanks;      /* since the start (the clock without sound) */
    u32 shown;        /* pictures handed out by the decoder */
    int video_done;   /* the last picture has been shown */
    s32 state, gain, paused;
};

static MediaMovie *g_live[4];
static int g_task;

static void demux(MediaMovie *m)
{
    u32 n = m->size - m->fpos > CHUNK ? CHUNK : m->size - m->fpos;
    int eof = m->fpos + n >= m->size;
    if (m->d.ended) return;
    m->fpos += sfd_parse(&m->d, m->file + m->fpos, n, eof);
    if (eof && m->fpos >= m->size) m->d.ended = 1;
    if (!m->have_sound && adx_parse(m->d.audio, m->d.alen, &m->a.h)) {
        adx_open(&m->a, m->d.audio, m->d.alen, 0);
        m->a.loops = 0;
        m->have_sound = 1;
    }
    if (m->have_sound) {
        m->a.buf = m->d.audio; /* sfd_parse may have moved it */
        m->a.size = m->d.alen;
        m->a.done = m->d.ended;
    }
}

static u32 pull(void *ctx, s16 *out, u32 frames)
{
    MediaMovie *m = ctx;
    u32 got;
    if (m->state != MEDIA_TALK_PLAYING || m->paused || !m->have_sound || m->sound_done) return 0;
    got = adx_read(&m->a, out, frames);
    for (u32 k = 0; k < 2u * got; k++) out[k] = (s16)(out[k] * m->gain >> 15);
    m->played += got;
    if (got < frames && m->a.done && adx_ended(&m->a)) m->sound_done = 1;
    return got;
}

static void show(const Mpv *v, const MpvFrame *f)
{
    gfx_movie_show(f->y, f->cb, f->cr, v->mb_w * 16u, v->mb_w * 8u, v->width, v->height);
}

/* the picture the clock has reached: frame index = seconds x frame rate */
static u32 due(const MediaMovie *m)
{
    u64 num = m->v.rate_num ? m->v.rate_num : 30u, den = m->v.rate_den ? m->v.rate_den : 1u;
    if (m->have_sound && !m->sound_done) return (u32)((u64)m->played * num / (den * m->a.h.rate));
    if (m->have_sound) return (u32)-1; /* the sound has played out: the rest of the pictures at once */
    return (u32)((u64)m->vblanks * num / (den * 60u));
}

/* the file demultiplexed far enough ahead for the sound */
static void feed(MediaMovie *m)
{
    m->vblanks++;
    while (!m->d.ended && (!m->have_sound || m->d.alen < m->a.h.data + (m->played + AHEAD_FRAMES) / 32u * 18u *
                                                             m->a.h.channels))
        demux(m);
}

/* the pictures decoded up to the one due now, the last of them shown; and the end */
static void pictures(MediaMovie *m)
{
    u32 st, sz, decodes = 0;
    const MpvFrame *f, *last = NULL;
    while (!m->video_done && m->shown <= due(m) && decodes < MAX_DECODES) {
        if (!sfd_unit(&m->d, &st, &sz)) {
            if (!m->d.ended) {
                demux(m);
                continue;
            }
            if ((f = mpv_flush(&m->v)) != NULL) last = f, m->shown++;
            m->video_done = 1;
            break;
        }
        const u8 *u = m->d.video + st;
        if (u[3] == 0xb3) m->have_seq = mpv_sequence(&m->v, u, sz);
        else if (u[3] == 0x00 && m->have_seq) {
            decodes++;
            if ((f = mpv_decode(&m->v, u, sz)) != NULL) last = f, m->shown++;
        }
        sfd_compact(&m->d);
    }
    if (last) show(&m->v, last);
    if (m->video_done && (!m->have_sound || m->sound_done)) {
        m->state = MEDIA_TALK_PLAYEND;
        port_log("movie: ended: %u pictures, %u.%02u s of sound (vblank %u)\n", m->shown,
                 m->have_sound ? m->played / m->a.h.rate : 0u,
                 m->have_sound ? m->played % m->a.h.rate * 100u / m->a.h.rate : 0u, port_vblank_count());
    }
}

static int live(const MediaMovie *m) { return m && m->state == MEDIA_TALK_PLAYING && !m->paused; }

/* The picture is picked when the frame is drawn (media_movie_frame, from the renderer), so the frame shows the
 * picture of the sound heard then; a game that draws no frames while its movie plays gets them at the vertical
 * blanks. */
static u32 g_drawn_at;

static void tick(void *arg)
{
    int drawing = port_vblank_count() - g_drawn_at <= 4u;
    (void)arg;
    for (u32 i = 0; i < 4u; i++)
        if (live(g_live[i])) {
            feed(g_live[i]);
            if (!drawing) pictures(g_live[i]);
        }
}

void media_movie_frame(void)
{
    g_drawn_at = port_vblank_count();
    for (u32 i = 0; i < 4u; i++)
        if (live(g_live[i])) pictures(g_live[i]);
}

static void drop(MediaMovie *m)
{
    for (u32 i = 0; i < 4u; i++)
        if (g_live[i] == m) g_live[i] = NULL;
    media_remove(m);
    sfd_free(&m->d);
    mpv_free(&m->v);
    if (m->state == MEDIA_TALK_PLAYING || m->state == MEDIA_TALK_PLAYEND) gfx_movie_hide();
}

MediaMovie *media_movie_new(void)
{
    MediaMovie *m = calloc(1, sizeof *m);
    if (m) m->gain = 32768;
    return m;
}

u8 *media_movie_load(MediaMovie *m, u32 size)
{
    if (!m) return NULL;
    media_movie_stop(m);
    free(m->file);
    m->file = malloc(size ? size : 1u);
    return m->file;
}

int media_movie_start(MediaMovie *m, u32 size)
{
    s32 gain;
    u8 *file;
    u32 i;
    if (!m || !m->file) return 0;
    gain = m->gain;
    file = m->file;
    media_movie_stop(m);
    memset(m, 0, sizeof *m);
    m->gain = gain;
    m->file = file;
    m->size = size;
    for (i = 0; i < 4u && g_live[i]; i++) {}
    if (i == 4u) return 0;
    m->state = MEDIA_TALK_PLAYING;
    g_live[i] = m;
    if (!g_task) port_add_vblank_task(tick, NULL), g_task = 1;
    demux(m);
    media_add(pull, m);
    return 1;
}

void media_movie_stop(MediaMovie *m)
{
    if (!m || m->state == MEDIA_TALK_STOP) return;
    drop(m);
    m->state = MEDIA_TALK_STOP;
    free(m->file); /* a movie's file is large: not kept for the next */
    m->file = NULL;
}

void media_movie_free(MediaMovie *m)
{
    media_movie_stop(m);
    if (m) free(m->file);
    free(m);
}

void media_movie_fail(MediaMovie *m)
{
    media_movie_stop(m);
    if (m) m->state = MEDIA_TALK_PLAYEND;
}

void media_movie_volume(MediaMovie *m, s32 centibels) { if (m) m->gain = media_gain(centibels); }
void media_movie_pause(MediaMovie *m, int on) { if (m) m->paused = on != 0; }
s32 media_movie_state(const MediaMovie *m) { return m ? m->state : MEDIA_TALK_STOP; }
u32 media_movie_pictures(const MediaMovie *m) { return m ? m->shown : 0u; }
