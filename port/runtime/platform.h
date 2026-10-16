/* Platform layer: the host services that SDK replacements (the kit's sdk/ modules and a game's own sources) use.
 * Those files include game.h, so they include no CRT or Windows header (game.h declares the CRT functions the game
 * links); runtime/host.c does the host calls. */
#ifndef PLATFORM_H
#define PLATFORM_H

#include "rd_rt.h"
#include "port_config.h" /* generated from game.json */

void port_log(const char *fmt, ...);
void port_exit(const char *why, int code);

/* host.c */
u64 host_ticks(u32 hz); /* ticks at hz since the first call (the performance counter) */
typedef struct HostTime {
    u16 year, month, day, hour, minute, second, day_of_week; /* day_of_week: 0 = Sunday */
} HostTime;
void host_local_time(HostTime *t);
void host_sleep_ms(u32 ms);
#define PORT_VBLANK_HZ 60u
void port_wait_vblank(void); /* waits for the next vertical blank (the host clock at 60 Hz) and counts it */
u32 port_vblank_count(void);  /* vertical blanks waited for: one per main-loop frame */
void *host_map_fixed(u32 addr, u32 size); /* commits read/write host memory at a fixed address; NULL if taken */

/* heap.c: a first-fit allocator over one region, 32-byte aligned blocks */
#define HEAP_ALIGN 32u
typedef struct Heap {
    const char *name;
    void *first, *end;
    u32 used, peak;
    int failed_logged;
} Heap;
void heap_init(Heap *h, const char *name, void *region, u32 size);
void heap_finish(Heap *h);
void *heap_alloc(Heap *h, u32 size);
int heap_free(Heap *h, void *ptr); /* 0 if ptr is not a live block */

/* gdi.c: the ISO 9660 volume of the disc the port booted from, for gdFs. LBAs are absolute; sectors are 2048 bytes.
 * Names are without the ";1" version; "." and ".." name the self and parent records. */
typedef struct HostDirEnt {
    u32 lba, size;
    u8 is_dir;
    char name[32];
} HostDirEnt;
int host_disc_root(u32 *lba, u32 *size);
int host_disc_read(u32 lba, u32 count, void *out); /* 0 on a read error */
int host_disc_find(u32 lba, u32 size, const char *name, HostDirEnt *out); /* case-insensitive; 0 if absent */
/* Lists the directory at lba/size without "." and "..": the count, -1 on a read error, -2 if over max. */
int host_disc_list(u32 lba, u32 size, HostDirEnt *out, int max);
int host_disc_toc(u32 *toc); /* u32[102], the high-density area's TOC in the GD-ROM form; 0 if unknown */

#endif
