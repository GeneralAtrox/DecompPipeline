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
const char *sat_bup_where(void);     /* and its name (sat_heads.c keeps its own save beside it) */
extern u32 sat_movie_fn; /* sat_movie.c: the game's movie routine the host plays instead (0: none, or emulated in this run) */
int sat_movie_play(const TcArgs *a, u32 *ret); /* 1 and the routine's result when the host played it */
void sat_movie_start(int play_run, int audio_on);
int sat_movie_option(const char *arg, const char *value); /* --movie-native on|off|always, --movie-dump DIR[,N] */
int sat_movie_get(void);
void sat_movie_set(int on);
extern u32 sat_logo_fn; /* sat_logos.c: the movie routine while the opening logos are skipped (0: off) */
int sat_logos_skip(const TcArgs *a, u32 *ret); /* 1 and the skipped answer when this call plays a logo to skip */
void sat_logos_start(int play_run);
void sat_logos_option(const char *value); /* --skip-logos on|off */
int sat_logos_on(void);
u32 sat_movie_find(const char *path, u32 *sectors); /* sat_movie.c: a file's first frame on the disc (0: none) */
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

/* sat_call_feat.c: the feature hooks sat_call.c's call runs around a game function, in their order */
typedef struct { TcArgs team, lvl, head; } SatFeat;                   /* the arguments a hook hands the function instead */
const TcArgs *sat_feat_enter(u32 fn, const TcArgs *a, SatFeat *t); /* before it: the arguments it runs with */
u32 sat_feat_leave(u32 fn, const TcArgs *a, u32 r);             /* after it: its result */

/* sat_team.c: team fights, two against two (game.json team_fight; Game menu) */
extern int sat_team_on;
void sat_team_option(const char *value);                        /* --team-fight on|off */
void sat_team_start(void);                                      /* at start: the replay's choice or the one asked for */
const TcArgs *sat_team_enter(u32 fn, const TcArgs *a, TcArgs *tmp); /* sat_call.c, while sat_team_on: before a call */
u32 sat_team_leave(u32 fn, const TcArgs *a, u32 r);             /* and after it: its result */
void sat_team_end(void);
void sat_team_once(void);                                       /* sat_menus.c: on for this fight only */
void sat_team_done(void);                                       /* and off again (the hub is entered) */
/* sat_ladder.c: 2 VS 2 on the game's FIGHT ladder (game.json menus fight_choice "ladder") */
void sat_ladder_arm(void);                                      /* sat_menus.c: the next fight set-up is two against two */
void sat_ladder_enter(u32 fn, const TcArgs *a, int choosing);   /* sat_menus.c, around each call; choosing: 2 VS 2's select */
void sat_ladder_leave(u32 fn, const TcArgs *a, int choosing);
void sat_ladder_test_option(const char *value);                 /* --ladder-test-weak */
void sat_ladder_rank_option(const char *value);                 /* --ladder-test-rank */
/* sat_heads.c: new head types after the game's own, one robot each (game.json heads) */
extern int sat_heads_on;
extern u8 sat_heads_mark[256];                                  /* the hooked functions, by (address >> 1) & 255: a call checks one byte */
void sat_heads_option(const char *value);                       /* --new-heads on|off */
void sat_heads_start(int play_run);                             /* at start: the replay's choice or the one asked for */
const TcArgs *sat_heads_enter(u32 fn, const TcArgs *a, TcArgs *tmp); /* sat_call.c, while sat_heads_on: before a call */
u32 sat_heads_leave(u32 fn, const TcArgs *a, u32 r);           /* and after it: its result */
void sat_heads_bup(u32 name, int wrote);                        /* sat_bup.c: the game wrote (1) or read (0) a file */

