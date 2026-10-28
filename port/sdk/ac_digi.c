/* Platform layer: Audio64's AC layer (ac.h), its digital ports and CD input, on the kit's AICA voices (aica.h): port
 * n is voice n, as the driver sets them up. On the console each call queues a command for the ARM's driver; here it
 * sets the voice directly, so it never fails for a busy queue. All return TRUE (1) or FALSE (0, a bad argument).
 *
 * The library version matters (this is mid-1999's, 1.x): acDigiPlayWithParameters is R10's without directLevel, its
 * last two arguments the loop start and end in frames. A loop end of 0 loops the whole sample; any other end plays
 * the sample once up to it (how the games' one-shot sounds pass the sample's end). Loop flags are AC_LOOP_ON 0xff and
 * AC_LOOP_OFF 0. Volume is the AICA direct level (0..15) and pan Audio64's 0..31 (ac.h AC_PAN_*: 0 right, 15 and 16
 * centre, 31 left), which the driver writes as the chip's DIPAN (dipan); the CD's volume and pan are AM's 0..127 for
 * each channel, its pan 0 left and 127 right (cd_pan). The driver's port events (acDigiRequestEvent) have no callback here. */
#include "game.h"
#include "platform.h"
#include "aica.h"

#define AC_LOOP_ON 0xff
#define AC_PITCH_AS_SAMPLE_RATE 1u
#define AC_PITCH_AS_CENT_VALUE 2u

typedef struct {
    u32 open, frames, rate, volume, pan;
} Port;

static Port g_port[AICA_VOICES];

static int port_ok(u32 port) { return port < AICA_VOICES; }

/* The frames in a sample of size bytes: 16-bit halves it, ADPCM doubles it (ac.h SampleLengthCheck). */
/* Audio64's pan as its driver writes it to DIPAN (read from Flycast's key ons, tools/audio_check.py): 16..31 as they
 * are (the right side down by pan - 16 steps), 1..15 as 16 - pan (the left side down by that many), 0 as 1 does. */
static u32 dipan(u32 pan) { return pan >= 16u ? pan : pan ? 16u - pan : 15u; }

static u32 frames_of(u32 bytes, u32 type) { return type == AICA_PCM16 ? bytes >> 1 : type >= AICA_ADPCM ? bytes << 1 : bytes; }

s32 acDigiOpen(u32 port, u32 aicaAddress, u32 sizeInBytes, u32 audioType, u32 sampleRate)
{
    if (!port_ok(port) || !sizeInBytes || audioType > AICA_ADPCM_STREAM || !aica_ram()) return 0;
    audio_start();
    g_port[port].open = 1;
    g_port[port].frames = frames_of(sizeInBytes, audioType);
    g_port[port].rate = sampleRate;
    aica_voice_sample(port, aicaAddress, g_port[port].frames, audioType);
    aica_voice_rate(port, sampleRate);
    g_port[port].volume = 15;
    g_port[port].pan = 15;
    aica_voice_level(port, 15, dipan(15));
    return 1;
}

s32 acDigiClose(u32 port)
{
    if (!port_ok(port)) return 0;
    aica_voice_stop(port);
    g_port[port].open = 0;
    return 1;
}

s32 acDigiPlay(u32 port, u32 startOffset, s16 loopFlag)
{
    if (!port_ok(port)) return 0;
    aica_voice_play(port, startOffset, loopFlag == AC_LOOP_ON, 0, 0);
    return 1;
}

s32 acDigiPlayWithParameters(u32 port, u32 volume, u32 pan, u32 dspMixerChannel, u32 dspSendLevel,
                             u32 frequencyOrCents, u32 frequencyOrCentsFlag, u32 eventOffsetInFrames,
                             s16 loopStartInFrames, u32 loopEndInFrames)
{
    (void)dspMixerChannel, (void)dspSendLevel, (void)eventOffsetInFrames;
    if (!port_ok(port)) return 0;
    g_port[port].volume = volume > 15u ? 15u : volume;
    g_port[port].pan = pan > 31u ? 31u : pan;
    aica_voice_level(port, g_port[port].volume, dipan(g_port[port].pan));
    if (frequencyOrCentsFlag == AC_PITCH_AS_SAMPLE_RATE) {
        aica_voice_rate(port, frequencyOrCents);
    } else if (frequencyOrCentsFlag == AC_PITCH_AS_CENT_VALUE) {
        aica_voice_rate(port, aica_rate_cents(g_port[port].rate, (s32)frequencyOrCents));
    }
    aica_voice_play(port, 0, loopEndInFrames == 0, (u16)loopStartInFrames, loopEndInFrames);
    return 1;
}

s32 acDigiStop(u32 port)
{
    if (!port_ok(port)) return 0;
    aica_voice_stop(port);
    return 1;
}

