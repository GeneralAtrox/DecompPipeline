/* Work counters (--work-counts FILE): exact counts of what the tick and frame paths do, for a fixed-clock replay.
 * port/tools/work_counts.py compares them with a tracked baseline per game and scene, so a change that adds an
 * allocation, a draw or an upload fails on any machine, loaded or not. Timing runs stay the judge of the 144 fps target
 * (docs/RUNTIME_TARGET.md "Work counters"); these only catch a slowdown early.
 *
 * Off unless asked for: nothing is patched, and a hook is one test of a flag. On, the exe's own imports of the heap
 * functions are pointed at counting versions, so a new allocation is counted wherever it is written; only the game's
 * thread counts, and only what the exe itself allocates (a driver's or SDL's own depend on the machine). Direct3D goes
 * through the macros present.h puts over the SDK's, so a new draw or upload is counted wherever it is written
 * (tests/test_work_counts.py refuses a call past them). Counted or not, the game runs the same. */
#ifndef WORK_COUNT_H
#define WORK_COUNT_H

#include "base.h"

enum {
    WC_ALLOCS,          /* heap allocations and reallocations */
    WC_ALLOC_BYTES,     /* the bytes they asked for */
    WC_DRAWS,           /* Direct3D draw calls */
    WC_UPLOADS,         /* a texture or buffer given data: UpdateSubresource, a Map to write, a creation with data */
    WC_GPU_CREATES,     /* textures and buffers created */
    WC_GPU_COPIES,      /* copies between GPU resources (each read back of a frame starts with one) */
    WC_PRESENTS,        /* frames shown */
    WC_GUEST_CALLS,     /* calls by guest address between translated functions */
    WC_GUEST_WRITES,    /* guest stores the kit handled out of line */
    WC_VECTOR_OPS,      /* calls of the vector helpers: a dot product, a matrix times a vector */
    WC_SOUND_CPU,       /* the emulated sound CPU: instructions on the ARM, clocks on the 68000 */
    WC_SCENE_HEADERS,   /* display-list headers parsed */
    WC_SCENE_TRIANGLES, /* display-list triangles parsed */
    WC_SPRITE_COMMANDS, /* software sprite and polygon commands drawn */
    WC_COMPOSES,        /* software pictures composed */
    WC_DISC_BYTES,      /* bytes read from the disc */
    WC_N
};

extern int g_work_on;
extern u64 g_work_now[WC_N];

/* a hook: one test of a flag when off */
static inline void work_add(int id, u32 n)
{
    if (g_work_on) g_work_now[id] += n;
}

int work_count_option(const char *arg, const char *value); /* --work-counts FILE, --work-from BLANK; 0 if not ours */
void work_count_tick(u32 blank);                           /* each vertical blank, on the game's thread */
void work_count_report(void);                              /* at exit: writes the file */


#endif
