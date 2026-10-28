/* sfd.c: a Sofdec movie's MPEG-1 system stream, demultiplexed into its video and sound */
#ifndef SFD_H
#define SFD_H

#include "mpv.h"

#define SFD_VIDEO 0xe0
#define SFD_AUDIO 0xc0

typedef struct SfdDemux {
    u8 *video;       /* the video stream from vpos (earlier bytes are decoded), MPV_PAD zeros past vlen */
    u32 vlen, vcap, vpos;
    u8 *audio;       /* the whole sound stream so far (an ADX file) */
    u32 alen, acap;
    int ended;       /* the end code, or the end of the file, has been reached */
} SfdDemux;

/* Parses the whole packets in p[0, n): the bytes taken; the rest begins a packet to parse again with more after it.
 * eof: p ends the file (everything is taken). */
u32 sfd_parse(SfdDemux *d, const u8 *p, u32 n, int eof);
/* The next whole video unit (a sequence header, a picture, ...) as video[start, start + size): 0 if none yet. */
int sfd_unit(SfdDemux *d, u32 *start, u32 *size);
void sfd_compact(SfdDemux *d); /* drops the units handed out (call between units, not while one is in use) */
void sfd_free(SfdDemux *d);

#endif