s32 acDigiSetSampleRate(u32 port, u32 sampleRate)
{
    if (!port_ok(port)) return 0;
    g_port[port].rate = sampleRate;
    aica_voice_rate(port, sampleRate);
    return 1;
}

s32 acDigiSetVolume(u32 port, u32 volume)
{
    if (!port_ok(port)) return 0;
    g_port[port].volume = volume > 15u ? 15u : volume;
    aica_voice_level(port, g_port[port].volume, dipan(g_port[port].pan));
    return 1;
}

s32 acDigiSetPan(u32 port, u32 pan)
{
    if (!port_ok(port)) return 0;
    g_port[port].pan = pan > 31u ? 31u : pan;
    aica_voice_level(port, g_port[port].volume, dipan(g_port[port].pan));
    return 1;
}

s32 acDigiRequestEvent(u32 port, u32 offset)
{
    (void)offset;
    return port_ok(port);
}

/* A game can wait on a voice's position (a movie player's audio clock): on the console the chip plays on while it
 * polls. Here voices are mixed at the vertical blank, and a read between blanks has moved on by the time through the
 * blank (port_vblank_phase; each read is a look at the clock, so under --fixed-clock a polling loop moves time on).
 * A playing voice found where it was SPIN_POLLS times with the blank's time used up (its frames all played, the next
 * blank due) means the game is spinning on it, and the poll waits for the next vertical blank. */
#define SPIN_POLLS 64u

static int spinning(u32 port, u32 position, u32 phase)
{
    static u32 last_port = ~0u, last_position, vblank, polls;
    if (port != last_port || position != last_position || vblank != port_vblank_count() || phase < 65536u) {
        last_port = port;
        last_position = position;
        vblank = port_vblank_count();
        polls = 0;
        return 0;
    }
    if (++polls < SPIN_POLLS || !aica_voice_playing(port)) return 0;
    polls = 0;
    return 1;
}

s32 acDigiGetCurrentReadPosition(u32 port, u32 *position)
{
    if (!port_ok(port) || !position) return 0;
    u32 phase = port_vblank_phase(1);
    *position = aica_voice_position(port, phase);
    if (spinning(port, *position, phase)) {
        port_wait_vblank();
        *position = aica_voice_position(port, port_vblank_phase(1));
    }
    return 1;
}

/* Ports 0..31 are bits of the lower mask, 32..63 of the upper one. */
s32 acDigiMultiSetMask(u32 port, u32 *upperMask, u32 *lowerMask)
{
    if (!port_ok(port) || !upperMask || !lowerMask) return 0;
    if (port < 32u) *lowerMask |= 1u << port;
    else *upperMask |= 1u << (port - 32u);
    return 1;
}

static int masked(u32 port, u32 upperMask, u32 lowerMask)
{
    return port < 32u ? (lowerMask >> port & 1u) != 0 : (upperMask >> (port - 32u) & 1u) != 0;
}

s32 acDigiMultiPlay(s32 loopFlag, u32 upperMask, u32 lowerMask)
{
    for (u32 port = 0; port < AICA_VOICES; port++)
        if (masked(port, upperMask, lowerMask)) aica_voice_play(port, 0, loopFlag == AC_LOOP_ON, 0, 0);
    return 1;
}

s32 acDigiMultiStop(u32 upperMask, u32 lowerMask)
{
    for (u32 port = 0; port < AICA_VOICES; port++)
        if (masked(port, upperMask, lowerMask)) aica_voice_stop(port);
    return 1;
}

/* AM's CD pan (0 left .. 127 right) as Audio64's (31 left .. 0 right). */
static u32 cd_pan(u32 am) { return 31u - (am >> 2); }

s32 acCdInit(void)
{
    aica_cd_level(15, 15);
    aica_cd_pan(dipan(cd_pan(0)), dipan(cd_pan(127)));
    return 1;
}

s32 acCdSetVolume(u32 left, u32 right)
{
    if (left > 127u || right > 127u) return 0;
    aica_cd_level(left >> 3, right >> 3);
    return 1;
}

s32 acCdSetPan(u32 left, u32 right)
{
    if (left > 127u || right > 127u) return 0;
    aica_cd_pan(dipan(cd_pan(left)), dipan(cd_pan(right)));
    return 1;
}

/* The interrupt plumbing the driver's messages come through: nothing comes, so nothing is kept. */
void Ac_SetChainAddFunc(SdkProc func) { (void)func; }
void Ac_SetChainDeleteFunc(SdkProc func) { (void)func; }
void Ac_SetIntCallback(SdkProc func) { (void)func; }
void Ac_SetIntEventId(u32 id) { (void)id; }
