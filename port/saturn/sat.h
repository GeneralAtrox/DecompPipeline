/* Saturn port kit: the SH-2 side's own declarations (port/saturn/*). A Saturn game's translated C includes only the
 * judge's tc_rt.h (rd/wr, tc_call, ...); this header is for the kit's files. No game names or guest code addresses:
 * a game's values come from port_config.h (tools/gen_saturn.py from game.json). */
#ifndef SAT_H
#define SAT_H

#include "tc_rt.h"

#define SAT_WRAM_H_BASE 0x06000000u
#define SAT_WRAM_SIZE 0x00100000u
#define SAT_WRAM_L_BASE 0x00200000u

/* port_log is the base's (base.h); port_exit ends the run: the base's reports, then this kit's (sat_main.c) */
#include "base.h"
#include "autoload.h"
void port_exit(const char *why, int code);

/* sat_mem.c: the SH-2's bus. Memory is big-endian; a hardware address goes to the region that claims it */
typedef u32 (*SatRead)(u32 phys, u32 size);
typedef void (*SatWrite)(u32 phys, u32 size, u32 value);
void sat_mem_init(void);
void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr); /* physical addresses, 64 KB granularity */
void sat_map_regs(u32 lo, u32 hi, const char *name); /* a plain register file that logs each first access */
u8 *sat_ram_h(void);
u8 *sat_ram_l(void);
void sat_load(u32 addr, const u8 *data, u32 size); /* into work RAM (high or low) */
u32 sat_dma_rd32(u32 addr);                         /* a word read by the SCU's DMA: not a CPU's poll */
u32 sat_peek32(u32 addr);                           /* a word without side effects (RAM only, else 0) */
void sat_mem_report(void);                          /* hardware regions touched, at exit */
u32 sat_frt_count(void);
void sat_mem_cpu(int id);                           /* which SH-2's on-chip registers the bus shows */
void sat_mem_icf(int cpu);                          /* sets that CPU's FTCSR.ICF (the other CPU signalled it) */                            /* the free-running timer, 16 bits */

/* sat_call.c: calls between translated functions and the interrupt model */
typedef struct {
    u32 addr;
    u32 (*fn)(u32 a0, u32 a1, u32 a2, u32 a3);
    u32 frame; /* bytes its body can push below its entry sp (tools/gen_saturn.py) */
} SatFn;
extern const SatFn sat_fns[];
extern const int sat_nfns;
extern u32 sat_entry_sp; /* the sp a translated function takes as its own on entry (the bring-up form's r15) */
void sat_call_init(void);
u32 tc_tail(u32 target, const TcArgs *args);   /* a jump to another function as the last thing a function does */
u32 tc_call_sp(u32 target, const TcArgs *args, u32 sp);   /* a call from a translated function at r15 = sp: the callee starts there, as on the CPU */
u32 tc_tail_sp(u32 target, const TcArgs *args, u32 sp);   /* the same, from a frame that is still open: the target runs with r15 = sp */
u32 sat_in_r(int i); /* r0..r3 as the caller held them at this call: a function that never writes r0 returns it unchanged; SGL's inner routines read r1..r3 */
void sat_pr_popped(void);                         /* the running function restored pr from its stack */
void sat_wide_cull_at(u32 fn);                  /* sat_wide.c: a function entered: the game's culling widened when it reads it */
void sat_window_back(u32 sp);                      /* the call that came back to a label hands the caller its stack words back */
int sat_pr_took(void);                            /* the function just called did (and clears the mark) */
u32 sat_call(u32 sp, u32 addr, u32 a0, u32 a1, u32 a2, u32 a3); /* runs a guest function on a stack at sp */
u32 sat_call_here(u32 addr, u32 level);                         /* an interrupt handler on a fresh frame, registers kept */
void sat_stub_report(void);
int sat_cpu_id(void);                                           /* 0 master, 1 slave */
void sat_cpu_yield(void);                                       /* the other SH-2 takes a turn, if it has a program */
void sat_poll_hint(u32 every);
typedef u32 (*SatNative)(u32, u32, u32, u32);
SatNative sat_bios_native(u32 addr); /* a BIOS service's native function, or 0 */
void sat_bios_init(void);            /* the BIOS' service pointers in work RAM (after the image is loaded) */
SatNative sat_bup_native(u32 addr);  /* sat_bup.c: the backup-RAM library's services, or 0 */
void sat_bup_init(void);             /* its pointers in work RAM (called by sat_bios_init) */
void sat_bup_path(const char *path); /* --save: the host file that holds the console's backup memory */
extern u32 sat_movie_fn; /* sat_movie.c: the game's movie routine the host plays instead (0: none, or emulated in this run) */
int sat_movie_play(const TcArgs *a, u32 *ret); /* 1 and the routine's result when the host played it */
void sat_movie_start(int play_run, int audio_on);
int sat_movie_option(const char *arg, const char *value); /* --movie-native on|off|always, --movie-dump DIR[,N] */
int sat_movie_get(void);
void sat_movie_set(int on);
void sat_bup_auto_load(int play_run); /* at the start: the game's load path driven when its file is in the save (autoload.c) */
void sat_scsp_init(void);                                       /* sound RAM and the SCSP's registers */
u8 *sat_sound_ram(void);
void sat_scsp_cpu(int on);                                      /* SMPC SNDON / SNDOFF */
void sat_scsp_frame(void);                                      /* once a frame: the absent 68K answers the SH-2's mailboxes */
void sat_vblank_wait(void);                                /* a loop on a flag only the vertical-blank handler sets */
void sat_idle_turn(void);                                  /* the game's main loop came round (game.json "main_loops") */
void sat_set_vblank_wait(int on);
void sat_busy_poll(void);                                  /* a read that looks like a wait on the other CPU */
void sat_slave_off(void);                                       /* SMPC SSHOFF: the slave stops; MINIT starts it afresh */
void sat_minit(void);                                           /* MINIT written: the master signals the slave */
void sat_sinit(void);                                           /* SINIT written: the slave signals the master */
void sat_set_slave_sp(u32 sp);
void sat_set_trace(u32 n);                                      /* log the next n calls (--trace) */
void sat_set_trace_vb(u32 vb, u32 n);                           /* log n calls from vertical blank vb (--trace-vb) */
void sat_set_watch_saved(u32 on);                                  /* log callees that hand back a changed r8..r14 (--watch-saved) */
void sat_set_trace_from(u32 addr, u32 n);                      /* log n calls from the first call of addr (--trace-from) */
void sat_set_profile(int on);                                   /* --profile: sample the master each vertical blank */
void sat_sample(void);
void sat_profile_report(void);
void sat_where(void);                                           /* logs the running call chain and the last calls */
void sat_irq_raise(u32 vector, u32 level);                      /* a pending interrupt, delivered at the next safe point */
void sat_irq_poll(void);                                        /* delivers pending interrupts the mask allows */

#endif
