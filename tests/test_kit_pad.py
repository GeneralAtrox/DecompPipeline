"""The kit's controllers (port/sdk/maple_pad.c) and scripted presses (port/runtime/gfx/gfx_input.c) in behaviour:
compiled at /W4 /WX with a synthetic game.h and stub host calls, then run.
1. A game that starts the peripheral system through an SDK entry the port does not bind (Power Stone 2 calls a
   newer library's five-argument variant of pdInitPeripheral, which its registry leaves unnamed) still finds a
   standard controller on port A at the first server pass: support has the buttons and axes, a held Start reads as
   on and as a press. Without it the record kept support 0 and the game read every pad, scripted or real, as
   unplugged. A game that calls pdInitPeripheral gets the same record and no extra log line.
2. --input keeps far more than 64 presses (Power Stone 2's input sweeps were cut short without a word): a press past
   the 64th is held at its vertical blank; past MAX_SCRIPT the rest are dropped with one log line naming the first.
   gfx_input.c compiles against a minimal SDL3 stand-in (SDL_WasInit says no video: scripted input only), as no
   kit test pulls in a game build's vcpkg SDL headers.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_kit_pad.py
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

GAME_H = """#include "rd_rt.h"
typedef struct LibPadDevice LibPadDevice;
typedef struct { u32 unit, flag; s8 power, freq, inc; } PDS_VIBPARAM;
"""

MAPLE_C = r"""#include "game.h"
#include "platform.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
typedef struct { u32 id, support, on, off, press, release; } Rec; /* the record's first words */
void pdInitPeripheral(s32 logic, void *recvBuffer, void *sendBuffer);
void pdExecPeripheralServer(void);
LibPadDevice *pdGetPeripheral(u32 port);

static u32 g_held;
const char *host_arg(const char *name) { (void)name; return NULL; }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("log ");
    vprintf(fmt, ap);
    va_end(ap);
}
u32 host_pads_read(HostPad pad[HOST_PADS])
{
    memset(pad, 0, sizeof(HostPad) * HOST_PADS);
    pad[0].buttons = g_held;
    return 1u;
}
void host_pad_rumble(u32 player, u32 strength, u32 ms) { (void)player, (void)strength, (void)ms; }
void host_vmu_lcd(u32 card, const u8 *dots) { (void)card, (void)dots; }

static void show(const char *what, u32 port)
{
    const Rec *r = (const Rec *)pdGetPeripheral(port);
    printf("%s support %x on %x press %x\n", what, r->support, r->on, r->press);
}

