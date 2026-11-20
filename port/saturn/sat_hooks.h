/* The table a Saturn game registers with the kit: what the game adds around the kit's own machine (its menus' extra
 * screens, a team fight, new part types, ...). The kit holds no game code: every entry is optional (NULL: nothing), and
 * a game with no table of its own links port/saturn/none/sat_hooks_none.c, an empty one. A game supplies
 * `const SatGameHooks sat_game_table` in its own source (build_saturn.py --game-src DIR; Robo Pit: port/game/rp_hooks.c).
 *
 * The switches the hooks use on the hot path (sat_team_on, sat_heads_on, sat_heads_mark, sat_lobby_match) are the kit's
 * (sat_hooks.c): a call into a hook costs what the flag test cost before, and nothing when the flag is off.
 *
 * Nearest existing: console.h's PortConsole (the base's table of a console kit's entries) - this is the same shape one
 * level down, for a game inside a console kit. */
#ifndef SAT_HOOKS_H
#define SAT_HOOKS_H

struct SatTextSprite; /* sat_text.h */
struct SatTextItem;
struct SatTextCells;

typedef struct SatGameHooks {
    int (*option)(const char *name, const char *value);            /* a command-line option of the game's: 1 taken, 0 not its */
    void (*start)(int play_run);                                   /* at start, after the kit's own parts; play_run: an unscripted run */
    void (*end)(void);                                             /* at exit */
    const TcArgs *(*team_enter)(u32 fn, const TcArgs *a, TcArgs *tmp); /* around a game function while sat_team_on: before it */
    u32 (*team_leave)(u32 fn, const TcArgs *a, u32 r);             /* and after it */
    const TcArgs *(*heads_enter)(u32 fn, const TcArgs *a, TcArgs *tmp); /* while sat_heads_on, for a function sat_heads_mark marks */
    u32 (*heads_leave)(u32 fn, const TcArgs *a, u32 r);
    void (*heads_bup)(u32 name, int wrote);                        /* the game wrote (1) or read (0) a save file, while sat_heads_on */
    void (*screen_grab)(void);                                     /* sat_screen.c: a kit screen was composed (the game's pictures taken) */
    void (*team_once)(void);                                       /* sat_menus.c: a team fight for this fight only */
    void (*team_done)(void);                                       /* and off again (the hub is entered) */
    void (*ladder_arm)(void);                                      /* the next fight's set-up is two against two */
    void (*ladder_enter)(u32 fn, const TcArgs *a, int choosing);   /* around each game call from the hub's fight menu */
    void (*ladder_leave)(u32 fn, const TcArgs *a, int choosing);
    int (*tlobby_run)(void);                                       /* the team lobby's screens: 1 a fight picked, 0 back to the hub */
    void (*lobby_note)(u32 hub_row, const u32 box[3]);             /* the hub's online screens: a match that ended, JOIN, HOST */
    void (*lobby_join)(u32 hub_row, const u32 box[3]);
    void (*lobby_host)(u32 hub_row, const u32 box[3]);
    void (*lobby_back)(const char *note);                          /* a match the game's own menu started goes back to it, saying why */
    void (*pad)(u32 data[2]);                                      /* sat_input.c: ports 1 and 2 as the game is about to read them
                                                                      (sat_pad_data's form), after the host's pads: the game may
                                                                      change them (Dragon Force's Quick dialogue); not online */
    const struct PortMenuItem *(*menu)(void);                             /* console_sat.c: one more menu entry of the game's, or NULL */
    int (*wide)(void);                                             /* sat_wide.c, each vertical blank: 1 this picture may be composed
                                                                      wider than the screen, 0 it keeps the screen's width (a 2D
                                                                      screen with no more art). Set, the game is a widescreen one
                                                                      (console_widens) without game.json "widescreen" (Dragon Force) */
    void (*frame)(u32 *px, u32 w, u32 h, u32 ox);                  /* sat_vdp2.c: the picture just composed (w dots a line, h lines,
                                                                      the screen ox dots in from the left: sat_wide.c), each
                                                                      compose, in-between frames too: the game may draw over it
                                                                      (Saturn Bomberman's pause menu) */
    int (*text)(const struct SatTextSprite *s);                    /* sat_text.c, Clearer text on: a textured VDP1 sprite about to
                                                                      be drawn; nonzero (a tag of the game's): it is lettering, shown
                                                                      sharp over the picture (Dragon Force) */
    int (*text_image)(const struct SatTextItem *it, int k, u32 *out); /* that lettering's picture k times its size, premultiplied
                                                                      0xAARRGGBB; 0: the kit's own (A3 of its dots) */
    int (*text_cells)(const struct SatTextCells *c);               /* sat_text_v2.c, Clearer text on: a scroll plane's cell (tag
                                                                      0), or a block of cells given that tag; nonzero: lettering
                                                                      (the block's tag), shown sharp over the picture as text's.
                                                                      NULL, once a picture: 0 nothing to look at here, else
                                                                      the screen (answers are kept per screen) */
} SatGameHooks;

