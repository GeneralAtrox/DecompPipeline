/* Platform layer: CRI ADX (ADXT, streamed music) and Sofdec (movies).
 *
 * ADXT_StartFname reads the whole file through the kit's gdFs and plays it into the kit's mix (runtime/media: the
 * ADX decoder and the talk, mixed beside the AICA's voices at each vertical blank), looping as its header says, at
 * the handle's output volume (0.1 dB steps, 0 the loudest). A game that fades music by reading the volume back
 * (ADXT_GetOutVol, then ADXT_SetOutVol a step further) sees its own steps and the fade ends. Sofdec's start-up and
 * the middleware servers do nothing. The error callback is kept and never called.
 *
 * mwPlyCreateSofdec hands out a player that plays movies (runtime/media/movie.c): a game calls the player's methods
 * through its table (the handle's first word). StartFname reads the whole movie through gdFs and starts it; GetStat
 * answers playing until its last picture and sound are out, then ended; Stop and Destroy end it and take its picture
 * away; Pause and SetOutVol act on it; every other method does nothing and returns 0. The handle and the table are
 * host memory in the guest's 32-bit address space: guest code reads them as words and calls the methods as host
 * function pointers with its argument words (runtime/tx/tx_rt.c, runtime/dispatch.c).
 *
 * Needs from the game's headers: ADXT (a handle pointer), AdxtErrFunc, MWS_PLY_INIT_SFD and the function names below
 * as the game's registry has them; and the kit's gdfs module in the game's "sdk" list. */
#include "game.h"
#include "platform.h"
#include "media/media.h"

#define HANDLES 8u
#define VOLUME_DEFAULT 0 /* ADX volumes are in 0.1 dB below full, 0 the loudest */
#define GD_SECTOR 2048

static struct {
    int used;
    s32 volume;
    s32 recover;
    MediaTalk *talk;
} g_talks[HANDLES];
static AdxtErrFunc g_err_fn;
static void *g_err_obj;

static u32 slot(ADXT adxt)
{
    u32 i = (u32)((uintptr_t)adxt - (uintptr_t)g_talks) / sizeof g_talks[0];
    return adxt && (uintptr_t)adxt >= (uintptr_t)g_talks && i < HANDLES ? i : HANDLES;
}

ADXT ADXT_Create(s32 maxChannels, void *work, s32 workSize)
{
    (void)maxChannels;
    (void)work;
    (void)workSize;
    for (u32 i = 0; i < HANDLES; i++) {
        if (g_talks[i].used) continue;
        g_talks[i].used = 1;
        g_talks[i].volume = VOLUME_DEFAULT;
        g_talks[i].recover = 0;
        if (!g_talks[i].talk) g_talks[i].talk = media_talk_new();
        return (ADXT)(void *)&g_talks[i];
    }
    port_log("adx: no free ADXT handle\n");
    return NULL;
}

void ADXT_Destroy(ADXT adxt)
{
    u32 i = slot(adxt);
    if (i >= HANDLES) return;
    media_talk_stop(g_talks[i].talk);
    g_talks[i].used = 0;
}

void ADXT_StartFname(ADXT adxt, const char *fname)
{
    u32 i = slot(adxt);
    s32 size = 0;
    GDFS fs;
    u8 *buf;
    const AdxInfo *h;
    if (i >= HANDLES || !g_talks[i].talk || !fname) return;
    fs = gdFsOpen(fname, NULL);
    if (!fs) {
        media_talk_stop(g_talks[i].talk);
        port_log("adx: stream %s: no such file\n", fname);
        return;
    }
    gdFsGetFileSize(fs, &size);
    buf = size > 0 ? media_talk_load(g_talks[i].talk, (u32)(size + GD_SECTOR - 1) & ~(u32)(GD_SECTOR - 1)) : NULL;
    if (!buf || gdFsRead(fs, (size + GD_SECTOR - 1) / GD_SECTOR, buf) != 0 ||
        !media_talk_start(g_talks[i].talk, (u32)size)) {
        gdFsClose(fs);
        port_log("adx: stream %s (%d bytes) is not ADX the kit plays\n", fname, size);
        return;
    }
    gdFsClose(fs);
    media_talk_volume(g_talks[i].talk, g_talks[i].volume);
    h = media_talk_info(g_talks[i].talk);
    port_log("adx: stream %s: %u Hz, %u channel(s), %u.%02u s%s (vblank %u)\n", fname, h->rate, h->channels,
             h->samples / h->rate, h->samples % h->rate * 100u / h->rate, h->loop ? ", looping" : "",
             port_vblank_count());
}

void ADXT_Stop(ADXT adxt)
{
    u32 i = slot(adxt);
    if (i < HANDLES) media_talk_stop(g_talks[i].talk);
}

void ADXT_SetOutVol(ADXT adxt, s32 volume)
{
    u32 i = slot(adxt);
    if (i >= HANDLES) return;
    g_talks[i].volume = volume;
    media_talk_volume(g_talks[i].talk, volume);
}

s32 ADXT_GetOutVol(ADXT adxt)
{
    u32 i = slot(adxt);
    return i < HANDLES ? g_talks[i].volume : VOLUME_DEFAULT;
}

