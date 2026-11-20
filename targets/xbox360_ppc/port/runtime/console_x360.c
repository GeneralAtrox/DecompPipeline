/* The Xbox 360 kit's side of the port base (base/console.h): the identity, guest memory for the scene dump, the pad as the
 * game reads it and the few hooks the base calls into the machine through. The rest of the machine is the other xb_*.c files.
 *
 * Nearest existing: saturn/console_sat.c (the same interface for a console with a CPU frame; this one draws into g_gfx's frame
 * target itself). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "console.h"
#include "host.h"
#include "scene_json.h"
#include "scene_dump.h"
#include "present.h"

#include "xb_game.h"

/* A replay's time limit at 2 frames a second: Too Human ran 12653 frames in 900 s headless on build box A (14 a second), but only 2 to 4 a second in its first
 * 90 s while it loads, and the limit is only a guard against a hang: --stop-at ends the run at its frame */
/* No fixed-clock replay yet. The fixed clock moves a microsecond on every look at the time base from any guest thread, and Too Human runs some thirty
 * guest threads on host threads. In a dump's replay on it, the render thread's loader started the movie player 39 times in frame 2 until guest
 * memory ran out (likely its time-outs passing at once; not proven). A dump replays on the real clock instead and says so */
const PortConsole port_console = {"xbox360", "Xbox 360", XB_GAME_NAME, "xbox360 kit 1", 1280u, 720u, 60u, PORT_CONSOLE_FCAP, 2u,
                                  "its guest threads run on the host's threads, so the fixed clock does not give the same run (on it the movie player "
                                  "started over and over in frame 2 until guest memory ran out)"};

/* ---- run state ---- */
u32 port_vblank_count(void) { return xb_frame_number(); }
int port_extra_drawing(void) { return 0; }

/* ---- the scene dump: guest memory is big-endian, so a scalar is handed over byte-reversed ---- */
const u8 *console_guest_ptr(u32 addr, u32 size)
{
    static u8 scratch[8][16]; /* the dump holds a few pointers at once */
    static unsigned next;
    MEMORY_BASIC_INFORMATION mi;
    const u8 *p;
    if (!size || addr + size < addr) return NULL;
    p = tc_base + addr;
    if (!VirtualQuery(p, &mi, sizeof mi) || mi.State != MEM_COMMIT || p + size > (const u8 *)mi.BaseAddress + mi.RegionSize) return NULL;
    if (size != 2 && size != 4 && size != 8) return p;
    {
        u8 *out = scratch[next++ & 7];
        for (u32 i = 0; i < size; i++) out[i] = p[size - 1 - i];
        return out;
    }
}

void console_scene_extra(struct Json *j)
{
    json_f(j, ",\n    \"frame\": {\"number\": %u, \"draws\": %u, \"draws_since_start\": %u}", xb_frame_number(), xb_gpu_draw_count(1), xb_gpu_draw_count(0));
}

void console_replay_args(struct Json *j, int after)
{
    if (after) return;
    scene_carry(j, "--image", 1);
    scene_carry(j, "--entry", 0);
    scene_carry(j, "--game-dir", 1);
}

int console_capture_bmp(const char *path) { return xb_gpu_capture(path); }

void console_view(PortView *v)
{
    memset(v, 0, sizeof *v);
    v->headless = g_gfx.headless;
    v->warp = g_gfx.warp;
    v->out_w = g_gfx.out_w;
    v->out_h = g_gfx.out_h;
    v->frame_w = g_gfx.frame_w;
    v->frame_h = g_gfx.frame_h;
    v->presented_fps = (float)gfx_presented_fps();
}

void console_watch(u32 word, u32 tick)
{
    (void)word;
    (void)tick;
}

/* --poke-ram: none yet (0: scene_dump.c logs that the address is not in the game's memory and stops poking) */
int console_poke(u32 addr, u8 value)
{
    (void)addr;
    (void)value;
    return 0;
}

/* ---- the answer-key emulator: none ---- */
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

/* ---- the menu bar: no console-only entries ---- */
const PortMenuItem *console_menu_extra(unsigned *count)
{
    *count = 0;
    return NULL;
}

/* ---- input: the XInput pad the game reads, sampled once a frame on the thread that pumps the window (the game's own polling
 * thread reads the sample). Every button of the pad and both sticks are actions the player can assign ---- */
void console_pads_changed(void) {}

enum { XI_UP = 0x1, XI_DOWN = 0x2, XI_LEFT = 0x4, XI_RIGHT = 0x8, XI_START = 0x10, XI_BACK = 0x20, XI_L3 = 0x40, XI_R3 = 0x80,
       XI_LB = 0x100, XI_RB = 0x200, XI_A = 0x1000, XI_B = 0x2000, XI_X = 0x4000, XI_Y = 0x8000 };
enum { ACT_BUTTONS = 14, ACT_LT = ACT_BUTTONS, ACT_RT, ACT_RSUP, ACT_RSDOWN, ACT_RSLEFT, ACT_RSRIGHT };
static const u16 k_xinput[ACT_BUTTONS] = {XI_A, XI_B, XI_X, XI_Y, XI_START, XI_BACK, XI_UP, XI_DOWN, XI_LEFT, XI_RIGHT, XI_LB, XI_RB, XI_L3, XI_R3};
static const PortAction k_actions[] = {{"A", "A"},       {"B", "B"},         {"X", "X"},       {"Y", "Y"},
                                       {"START", "START"}, {"BACK", "BACK"},   {"UP", "UP"},     {"DOWN", "DOWN"},
                                       {"LEFT", "LEFT"}, {"RIGHT", "RIGHT"}, {"LB", "LB"},     {"RB", "RB"},
                                       {"L3", "L3"},     {"R3", "R3"},       {"LT", "LT"},     {"RT", "RT"},
                                       {"RSUP", "RSUP"}, {"RSDOWN", "RSDOWN"}, {"RSLEFT", "RSLEFT"}, {"RSRIGHT", "RSRIGHT"}};

