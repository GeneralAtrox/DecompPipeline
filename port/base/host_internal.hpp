// The host's own C++ interfaces (host/*.cpp). Nothing here is visible to the kit's C side.
#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

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