/* How the stream recovers from a read error; nothing reads the disc for it here. */
void ADXT_SetAutoRcvr(ADXT adxt, s32 rmode)
{
    u32 i = slot(adxt);
    if (i < HANDLES) g_talks[i].recover = rmode;
}

void ADXT_EntryErrFunc(AdxtErrFunc func, void *obj)
{
    g_err_fn = func;
    g_err_obj = obj;
}

/* The per-frame servers: ADX's stream and the middleware's (Sofdec's) work. */
void ADXT_ExecServer(void) {}
void Mw_ExecServer(void) {}

void mwPlyInitSofdec(MWS_PLY_INIT_SFD *param) { (void)param; }

#define PLY_METHODS 32u /* the slots a player table may have; Sofdec's has fewer */
/* Sofdec's player methods, by their place in the table (word index) */
#define PLY_DESTROY 5u
#define PLY_START_FNAME 6u
#define PLY_STOP 7u
#define PLY_GET_STAT 8u
#define PLY_PAUSE 10u
#define PLY_SET_OUT_VOL 11u
/* MWE_PLY_STAT: what GetStat answers */
#define PLY_STAT_STOP 0u
#define PLY_STAT_PLAYING 2u
#define PLY_STAT_PLAYEND 3u
#define PLY_STAT_ERROR 4u

static u32 g_ply_table[PLY_METHODS];
static u32 g_ply[16]; /* the handle: its table, then words a game may read; one player at a time */
static MediaMovie *g_movie;

static u32 ply_nothing(u32 ply) { (void)ply; return 0; }

static u32 ply_stop(u32 ply)
{
    (void)ply;
    if (g_movie && media_movie_state(g_movie) != MEDIA_TALK_STOP)
        port_log("sofdec: stopped after %u pictures (vblank %u)\n", media_movie_pictures(g_movie), port_vblank_count());
    media_movie_stop(g_movie);
    return 0;
}

static u32 ply_destroy(u32 ply) { return ply_stop(ply); }

/* fname: the guest's address of the file name, which the host reads where the guest's RAM is mapped */
static u32 ply_start_fname(u32 ply, u32 fname)
{
    const char *name = (const char *)(uintptr_t)fname;
    s32 size = 0, r;
    GDFS fs;
    u8 *buf;
    ply_stop(ply);
    if (!g_movie) g_movie = media_movie_new();
    if (!g_movie || !fname) return 0;
    fs = gdFsOpen(name, NULL);
    if (!fs) {
        port_log("sofdec: movie %s: no such file\n", name);
        media_movie_fail(g_movie);
        return 0;
    }
    gdFsGetFileSize(fs, &size);
    buf = size > 0 ? media_movie_load(g_movie, (u32)(size + GD_SECTOR - 1) & ~(u32)(GD_SECTOR - 1)) : NULL;
    r = buf ? gdFsRead(fs, (size + GD_SECTOR - 1) / GD_SECTOR, buf) : -1;
    if (r != 0 || !media_movie_start(g_movie, (u32)size)) {
        gdFsClose(fs);
        port_log("sofdec: movie %s (%d bytes) could not be %s (%d)\n", name, size, r ? "read" : "started", r);
        media_movie_fail(g_movie); /* the game sees it end and moves on */
        return 0;
    }
    gdFsClose(fs);
    port_log("sofdec: movie %s: %d bytes (vblank %u)\n", name, size, port_vblank_count());
    return 0;
}

static u32 ply_get_stat(u32 ply)
{
    (void)ply;
    switch (media_movie_state(g_movie)) {
    case MEDIA_TALK_PLAYING: return PLY_STAT_PLAYING;
    case MEDIA_TALK_PLAYEND: return PLY_STAT_PLAYEND;
    case MEDIA_TALK_ERROR: return PLY_STAT_ERROR;
    default: return PLY_STAT_STOP;
    }
}

static u32 ply_pause(u32 ply, u32 on)
{
    (void)ply;
    media_movie_pause(g_movie, (s32)on != 0);
    return 0;
}

static u32 ply_set_out_vol(u32 ply, u32 volume)
{
    (void)ply;
    media_movie_volume(g_movie, (s32)volume);
    return 0;
}

void *mwPlyCreateSofdec(void *cprm)
{
    (void)cprm;
    for (u32 i = 0; i < PLY_METHODS; i++) g_ply_table[i] = (u32)(uintptr_t)ply_nothing;
    g_ply_table[PLY_DESTROY] = (u32)(uintptr_t)ply_destroy;
    g_ply_table[PLY_START_FNAME] = (u32)(uintptr_t)ply_start_fname;
    g_ply_table[PLY_STOP] = (u32)(uintptr_t)ply_stop;
    g_ply_table[PLY_GET_STAT] = (u32)(uintptr_t)ply_get_stat;
    g_ply_table[PLY_PAUSE] = (u32)(uintptr_t)ply_pause;
    g_ply_table[PLY_SET_OUT_VOL] = (u32)(uintptr_t)ply_set_out_vol;
    g_ply[0] = (u32)(uintptr_t)g_ply_table;
    port_log("sofdec: player created\n");
    return g_ply;
}
