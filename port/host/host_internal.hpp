// The host's own C++ interfaces (host/*.cpp). Nothing here is visible to the kit's C side.
#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace host {

// host_data.cpp: <exe folder>\data, or PORT_DATA_DIR. Saves go in data\saves (runtime/host.c), logs and crash
// dumps in data\logs, the settings in data\settings.txt.
std::filesystem::path data_dir();
void set_data_dir(const std::filesystem::path &dir); // --data-directory: overrides both
std::filesystem::path logs_dir(); // created on first use
void prepare_data_dir();          // creates the folders; moves a saves folder from beside the exe into data\saves

// host_settings.cpp: the saved options. Each "key=value" line of the file is the option "--key value", placed
// before the command line, so the command line wins.
std::vector<std::string> settings_args(const std::filesystem::path &file);
void settings_use(const std::filesystem::path &file);                // the file settings_set writes (play runs)
void settings_set(const std::string &key, const std::string &value); // the menu's choices, kept for the next run

// host_window.cpp, for the menu: the window's mode and the host's own preferences
bool window_fullscreen();
void window_set_fullscreen(bool on);
void window_set_scale(int n); // a window N x 640x480; 0: fit
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

// host_crash.cpp: an unhandled exception writes data\logs\crash-<time>.dmp and a log line, and (when interactive)
// tells the user where the dump is.
void crash_install(bool interactive);

} // namespace host
