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

/* ---- the answer-key emulator ---- */
int console_lockstep_pad(HostPad *pad) { return lockstep_pad(pad); }
s32 console_lockstep_timer(s32 live) { return lockstep_timer(live); }
int console_lockstep_time(HostTime *t) { return lockstep_time(t); }

/* ---- the menu bar: Display > VMU screen, Display > Texture pack, Display > In-between frames ---- */
static const char *const k_vmu_labels[] = {"&Off", "&Beside the picture", "O&ver the picture (top left)"};
static const char *const k_vmu_values[] = {"off", "side", "over"}; /* gfx_vmu_mode_name's words */
/* --interp-scene, greyed while the game's own draw function makes in-between frames: off (the ticks alone, or that draw
 * function where the game offers one) or the renderer's screen blending (pvr/pvr_blend.c). The renderer keeps the
 * scene before from the start, so a change applies from the next start. */
static const char *const k_scene_labels[] = {"&Off", "&Screen blending"};
static const char *const k_scene_values[] = {"off", "only"};
static int g_scene_next = -1;
static int scene_get(void) { return g_scene_next >= 0 ? g_scene_next : extra_scene_mode() > 0; }
static void scene_set(int choice) { g_scene_next = choice; }
static int scene_available(void) { return extra_scene_mode() >= 0; }

static PortMenuItem k_menu[] = {
    {"Display", "V&MU screen", PORT_MENU_CHOICE, "vmu-screen", gfx_vmu_mode, gfx_vmu_set_mode, k_vmu_labels, k_vmu_values, 3,
     NULL},
    {"Display", "&Texture pack", PORT_MENU_CHOICE, "texture-pack", pvr_pack_choice, pvr_pack_set_choice, NULL, NULL, 0,
     pvr_pack_available},
    {"Display", "In-&between frames (from the next start)", PORT_MENU_CHOICE, "interp-scene", scene_get, scene_set,
     k_scene_labels, k_scene_values, 2, scene_available},
};

const PortMenuItem *console_menu_extra(unsigned *count)
{
    k_menu[1].n_choices = pvr_pack_choices(&k_menu[1].choices, &k_menu[1].values); /* what this release carries */
    *count = (unsigned)(sizeof k_menu / sizeof k_menu[0]);
    return k_menu;
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
