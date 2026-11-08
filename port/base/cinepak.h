/* cinepak.c: a Cinepak ("cvid") video decoder into a 0x00RRGGBB frame, for a console's movies played by the host instead
 * of the console's own decode (a Saturn's Sega FILM files, port/saturn/sat_movie.c). Key and inter frames, several
 * strips, 4- and 6-element codebooks, and the Sega FILM variant's 2 or 6 extra bytes after the frame header. */
#ifndef CINEPAK_H
#define CINEPAK_H
#include "base.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct Cinepak Cinepak;
Cinepak *cinepak_open(u32 w, u32 h);  /* NULL if there is no memory; the frame starts black */
void cinepak_close(Cinepak *c);
int cinepak_frame(Cinepak *c, const u8 *data, u32 size); /* decodes one frame over the last; 0 if it is malformed */
const u32 *cinepak_pixels(const Cinepak *c);              /* w * h pixels, 0x00RRGGBB, rows top down */
#ifdef __cplusplus
}
#endif
#endif
