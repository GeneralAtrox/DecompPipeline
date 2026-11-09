/* The Saturn kit's side of the port base (base/console.h): the identity, the picture, guest memory for the scene dump and
 * the few hooks the base calls into the machine through. The rest of the Saturn is the other sat_*.c files.
 *
 * Nearest existing: runtime/console_dc.c (the Dreamcast's side of the same interface). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_disc.h"
#include "sat_cd.h"
#include "sat_part.h"
const PortMenuItem *sat_level_menu(void); /* sat_level.c */
const PortMenuItem *sat_logos_menu(void); /* sat_logos.c */
#include "sat_game.h"
#include "sat_net.h"
#include "console.h"
#include "scene_json.h"
#include "scene_dump.h"
#include "present_cpu.h"

#if defined(SAT_NETPLAY) && !defined(SAT_NET_IN_GAME)
#define SAT_CONSOLE_FLAGS (PORT_CONSOLE_FPSCAP | PORT_CONSOLE_ONLINE) /* online play (sat_net.c): the Online menu */
#else
#define SAT_CONSOLE_FLAGS PORT_CONSOLE_FPSCAP
#endif
const PortConsole port_console = {"saturn", "Saturn", SAT_GAME_NAME, "saturn kit 1", 320u, 224u, SAT_FRAME_HZ, SAT_CONSOLE_FLAGS};

/* ---- run state ---- */
u32 port_vblank_count(void) { return sat_vblank_count(); }

int port_extra_drawing(void) { return sat_inb_drawing(); } /* in-between frames: sat_inb.c */

/* ---- the scene dump: the SH-2's memory is big-endian, so a scalar is handed over byte-reversed (the dump reads host
 * order). A field of 2, 4 or 8 bytes is a number; a longer one is a string or a block and is read as it lies. ---- */
const u8 *console_guest_ptr(u32 addr, u32 size)
{
    static u8 scratch[8][16]; /* the dump holds a few pointers at once */
    static unsigned next;
    const u8 *p = NULL;
    u32 a = addr & 0x1fffffffu; /* the cached (0x06) and uncached (0x26) mirrors are the same memory */
    if (!size) return NULL;
    if (a >= SAT_WRAM_H_BASE && a - SAT_WRAM_H_BASE <= SAT_WRAM_SIZE - size) p = sat_ram_h() + (a - SAT_WRAM_H_BASE);
    else if (a >= SAT_WRAM_L_BASE && a - SAT_WRAM_L_BASE <= SAT_WRAM_SIZE - size) p = sat_ram_l() + (a - SAT_WRAM_L_BASE);
    if (!p || (size != 2 && size != 4 && size != 8)) return p;
    u8 *out = scratch[next++ & 7];
    for (u32 i = 0; i < size; i++) out[i] = p[size - 1 - i];
    return out;
}

/* The video chips' state: what a Saturn game does to the picture shows in these before anything on the CPU's side. */
void console_scene_extra(struct Json *j)
{
    u16 r2[8];
    json_f(j, ",\n    \"vdp1\": {\"tvmr\": %u, \"fbcr\": %u, \"ptmr\": %u, \"ewdr\": %u}", (sat_v1_reg[0] << 8) | sat_v1_reg[1],
           (sat_v1_reg[2] << 8) | sat_v1_reg[3], (sat_v1_reg[4] << 8) | sat_v1_reg[5], (sat_v1_reg[6] << 8) | sat_v1_reg[7]);
    for (int i = 0; i < 8; i++) r2[i] = (u16)((sat_v2_reg[i * 2] << 8) | sat_v2_reg[i * 2 + 1]);
    json_f(j, ",\n    \"vdp2\": {\"tvmd\": %u, \"exten\": %u, \"tvstat\": %u, \"vram_size\": %u, \"ramctl\": %u, \"cyc_a0\": %u}", r2[0], r2[1],
           r2[2], r2[3], (sat_v2_reg[0x0e] << 8) | sat_v2_reg[0x0f], (sat_v2_reg[0x10] << 8) | sat_v2_reg[0x11]);
    json_f(j, ",\n    \"frame\": {\"width\": %u, \"height\": %u, \"vblank\": %u}", sat_frame_w, sat_frame_h, sat_vblank_count());
}

void console_replay_args(struct Json *j, int after)
{
    if (!after) {
        scene_carry(j, "--disc", 1);
        scene_carry(j, "--image", 1);
    } else {
        scene_carry(j, "--cdrate", 0);
#ifdef SAT_GROUND_SHADOW_PMOD
        json_f(j, " --ground-shadows %s", sat_v1_ground_shadows > 0 ? "under" : "original"); /* the play run's, from its settings too */
#endif
    }
}

/* The frame as composed, to a 24-bit BMP. */
int console_capture_bmp(const char *path)
{
    const u32 w = sat_frame_w, h = sat_frame_h, row = (w * 3u + 3u) & ~3u;
    u8 head[54] = {'B', 'M'};
    FILE *f;
    u8 *line;
    if (!w || !h) return 0;
    if (fopen_s(&f, path, "wb")) return 0;
    line = (u8 *)calloc(1, row);
    if (!line) {
        fclose(f);
        return 0;
    }
    *(u32 *)(head + 2) = 54u + row * h;
    *(u32 *)(head + 10) = 54u;
    *(u32 *)(head + 14) = 40u;
    *(s32 *)(head + 18) = (s32)w;
    *(s32 *)(head + 22) = (s32)h;
    *(u16 *)(head + 26) = 1;
    *(u16 *)(head + 28) = 24;
    fwrite(head, 1, sizeof head, f);
    for (u32 y = h; y-- > 0;) {
        for (u32 x = 0; x < w; x++) {
            u32 c = sat_frame[y * w + x];
            line[x * 3] = (u8)c;
            line[x * 3 + 1] = (u8)(c >> 8);
            line[x * 3 + 2] = (u8)(c >> 16);
        }
        fwrite(line, 1, row, f);
    }
    free(line);
    fclose(f);
    return 1;
}

