/* Dreamcast port kit: the runtime's own declarations (readable game code never includes this). The game's values
 * (entry, image base, boot file) come from port_config.h, which tools/gen_port.py writes from the game's game.json. */
#ifndef PORT_H
#define PORT_H

#include <stdio.h>
#include "rd_rt.h"
#include "port_config.h"

#define GUEST_RAM_BASE 0x8c000000u
#define GUEST_RAM_SIZE 0x01000000u /* 16 MB main RAM */
#define GUEST_MIRROR_BASE 0x0c000000u /* the same RAM in its P0 form (MMU off) */
#define GUEST_UNCACHED_BASE 0xac000000u /* and in its P2 (uncached) form */

/* port_log.c: one log, stdout and port.log */
extern FILE *g_port_log;
void port_log_open(const char *path);
void port_log(const char *fmt, ...);
void port_log_flush(void);

/* guest_mem.c */
int guest_mem_init(void);
int guest_is_ram(u32 addr);
u32 guest_fold(u32 addr); /* 0x0c... -> 0x8c... */
int guest_load(u32 addr, const u8 *data, u32 size);

/* gdi.c: a Dreamcast disc image (.gdi or Redump .cue), read-only */
typedef struct PortDisc PortDisc;
PortDisc *disc_open(const char *path);
void disc_close(PortDisc *d);
int disc_boot_name(PortDisc *d, char *out, int cap); /* IP.BIN's boot file name */
/* Reads a file of the ISO 9660 volume (path relative to the root, '/' separated) into a malloc'd buffer. */
u8 *disc_read_file(PortDisc *d, const char *path, u32 *size_out);
void disc_set_run(PortDisc *d); /* the disc gdFs reads at run time (platform.h host_disc_*) */

/* sh4_rt.c */
int sh4_rt_init(const char *check_table); /* computes the fsca table; check_table: an optional file to compare */
void sh4_rt_set_fpu_mode(void);           /* MXCSR/x87 as the game's FPSCR (0x00040001: DN=1, RM=toward zero) */

/* dispatch.c */
int dispatch_install(void);
int dispatch_count(void);

/* port_stub.c */
void port_stub_report(void);
void port_stub_hot(int top); /* the most-called stubs */
/* Reads the must-work list (one function name per line, '#' comments): the first call of a listed stub ends the
 * run with exit code 3, since start-up cannot go on correctly past it. Returns 0 if the file cannot be read. */
int port_stub_must_work(const char *path);

/* port_entry.c (generated): calls the game's entry function, game.json "entry" */
void port_game_entry(void);

/* main.c */
void port_exit(const char *why, int code); /* report, then exit */

#endif
