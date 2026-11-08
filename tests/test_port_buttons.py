"""The port base's extended buttons and per-game settings (split from tests/test_port_base.py at its 400-line cap):
base.h port_pad_ex_* held / pressed / name (input_ex.c), the left stick as movement (pad_stick.c), twin-stick
(pad_twin.c) and the per-game settings store (host_settings.cpp): each built at /W4 /WX with a stub beside it and run;
skipped without the 32-bit Visual Studio tools.

usage: python -W error tests/test_port_buttons.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))


class BuildRun:
    """A C or C++ file built with the base's sources at /W4 /WX and run (ExButtons; test_port_base.SessionAhead)."""

    @classmethod
    def setUpClass(cls):
        import game_config
        cls.vc = game_config.vcvars32()
        cls.d = Path(tempfile.mkdtemp(prefix="exbuttons_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def build_run(self, name, source, files, std):
        import subprocess
        if not self.vc:
            self.skipTest("no 32-bit Visual Studio environment")
        src = self.d / (name + (".cpp" if std == "c++20" else ".c"))
        src.write_text(source)
        bat = self.d / (name + ".bat")
        srcs = " ".join('"%s"' % f for f in [src] + files)
        bat.write_text('@echo off\r\ncall "%s" >nul\r\ncd /d "%s"\r\ncl /nologo /W4 /WX %s /I"%s" %s /Fe%s.exe /Fo"%s/" && "%s\\%s.exe"\r\n' %
                       (self.vc, self.d, "/std:c++20 /EHsc" if std == "c++20" else "/TC", KIT / "base", srcs, name, self.d, self.d, name), encoding="ascii")
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ExButtons(BuildRun, unittest.TestCase):
    """base.h port_pad_ex_*: held / pressed / name (input_ex.c) and the per-game settings store (host_settings.cpp), built
    with a stub beside them and run."""

    def test_left_stick_as_movement(self):
        """pad_stick.c, with input_ex_assign.c: dead zone, eight sectors, no chatter, the d-pad wins, off changes nothing."""
        self.build_run("stick", r"""
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"
int pad_style(void) { return 0; }
int port_setting_get_int(const char *key, int dflt) { (void)key; return dflt; }
void port_setting_set_int(const char *key, int value) { (void)key; (void)value; }
int session_note_replayed(const char *n, void *d, u32 s) { (void)n; (void)d; (void)s; return 0; }
int session_replaying(void) { return 0; }
void session_save_read(const char *n, const void *d, u32 s, int f) { (void)n; (void)d; (void)s; (void)f; }
#define L HOST_PAD_LEFT
#define R HOST_PAD_RIGHT
#define U HOST_PAD_UP
#define D HOST_PAD_DOWN
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
    HostPad pad[HOST_PADS];
    CHECK(port_pad_stick_dirs(0, 0, 0) == 0);
    CHECK(port_pad_stick_dirs(30, -30, 0) == 0);           /* drift */
    CHECK(port_pad_stick_dirs(47, 0, 0) == 0);             /* under ON */
    CHECK(port_pad_stick_dirs(48, 0, 0) == R);
    CHECK(port_pad_stick_dirs(40, 0, R) == R);             /* over OFF: it stays */
    CHECK(port_pad_stick_dirs(31, 0, R) == 0);             /* under OFF: it lets go, so nothing sticks */
    CHECK(port_pad_stick_dirs(-127, 0, 0) == L);
    CHECK(port_pad_stick_dirs(0, -127, 0) == U);
    CHECK(port_pad_stick_dirs(0, 127, 0) == D);
    CHECK(port_pad_stick_dirs(100, 100, 0) == (R | D));    /* a diagonal */
    CHECK(port_pad_stick_dirs(-100, -100, 0) == (L | U));
    CHECK(port_pad_stick_dirs(100, 40, 0) == R);           /* under 22.5 degrees */
    CHECK(port_pad_stick_dirs(100, 42, 0) == (R | D));
    CHECK(port_pad_stick_dirs(100, 36, R | D) == (R | D)); /* the diagonal holds near its edge */
    CHECK(port_pad_stick_dirs(100, 30, R | D) == R);
    CHECK(port_pad_stick_dirs(100, 36, R) == R);
    CHECK(port_pad_stick_dirs(-100, 0, R) == L);           /* across the centre between two reads */
    memset(pad, 0, sizeof pad);
    pad[0].x = 127;
    port_pad_ex_assign_load(8);
    port_pad_stick_apply(pad);
    CHECK(pad[0].buttons == 0);                            /* not chosen: nothing */
    port_pad_ex_assign_set(0, PORT_PAD_EX_STICK_ROW, 1);
    pad[0].x = 127, pad[1].x = 127;
    port_pad_stick_apply(pad);
    CHECK(pad[0].buttons == R && pad[1].buttons == 0);     /* one player's choice */
    CHECK(pad[0].x == 127);                                /* the raw stick still goes to the game */
    pad[0].buttons = L;
    port_pad_stick_apply(pad);
    CHECK(pad[0].buttons == L);                            /* the d-pad wins on its axis */
    pad[0].buttons = U;
    port_pad_stick_apply(pad);
    CHECK(pad[0].buttons == (U | R));                      /* and only there */
    pad[0].buttons = 0;
    port_pad_stick_apply(pad);
    CHECK(pad[0].buttons == R);                            /* the stick is back after a tap */
    pad[0].x = 0, pad[0].buttons = 0;
    port_pad_stick_apply(pad);
    CHECK(pad[0].buttons == 0);
    CHECK(port_pad_ex_row_bit(PORT_PAD_EX_STICK_ROW) == 0);
    {
        u32 codes[8] = {1, 2, 4, 8, 16, 32, 64, 128};
        CHECK(port_pad_ex_assign_codes(0, codes, 8) == 0); /* the row presses no action */
    }
    return 0;
}
""", [KIT / "base" / "pad_stick.c", KIT / "base" / "input_ex_assign.c", KIT / "base" / "input_ex.c"], "c")

    def test_twin_stick(self):
        """pad_twin.c: only in a fight (the game's function entered lately), the right stick turns, the left stick sideways
        sidesteps and is no d-pad there, the d-pad wins, a right stick the player put on an action keeps it, off and a
        scripted run change nothing, a button the console lacks turns it off."""
        self.build_run("twin", r"""
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"
static const PortAction k_act[] = {{"L", "LB"}, {"R", "RB"}, {"LEFT", "LEFT"}, {"RIGHT", "RIGHT"}};
static const char *const k_src[] = {"none", "LB", "RB", "LEFT", "RIGHT", "RSLEFT", "RSRIGHT"};
static const u32 k_bit[] = {0, HOST_PAD_LB, HOST_PAD_RB, HOST_PAD_LEFT, HOST_PAD_RIGHT, HOST_PAD_RS_LEFT, HOST_PAD_RS_RIGHT};
static int g_scripted;
static u32 g_rs_bound;
const PortAction *console_actions(unsigned *n) { *n = 4; return k_act; }
u32 input_name_bit(const char *name) { for (int i = 1; i < 7; i++) if (!strcmp(name, k_src[i])) return k_bit[i]; return 0; }
int input_scripted(void) { return g_scripted; }
unsigned input_source_count(void) { return 7; }
const char *input_source_name(unsigned s) { return s < 7 ? k_src[s] : "none"; }
u32 input_bind_get(unsigned i) { return i == 2 ? g_rs_bound : 0; }
void port_log(const char *fmt, ...) { va_list v; va_start(v, fmt); vprintf(fmt, v); va_end(v); }
u32 port_vblank_count(void) { return 0; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
static HostPad pad[HOST_PADS];
static void set(u32 b, int x, int y) { memset(pad, 0, sizeof pad); pad[0].buttons = b, pad[0].x = (s8)x, pad[0].y = (s8)y; }
int main(void)
{
    static const PortTwinStick fight = {0x06001000u, {"LEFT", "RIGHT"}, {"L", "R"}}, bad = {0x06001000u, {"LEFT", "RIGHT"}, {"L", "Q"}};
    port_twin_start(NULL);
    port_twin_hit();
    set(HOST_PAD_RS_RIGHT, -127, 0);
    port_twin_apply(pad);
    CHECK(port_twin_fn == 0 && !port_twin_available() && pad[0].buttons == HOST_PAD_RS_RIGHT && pad[0].x == -127); /* none */
    port_twin_start(&fight);
    CHECK(port_twin_fn == 0x06001000u && port_twin_available() && port_twin_get());
    for (int i = 0; i < 9; i++) port_twin_apply(pad);
    set(HOST_PAD_RS_RIGHT, -127, 0);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == HOST_PAD_RS_RIGHT && pad[0].x == -127);            /* no fight yet: as it was */
    port_twin_hit();
    set(HOST_PAD_RS_RIGHT, 0, -127);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == (HOST_PAD_RS_RIGHT | HOST_PAD_RIGHT) && pad[0].y == -127); /* turns; the stick still walks */
    set(HOST_PAD_RS_LEFT, -127, 0);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == (HOST_PAD_RS_LEFT | HOST_PAD_LEFT | HOST_PAD_LB) && pad[0].x == 0); /* sidesteps, no d-pad left */
    set(0, 100, 0);
    pad[1].x = 100;
    port_twin_apply(pad);
    CHECK(pad[0].buttons == HOST_PAD_RB && pad[0].x == 0 && pad[1].buttons == HOST_PAD_RB); /* every player */
    set(0, 40, 0);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == 0 && pad[0].x == 0);                                /* under ON: nothing, and no d-pad */
    set(HOST_PAD_LEFT, -128, 0);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == HOST_PAD_LEFT && pad[0].x == -128);                 /* the d-pad (an arrow key) still turns */
    g_rs_bound = 1u << 6;
    set(HOST_PAD_RS_RIGHT, 127, 0);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == (HOST_PAD_RS_RIGHT | HOST_PAD_RB));                 /* the player's right stick keeps its action */
    g_rs_bound = 0;
    for (int i = 0; i < 3; i++) port_twin_apply(pad);
    set(HOST_PAD_RS_RIGHT, 127, 0);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == 0x0u + HOST_PAD_RS_RIGHT && pad[0].x == 127);       /* the fight's function gone 8 reads: as it was */
    port_twin_set(0);
    port_twin_hit();
    set(HOST_PAD_RS_RIGHT, 127, 0);
    port_twin_apply(pad);
    CHECK(!port_twin_get() && pad[0].buttons == HOST_PAD_RS_RIGHT && pad[0].x == 127); /* off */
    CHECK(port_twin_option("--twin-stick", "on") == 2 && port_twin_option("--twin", "on") == 0);
    port_twin_start(&fight);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == (HOST_PAD_RS_RIGHT | HOST_PAD_RIGHT | HOST_PAD_RB));
    g_scripted = 1;
    port_twin_start(&fight);
    port_twin_hit();
    set(HOST_PAD_RS_RIGHT, 127, 0);
    port_twin_apply(pad);
    CHECK(pad[0].buttons == (HOST_PAD_RS_RIGHT | HOST_PAD_RIGHT | HOST_PAD_RB)); /* a script that asks for it has it */
    port_twin_set(0);
    port_twin_option("--twin-stick", "off");
    port_twin_start(&bad);
    CHECK(port_twin_fn == 0 && !port_twin_available());                         /* the console has no Q */
    return 0;
}
""", [KIT / "base" / "pad_twin.c"], "c")

    def test_twin_stick_off_in_a_scripted_run(self):
        """pad_twin.c: a scripted run that does not ask for twin-stick has it off, so older scripts press what they did."""
        self.build_run("twin2", r"""
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"
static const PortAction k_act[] = {{"L", "LB"}, {"R", "RB"}, {"LEFT", "LEFT"}, {"RIGHT", "RIGHT"}};
const PortAction *console_actions(unsigned *n) { *n = 4; return k_act; }
u32 input_name_bit(const char *name) { return !strcmp(name, "LB") ? HOST_PAD_LB : !strcmp(name, "RB") ? HOST_PAD_RB : !strcmp(name, "LEFT") ? HOST_PAD_LEFT : HOST_PAD_RIGHT; }
int input_scripted(void) { return 1; }
unsigned input_source_count(void) { return 1; }
const char *input_source_name(unsigned s) { (void)s; return "none"; }
u32 input_bind_get(unsigned i) { (void)i; return 0; }
void port_log(const char *fmt, ...) { va_list v; va_start(v, fmt); vprintf(fmt, v); va_end(v); }
u32 port_vblank_count(void) { return 0; }
int main(void)
{
    static const PortTwinStick fight = {0x06001000u, {"LEFT", "RIGHT"}, {"L", "R"}};
    HostPad pad[HOST_PADS] = {{HOST_PAD_RS_RIGHT, 0, 0, -127, 0}};
    port_twin_start(&fight);
    port_twin_hit();
    port_twin_apply(pad);
    if (pad[0].buttons != HOST_PAD_RS_RIGHT || pad[0].x != -127) { printf("fail: changed %x %d\n", pad[0].buttons, pad[0].x); return 1; }
    return 0;
}
""", [KIT / "base" / "pad_twin.c"], "c")

    def test_held_pressed_and_names(self):
        self.build_run("ex", r'''
#include <stdio.h>
#include <string.h>
#include "base.h"
static int g_style;
int pad_style(void) { return g_style; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
    HostPad pad[HOST_PADS] = {0};
    CHECK(port_pad_ex_held(0) == 0 && port_pad_ex_pressed(0) == 0 && port_pad_ex_held(9) == 0);
    pad[1].buttons = HOST_PAD_LB | HOST_PAD_RS_UP | HOST_PAD_A; /* a Dreamcast bit is not a modern one */
    input_ex_note(pad);
    CHECK(port_pad_ex_held(1) == (HOST_PAD_LB | HOST_PAD_RS_UP));
    CHECK(port_pad_ex_held(0) == 0);
    pad[1].buttons = HOST_PAD_LB; /* RS_UP released, LB still held: only the new press is "pressed" */
    input_ex_note(pad);
    CHECK(port_pad_ex_pressed(1) == (HOST_PAD_LB | HOST_PAD_RS_UP)); /* both were new since the last call */
    CHECK(port_pad_ex_pressed(1) == 0);
    pad[1].buttons = 0;
    input_ex_note(pad);
    pad[1].buttons = HOST_PAD_RB;
    input_ex_note(pad);
    CHECK(port_pad_ex_pressed(1) == HOST_PAD_RB && port_pad_ex_held(1) == HOST_PAD_RB);
    g_style = 1;
    CHECK(!strcmp(port_pad_ex_name(HOST_PAD_BACK, 0), "View"));
    g_style = 2;
    CHECK(!strcmp(port_pad_ex_name(HOST_PAD_LB, 0), "L1") && !strcmp(port_pad_ex_name(HOST_PAD_GUIDE, 0), "PS"));
    g_style = 0;
    CHECK(!strcmp(port_pad_ex_name(HOST_PAD_L3, 0), "Left stick click") && !strcmp(port_pad_ex_name(0x1, 0), ""));
    puts("ok");
    return 0;
}
''', [KIT / "base" / "input_ex.c"], "c")

    def test_assignments(self):
        self.build_run("as", r'''
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"
#define SLOTS 32
static char g_key[SLOTS][48];
static int g_value[SLOTS], g_keys, g_reads, g_writes, g_noted, g_replaying;
static u8 g_note[HOST_PADS][PORT_PAD_EX_STICK_ROW + 1];
static int g_has_note;
int pad_style(void) { return 0; }
int port_setting_get_int(const char *key, int def)
{
    g_reads++;
    for (int i = 0; i < g_keys; i++)
        if (!strcmp(g_key[i], key)) return g_value[i];
    return def;
}
void port_setting_set_int(const char *key, int value)
{
    g_writes++;
    for (int i = 0; i < g_keys; i++)
        if (!strcmp(g_key[i], key)) {
            g_value[i] = value;
            return;
        }
    strcpy_s(g_key[g_keys], sizeof g_key[0], key);
    g_value[g_keys++] = value;
}
int session_replaying(void) { return g_replaying; }
int session_note_replayed(const char *name, void *buf, u32 size)
{
    if (!g_has_note || strcmp(name, "padex") || size != sizeof g_note) return 0;
    memcpy(buf, g_note, size);
    return 1;
}
void session_save_read(const char *name, const void *buf, u32 size, int got)
{
    (void)buf;
    if (!strcmp(name, "padex") && size == sizeof g_note && got == 1) g_noted++;
}
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
    static const u32 codes[3] = {0x10u, 0x20u, 0x40u};
    HostPad pad[HOST_PADS] = {0};
    port_pad_ex_assign_load(3);
    CHECK(g_noted == 0); /* none assigned: the recording is unchanged */
    pad[1].buttons = HOST_PAD_LB | HOST_PAD_RS_UP;
    input_ex_note(pad);
    CHECK(port_pad_ex_assign_codes(1, codes, 3) == 0); /* every row starts unassigned */
    for (unsigned r = 0; r < PORT_PAD_EX_ROWS; r++) CHECK(port_pad_ex_assign_get(0, r) == 0);
    CHECK(port_pad_ex_row_bit(0) == HOST_PAD_LB && port_pad_ex_row_bit(6) == HOST_PAD_RS_UP && port_pad_ex_row_bit(PORT_PAD_EX_ROWS) == 0);
    port_pad_ex_assign_set(1, 0, 2);
    CHECK(g_writes == 1 && !strcmp(g_key[0], "game.padex.p2.lb") && g_value[0] == 2); /* saved at once, per player */
    port_pad_ex_assign_set(1, 0, 2);
    port_pad_ex_assign_set(HOST_PADS, 0, 2);
    port_pad_ex_assign_set(0, PORT_PAD_EX_STICK_ROW + 1, 2);
    CHECK(g_writes == 1);
    CHECK(port_pad_ex_assign_codes(1, codes, 3) == 0x20u);
    port_pad_ex_assign_set(1, 6, 3);
    CHECK(port_pad_ex_assign_codes(1, codes, 3) == 0x60u); /* two rows held: ORed */
    CHECK(port_pad_ex_assign_codes(0, codes, 3) == 0);     /* the other player's rows are their own */
    pad[1].buttons = HOST_PAD_RB;
    input_ex_note(pad);
    CHECK(port_pad_ex_assign_codes(1, codes, 3) == 0);
    port_pad_ex_assign_set(1, 6, 0); /* unassigning is saved too, and a choice past the game's actions is not taken */
    CHECK(g_value[1] == 0);
    port_pad_ex_assign_set(1, 1, 9);
    port_setting_set_int("game.padex.p1.back", 9);
    g_noted = 0;
    port_pad_ex_assign_load(3);
    CHECK(port_pad_ex_assign_get(1, 0) == 2 && port_pad_ex_assign_get(1, 1) == 0 && port_pad_ex_assign_get(0, 4) == 0);
    CHECK(port_pad_ex_assign_codes(1, codes, 3) == 0);
    CHECK(g_noted == 1); /* some assigned: kept with the recording */
    g_reads = 0;
    g_replaying = 1;
    g_has_note = 1;
    g_note[0][3] = 1;
    port_pad_ex_assign_load(3);
    CHECK(g_reads == 0 && port_pad_ex_assign_get(0, 3) == 1 && port_pad_ex_assign_get(1, 0) == 0); /* a replay takes the recording's, no settings */
    puts("ok");
    return 0;
}
''', [KIT / "base" / "input_ex.c", KIT / "base" / "input_ex_assign.c"], "c")

    def test_settings_store(self):
        self.build_run("st", r'''
#include <cstdio>
#include <fstream>
#include <string>
#include "base.h"
#include "host_internal.hpp"
extern "C" void port_log(const char *, ...) {}
#define CHECK(c) do { if (!(c)) { std::printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main()
{
    CHECK(port_setting_get_int("game.a", 7) == 7); // no settings file: the default
    port_setting_set_int("game.a", 3);             // and nothing is written
    CHECK(port_setting_get_int("game.a", 7) == 7);
    host::settings_use("st_settings.txt");
    { std::ofstream o("st_settings.txt"); o << "window=800x600\ngame.b=5 # a comment\n"; }
    CHECK(port_setting_get_int("game.b", 0) == 5 && port_setting_get_int("game.c", 9) == 9);
    port_setting_set_int("game.c", -4);
    port_setting_set_int("game.b", 6);
    port_setting_set_int("bind-A", 1); // not a game key: refused
    CHECK(port_setting_get_int("game.c", 9) == -4 && port_setting_get_int("game.b", 0) == 6);
    std::vector<std::string> args = host::settings_args("st_settings.txt");
    CHECK(args.size() == 2 && args[0] == "--window"); // game.* are not options
    std::puts("ok");
    return 0;
}
''', [KIT / "base" / "host_settings.cpp"], "c++20")


if __name__ == "__main__":
    unittest.main()
