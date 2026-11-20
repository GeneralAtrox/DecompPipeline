/* The Dreamcast kit's side of the port base (base/console.h): the identity, the picture, and the few hooks the base
 * calls into the machine through. Everything else the Dreamcast does is in the rest of runtime/, pvr/ and sdk/. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <bcrypt.h>
#include "port.h"
#include "platform.h"
#include "gfx/gfx.h"
#include "lockstep.h"
#include "lockstep_parts.h"
#include "scene_dump.h"
#include "scene_json.h"
#include "host.h"
#include "game_log.h"



const PortConsole port_console = {
    "dreamcast", "Dreamcast", PORT_GAME_NAME, port_version, 640u, 480u, PORT_VBLANK_HZ, PORT_CONSOLE_FPSCAP,
};

/* ---- the scene dump ---- */
const u8 *console_guest_ptr(u32 addr, u32 size)
{
    u32 a = guest_fold(addr);
    if (!guest_is_ram(a) || !guest_is_ram(guest_fold(addr + size - 1u))) return NULL;
    return (const u8 *)(uintptr_t)a;
}

void console_scene_extra(struct Json *j) { (void)j; }

void console_replay_args(struct Json *j, int after)
{
    if (!after) {
        scene_carry(j, "--disc", 1);
    } else {
        scene_carry(j, "--vmu", 0);
        scene_carry(j, "--thunks", 0);
        scene_carry(j, "--check-fsca", 1);
    }
}

int console_capture_bmp(const char *path) { return gfx_capture_bmp(path); }

void console_view(PortView *v)
{
    v->out_w = g_gfx.out_w;
    v->out_h = g_gfx.out_h;
    v->frame_w = g_gfx.frame_w;
    v->frame_h = g_gfx.frame_h;
    v->headless = g_gfx.headless;
    v->warp = g_gfx.warp;
    v->interp = extra_interp();
    v->presented_fps = (float)gfx_presented_fps();
}

/* --watch ADDR@FROM:TO: the emulator-side write watch on a game word (lockstep.c); a guest address in P1 form. */
void console_watch(u32 word, u32 tick) { lockstep_watch(word ? (volatile u32 *)(uintptr_t)(word | 0x80000000u) : NULL, tick); }

int console_poke(u32 addr, u8 value)
{
    u32 a = guest_fold(addr);
    if (!guest_is_ram(a)) return 0;
    *(u8 *)(uintptr_t)a = value;
    return 1;
}

/* ---- the answer-key emulator ---- */
int console_lockstep_pad(HostPad *pad) { return lockstep_pad(pad); }
s32 console_lockstep_timer(s32 live) { return lockstep_timer(live); }
int console_lockstep_time(HostTime *t) { return lockstep_time(t); }

/* ---- the menu bar: Display > VMU screen, Display > Texture pack ---- */
static const char *const k_vmu_labels[] = {"&Off", "&Beside the picture", "O&ver the picture (top left)"};
static const char *const k_vmu_values[] = {"off", "side", "over"}; /* gfx_vmu_mode_name's words */
static PortMenuItem k_menu[] = {
    {"Display", "V&MU screen", PORT_MENU_CHOICE, "vmu-screen", gfx_vmu_mode, gfx_vmu_set_mode, k_vmu_labels, k_vmu_values, 3,
     NULL},
    {"Display", "&Texture pack", PORT_MENU_CHOICE, "texture-pack", pvr_pack_choice, pvr_pack_set_choice, NULL, NULL, 0,
     pvr_pack_available},
};

/* Game > the game's own on/off settings (game.json menu_checks): each is the settings file's game.NAME, which the game reads
 * with port_setting_get_int (on is 1), so a choice applies from the game's next read of it; off unless chosen, or on when
 * the entry's "default" is true (never in a check run). A texture pack line's if= word reads one through
 * port_menu_check_on, and a choice reloads the pack at once. An entry whose file is not in the data folder is greyed, and
 * the log says why. */
#ifdef PORT_MENU_CHECKS
typedef struct MenuCheck {
    const char *label, *setting, *needs;
    int on; /* its default: game.json "default" */
} MenuCheck;
static const MenuCheck k_checks[] = {PORT_MENU_CHECKS};
enum { N_CHECKS = (int)(sizeof k_checks / sizeof k_checks[0]) };
static int g_check_next[4] = {-1, -1, -1, -1}; /* chosen in this run */
static int g_check_told[4];
static int check_get(int i)
{
    int def = k_checks[i].on && !host_fixed_clock();
    return g_check_next[i] >= 0 ? g_check_next[i] : port_setting_get_int(k_checks[i].setting, def) != 0;
}
static void check_set(int i, int on)
{
    g_check_next[i] = on != 0;
    pvr_pack_refresh();
}
static int check_available(int i)
{
    char path[1024];
    DWORD at;
    if (!k_checks[i].needs[0]) return 1;
    snprintf(path, sizeof path, "%s/%s", host_data_folder(), k_checks[i].needs);
    at = GetFileAttributesA(path);
    if (at != INVALID_FILE_ATTRIBUTES && !(at & FILE_ATTRIBUTE_DIRECTORY)) return 1;
    if (!g_check_told[i]) port_log("menu: %s is greyed: %s is not there\n", k_checks[i].label, path);
    if (!g_check_told[i]) game_log_line("menu: %s is greyed: %s is not there", k_checks[i].label, path);
    g_check_told[i] = 1;
    return 0;
}
#define CHECK_FNS(n)                                                                                                   \
    static int check_get##n(void) { return check_get(n); }                                                            \
    static void check_set##n(int on) { check_set(n, on); }                                                            \
    static int check_available##n(void) { return check_available(n); }