int main(int argc, char **argv)
{
    (void)argv;
    if (argc > 1) pdInitPeripheral(0, NULL, NULL);
    pdExecPeripheralServer();
    show("idle", 0);
    g_held = HOST_PAD_START;
    pdExecPeripheralServer();
    show("start", 0);
    pdExecPeripheralServer();
    show("held", 0);
    show("portB", 6);
    return 0;
}
"""

SDL_H = r"""/* A minimal SDL3 stand-in for tests/test_kit_pad.py: no video, so gfx_input.c reads its script only. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
typedef uint8_t Uint8;
typedef uint16_t Uint16;
typedef int16_t Sint16;
typedef uint32_t SDL_JoystickID;
typedef struct SDL_Gamepad SDL_Gamepad;
typedef struct SDL_Joystick SDL_Joystick;
typedef enum { SDL_GAMEPAD_BUTTON_SOUTH, SDL_GAMEPAD_BUTTON_EAST, SDL_GAMEPAD_BUTTON_WEST, SDL_GAMEPAD_BUTTON_NORTH,
               SDL_GAMEPAD_BUTTON_START, SDL_GAMEPAD_BUTTON_DPAD_UP, SDL_GAMEPAD_BUTTON_DPAD_DOWN,
               SDL_GAMEPAD_BUTTON_DPAD_LEFT, SDL_GAMEPAD_BUTTON_DPAD_RIGHT } SDL_GamepadButton;
typedef enum { SDL_GAMEPAD_AXIS_LEFTX, SDL_GAMEPAD_AXIS_LEFTY, SDL_GAMEPAD_AXIS_LEFT_TRIGGER,
               SDL_GAMEPAD_AXIS_RIGHT_TRIGGER } SDL_GamepadAxis;
typedef enum { SDL_SCANCODE_A = 4, SDL_SCANCODE_I = 12, SDL_SCANCODE_J, SDL_SCANCODE_K, SDL_SCANCODE_L,
               SDL_SCANCODE_Q = 20, SDL_SCANCODE_S = 22, SDL_SCANCODE_W = 26, SDL_SCANCODE_X, SDL_SCANCODE_Z = 29,
               SDL_SCANCODE_RETURN = 40, SDL_SCANCODE_SPACE = 44, SDL_SCANCODE_RIGHT = 79, SDL_SCANCODE_LEFT,
               SDL_SCANCODE_DOWN, SDL_SCANCODE_UP, SDL_SCANCODE_KP_ENTER = 88, SDL_SCANCODE_LSHIFT = 225 } SDL_Scancode;
#define SDL_INIT_VIDEO 0x20u
#define SDL_INIT_GAMEPAD 0x2000u
#define SDL_KMOD_ALT 0x0300u
#define SDL_HAT_UP 1u
#define SDL_HAT_RIGHT 2u
#define SDL_HAT_DOWN 4u
#define SDL_HAT_LEFT 8u
static inline uint32_t SDL_WasInit(uint32_t f) { (void)f; return 0; }
static inline bool SDL_InitSubSystem(uint32_t f) { (void)f; return false; }
static inline bool SDL_GamepadConnected(SDL_Gamepad *g) { (void)g; return false; }
static inline bool SDL_JoystickConnected(SDL_Joystick *j) { (void)j; return false; }
static inline const char *SDL_GetJoystickNameForID(SDL_JoystickID i) { (void)i; return ""; }
static inline bool SDL_IsGamepad(SDL_JoystickID i) { (void)i; return false; }
static inline SDL_Gamepad *SDL_OpenGamepad(SDL_JoystickID i) { (void)i; return NULL; }
static inline SDL_Joystick *SDL_OpenJoystick(SDL_JoystickID i) { (void)i; return NULL; }
static inline const char *SDL_GetError(void) { return ""; }
static inline Uint16 SDL_GetJoystickVendorForID(SDL_JoystickID i) { (void)i; return 0; }
static inline Uint16 SDL_GetJoystickProductForID(SDL_JoystickID i) { (void)i; return 0; }
static inline bool SDL_IsJoystickVirtual(SDL_JoystickID i) { (void)i; return false; }
static inline void SDL_CloseGamepad(SDL_Gamepad *g) { (void)g; }
static inline void SDL_CloseJoystick(SDL_Joystick *j) { (void)j; }
static inline SDL_JoystickID *SDL_GetJoysticks(int *n) { *n = 0; return NULL; }
static inline void SDL_free(void *p) { (void)p; }
static inline bool SDL_GetGamepadButton(SDL_Gamepad *g, SDL_GamepadButton b) { (void)g, (void)b; return false; }
static inline Sint16 SDL_GetGamepadAxis(SDL_Gamepad *g, SDL_GamepadAxis a) { (void)g, (void)a; return 0; }
static inline int SDL_GetNumJoystickButtons(SDL_Joystick *j) { (void)j; return 0; }
static inline int SDL_GetNumJoystickAxes(SDL_Joystick *j) { (void)j; return 0; }
static inline int SDL_GetNumJoystickHats(SDL_Joystick *j) { (void)j; return 0; }
static inline Uint8 SDL_GetJoystickHat(SDL_Joystick *j, int h) { (void)j, (void)h; return 0; }
static inline bool SDL_GetJoystickButton(SDL_Joystick *j, int b) { (void)j, (void)b; return false; }
static inline Sint16 SDL_GetJoystickAxis(SDL_Joystick *j, int a) { (void)j, (void)a; return 0; }
static inline const bool *SDL_GetKeyboardState(int *n) { static bool k[512]; (void)n; return k; }
static inline uint32_t SDL_GetModState(void) { return 0; }
static inline bool SDL_RumbleGamepad(SDL_Gamepad *g, Uint16 a, Uint16 b, uint32_t ms)
{ (void)g, (void)a, (void)b, (void)ms; return true; }
static inline bool SDL_RumbleJoystick(SDL_Joystick *j, Uint16 a, Uint16 b, uint32_t ms)
{ (void)j, (void)a, (void)b, (void)ms; return true; }
"""

INPUT_C = r"""#include "port.h"
#include "platform.h"
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
int input_option(const char *arg, const char *value);

static u32 g_now;
u32 port_vblank_count(void) { return g_now; }
void session_pad(HostPad *pad) { (void)pad; }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("log ");
    vprintf(fmt, ap);
    va_end(ap);
}

/* START at 10 * k for k = 0 .. n - 1, each held 2 blanks; the held word at blank `at`. */
static u32 held_at(u32 at)
{
    HostPad pad[HOST_PADS];
    g_now = at;
    host_pads_read(pad);
    return pad[0].buttons;
}

