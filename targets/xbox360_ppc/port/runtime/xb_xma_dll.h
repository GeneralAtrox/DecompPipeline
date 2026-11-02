/* XMA waves are decoded by a library apart from the kit, loaded when the first one plays: xb_xma_dll.c */
#ifndef XB_XMA_DLL_H
#define XB_XMA_DLL_H
#include <stdint.h>

/* Starts a wave on a stream, reusing the stream given (NULL for a new one). NULL back when there is no library. */
void *xb_xma_open(void *stream, const uint8_t *data, uint32_t bytes, uint32_t rate);
/* One sample of the wave; silence on a NULL stream. */
float xb_xma_at(void *stream, uint32_t sample);
#endif