CHECK_FNS(0)
CHECK_FNS(1)
CHECK_FNS(2)
CHECK_FNS(3)
static int (*const k_check_get[4])(void) = {check_get0, check_get1, check_get2, check_get3};
static void (*const k_check_set[4])(int) = {check_set0, check_set1, check_set2, check_set3};
static int (*const k_check_available[4])(void) = {check_available0, check_available1, check_available2, check_available3};
#endif

int port_menu_check_on(const char *setting)
{
#ifdef PORT_MENU_CHECKS
    for (int i = 0; i < N_CHECKS && i < 4; i++)
        if (!strcmp(k_checks[i].setting, setting)) return check_get(i);
#endif
    return host_fixed_clock() ? 0 : port_setting_get_int(setting, 0) != 0;
}

const PortMenuItem *console_menu_extra(unsigned *count)
{
    static PortMenuItem menu[sizeof k_menu / sizeof k_menu[0] + 4];
    unsigned n = 0;
    k_menu[1].n_choices = pvr_pack_choices(&k_menu[1].choices, &k_menu[1].values); /* what this release carries */
    for (unsigned i = 0; i < sizeof k_menu / sizeof k_menu[0]; i++) menu[n++] = k_menu[i];
#ifdef PORT_MENU_CHECKS
    for (int i = 0; i < N_CHECKS && i < 4; i++) {
        PortMenuItem e = {"Game", k_checks[i].label, PORT_MENU_CHECK, k_checks[i].setting, k_check_get[i], k_check_set[i],
                          NULL, NULL, 0, k_check_available[i]};
        menu[n++] = e;
    }
#endif
    *count = n;
    return menu;
}

/* ---- input ---- */
void console_pads_changed(void) { pad_style_poll(); }

/* ---- the launcher contract: is this disc the game (base/install.c does the rest) ---- */
static int sha256_hex(const u8 *data, u32 size, char out[65])
{
    u8 digest[32];
    if (!BCRYPT_SUCCESS(BCryptHash(BCRYPT_SHA256_ALG_HANDLE, NULL, 0, (PUCHAR)data, size, digest, sizeof digest))) return 0;
    for (int i = 0; i < 32; i++) sprintf_s(out + 2 * i, 3, "%02x", digest[i]);
    return 1;
}

int console_install(const char *image, const char *work, const char *assets) /* the disc copy (install.c) */
{
    (void)image;
    (void)work;
    (void)assets;
    return -1;
}

int console_check_image(const char *image)
{
    static const char *const accepted[] = PORT_BOOT_SHA256;
    char boot[32], sha[65];
    u32 size = 0;
    PortDisc *d = disc_open(image);
    if (!d) return port_install_fail(5, "%s is not a Dreamcast GD-ROM image", image);
    if (PORT_BOOT_FILE[0]) strncpy_s(boot, sizeof boot, PORT_BOOT_FILE, _TRUNCATE);
    else if (!disc_boot_name(d, boot, sizeof boot)) boot[0] = 0;
    u8 *data = boot[0] ? disc_read_file(d, boot, &size) : NULL;
    disc_close(d);
    if (!data) return port_install_fail(5, "%s is not %s: it has no boot file %s", image, PORT_GAME_NAME, boot);
    int hashed = sha256_hex(data, size, sha);
    free(data);
    if (!hashed) return port_install_fail(1, "cannot hash the boot file");
    port_log("setup: %s boot file %s, SHA-256 %s\n", image, boot, sha);
    if (!accepted[0][0]) return 0;
    for (int i = 0; accepted[i][0]; i++)
        if (!strcmp(accepted[i], sha)) return 0;
    return port_install_fail(5, "%s is another game or region than this port's %s (boot file SHA-256 %s)", image, PORT_GAME_NAME, sha);
}

/* ---- the pad: the controller's buttons, each taking any host button (Input > Controls, base/input_bind.c). The game reads
 * the HostPad, an assignment has pressed the default button in it by then. ---- */
static const PortAction k_actions[] = {
    {"A", "A"},   {"B", "B"},     {"X", "X"},       {"Y", "Y"},       {"START", "START"}, {"UP", "UP"},
    {"DOWN", "DOWN"}, {"LEFT", "LEFT"}, {"RIGHT", "RIGHT"}, {"LT", "LT"}, {"RT", "RT"},
};

const PortAction *console_actions(unsigned *count)
{
    *count = (unsigned)(sizeof k_actions / sizeof k_actions[0]);
    return k_actions;
}
