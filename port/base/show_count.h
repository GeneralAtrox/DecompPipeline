/* The counts taken from the swap chain's frame-latency object (show_count.c). */
#ifndef SHOW_COUNT_H
#define SHOW_COUNT_H

int show_count_take(void *latency, unsigned wait_ms); /* 1 if a count is held: a Present now would not block */
void show_count_used(void *latency);                  /* a frame was presented with one of them */
long show_count_held(void);                           /* how many are held */

#endif
