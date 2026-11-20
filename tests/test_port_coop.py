"""The port base's co-op and players 2..8 (split from tests/test_port_base.py at its 400-line cap): session.c keeps
players 2..8 in a fifth recording section only when one of them played, and base/coop_join.c's join and pick screen;
each built at /W4 /WX with a stub beside it and run; skipped without the 32-bit Visual Studio tools.

usage: python -W error tests/test_port_coop.py
"""
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))
from test_port_buttons import BuildRun  # noqa: E402  (the build-and-run helper)


SESSION_STUBS = r'''#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"
static u32 g_vb;
u32 port_vblank_count(void) { return g_vb; }
int port_extra_drawing(void) { return 0; }
int console_lockstep_pad(HostPad *pad) { (void)pad; return 0; }
s32 console_lockstep_timer(s32 live) { return live; }
int console_lockstep_time(HostTime *t) { (void)t; return 0; }
int input_live(void) { return 0; }
void port_log(const char *fmt, ...) { (void)fmt; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
'''


def ptrec_sections(path):
    """A recording's sections after the header, and the pad reads and vertical blanks between the fourth and a fifth."""
    import struct
    d = path.read_bytes()
    assert d[:8] == b"PORTREC1"
    at = 12 + struct.unpack_from("<I", d, 8)[0]
    sections = []
    for _ in range(4):
        n = struct.unpack_from("<I", d, at)[0]
        sections.append(d[at + 4:at + 4 + n])
        at += 4 + n
    trailer = struct.unpack_from("<II", d, at)
    at += 8
    if at < len(d):
        n = struct.unpack_from("<I", d, at)[0]
        sections.append(d[at + 4:at + 4 + n])
        at += 4 + n
    assert at == len(d), "bytes past the end"
    return sections, trailer


class SessionOthers(BuildRun, unittest.TestCase):
    """session.c session_pad_other: players 2..8 and every right stick are kept in a fifth section only when one of
    them is used, so a run only player 1 plays without the right stick writes the four-section file it always did; a
    recording with the section gives them back on replay, and one without it leaves them as the run has them."""

    def test_one_player_writes_four_sections(self):
        self.build_run("others1", SESSION_STUBS + r'''
int main(void)
{
    for (int i = 0; i < 5; i++) {
        HostPad pad[4] = {{0}};
        pad[0].buttons = i >= 2 ? 4u : 0u;
        g_vb = (u32)i;
        session_pad(&pad[0]);
        s8 rs[2] = {0, 0};
        CHECK(session_pad_other(0, NULL, rs, 1) == 1);
        for (int p = 1; p < 4; p++) CHECK(session_pad_other(p, &pad[p], rs, 0) == 0);
    }
    CHECK(session_write("one.ptrec", "test\n"));
    puts("ok");
    return 0;
}
''', [KIT / "base" / "session.c", KIT / "base" / "session_other.c", KIT / "base" / "session_events.c"], "c")
        sections, trailer = ptrec_sections(self.d / "one.ptrec")
        self.assertEqual(len(sections), 4)
        self.assertEqual(trailer, (5, 4))

    def test_players_2_to_8_round_trip(self):
        # a recording is written by one run and replayed by another, as port.exe does (the pad reads count from 0)
        self.build_run("others2", SESSION_STUBS + r'''
int main(void)
{
    HostPad pad = {0};
    for (int i = 0; i < 6; i++) {
        g_vb = (u32)i;
        session_pad(&pad);
        for (int p = 0; p < 8; p++) {
            HostPad o = {0};
            s8 rs[2] = {(s8)(p == 0 && i >= 1 ? 100 : 0), (s8)(p == 3 && i == 5 ? -128 : 0)};
            o.buttons = p == 1 && i >= 2 ? 0x200u : p == 7 && i == 4 ? 0x4u : 0u;
            o.x = (s8)(p == 1 ? -i : 0);
            CHECK(session_pad_other(p, p ? &o : NULL, rs, p == 1 || (p == 7 && i >= 3)) ==
                  (p <= 1 || (p == 7 && i >= 3)));
        }
    }
    CHECK(session_write("many.ptrec", "test\n"));
    puts("ok");
    return 0;
}
''', [KIT / "base" / "session.c", KIT / "base" / "session_other.c", KIT / "base" / "session_events.c"], "c")
        self.build_run("others2r", SESSION_STUBS + r'''
int main(void)
{
    HostPad pad = {0};
    CHECK(session_option("--replay", "many.ptrec") == 2 && session_replaying());
    for (int i = 0; i < 6; i++) {
        g_vb = (u32)i;
        session_pad(&pad);
        for (int p = 0; p < 8; p++) {
            HostPad o = {0};
            s8 rs[2] = {55, 55}; /* live input the replay replaces */
            o.buttons = 0xffffu;
            int on = session_pad_other(p, p ? &o : NULL, rs, 1);
            CHECK(on == (p <= 1 || (p == 7 && i >= 3)));
            CHECK(rs[0] == (p == 0 && i >= 1 ? 100 : 0) && rs[1] == (p == 3 && i == 5 ? -128 : 0));
            if (!p) continue;
            CHECK(o.buttons == (p == 1 && i >= 2 ? 0x200u : p == 7 && i == 4 ? 0x4u : 0u));
            CHECK(o.x == (s8)(p == 1 ? -i : 0));
        }
    }
    puts("ok");
    return 0;
}
''', [KIT / "base" / "session.c", KIT / "base" / "session_other.c", KIT / "base" / "session_events.c"], "c")
        sections, _ = ptrec_sections(self.d / "many.ptrec")
        self.assertEqual(len(sections), 5)
        self.assertEqual(len(sections[4]) % 20, 0)

    def test_a_four_section_replay_leaves_players_2_to_8(self):
        self.build_run("others3", SESSION_STUBS + r'''
int main(void)
{
    HostPad pad = {0};
    session_pad(&pad);
    CHECK(session_write("old.ptrec", "test\n"));
    puts("ok");
    return 0;
}
''', [KIT / "base" / "session.c", KIT / "base" / "session_other.c", KIT / "base" / "session_events.c"], "c")
        self.build_run("others3r", SESSION_STUBS + r'''
int main(void)
{
    HostPad pad = {0};
    CHECK(session_option("--replay", "old.ptrec") == 2 && session_replaying());
    session_pad(&pad);
    HostPad o = {0};
    s8 rs[2] = {9, -9};
    o.buttons = 0x10u;
    CHECK(session_pad_other(2, &o, rs, 1) == 1 && o.buttons == 0x10u && rs[0] == 9 && rs[1] == -9);
    puts("ok");
    return 0;
}
''', [KIT / "base" / "session.c", KIT / "base" / "session_other.c", KIT / "base" / "session_events.c"], "c")


