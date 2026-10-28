/* The AICA, the console's sound chip, as the kit's sound modules drive it (runtime/aica.c): its 2 MB sound RAM, 64
 * voices and the CD input, mixed to 44,100 Hz stereo by gfx/gfx_audio.c at each vertical blank. Voices play 16-bit or
 * 8-bit PCM or Yamaha 4-bit ADPCM from the sound RAM, at a rate quantised as the chip's octave and 10-bit fraction,
 * with linear interpolation, once or looped between two points. Levels are the chip's direct send level (DISDL 0..15,
 * 3 dB steps, 0 off) and pans its DIPAN (bit 4 the side turned down, 0 the left and 1 the right; bits 0..3 by 3 dB
 * steps, 15 off). A stop is a key off: the voice fades through the release Audio64's driver sets on every voice.
 * Measured on Flycast (tools/audio_check.py): the driver keys every voice on with attack 31, no decay, release 31 and
 * TL 0, the filter open and no LFO, and sends nothing to the DSP; so these levels, pans and the release are the
 * envelope. Voices sum at full scale and the mix clamps, as the chip's does. */
#ifndef AICA_H
#define AICA_H

#include "platform.h"

#define AICA_RAM_BASE 0xa0800000u /* the sound RAM's uncached SH-4 address */
#define AICA_RAM_SIZE 0x00200000u
#define AICA_VOICES 64u
#define AICA_RATE 44100u
#define AICA_MAX_FRAMES 0xffffu /* a voice's 16-bit sample counter */

#define AICA_PCM16 0u
#define AICA_PCM8 1u
#define AICA_ADPCM 2u        /* the decoder restarts at the loop start */
#define AICA_ADPCM_STREAM 3u /* the decoder runs on through the loop (a ring) */

u8 *aica_ram(void); /* maps the sound RAM on first use (zeroed); NULL if the address range is taken */

/* A voice's sample: addr is a sound RAM address (SH-4 or AICA form), frames its length. Stops the voice. */
void aica_voice_sample(u32 v, u32 addr, u32 frames, u32 format);
void aica_voice_rate(u32 v, u32 hz);
u32 aica_rate_cents(u32 hz, s32 cents); /* hz moved by cents (1/1200 of an octave) */
void aica_voice_level(u32 v, u32 level, u32 dipan);
/* Plays from frame start: loop between loop_start and end, or once up to end (0: the sample's end). */
void aica_voice_play(u32 v, u32 start, int loop, u32 loop_start, u32 end);
void aica_voice_stop(u32 v); /* key off: the release, then the voice stops */
int aica_voice_playing(u32 v);
/* The frame being played, from the sample's start, at phase through the vertical blank (port_vblank_phase, 0..65536):
 * the chip plays on between blanks, so a voice read mid-blank has moved on by that part of the next mix's frames. */
u32 aica_voice_position(u32 v, u32 phase);
void aica_next_frames(u32 frames); /* gfx_audio.c: the frames the next blank's mix plays (735 or 736; 735 at first) */

void aica_cd_level(u32 left, u32 right); /* 0..15 each */
void aica_cd_pan(u32 left, u32 right);   /* DIPAN each */
void aica_cd_mute(int mute);             /* the audio check's --audio-cd off: the CD left out of the mix only */

void aica_mix(s16 *stereo, u32 frames); /* advances every voice and the CD by frames, mixing them */

/* The audio check's key log (tools/audio_check.py, --audio-keys): called at each voice start (on) and key off with
 * the output frame it comes at (frames mixed so far), the voice, its level and DIPAN, its step (16.16) and its sample's
 * length. */
typedef void (*AicaKeyLog)(u32 v, u64 frame, int on, u32 level, u32 pan, u32 step, u32 frames);
void aica_key_log(AicaKeyLog fn);
int aica_key_logging(void); /* the key log is on (a measuring run) */
void aica_key_log_off(u32 v); /* a key off the caller fades itself (an own envelope's release) */

/* runtime/aica_arm.c's chip, whose voices the game's sound driver sets through the AICA's registers:
 *   - an own envelope: the voice's gain at each output frame is fn(v) (the chip's AEG and TL), in place of the
 *     fixed release; a negative gain ends the voice (its release is over);
 *   - the slice hook: aica_mix mixes in slices of at most `frames` output frames and calls fn(n) before each slice's
 *     mix, so the sound processor runs up to it; a key on then starts at its slice, not at a point in the blank;
 *   - the step as the chip's OCT and FNS give it (16.16), and the LP flag (aica_voice_looped). */
typedef float (*AicaEnvFn)(u32 v);
typedef void (*AicaSliceFn)(u32 frames);
void aica_envelope_hook(AicaEnvFn fn);
void aica_voice_envelope(u32 v, int own);
void aica_slice_hook(AicaSliceFn fn, u32 frames);
void aica_voice_step(u32 v, u32 step);
int aica_voice_looped(u32 v);

/* The console's vertical blank is NTSC's, 60000/1001 Hz, so the chip's 44,100 Hz is 735.735 frames a blank, not 735:
 * at 735 a CD track ends 0.1% late in game time (a 141.8 s track 8.5 blanks late, so a game that waits for its end
 * goes on a frame pair after the console). The frames vertical blank n advances: 735 or 736, exact over any span. */
#define AICA_VBLANK_MAX_FRAMES 736u
static inline u32 aica_vblank_frames(u64 n)
{
    return (u32)((n + 1u) * AICA_RATE * 1001u / 60000u - n * AICA_RATE * 1001u / 60000u);
}

/* runtime/cdda.c: the drive's CD audio output for the AICA's CD input, silence when it is not playing. */
void cdda_read(s16 *stereo, u32 frames);

/* gfx/gfx_audio.c: the vertical-blank mix and the host output. The sound modules call audio_start on first use. */
void audio_start(void);

#endif
