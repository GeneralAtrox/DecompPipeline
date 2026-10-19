/* Platform layer: Audio64's AC layer (ac.h), its digital ports and CD input, on the kit's AICA voices (aica.h): port
 * n is voice n, as the driver sets them up. On the console each call queues a command for the ARM's driver; here it
 * sets the voice directly, so it never fails for a busy queue. All return TRUE (1) or FALSE (0, a bad argument).
 *
 * The library version matters (this is mid-1999's, 1.x): acDigiPlayWithParameters is R10's without directLevel, its
 * last two arguments the loop start and end in frames. A loop end of 0 loops the whole sample; any other end plays
 * the sample once up to it (how the games' one-shot sounds pass the sample's end). Loop flags are AC_LOOP_ON 0xff and
 * AC_LOOP_OFF 0. Volume is the AICA direct level (0..15) and pan Audio64's 0..31 (aica.h: 0 right, 31 left); the CD's
 * volume and pan are AM's 0..127 for each channel, its pan 0 left and 127 right (cd_pan). The driver's port events (acDigiRequestEvent) have no callback here. */
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
    aica_voice_level(port, 15, 15);
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
    aica_voice_level(port, g_port[port].volume, g_port[port].pan);
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
    aica_voice_level(port, g_port[port].volume, g_port[port].pan);
    return 1;
}

s32 acDigiSetPan(u32 port, u32 pan)
{
    if (!port_ok(port)) return 0;
    g_port[port].pan = pan > 31u ? 31u : pan;
    aica_voice_level(port, g_port[port].volume, g_port[port].pan);
    return 1;
}

s32 acDigiRequestEvent(u32 port, u32 offset)
{
    (void)offset;
    return port_ok(port);
}

s32 acDigiGetCurrentReadPosition(u32 port, u32 *position)
{
    if (!port_ok(port) || !position) return 0;
    *position = aica_voice_position(port);
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
    aica_cd_pan(cd_pan(0), cd_pan(127));
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
    aica_cd_pan(cd_pan(left), cd_pan(right));
    return 1;
}

/* The interrupt plumbing the driver's messages come through: nothing comes, so nothing is kept. */
void Ac_SetChainAddFunc(SdkProc func) { (void)func; }
void Ac_SetChainDeleteFunc(SdkProc func) { (void)func; }
void Ac_SetIntCallback(SdkProc func) { (void)func; }
void Ac_SetIntEventId(u32 id) { (void)id; }