class CoopJoin(BuildRun, unittest.TestCase):
    """coop_join.c: joining, picks no other player holds, ready, and the opener starting or closing the screen."""

    def test_coop_off(self):
        # --coop off: never enabled, the screen never opens (read once, so its own run)
        self.build_run("coopoff", r'''
#include <stdio.h>
#include <string.h>
#define PORT_COOP 3
#include "coop_join.c"
const char *host_arg(const char *name) { return strcmp(name, "--coop") ? NULL : "off"; }
int port_setting_get_int(const char *key, int def) { (void)key; return def; }
int port_play_option(const char *name, int live) { (void)name; return live; }
int port_extra_drawing(void) { return 0; }
void game_log_line(const char *fmt, ...) { (void)fmt; }
int main(void)
{
    static const char *const names[] = {"tank"};
    if (port_coop_enabled() || port_coop_join_open(0, names, 1, 1) || port_coop_join_showing()) return 1;
    puts("ok");
    return 0;
}
''', [], "c")

    def test_join_pick_ready_start(self):
        self.build_run("coopjoin", r'''
#include <stdio.h>
#include <string.h>
#define PORT_COOP 3
#include "coop_join.c"
static const char *g_cli;
const char *host_arg(const char *name) { return strcmp(name, "--coop") ? NULL : g_cli; }
int port_setting_get_int(const char *key, int def) { (void)key; return def; }
int port_play_option(const char *name, int live) { (void)name; return live; }
int port_extra_drawing(void) { return 0; }
void game_log_line(const char *fmt, ...) { (void)fmt; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
#define OK PORT_PAD_PAGE_OK
#define BACK PORT_PAD_PAGE_BACK
#define RIGHT PORT_PAD_PAGE_RIGHT
int main(void)
{
    static const char *const names[] = {"tank", "plane", "truck"};
    char line[64];
    g_cli = NULL;
    CHECK(port_coop_max() == 3 && port_coop_enabled());
    CHECK(port_coop_join_open(1, names, 3, 0) && port_coop_join_showing());
    CHECK(port_coop_join_press(0, OK) == 0);               /* pad 1 joins on the first choice not taken */
    port_coop_join_line(0, line, sizeof line);
    CHECK(!strcmp(line, "P1  < plane >"));
    CHECK(port_coop_join_press(0, RIGHT) == 0);            /* tank is pad 2's: skipped */
    port_coop_join_line(0, line, sizeof line);
    CHECK(!strcmp(line, "P1  < truck >"));
    CHECK(port_coop_join_press(2, OK) == 0);               /* pad 3 joins */
    port_coop_join_press(3, OK);                           /* a fourth is over game.json coop 3 */
    port_coop_join_line(3, line, sizeof line);
    CHECK(!strcmp(line, "P4  PRESS A TO JOIN"));
    CHECK(port_coop_join_press(2, BACK) == 0);             /* pad 3 leaves */
    port_coop_join_press(0, OK);
    port_coop_join_press(1, OK);
    CHECK(port_coop_join_press(1, OK) == 1);               /* all ready: the opener starts it */
    CHECK(!port_coop_join_showing() && port_coop_players() == 2);
    CHECK(port_coop_port(0) == 1 && port_coop_pick(0) == 0 && port_coop_port(1) == 0 && port_coop_pick(1) == 2);
    CHECK(port_coop_port(2) == -1);
    port_coop_end();
    CHECK(port_coop_players() == 0);
    CHECK(port_coop_join_open(0, names, 3, 1));            /* shared: copies of one vehicle */
    port_coop_join_press(1, OK);
    port_coop_join_line(1, line, sizeof line);
    CHECK(!strcmp(line, "P2  < tank >"));
    CHECK(port_coop_join_press(0, BACK) == -1 && !port_coop_join_showing() && port_coop_players() == 0);
    puts("ok");
    return 0;
}
''', [], "c")


if __name__ == "__main__":
    unittest.main()