extern const SatGameHooks sat_game_table; /* the game's (or the empty one) */
#define SAT_G(f) (sat_game_table.f)

static __inline int sat_game_option(const char *a, const char *v) { return SAT_G(option) ? SAT_G(option)(a, v) : 0; }
static __inline void sat_game_start(int play_run) { if (SAT_G(start)) SAT_G(start)(play_run); }
static __inline void sat_game_end(void) { if (SAT_G(end)) SAT_G(end)(); }
static __inline void sat_game_team_once(void) { if (SAT_G(team_once)) SAT_G(team_once)(); }
static __inline void sat_game_team_done(void) { if (SAT_G(team_done)) SAT_G(team_done)(); }
static __inline void sat_game_ladder_arm(void) { if (SAT_G(ladder_arm)) SAT_G(ladder_arm)(); }
static __inline void sat_game_ladder_enter(u32 fn, const TcArgs *a, int c) { if (SAT_G(ladder_enter)) SAT_G(ladder_enter)(fn, a, c); }
static __inline void sat_game_ladder_leave(u32 fn, const TcArgs *a, int c) { if (SAT_G(ladder_leave)) SAT_G(ladder_leave)(fn, a, c); }
static __inline int sat_game_tlobby_run(void) { return SAT_G(tlobby_run) ? SAT_G(tlobby_run)() : 0; }
static __inline void sat_game_lobby_note(u32 r, const u32 b[3]) { if (SAT_G(lobby_note)) SAT_G(lobby_note)(r, b); }
static __inline void sat_game_lobby_join(u32 r, const u32 b[3]) { if (SAT_G(lobby_join)) SAT_G(lobby_join)(r, b); }
static __inline void sat_game_lobby_host(u32 r, const u32 b[3]) { if (SAT_G(lobby_host)) SAT_G(lobby_host)(r, b); }
static __inline void sat_game_lobby_back(const char *note) { if (SAT_G(lobby_back)) SAT_G(lobby_back)(note); }
static __inline void sat_game_heads_bup(u32 name, int wrote) { if (SAT_G(heads_bup)) SAT_G(heads_bup)(name, wrote); }
static __inline void sat_game_pad(u32 data[2]) { if (SAT_G(pad)) SAT_G(pad)(data); }
static __inline const struct PortMenuItem *sat_game_menu(void) { return SAT_G(menu) ? SAT_G(menu)() : NULL; }
static __inline void sat_game_frame(u32 *px, u32 w, u32 h, u32 ox) { if (SAT_G(frame)) SAT_G(frame)(px, w, h, ox); }
static __inline int sat_game_wide_set(void) { return SAT_G(wide) != NULL; }
static __inline void sat_game_screen_grab(void) { if (SAT_G(screen_grab)) SAT_G(screen_grab)(); }

#endif