/* sat_menus.c: the kit's entries in the game's own menus (game.json menus) */
extern int sat_menus_on;
void sat_fight_choice_option(const char *value);                /* --fight-choice on|off */
void sat_menus_start(int play_run);
int sat_menus_enter(u32 fn, const TcArgs **a, TcArgs *tmp, u32 *ret); /* sat_call.c, while sat_menus_on: 1 when answered */
u32 sat_menus_leave(u32 fn, const TcArgs *a, u32 r);
void sat_menus_end(void);
void sat_online_menu_option(const char *value);                 /* --online-menu on|off: JOIN GAME and HOST GAME in the hub */
u32 sat_menu_frame(u32 hub_row, const u32 box[3], const char *const *lines, int n, int sel); /* a kit box's frame; sel < 0: no bar */
void sat_menu_keep(const u32 box[3], int n, int put);           /* the cells under it, kept and put back */
void sat_menu_sound(void);                                      /* the game's cursor sound */

/* sat_lobby.c: the game's own online screens (game.json netplay "menu": "in-game") */
extern int sat_lobby_match;                                     /* --net-menu on: a match they started */
void sat_online_note_option(const char *value);                 /* --online-note WHY: shown once in the hub */
void sat_join_address_option(const char *value);                /* --join-address ADDRESS: JOIN GAME starts with it */
void sat_text_test_option(const char *value);                   /* --text-test KEYS: typed into JOIN GAME's field (a test) */
void sat_lobby_note(u32 hub_row, const u32 box[3]);
void sat_lobby_join(u32 hub_row, const u32 box[3]);
void sat_lobby_host(u32 hub_row, const u32 box[3]);
void sat_lobby_back(const char *note);

/* sat_online.c: a game the game's own online screens started, while it runs (hosted in the background, status, notices) */
void sat_online_begin(const char *addr);           /* sat_net_setup.c: the address to show */
void sat_online_frame(u32 f, int port2);           /* sat_net.c: each frame's pads decided; port 2 in or not */
void sat_online_hub(u32 x, u32 y, int on);         /* sat_menus.c: the hub drawn (its line under the items), or left */
void sat_online_call(u32 fn);                      /* sat_menus.c: a call of the game's returned */
void sat_online_choose(void);                      /* sat_menus.c: STOP HOSTING */
void sat_online_refused(void);                     /* the host turned away another version */
int sat_menus_online(void);                        /* sat_menus.c: the hub's online lines are on */
void sat_menu_line(u32 x, u32 y, const char *s, u32 width, u32 slot); /* sat_menus.c: small type, palette slot (~0u: the hub's) */

/* sat_panel.c: the game's half-transparent menu panel drawn by the presenter (game.json menu_panel) */
extern int sat_panel_on;
extern u32 sat_panel_seen;
void sat_panel_option(const char *value);                      /* --menu-panel original|shader */
void sat_panel_start(int play_run);
void sat_panel_mark(u32 pmod, u32 colr);                       /* sat_vdp1.c: each command */
int sat_panel_frame(void);                                     /* sat_vdp2.c: this picture may show the panel */
u32 sat_panel_dot(u32 (*word)(u32 x, u32 y), u32 x, u32 y, u32 rgb, int spr_pr, int pn, u32 under, u32 left);
u32 sat_panel_shade(u32 c);                                    /* a marked dot as shown */                          /* a match they started that cannot go on: alone again */
u32 sat_pad_seats(u32 data[4]);                                 /* sat_input.c: all four pads; bit p: pad p + 1 is there */

/* sat_level*.c: an edited arena (game.json level_edit; Game > Edited arena) and the level editor's Saturn side */
extern int sat_level_on;
int sat_level_option(const char *a, const char *v);             /* --level-mod on|off, --level-play on, --level-file, --level-test-* */
void sat_level_start(void);                                     /* at start: the replay's edits or the file's */
void sat_level_loaded(void);                                    /* the image is in work RAM (--level-test-save runs here) */
const TcArgs *sat_level_enter(u32 fn, const TcArgs *a, TcArgs *tmp); /* sat_call.c, while sat_level_on: before a call */
u32 sat_level_leave(u32 fn, const TcArgs *a, u32 r);            /* and after it: its result */
void sat_level_end(void);

#endif
