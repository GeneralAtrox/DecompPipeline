// The host's own C++ interfaces (host/*.cpp). Nothing here is visible to the kit's C side.
#pragma once

union SDL_Event;

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

struct PortMenuItem; // console.h

namespace host {

// host_data.cpp: <exe folder>\data, or PORT_DATA_DIR. Saves go in data\saves (base/clock.c), logs and crash
// dumps in data\logs, the settings in data\settings.txt.
std::filesystem::path data_dir();
void set_data_dir(const std::filesystem::path &dir); // --data-directory: overrides both
std::filesystem::path logs_dir(); // created on first use
// A play run's logs: port.log becomes port.1.log (and port.1.log port.2.log, up to port.8.log) so that the last runs
// stay. A previous log with no END line (runtime/game_log.h) is marked, and the autosaved recording beside it kept as
// killed-<time>.ptrec; game_log_previous gets the note, which the new log's first lines repeat.
void rotate_logs();
void prepare_data_dir();          // creates the folders; moves a saves folder from beside the exe into data\saves

// host_settings.cpp: the saved options. Each "key=value" line of the file is the option "--key value", placed
// before the command line, so the command line wins.
std::vector<std::string> settings_args(const std::filesystem::path &file);
extern std::filesystem::path g_file; // the settings file (host_settings.cpp), empty when none
std::string trim(const std::string &s);
void settings_use(const std::filesystem::path &file);                // the file settings_set writes (play runs)
bool settings_is_json(const std::filesystem::path &file);          // an older build's JSON settings, never written
void settings_legacy(const std::filesystem::path &file);            // kept for port_settings_legacy
void settings_set(const std::string &key, const std::string &value); // the menu's choices, kept for the next run

// host_window.cpp, for the menu: the window's mode and the host's own preferences
bool window_fullscreen();
void window_set_fullscreen(bool on);
void window_set_scale(int n); // a window N x the console's picture; 0: fit
int window_scale();           // N, 0 for fit, -1 for a WxH size
struct Prefs {
    bool fps_counter = false, pause_unfocused = false, mute_unfocused = false;
};
Prefs &prefs();

// host_menu.cpp: the Win32 menu bar on the SDL window, after Lodoss's desktop_stage_menu (MIT, the user's own).
// Shown while windowed, hidden in full screen.
void menu_attach(void *hwnd);
void menu_show(bool on);
// The menu's extra entries, the console's then the game's (host_menu_extra.cpp), and one applied (a choice: its n-th).
const PortMenuItem *menu_extras(unsigned *count);
void menu_extra_apply(unsigned i, unsigned n);
// host_window.cpp: the window keeps the pointer (relative mode) while the keyboard and mouse layout wants it
// (input_kbm_capture_wanted) and lets go at once when it does not; the menu calls it as it opens and closes.
void mouse_capture_sync();
// host_modal.cpp: runs poll (the window's events) on a fiber of its own. A modal loop inside it (a menu held open, the
// title bar dragged) lends the game's fiber one tick per timer, to its next gfx_pump, so play, sound and frames go on.
void modal_pump(void (*poll)());
// host_mouse.cpp: a mouse button, move or wheel event, for the keyboard and mouse layout (input_kbm.c) or the game's own
// mouse (input_kbm_mouse.c), and SDL's text input for the game's text field (host_text.cpp).
void mouse_event(const SDL_Event &e);
// host_mouse.cpp: whether the window keeps the pointer (relative mode) now (mouse_capture_sync); Alt held lets the
// game's mouse go, and SDL's text input follows the game's text field.
bool mouse_capture_want(void *sdl_window);
// host_menu_tagged.cpp: the game's own commands (PortMenuItem tag): a tagged submenu's popup (nested paths, long lists
// split, a runtime list built again when it changes, before a top menu opens); a pick of one (false: not its id).
void *menu_tagged_popup(unsigned item);
void menu_tagged_sync(void *bar_menu, void *opening);
bool menu_tagged_command(unsigned id);
// host_menu_extra.cpp: a tagged entry's labels (static or runtime) and their change count; the tree they make.
struct MenuNode {
    std::string label;
    int index = -1;     // a leaf: the entry's index in the labels
    bool group = false; // "17-32": a long list's part, which a --menu-pick path may leave out
    std::vector<MenuNode> kids;
};
unsigned menu_entries(const PortMenuItem &e, std::vector<std::string> *labels);
MenuNode menu_tree(const std::vector<std::string> &labels);
// host_menu_kbm.cpp: Input > Keyboard and mouse (an HMENU as void *): built; its marks set as a menu opens; a choice done
// and kept in the settings (false: not one of its commands).
void *kbm_menu();
void kbm_menu_sync(void *bar);
bool kbm_menu_command(unsigned id);

// host_copy_button.cpp: the bar's "Copy agent prompt" icon, and the command that the icon, the Help menu's entry
// and F9 share (a scene dump: runtime/scene_dump.c). message: true if it handled msg (then *result)
constexpr unsigned kCopyAgentPrompt = 0x7207;
void copy_button_attach(void *hwnd, void *bar);
bool copy_button_message(void *hwnd, unsigned msg, uintptr_t wp, intptr_t lp, intptr_t *result);

// host_counter.cpp: the bar's frame-rate counter (Display > Frame-rate counter): frames shown a second and the game's
// own rate against its target, the game's figure red under 98% of it. counter_show may be called from any thread
// (host_window.cpp's frame_counter, once a second); on false the counter is hidden.
constexpr unsigned kFrameCounter = 0x7208;
void counter_attach(void *hwnd, void *bar);
void counter_show(bool on, double shown_fps, double game_hz, unsigned target_hz);
bool counter_message(void *hwnd, unsigned msg, uintptr_t wp, intptr_t lp, intptr_t *result);
void counter_text(wchar_t *shown, wchar_t *game, bool *slow); // the reading now (32 wide chars each; empty: off)
void copy_button_tip(void *hwnd, const void *screen_rect);    // the icon's tooltip under a RECT on screen; nullptr hides it

// host_bar.cpp: the Phangrade title bar (option A, the user's choice of 2026-11-12) in place of the Windows title bar and
// the native menu bar; it draws this menu table. menu-bar=native (--menu-bar native) keeps the native ones.
constexpr unsigned kMenuFullScreen = 100, kMenuSlimBar = 125; // Display > Full screen, > Phangrade title bar (host_menu.cpp)
bool bar_on();
void bar_set(bool slim); // at once, and kept in the settings
int bar_option(const char *arg, const char *value);
// host_resolution.cpp: Display > Resolution. In a window, the window's size; in full screen, the display's modes (the
// desktop's own by default), kept as fullscreen-mode. window_options: bar_option, then --fullscreen-mode.
int window_options(const char *arg, const char *value);
void *resolution_menu(); // the group (an HMENU as void *), the saved full-screen mode applied to the window
void resolution_sync();  // its entries for the window's state, as a menu opens
bool resolution_command(unsigned id); // a choice of it: done and kept
void bar_attach(void *hwnd, void *menu);
void bar_show(bool windowed);
bool bar_message(void *hwnd, unsigned msg, uintptr_t wp, intptr_t lp, intptr_t *result);
void bar_redraw();      // the frame-rate reading changed
void bar_poll();        // each pump, outside the lock: opens a menu asked for
void bar_event(const void *sdl_event); // each SDL event: Esc, Guide, the pointer at a full screen's top
bool bar_size(void *hwnd, int outer_w, int outer_h); // sizes the windowed window by the bar's frame; false: SDL's
void window_replace();     // host_window.cpp: the windowed window sized and placed again (its frame changed)

// host_resume.cpp: Help > Resume before the last crash. find: the newest crash or killed recording in the logs folder
// and the vertical blank it reaches (false if none); start: asks, starts the game again on it and closes this window;
// wait: at start, the new run waits for the one that started it to end (it moves that run's log aside).
bool resume_find(std::filesystem::path *rec, unsigned *end);
void resume_start(void *hwnd);
void resume_wait();
std::wstring arg_text(const std::wstring &a); // an argument as CommandLineToArgvW reads it back

// host_online.cpp: the Online menu of a console with online play (PORT_CONSOLE_ONLINE): what this run is, play alone, host
// a game, join a game (an address asked for). Saved as net=off|on|ADDRESS; each offers to start the game again at once.
void *online_menu(unsigned first_id);
bool online_command(void *hwnd, unsigned id); // true if id was the Online menu's
void online_sync(void *bar);
const char *online_locked(); // why the entries that change the game are locked (a match is on), or nullptr

// host_crash.cpp: an unhandled exception writes data\logs\crash-<time>.dmp and a log line, and (when interactive)
// tells the user where the dump is.
void crash_install(bool interactive);

// host_online.cpp: this program again on its own command line less drop's options (and --level-play), then add; this
// one closes. The new process's id, 0 if it could not start.
unsigned long start_again(void *hwnd, const std::vector<std::wstring> &drop, const std::vector<std::wstring> &add, const char *who);

} // namespace host