const PortAction *console_actions(unsigned *count)
{
    *count = (unsigned)(sizeof k_actions / sizeof k_actions[0]);
    return k_actions;
}

static struct { HostPad pad; u32 held; } s_pads[HOST_PADS];
static u32 s_pads_there = 1u;
int xb_padlog_on(void);   /* xb_call.c */

void xb_pads_poll(void)
{
    HostPad pad[HOST_PADS];
    u32 there = host_pads_read(pad) | 1u;
    if (xb_padlog_on()) {   /* XB_PADLOG_HOLD (xb_call.c): host buttons held on player 1, through the player's assignments */
        static u32 hold = ~0u, from, to;
        static int sx, sy;
        u32 v = xb_frame_number();
        if (hold == ~0u) {   /* XB_PADLOG_STICK=X,Y@FROM-TO: player 1's left stick (HostPad: -128..127, y down) over those vertical blanks */
            const char *e = getenv("XB_PADLOG_STICK");
            hold = getenv("XB_PADLOG_HOLD") ? (u32)strtoul(getenv("XB_PADLOG_HOLD"), NULL, 16) : 0u;
            if (e && sscanf_s(e, "%d,%d@%u-%u", &sx, &sy, &from, &to) == 4)
                port_log("padlog: left stick %d,%d over vertical blanks %u-%u\n", sx, sy, from, to);
            else from = 1, to = 0;
        }
        if (v >= from && v <= to) pad[0].x = (s8)sx, pad[0].y = (s8)sy;
        if (hold) {
            pad[0].buttons |= hold;
            input_binds_apply(pad);
        }
    }
    for (unsigned p = 0; p < HOST_PADS; p++) {
        /* The D-pad bits come from the real D-pad (and a key or script, which presses it) only. The base also counts the left stick past
         * half as a held direction; given to the game as wButtons that is a D-pad press, and Too Human binds D-pad Up and Down to the camera
         * style (TH1Input.ini, handlers 82632bb0 and 82632c58), so walking changed the camera. The stick still reaches the game whole, as lx
         * and ly (401) and as left-stick keys (408) */
        HostPad b = pad[p];
        s_pads[p].pad = pad[p];
        b.x = b.y = 0;
        s_pads[p].held = host_actions(&b);
    }
    s_pads_there = there;
}

static s16 stick_of(s8 v) { return (s16)(v < 0 ? v * 256 : v * 258 - (v == 127)); }

int xb_pad_state(u32 user, XbPadState *out)
{
    u32 held;
    const HostPad *pad;
    if (user == 0 && xb_flow_auto_set() && !(s_pads_there & 1u)) {   /* XB_AUTO_A (the front-end check): a pad that only presses A */
        memset(out, 0, sizeof *out);
        out->buttons = xb_flow_auto_a() ? 0x1000u : 0u;
        return 1;
    }
    if (user >= HOST_PADS || !(s_pads_there >> user & 1u)) return 0;
    pad = &s_pads[user].pad;
    held = s_pads[user].held;
    out->buttons = 0;
    for (unsigned i = 0; i < ACT_BUTTONS; i++)
        if (held >> i & 1u) out->buttons |= k_xinput[i];
    out->lt = held >> ACT_LT & 1u ? (pad->l ? pad->l : 255u) : 0u; /* the analog value when the host trigger is what is held */
    out->rt = held >> ACT_RT & 1u ? (pad->r ? pad->r : 255u) : 0u;
    out->lx = stick_of(pad->x);
    out->ly = (s16)-stick_of(pad->y); /* HostPad: negative is up; XInput: positive is up */
    out->rx = (s16)((held >> ACT_RSRIGHT & 1u ? 32767 : 0) - (held >> ACT_RSLEFT & 1u ? 32767 : 0));
    out->ry = (s16)((held >> ACT_RSUP & 1u ? 32767 : 0) - (held >> ACT_RSDOWN & 1u ? 32767 : 0));
    if (user == 0 && xb_flow_auto_a()) out->buttons |= 0x1000u;
    return 1;
}

/* ---- the launcher contract: not reached yet. The game comes as an extracted package and a code image, and base/install.c
 * looks for a disc image, so nothing installs through it until that is settled ---- */
int console_install(const char *image, const char *work, const char *assets) /* the disc copy (install.c) */
{
    (void)image;
    (void)work;
    (void)assets;
    return -1;
}

int console_check_image(const char *image)
{
    return port_install_fail(5, "%s: the Xbox 360 kit has no launcher path yet", image);
}

/* ---- the presenter's hooks: xb_gpu.c draws into g_gfx's frame target. A play run's frame fills the window, with the game's own
 * 16:9 picture inside it, never stretched (the world does not widen yet) ---- */
int console_present_option(const char *arg, const char *value)
{
    (void)arg;
    (void)value;
    return 0;
}
void console_present_over(void *back_buffer) { (void)back_buffer; }
void console_present_release(void) {}
int console_widens(void) { return 1; }
void extra_set_interp(int on) { (void)on; }
int extra_interp(void) { return 0; }
void extra_set_fps(unsigned fps) { (void)fps; }
unsigned extra_fps(void) { return 0; }
