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
void audio_out_start(AudioOut *a);                      /* it plays from here */
void audio_out_put(AudioOut *a, const void *frames, u32 bytes);
u32 audio_out_queued(AudioOut *a);                      /* bytes not yet played */
void audio_out_clear(AudioOut *a);                      /* what it holds is dropped */
void audio_out_ratio(AudioOut *a, float ratio);         /* plays this many times as fast (a console's tick against the host's) */
u64 audio_out_now_ns(void);                             /* the clock the device's play position is judged against */

void audio_set_volume(int percent, int muted);          /* --volume 0..100 and the menu; muted silences every output */
int audio_volume(void);

#ifdef __cplusplus
}
#endif

#endif
