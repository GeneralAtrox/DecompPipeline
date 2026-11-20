/* The game-owned renderer mode (native_frame.c, port/native/console_native.c): a native C or C++ game, with no console
 * emulated under it, makes and owns its own Direct3D 11 device and draws with it, and the base does the rest the same as
 * for every other port: the window and its title bar menu, the settings (frame-rate cap, aspect, resolution: nothing
 * else), Copy agent prompt (the copy icon, Help, F9), the game log, the crash reporter, the controllers, recording and
 * replay of the pads, and the launcher's path (--setup-only, --data-directory, --version).
 *
 * Who owns what:
 *   the game     its ID3D11Device and context (made by the game: D3D11CreateDevice, or an SDL_Renderer's), everything it
 *                draws, its fixed logic tick and its own data
 *   the base     the window, a flip-model swap chain on it made with the game's device (from that device's own DXGI
 *                factory), the frame target (the chosen aspect at the window's size), presenting it with bars where the
 *                shapes differ, dropping a frame the display will not take, the frame-rate cap's pacing, the log lines
 *
 * The game's side, in order:
 *   1. define pg_native_game (below): its options, its main, its end
 *   2. in main: make the device (pg_native_warp() asks for WARP in a check run), then pg_native_attach(device)
 *   3. each logic tick: pg_native_tick_begin(); host_pads_read(pads) (base.h; recorded and replayed); the game's update;
 *      pg_native_tick_end()
 *   4. each shown frame: pg_native_frame(&f) and draw into f.rtv at f.w x f.h (or draw into a texture of its own of that
 *      size and format and hand it over with pg_native_show_texture: an SDL_Renderer's target texture), then
 *      pg_native_present(). Time for the game's fixed step comes from pg_native_now_us().
 * Closing the window, --frames, the watchdog or a crash end the run through port_exit, which calls the game's end first
 * (from whichever thread ended the run).
 * The frame target's size changes with the window and the aspect setting: read it every frame, never keep it.
 * Nothing here adds a player setting or a technical switch: the mode is chosen by linking port/native (the game's build). */
#ifndef PORT_NATIVE_GAME_H
#define PORT_NATIVE_GAME_H

#include "present.h"
#include "console.h"

