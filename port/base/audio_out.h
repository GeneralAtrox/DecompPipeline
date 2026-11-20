/* audio_out.c: the base's sound output. The one place a port opens the audio device: a console mixes its samples and puts
 * them here (16-bit stereo), the base owns the device, the volume and the mute. A console may open several (a Saturn's
 * SCSP and its CD audio): the system mixes them. */
#ifndef AUDIO_OUT_H
#define AUDIO_OUT_H

#include "base.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AudioOut AudioOut;

AudioOut *audio_out_open(u32 rate);                     /* paused; NULL if there is no device (logged) */
/* A native game's own mixer (base/native_game.h): its samples as it makes them, 32-bit float (1) or 16-bit, 1..8
 * channels interleaved; the Core's volume and mute apply as to every output. */
AudioOut *audio_out_open_format(u32 rate, u32 channels, int is_float);
/* The device asks for sound as it plays (a mixer run on the device's thread): need(user, a, bytes) puts about that much
 * with audio_out_put. NULL stops asking. */
void audio_out_pull(AudioOut *a, void (*need)(void *user, AudioOut *a, u32 bytes), void *user);
void audio_out_gain(AudioOut *a, float gain);           /* the game's own level of this output (its music, its effects), 0..1,
                                                           under the Core's volume */
void audio_out_close(AudioOut *a);                      /* the output and what it holds let go */
void audio_out_pause(AudioOut *a);                      /* it stops taking sound (audio_out_start goes on) */
void audio_out_lock(AudioOut *a, int on);               /* 1: audio_out_pull's mixer is not running until 0 */
void audio_out_flush(AudioOut *a);                      /* the end of a sound: what is held is played out whole */
void audio_out_start(AudioOut *a);                      /* it plays from here */
void audio_out_put(AudioOut *a, const void *frames, u32 bytes);
u32 audio_out_queued(AudioOut *a);                      /* bytes not yet played */
u32 audio_out_gaps(AudioOut *a, u32 *missing_us);       /* times the device played silence mid-sound (it asked for more than was put) */
void audio_out_clear(AudioOut *a);                      /* what it holds is dropped */
void audio_out_rest(AudioOut *a);                       /* the sound stops here on purpose: the silence until the next put is no gap */
void audio_out_ratio(AudioOut *a, float ratio);         /* plays this many times as fast (a console's tick against the host's) */
float audio_out_steer(AudioOut *a, u32 queued, u32 target); /* before each put: up to 1 % faster or slower to hold `target` frames queued */
float audio_out_trim(AudioOut *a);                       /* the steady part of that, learnt (0.01: 1 % fast) */
u32 audio_out_device_us(AudioOut *a);                  /* the sound the device itself holds beyond the queue (its buffer), in microseconds */
u64 audio_out_now_ns(void);                             /* the clock the device's play position is judged against */

void audio_set_volume(int percent, int muted);          /* --volume 0..100 and the menu; muted silences every output */
int audio_volume(void);

#ifdef __cplusplus
}
#endif

#endif