void console_view(PortView *v)
{
    memset(v, 0, sizeof *v);
    v->headless = sat_video_headless();
    if (v->headless) {
        v->out_w = v->frame_w = sat_frame_w;
        v->out_h = v->frame_h = sat_frame_h;
    } else {
        cpu_present_view(v);
    }
}

void console_watch(u32 word, u32 tick)
{
    (void)word;
    (void)tick;
}

/* ---- the answer-key emulator: none for the Saturn ---- */
int console_lockstep_pad(HostPad *pad)
{
    (void)pad;
    return 0;
}

s32 console_lockstep_timer(s32 live) { return live; }

int console_lockstep_time(HostTime *t)
{
    (void)t;
    return 0;
}

/* ---- the menu bar: Display > Fast disc loads (sat_cd.c), Native movie player (sat_movie.c) for a game that names its
 * movie routine, Shadows for a game that names its floor shadows
 * (sat_vdp1_list.c) and Game > Skip the opening logos (sat_logos.c); Game > Edited arena for a game with an editable level
 * (sat_level.c); a team fight is chosen in the game's own FIGHT menu (sat_menus.c) and a model part's versions as new head
 * types in its own part choice (sat_heads.c). A load's speed is kept in the recording at start, so a change applies from the
 * next start. While an online match is on, those that change the game (fast loads, the movie player) are greyed: the joiner
 * plays the host's (sat_net_setup.c). ---- */
static int g_fast_next = -1;
static int alone(void) { return !sat_net_on(); }
static int fast_get(void) { return g_fast_next >= 0 ? g_fast_next : sat_cd_fast_loads > 0; }
static void fast_set(int on) { g_fast_next = on != 0; }
static const char *const k_shadow_labels[] = {"&Under what stands on them", "In the game's &order (as on the Saturn)"};
static const char *const k_shadow_values[] = {"under", "original"};
static int shadows_get(void) { return sat_v1_ground_shadows > 0 ? 0 : 1; }
static void shadows_set(int choice) { sat_v1_ground_shadows = choice == 0; }
static int inb_get(void) { return sat_inb_on > 0; }
static void inb_set(int on) { sat_inb_on = on != 0; } /* applied at the next vertical blank (sat_inb.c) */

static const PortMenuItem k_menu[] = {
    {"Display", "Fast &disc loads (from the next start)", PORT_MENU_CHECK, "cd-fast-loads", fast_get, fast_set, NULL, NULL, 0, alone},
    {"Display", "&In-between frames", PORT_MENU_CHECK, "in-between", inb_get, inb_set, NULL, NULL, 0, NULL},
    {"Display", "&Shadows", PORT_MENU_CHOICE, "ground-shadows", shadows_get, shadows_set, k_shadow_labels, k_shadow_values, 2, NULL},
    {"Display", "Native mo&vie player (from the next start)", PORT_MENU_CHECK, "movie-native", sat_movie_get, sat_movie_set, NULL, NULL, 0, alone},
};

const PortMenuItem *console_menu_extra(unsigned *count)
{
    static PortMenuItem menu[8];
    unsigned n = 0;
    menu[n++] = k_menu[0];
    menu[n++] = k_menu[1];
#ifdef SAT_GROUND_SHADOW_PMOD
    menu[n++] = k_menu[2];
#endif
#ifdef SAT_MOVIE_FN
    menu[n++] = k_menu[3];
#endif
    if (sat_logos_menu()) menu[n++] = *sat_logos_menu(); /* Game > Skip the opening logos (sat_logos.c) */
    if (sat_level_menu()) menu[n++] = *sat_level_menu(); /* Game > Edited arena, greyed online (sat_level.c) */
    *count = n;
    return menu;
}

void console_pads_changed(void) {}

/* ---- the presenter's hooks: a CPU frame goes through base/present_cpu.c, which has no overlay; a game with game.json
 * "widescreen" has its picture composed wider in a play run (sat_wide.c) ---- */
int console_present_option(const char *arg, const char *value)
{
    (void)arg;
    (void)value;
    return 0;
}
void console_present_over(void *back_buffer) { (void)back_buffer; }
void console_present_release(void) {}
#ifdef SAT_WIDESCREEN
int console_widens(void) { return 1; }
#else
int console_widens(void) { return 0; }
#endif
int extra_interp(void) { return sat_inb_live(); }
void extra_set_fps(unsigned fps) { cpu_present_set_fps(fps); } /* the menu's cap: the showing thread's rate */
unsigned extra_fps(void) { return cpu_present_fps(); }

/* ---- the launcher contract: is this disc the game (base/install.c does the rest) ---- */
int console_check_image(const char *image)
{
    u8 raw[SAT_SECTOR];
    const u8 *hdr = raw + 16; /* the data track's first sector: its user data starts after the 16-byte sync and header */
    if (sat_disc_open(image)) return port_install_fail(4, "%s cannot be opened as a Saturn disc image", image);
    if (!sat_disc_read(150, raw) || memcmp(hdr, "SEGA SEGASATURN ", 16))
        return port_install_fail(5, "%s is not a Saturn disc (no SEGA SEGASATURN header)", image);
#ifdef SAT_PRODUCT
    if (memcmp(hdr + 0x20, SAT_PRODUCT, sizeof SAT_PRODUCT - 1))
        return port_install_fail(5, "%s is another game than this port's " SAT_GAME_NAME " (product %.10s, wanted " SAT_PRODUCT ")", image,
                                 hdr + 0x20);
#endif
    return 0;
}