int main(int argc, char **argv)
{
    u32 n = (u32)atoi(argv[1]);
    char *s = malloc(n * 16u + 1u), *p = s;
    (void)argc;
    for (u32 k = 0; k < n; k++) p += snprintf(p, 32, "%sSTART@%u+2", k ? "," : "", 10u * k);
    input_option("--input", s);
    printf("held 100 %x 101 %x 102 %x last %x past %x\n", held_at(1000), held_at(1001), held_at(1002),
           held_at(10u * (n - 1u)), held_at(10u * n));
    free(s);
    return 0;
}
"""


def compile_run(d, name, cl_args, runs):
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /TC /std:c11 /W4 /WX /O1 {cl_args} '
                       f'/Fe"{d / name}.exe""', cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
    if r.returncode or "warning" in r.stdout.lower():
        return r.stdout[-3000:] + r.stderr[-1000:], [[] for _ in runs]
    return "", [subprocess.run([str(d / f"{name}.exe"), *a], capture_output=True, text=True, timeout=60)
                .stdout.splitlines() for a in runs]


@unittest.skipUnless(HAVE_VC, NO_VC)
class Pads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = d = pathlib.Path(tempfile.mkdtemp())
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "game.h").write_text(GAME_H, encoding="utf-8")
        (d / "SDL3").mkdir()
        (d / "SDL3" / "SDL.h").write_text(SDL_H, encoding="utf-8")
        (d / "maple.c").write_text(MAPLE_C, encoding="utf-8")
        (d / "input.c").write_text(INPUT_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        inc = f'/I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "host"}" /I"{rd}"'
        cls.maple_build, (cls.no_init, cls.init) = compile_run(
            d, "maple", f'{inc} "{d / "maple.c"}" "{KIT / "sdk" / "maple_pad.c"}"', [[], ["init"]])
        cls.input_build, (cls.few, cls.many) = compile_run(
            d, "input", f'{inc} "{d / "input.c"}" "{KIT / "runtime" / "gfx" / "gfx_input.c"}"',
            [["200"], ["4100"]])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_controller_without_pdInitPeripheral(self):
        self.assertEqual(self.maple_build, "")
        out = self.no_init
        self.assertEqual(sum(x.startswith("log maple: the game reads the bus without pdInitPeripheral") for x in out),
                         1, out)
        self.assertIn("idle support f06fe on 0 press 0", out)     # the buttons, d-pad, triggers and stick
        self.assertIn("start support f06fe on 8 press 8", out)    # Start held: on and a press this pass
        self.assertIn("held support f06fe on 8 press 0", out)     # still held: no new press
        self.assertIn("portB support 0 on 0 press 0", out)        # no player 2: an empty port

    def test_pdInitPeripheral_unchanged(self):
        self.assertEqual(self.maple_build, "")
        out = self.init
        self.assertFalse(any("without pdInitPeripheral" in x for x in out), out)
        self.assertTrue(out[0].startswith("log maple: controllers on ports A..D"), out)
        self.assertEqual([x for x in out if not x.startswith("log ")],
                         [x for x in self.no_init if not x.startswith("log ")])

    def test_more_than_64_presses(self):
        self.assertEqual(self.input_build, "")
        self.assertIn("held 100 8 101 8 102 0 last 8 past 0", self.few)  # press 101 of 200 at its blanks
        self.assertFalse(any("dropped" in x for x in self.few), self.few)

    def test_dropped_presses_logged_once(self):
        self.assertEqual(self.input_build, "")
        logs = [x for x in self.many if "dropped" in x]
        self.assertEqual(logs, ['log input: --input holds at most 4096 presses: "START@40960+2" and the ones after it '
                                'are dropped'])
        held = re.search(r"last (\w+) past (\w+)", next(x for x in self.many if x.startswith("held "))).groups()
        self.assertEqual(held, ("0", "0"))  # 4100 asked: the 4097th and later never press


if __name__ == "__main__":
    unittest.main()
