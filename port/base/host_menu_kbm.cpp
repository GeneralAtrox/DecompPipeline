// Input > Keyboard and mouse, for host_menu.cpp's menu bar: Reset to the game's layout; mouse steer's settings
// (input_kbm_steer.c: Mouse steering, Mouse sensitivity, Invert mouse Y, kept as kbm-mouse-*); then each key and mouse
// input of the layout (input_kbm.c) taking any of the host pad's buttons, kept as kbm-<source>. Every choice applies at
// once and goes into the settings file, apart from the game's save.
#include <cctype>
#include <cstring>
#include <string>
#include <windows.h>
#include "host.h"
#include "host_internal.hpp"

namespace {

enum : UINT {
    kSteerSens = 7960, // kSteerSens + N: Mouse sensitivity N (1..10)
    kSteer = 7980,
    kSteerInvert = 7981,
    kKbmReset = 7999,
    kKbm = 8000, // kKbm + 32 * source + target
    kKbmEnd = kKbm + 32 * 64,
};

std::wstring ascii(const char *s) { return std::wstring(s, s + std::strlen(s)); }

void check(HMENU bar, UINT id, bool on) { CheckMenuItem(bar, id, MF_BYCOMMAND | (on ? MF_CHECKED : MF_UNCHECKED)); }

} // namespace

namespace host {

void *kbm_menu()
{
    HMENU kbm = CreatePopupMenu();
    AppendMenuW(kbm, MF_STRING, kKbmReset, L"&Reset to the game's layout");
    AppendMenuW(kbm, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(kbm, MF_STRING, kSteer, L"Mouse &steering (when the game asks for it)");
    HMENU sens = CreatePopupMenu();
    for (UINT level = 1; level <= 10; level++) AppendMenuW(sens, MF_STRING, kSteerSens + level, std::to_wstring(level).c_str());
    AppendMenuW(kbm, MF_POPUP, reinterpret_cast<UINT_PTR>(sens), L"Mouse se&nsitivity");
    AppendMenuW(kbm, MF_STRING, kSteerInvert, L"&Invert mouse Y");
    AppendMenuW(kbm, MF_SEPARATOR, 0, nullptr);
    for (unsigned i = 0; i < input_kbm_count() && i < 64; i++) {
        HMENU one = CreatePopupMenu();
        for (unsigned t = 0; t < input_kbm_target_count() && t < 32; t++)
            AppendMenuW(one, MF_STRING, kKbm + 32 * i + t, t ? ascii(input_kbm_target_name(t)).c_str() : L"(none)");
        AppendMenuW(kbm, MF_POPUP, reinterpret_cast<UINT_PTR>(one), ascii(input_kbm_name(i)).c_str());
    }
    return kbm;
}

void kbm_menu_sync(void *bar)
{
    HMENU m = static_cast<HMENU>(bar);
    for (unsigned i = 0; i < input_kbm_count() && i < 64; i++)
        for (unsigned t = 0; t < input_kbm_target_count() && t < 32; t++) check(m, kKbm + 32 * i + t, t ? (input_kbm_get(i) >> t & 1u) != 0 : input_kbm_get(i) == 0);
    check(m, kSteer, input_kbm_steer_get(KBM_STEER_ON) != 0);
    for (UINT level = 1; level <= 10; level++) check(m, kSteerSens + level, input_kbm_steer_get(KBM_STEER_SENSITIVITY) == static_cast<int>(level));
    check(m, kSteerInvert, input_kbm_steer_get(KBM_STEER_INVERT) != 0);
}

bool kbm_menu_command(unsigned id)
{
    if (id == kSteer || id == kSteerInvert || (id > kSteerSens && id <= kSteerSens + 10)) {
        int which = id == kSteer ? KBM_STEER_ON : id == kSteerInvert ? KBM_STEER_INVERT : KBM_STEER_SENSITIVITY;
        input_kbm_steer_set(which, which == KBM_STEER_SENSITIVITY ? static_cast<int>(id - kSteerSens) : input_kbm_steer_get(which) ? 0 : 1);
        int now = input_kbm_steer_get(which);
        settings_set(input_kbm_steer_key(which), which == KBM_STEER_SENSITIVITY ? std::to_string(now) : std::string(now ? "on" : "off"));
        return true;
    }
    if (id != kKbmReset && (id < kKbm || id >= kKbmEnd)) return false;
    if (id == kKbmReset) input_kbm_reset();
    else input_kbm_toggle((id - kKbm) / 32, (id - kKbm) % 32);
    for (unsigned i = 0; i < input_kbm_count(); i++) { // every source's line, so a reset is kept too
        char text[128];
        std::string key = std::string("kbm-") + input_kbm_name(i), value = input_kbm_text(i, text, sizeof text);
        for (char &c : key) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        for (char &c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        settings_set(key, value);
    }
    return true;
}

} // namespace host
