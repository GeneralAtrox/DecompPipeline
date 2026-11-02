/* CD-DA: the audio tracks of the game disc, played as the drive plays them. The CD block (sat_cd.c) reads each audio
 * sector (2352 bytes: 588 stereo 16-bit samples at 44100 Hz, little-endian) at the 75 a second the drive turns, and
 * hands it to the base's output (base/audio_out.c). A run without a window or on the fast clock (`--headless`, `--fast`)
 * plays nothing. The SCSP's mixing of the disc input (volume, pan) is not modelled: the track plays at full level.
 *
 * Nearest existing: runtime/ (the Dreamcast kit's ADX music path) - that decodes the game's own streamed files; here
 * the disc holds plain PCM, so this is only the queue. */
#include "sat.h"
#include "sat_cd.h"
#include "audio_out.h"

enum { QUEUE_SECTORS = 48 }; /* the most held: a sector past that is lost, as a late one is on a real drive */
enum { LEAD_SECTORS = 6 };   /* silence before a sector that finds the output empty: 80 ms for a late frame to use up */

static AudioOut *g_out;
static u32 g_sectors;

void sat_cdda_enable(int on)
{
    if (!on || g_out) return;
    g_out = audio_out_open(44100u);
    if (!g_out) {
        port_log("cdda: no output, music stays silent\n");
        return;
    }
    audio_out_start(g_out);
}

/* what the device holds stops */
void sat_cdda_flush(void)
{
    if (g_out) audio_out_clear(g_out);
}

void sat_cdda_push(const u8 *raw)
{
    static u32 lost, dry; /* the output full, or empty when the next sector came: a gap in the music (the first of a track counts too) */
    u32 queued = g_out ? (u32)audio_out_queued(g_out) : 0u;
    if (g_sectors++ % 750u == 0) port_log("cdda: audio sector %u played so far (a window run plays it), %u sectors lost, ran dry %u times\n", g_sectors, lost, dry);
    if (!g_out) return;
    if (queued >= QUEUE_SECTORS * SAT_SECTOR) {
        lost++;
        return;
    }
    if (!queued) {
        static const u8 silence[LEAD_SECTORS * SAT_SECTOR] = {0};
        dry++;
        audio_out_put(g_out, silence, sizeof silence);
    }
    audio_out_put(g_out, raw, SAT_SECTOR);
}
