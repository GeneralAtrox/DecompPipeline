/* XMA waves are decoded by a library apart from the kit, loaded when the first one plays: xb_xma_dll.c */
#ifndef XB_XMA_DLL_H
#define XB_XMA_DLL_H
#include <stdint.h>

/* Starts a wave on a stream, reusing the stream given (NULL for a new one). NULL back when there is no library. */
void *xb_xma_open(void *stream, const uint8_t *data, uint32_t bytes, uint32_t rate);
/* One sample of the wave; silence on a NULL stream. */
float xb_xma_at(void *stream, uint32_t sample);
/* Packets as they come (Too Human's XMA contexts), from the library's optional functions: a stream of 1 or 2 channels at this sample rate (NULL back
 * when the library lacks them: a stereo stream needs xma_packets_open_channels, which earlier builds lack), then one frame of 512 samples a channel,
 * channel after channel, from a window of the stream's packets, as xma_packets_frame says (1 made, 0 more packets needed, 2 ended, -1 bad) */
void *xb_xma_packets_open(void *stream, uint32_t rate, uint32_t channels);
int xb_xma_packets_frame(void *stream, const uint8_t *data, uint32_t bits, uint32_t *pos, int final, float *out);
#endif