#ifdef __cplusplus
extern "C" {
#endif

/* What the game hands the base (defined once, by the game). */
typedef struct PgNativeGame {
    int (*option)(const char *arg, const char *value); /* the game's own options: the arguments taken, 0 if not its own */
    int (*main)(void);                                  /* the game itself: returns the exit code (0: it ended normally) */
    void (*end)(const char *why, int code);             /* the run is ending (any cause): save what must be kept; may be NULL */
    const PortAction *actions;                          /* the game's pad buttons the player assigns (Input > Controls), in
                                                           host_actions' bit order; NULL: the game reads the HostPad itself */
    unsigned n_actions;
    void (*scene)(struct Json *j);                      /* Copy agent prompt: members for the game's state, each starting
                                                           ",\n    " (scene_dump.c); may be NULL */
    int (*install)(const char *image, const char *work, const char *assets);
                                                        /* the launcher's setup for a game that extracts its disc into data
                                                           files (console.h console_install): --setup-only gives image and
                                                           work (the game ends with port_install_record), --verify-assets
                                                           gives assets; the exit code. NULL: the disc copy (install.c) */
} PgNativeGame;
extern const PgNativeGame pg_native_game;

/* The frame target of this frame: draw into rtv (or tex) at w x h. screen is where the game's original picture shape
 * (port_console.native_w x native_h) sits in it when the frame is wider or taller (x, y, w, h); wide_k and tall_k are the
 * frame's width and height over that screen's (1 at the original shape). */
typedef struct PgNativeFrame {
    ID3D11RenderTargetView *rtv;
    ID3D11Texture2D *tex; /* R8G8B8A8_UNORM, render target and shader resource */
    u32 w, h;
    u32 screen[4];
    f32 wide_k, tall_k;
} PgNativeFrame;

int pg_native_option(const char *arg, const char *value); /* --fps N, --present and --interp-frames (taken, no effect),
                                                             then the presenter's (gfx_option: window, aspect, size,
                                                             headless, warp); the arguments taken, 0 if none */
int pg_native_warp(void);                                 /* --warp: make the device on WARP (the same pictures on any machine) */
int pg_native_attach(ID3D11Device *device);               /* the window, the swap chain and the frame target; 0 on failure (logged) */
int pg_native_frame(PgNativeFrame *f);                    /* 0 before pg_native_attach */
int pg_native_show_texture(ID3D11Texture2D *src);         /* copied whole into the frame target: src must be w x h of this
                                                             frame and R8G8B8A8 (UNORM or TYPELESS); 0 (logged once) if not */
void pg_native_present(void);                             /* the frame onto the window, the polls, the cap's wait */
void pg_native_tick_begin(void);
void pg_native_tick_end(void);                            /* one logic tick done: port_vblank_count moves on */
u32 pg_native_ticks(void);
u64 pg_native_now_us(void);                               /* the clock for the game's step: real time, or the fixed clock's
                                                             (one tick of port_console.tick_hz per presented frame) */
unsigned pg_native_fps(void);                             /* the frame-rate cap (Display > Frame-rate cap): 0 none */
void pg_native_set_fps(unsigned fps);
int pg_native_capture_bmp(const char *path);              /* the frame target as drawn, to a 24-bit BMP; 0 on failure */
void pg_native_split_report(void);                       /* at exit: PG_DRAW_SPLIT's line (native_frame.c); nothing when unset */
void pg_native_release(void);                             /* the base lets the game's device go (a game ending its device before the run ends) */

/* What the game wants from the keyboard and mouse beside its pads, said each tick (any kit's game may use them). What came
 * is taken at the tick's pad read (host_pads_read; never in an in-between frame) and is recorded (the recording's EVENTS
 * section, base/session_events.c); a replay gives the recording's instead and ignores the window's.
 *   text   pg_native_text(1) while the game's own text field is open: the keyboard types into it (SDL's text input, so
 *          any layout), the layout's keys press no pad button (pads and --input still do), Esc is the field's (Guide or
 *          the pointer open the title bar; Alt+Enter, F9 and F11 stay the Core's). pg_native_text_take gives UTF-8,
 *          whole characters and NUL-ended, with '', '
', '	' and 0x1b for Backspace, Enter, Tab and Esc; Ctrl+V or
 *          Shift+Insert paste into the same stream. Returns the bytes given, 0 when none is left this tick.
 *   mouse  pg_native_mouse_mode(PG_MOUSE_POINTER or PG_MOUSE_LOOK): the mouse is the game's (the layout's mouse sources,
 *          the middle-drag look, mouse steer and the menu pointer press nothing); PG_MOUSE_LAYOUT, the default, as before.
 *          pg_native_mouse gives this tick's: dx, dy the moves in the window's pixels (right, down: the game's own
 *          sensitivity applies, the Core adds none), x, y the pointer in the original picture (native_w x native_h),
 *          buttons held and pressed this tick (bit 0 left, 1 right, 2 middle), wheel notches (up positive), captured
 *          (0 while the player has the mouse: the focus lost, a menu or the title bar open, a modal, Alt held; the next
 *          click in the picture gives it back and is not the game's). Returns 1 in POINTER or LOOK, else 0 and zeros.
 *   menu   pg_native_menu_take: a pick of the game's own menu commands (console.h PortMenuItem tag, game.json
 *          menu_commands): 1 with its tag and index, each call, until none is left this tick. pg_native_menu_list gives
 *          a runtime list its labels (copied; '>' in one makes nested popups).
 * Known limits: IME composition (Chinese, Japanese, Korean input) is not covered; netplay (net_inputs.c) carries pads
 * only, so text, the game's mouse and its menu picks would go out of step online. */
void pg_native_text(int active);
int pg_native_text_take(char *utf8, int cap);
void pg_native_mouse_mode(int mode);
int pg_native_mouse(PgNativeMouse *m);
int pg_native_menu_take(int *tag, int *index);
void pg_native_menu_list(int tag, const char *const *labels, int n);

#ifdef __cplusplus
}
#endif

#endif
