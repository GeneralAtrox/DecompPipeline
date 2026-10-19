/* The AICA, the console's sound chip, as the kit's sound modules drive it (runtime/aica.c): its 2 MB sound RAM, 64
 * voices and the CD input, mixed to 44,100 Hz stereo by gfx/gfx_audio.c at each vertical blank. Voices play 16-bit or
 * 8-bit PCM or Yamaha 4-bit ADPCM from the sound RAM, at a rate quantised as the chip's octave and 10-bit fraction,
 * with linear interpolation, once or looped between two points. Levels are the chip's direct send level (0..15, 3 dB
 * steps, 0 off); pans are Audio64's (0..31: 0 right, 15 and 16 centre, 31 left, ac.h's AC_PAN_*; 3 dB steps, the far
 * end off), which its driver turns into the chip's DIPAN (0..15 to 15 - pan, the left side down; 16..31 as they are,
 * the right side down). Voices sum at full scale and the mix clamps, as the chip's does. */
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
void aica_voice_level(u32 v, u32 level, u32 pan);
/* Plays from frame start: loop between loop_start and end, or once up to end (0: the sample's end). */
void aica_voice_play(u32 v, u32 start, int loop, u32 loop_start, u32 end);
void aica_voice_stop(u32 v);
int aica_voice_playing(u32 v);
u32 aica_voice_position(u32 v); /* the frame being played, from the sample's start */

void aica_cd_level(u32 left, u32 right); /* 0..15 each */
void aica_cd_pan(u32 left, u32 right);   /* 0..31 each */

void aica_mix(s16 *stereo, u32 frames); /* advances every voice and the CD by frames, mixing them */

/* runtime/cdda.c: the drive's CD audio output for the AICA's CD input, silence when it is not playing. */
void cdda_read(s16 *stereo, u32 frames);

/* gfx/gfx_audio.c: the vertical-blank mix and the host output. The sound modules call audio_start on first use. */
void audio_start(void);

#endif
