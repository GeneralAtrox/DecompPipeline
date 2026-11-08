/* bands.c: work split across cores. bands_run(fn, ctx) calls fn(ctx, band, bands) once for each band and returns when
 * every one is done; the calling thread takes band 0. A band's work must write only its own part of the output and
 * keep its own scratch, so the result is the same at any count. */
#ifndef BANDS_H
#define BANDS_H

#ifdef __cplusplus
extern "C" {
#endif

#define BANDS_MAX 8
typedef void (*BandFn)(void *ctx, unsigned band, unsigned bands);
int bands_option(const char *arg, const char *value); /* --bands N: 1 all on the calling thread, 0 (the default) as many as the cores, at most 4 */
void bands_set(unsigned n);                           /* before the first bands_run */
unsigned bands_count(void);
void bands_run(BandFn fn, void *ctx);

#ifdef __cplusplus
}
#endif

#endif
