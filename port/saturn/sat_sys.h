/* Saturn port kit: the machine's clock and interrupts (sat_sys.c), shared by the device files. */
#ifndef SAT_SYS_H
#define SAT_SYS_H

#include "sat.h"

u32 sat_vblank_count(void);          /* vertical blanks since boot */
u32 sat_scanline(u32 total, u32 *along); /* the raster position from the real clock */
void sat_idle_ask(void);             /* a question that only time answers: enough of them in a frame is a wait */
void sat_spin(void);                 /* tc_spin: let the machine advance to the next vertical blank */
u32 sat_sys_hz(void);                /* the frame rate */
void sat_cd_frame(void);             /* once a frame: the drive delivers sectors */
void sat_sys_init(void);
int sat_sys_virtual(void);                 /* --fixed-clock: the machine runs on a virtual clock (a run repeats exactly) */
u64 sat_clock_ticks(u32 rate);        /* time since start-up in 1/rate second */
void sat_sys_set(u32 hz, int fast); /* the frame rate and the test-only no-wait mode */
int sat_sys_det(void);           /* --det: vertical blanks and the timer follow the machine's progress, not the real clock */
long long sat_inb_due(void);     /* sat_inb.c: when the next in-between frame is due (QueryPerformanceCounter), 0 none */
void sat_inb_poll(long long now, long long next); /* sat_inb.c: draws it if it is due and there is time */
void sat_sys_set_det(int on);
u32 sat_det_frt(u32 shift);
int sat_black_possible(void);    /* a play run with fast loads: black screens may run without waiting */
void sat_black_frame(int black); /* sat_video.c, each composed picture: black or not */
int sat_black_fast(void);        /* this blank's picture is black and silent: the next blank comes without waiting */
int sat_sys_fast(void);          /* the no-wait test mode: frames are not paced by the real clock */
void sat_vblank_counted(void);
void sat_frame_limit_check(void);
u32 sat_scu_deliverable(void);       /* SCU interrupt bits pending and unmasked */
void sat_scu_ack(u32 bit);           /* delivery clears the bit */
void sat_set_vblank_handler(u32 guest_addr); /* the game's vblank-in handler, registered via the SCU vector table */
u32 sat_sr(void);
extern u32 sat_rd_ring[8], sat_rd_n; /* the last guest reads, for the stuck-wait reports (sat_where.c) */
void sat_set_gbr(u32 v);

/* Devices call these as they run */
void sat_vblank_in(void);            /* sat_sys.c calls each frame: VDP2 latch, SCU interrupt status */
void sat_perf_wait_begin(void);      /* sat_perf.c: the time the thread waits for the real clock is not frame cost */
void sat_perf_wait_end(void);
void sat_perf_compose(long long ticks);
void sat_perf_show(long long ticks);
void sat_perf_vblank(void);
void sat_perf_v1(long long ticks);
void sat_perf_audio(long long ticks);  /* the 68000 and the SCSP's voices for one frame */
void sat_perf_phase(int phase);      /* 1 while the software picture is composed (the --sample thread reads it) */
void sat_perf_sample(int on);        /* --sample */
void sat_perf_sample_report(void);
void sat_video_pacing_report(void); /* sat_video.c: picture pacing, to the log */
void sat_perf_poll(void);            /* the master polls: a stretch of polls with no guest write between is the game waiting */
extern u32 sat_wr_n;                 /* guest writes so far (sat_perf.c counts the waits by it) */
void sat_perf_report(void);
void sat_present(void);              /* end of a frame: show the framebuffer (sat_video.c) */
void sat_video_init(void);
int sat_video_dump_option(const char *arg, const char *value); /* --dump-frames, --dump-dir (sat_video.c) */
int sat_video_headless(void);
void sat_video_options(int headless, u32 shot_vb, const char *shot_path, u32 every); /* --headless, --shot VB FILE [--shot-every N] */
extern u32 sat_shot_max;                                                             /* --shot-max N (60) */
void sat_scu_init(void);
void sat_smpc_init(void);
void sat_smpc_clock_start(int play_run); /* the clock: the host's in a play run, else 1997-01-01 (sat_smpc.c) */
void sat_cd_init(const char *cue);
void sat_vdp_init(void);
void sat_scu_state(u32 *ims, u32 *ist); /* the mask and pending registers, for the watchdog's report */
void sat_scu_set_ims(u32 mask);
void sat_scu_raise(u32 bit);         /* an SCU interrupt source */
int sat_quit_requested(void);
void sat_pad_key(unsigned vk, int down);  /* sat_pad.c: a key message from the window */
void sat_pad_focus_lost(void);
void sat_pad_script(const char *spec);          /* --input NAME@V[+N],...: scripted presses */
u32 sat_pad_data(u32 data[2]);            /* each port's two data bytes, 0 = pressed; returns bit p: port p + 1 has a pad */

#endif
