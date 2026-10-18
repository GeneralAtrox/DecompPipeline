/* The GD-ROM drive's CD audio (platform.h host_cd_*): the disc image's audio tracks, read a sector at a time as the
 * AICA mixes them (aica.c pulls cdda_read at 44,100 Hz, the CD's own rate). A play runs from the first track's start
 * to the last track's end, then again repeat times (15: for ever), then stops as a drive does, in pause. */
#include <string.h>
#include "aica.h"

#define FRAMES_PER_SECTOR 588u
#define SECTOR_BYTES 2352u
#define REPEAT_FOREVER 15u

static struct {
    int playing, paused;
    u32 lba, sectors; /* the span played */
    u32 frame;        /* the next frame, from the span's start */
    u32 repeats;      /* plays left after this one */
    u32 cached;       /* the sector in buf, from the span's start; ~0 none */
    s16 buf[2 * FRAMES_PER_SECTOR];
} g_cd = {0, 0, 0, 0, 0, 0, 0xffffffffu, {0}};

int host_cd_play(u32 first_track, u32 last_track, u32 repeat)
{
    u32 lba, sectors, last_lba, last_sectors;
    if (!host_disc_audio(first_track, &lba, &sectors) || !host_disc_audio(last_track, &last_lba, &last_sectors) ||
        last_lba < lba) {
        port_log("cdda: tracks %u-%u are not audio tracks of the image\n", first_track, last_track);
        g_cd.playing = 0;
        return 0;
    }
    g_cd.lba = lba;
    g_cd.sectors = last_lba + last_sectors - lba;
    g_cd.frame = 0;
    g_cd.repeats = repeat;
    g_cd.cached = 0xffffffffu;
    g_cd.playing = 1;
    g_cd.paused = 0;
    port_log("cdda: tracks %u-%u, %u sectors, repeat %u\n", first_track, last_track, g_cd.sectors, repeat);
    audio_start();
    return 1;
}

void host_cd_stop(void) { g_cd.playing = g_cd.paused = 0; }

void host_cd_pause(int paused) { g_cd.paused = paused; }

int host_cd_playing(void) { return g_cd.playing && !g_cd.paused; }

void cdda_read(s16 *stereo, u32 frames)
{
    for (; frames; frames--, stereo += 2) {
        stereo[0] = stereo[1] = 0;
        if (!g_cd.playing || g_cd.paused) continue;
        if (g_cd.frame >= g_cd.sectors * FRAMES_PER_SECTOR) {
            if (g_cd.repeats == 0) {
                g_cd.playing = 0;
                continue;
            }
            if (g_cd.repeats != REPEAT_FOREVER) g_cd.repeats--;
            g_cd.frame = 0;
        }
        u32 sector = g_cd.frame / FRAMES_PER_SECTOR;
        if (sector != g_cd.cached) {
            if (!host_disc_read_audio(g_cd.lba + sector, 1, g_cd.buf)) memset(g_cd.buf, 0, sizeof g_cd.buf);
            g_cd.cached = sector;
        }
        u32 k = g_cd.frame++ % FRAMES_PER_SECTOR;
        stereo[0] = g_cd.buf[2 * k];
        stereo[1] = g_cd.buf[2 * k + 1];
    }
}
