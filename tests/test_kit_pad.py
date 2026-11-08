"""The kit's controllers (port/sdk/maple_pad.c) and scripted presses (port/base/input_script.c) in behaviour:
compiled at /W4 /WX with a synthetic game.h and stub host calls, then run.
1. A game that starts the peripheral system through an SDK entry the port does not bind (Power Stone 2 calls a
   newer library's five-argument variant of pdInitPeripheral, which its registry leaves unnamed) still finds a
   standard controller on port A at the first server pass: support has the buttons and axes, a held Start reads as
   on and as a press. Without it the record kept support 0 and the game read every pad, scripted or real, as
   unplugged. A game that calls pdInitPeripheral gets the same record and no extra log line.
2. --input keeps far more than 64 presses (Power Stone 2's input sweeps were cut short without a word): a press past
   the 64th is held at its vertical blank; past MAX_SCRIPT the rest are dropped with one log line naming the first.
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

INPUT_C = r"""#include "base.h"
#include "console.h"
#include "host.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

static u32 g_now;
u32 port_vblank_count(void) { return g_now; }
const PortAction *console_actions(unsigned *n) { *n = 0; return NULL; }
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
    u32 trig[2];
    g_now = at;
    return input_script_buttons(0, trig);
}

int main(int argc, char **argv)
{
    u32 n = (u32)atoi(argv[1]);
    char *s = malloc(n * 16u + 1u), *p = s;
    (void)argc;
    for (u32 k = 0; k < n; k++) p += snprintf(p, 32, "%sSTART@%u+2", k ? "," : "", 10u * k);
    input_script_option("--input", s);
    printf("held 100 %x 101 %x 102 %x last %x past %x\n", held_at(1000), held_at(1001), held_at(1002),
           held_at(10u * (n - 1u)), held_at(10u * n));
    free(s);
    return 0;
}
"""


# The player's assignments, as input_binds_apply leaves the pad a recording then sees.
BIND_C = r"""#define _CRT_SECURE_NO_WARNINGS
#include "base.h"
#include "console.h"
#include "host.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

u32 port_vblank_count(void) { return 0; }
static const PortAction k_actions[] = {{"A", "A"}, {"B", "B"}, {"RT", "RT"}, {"UP", "UP"}};
const PortAction *console_actions(unsigned *count) { *count = 4; return k_actions; }
void port_log(const char *fmt, ...) { (void)fmt; }

/* What the pad reads as with the named host buttons held; "RT" is the trigger, "RSUP" the right stick. */
static void show(const char *what, const char *held)
{
    HostPad pad[HOST_PADS];
    char names[64], *one;
    memset(pad, 0, sizeof pad);
    strcpy(names, held);
    for (one = strtok(names, ","); one; one = strtok(NULL, ",")) {
        if (!strcmp(one, "RT")) pad[0].r = 255;
        else pad[0].buttons |= input_name_bit(one);
    }
    input_binds_apply(pad);
    printf("%s A%d B%d UP%d LB%d rt%d stick%d\n", what, (pad[0].buttons & input_name_bit("A")) != 0,
           (pad[0].buttons & input_name_bit("B")) != 0, (pad[0].buttons & input_name_bit("UP")) != 0,
           (pad[0].buttons & input_name_bit("LB")) != 0, pad[0].r >= 128, pad[0].y);
}

int main(int argc, char **argv)
{
    char text[64];
    (void)argc;
    show("untouched", "LB");
    input_bind_option("--bind-A", "A+LB");          /* a second button for A */
    show("added-lb", "LB");
    show("added-a", "A");
    input_bind_option("--bind-A", "B");             /* A and B change places */
    input_bind_option("--bind-B", "A");
    show("swap-a", "A");
    show("swap-b", "B");
    input_bind_option("--bind-RT", "LB");           /* a trigger taken by a button; the trigger itself no longer counts */
    show("trigger-lb", "LB");
    show("trigger-rt", "RT");
    input_bind_option("--bind-UP", "none");         /* unassigned: the button does nothing */
    show("none", "UP");
    printf("text %s\n", input_bind_text(2, text, sizeof text));
    if (!strcmp(argv[1], "script")) {
        input_script_option("--input", "START@1000+2");    /* a scripted run ignores all of it */
        show("scripted-a", "A");
        show("scripted-lb", "LB");
    }
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
        (d / "maple.c").write_text(MAPLE_C, encoding="utf-8")
        (d / "input.c").write_text(INPUT_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        inc = f'/I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "base"}" /I"{rd}"'
        cls.maple_build, (cls.no_init, cls.init) = compile_run(
            d, "maple", f'{inc} "{d / "maple.c"}" "{KIT / "sdk" / "maple_pad.c"}"', [[], ["init"]])
        cls.input_build, (cls.few, cls.many) = compile_run(
            d, "input", f'{inc} "{d / "input.c"}" "{KIT / "base" / "input_script.c"}"',
            [["200"], ["4100"]])
        (d / "drv_bind.c").write_text(BIND_C, encoding="utf-8")
        cls.bind_build, (cls.bind, cls.bind_script) = compile_run(
            d, "bind", f'{inc} "{d / "drv_bind.c"}" "{KIT / "base" / "input_script.c"}" "{KIT / "base" / "input_bind.c"}" "{KIT / "base" / "pad_twin.c"}"',
            [["plain"], ["script"]])

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

    def test_assignments_press_the_default_button(self):
        self.assertEqual(self.bind_build, "")
        for line in ("untouched A0 B0 UP0 LB1 rt0 stick0",  # nothing assigned: the new button reaches no action
                     "added-lb A1 B0 UP0 LB1 rt0 stick0", "added-a A1 B0 UP0 LB0 rt0 stick0",
                     "swap-a A0 B1 UP0 LB0 rt0 stick0", "swap-b A1 B0 UP0 LB0 rt0 stick0",
                     "trigger-lb A0 B0 UP0 LB1 rt1 stick0", "trigger-rt A0 B0 UP0 LB0 rt0 stick0",
                     "none A0 B0 UP0 LB0 rt0 stick0", "text LB"):
            self.assertIn(line, self.bind)

    def test_a_scripted_run_ignores_assignments(self):
        self.assertEqual(self.bind_build, "")
        self.assertIn("scripted-a A1 B0 UP0 LB0 rt0 stick0", self.bind_script)
        self.assertIn("scripted-lb A0 B0 UP0 LB1 rt0 stick0", self.bind_script)

    def test_assignments_are_applied_before_the_recording(self):
        text = (KIT / "base" / "input.c").read_text(encoding="utf-8")
        self.assertLess(text.index("input_binds_apply(pad);"), text.index("session_pad(&pad[0]);"))


if __name__ == "__main__":
    unittest.main()
