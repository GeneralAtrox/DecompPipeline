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
/* The SH-4 store-queue window, 0xe0000000..0xe3ffffff: with QACR0/1 set to area 3, as the SDKs leave them, a
 * store-queue write to 0xe0000000 + offset lands in area 3 at offset, where the 16 MB of RAM repeats four times.
 * Games write their PowerVR vertex lists this way (an uncached list address XOR 0x4c000000) and read them back
 * through the cached form (XOR 0x6c000000). */
#define GUEST_SQ_BASE 0xe0000000u
#define GUEST_SQ_VIEWS 4u

/* port_log.c: one log, stdout and port.log */
extern FILE *g_port_log;
void port_log_open(const char *path);
void port_log(const char *fmt, ...);
void port_log_flush(void);
const char *port_log_path(void); /* the log file, "" if none */

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

/* host.c (the rest is in platform.h) */
void host_set_args(int argc, char **argv); /* the command line, for host_arg */
char *const *host_args(int *argc);         /* that list (the host's: defaults, settings, command line) */
void host_set_save_dir(const char *dir);   /* --save-dir */
void host_extra_state(void);               /* registers the virtual clock with frame_extra.c */

/* sh4_rt.c */
int sh4_rt_init(const char *check_table); /* computes the fsca table; check_table: an optional file to compare */
void sh4_rt_set_fpu_mode(void);           /* MXCSR/x87 as the game's FPSCR (0x00040001: DN=1, RM=toward zero) */
void sh4_rt_extra_state(void);            /* registers the register file with frame_extra.c */

/* port_libm.c */
void port_libm_extra_state(void); /* registers rand's state with frame_extra.c */
u32 port_rand_state(void);         /* rand's state (lockstep.c) */

/* frame_extra.c: --present tick|interp, --interp-frames N, --interp-verify 1; 0 if not ours */
int extra_option(const char *arg, const char *value);

/* dispatch.c */
int dispatch_install(void);
int dispatch_count(void);
void dispatch_report(void); /* the calls through guest pointers, most called first */
void dispatch_thunks(u32 lo, u32 hi); /* jump thunks at the function starts of guest code lo..hi (P1) */

/* rom_read.c: finishes a read of the boot ROM area as if the ROM held zeros; 0 if it cannot */
struct _EXCEPTION_POINTERS;
int rom_read(struct _EXCEPTION_POINTERS *ep);

/* gfx/gfx_input.c: --input NAME@VBLANK[+N],.. (a scripted press, held N vertical blanks, default 6); 0 if not ours */
int input_option(const char *arg, const char *value);
/* gfx/gfx_audio.c: --audio on|off, --audio-dump FILE.wav; 0 if not ours */
int audio_option(const char *arg, const char *value);

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
