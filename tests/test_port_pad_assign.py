"""A game's key screen on the port base (split from tests/test_port_buttons.py at its 400-line cap): the rows each player
assigns, kept in the settings and in the recording (input_ex_assign.c: padex, padsh for Share, padex5 for players 5..8),
and the screen's page (pad_ex_page.c: Share offered or not, a page, a list after the game's own rows, port_pad_list_top);
each built at /W4 /WX with a stub beside it and run; skipped without the 32-bit Visual Studio tools.

usage: python -W error tests/test_port_pad_assign.py
"""
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))
from test_port_buttons import BuildRun  # noqa: E402  (the build-and-run helper)


class PadAssign(BuildRun, unittest.TestCase):
    def test_assignments(self):
        self.build_run("as", r'''
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"
#define SLOTS 32
static char g_key[SLOTS][48];
static int g_value[SLOTS], g_keys, g_reads, g_writes, g_noted, g_noted_share, g_replaying, g_share_on = 1;
static u8 g_note[HOST_GAME_PADS][11]; /* players 1..4's first record: the ten buttons, then the left stick, as always kept */
static u8 g_share_note[HOST_GAME_PADS];
static int g_has_note, g_has_share_note;
int port_switch(const char *name, const char *label) { (void)label; return !strcmp(name, "pad-share") && g_share_on; }
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
    if (g_has_share_note && !strcmp(name, "padsh") && size == sizeof g_share_note) {
        memcpy(buf, g_share_note, size);
        return 1;
    }
    if (!g_has_note || strcmp(name, "padex") || size != sizeof g_note) return 0;
    memcpy(buf, g_note, size);
    return 1;
}
void session_save_read(const char *name, const void *buf, u32 size, int got)
{
    if (!strcmp(name, "padex") && size == sizeof g_note && got == 1) g_noted++;
    if (!strcmp(name, "padsh") && size == sizeof g_share_note && got == 1) g_noted_share++, memcpy(g_share_note, buf, size);
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
    g_note[2][10] = 1; /* an old recording's left stick: its last byte, now the row after Share */
    port_pad_ex_assign_load(3);
    CHECK(port_pad_ex_assign_get(2, PORT_PAD_EX_STICK_ROW) == 1 && port_pad_ex_assign_get(2, PORT_PAD_EX_SHARE_ROW) == 0);
    /* Share: saved and held like the rest, recorded apart (the first record's size is unchanged) */
    g_replaying = 0, g_has_note = 0, g_noted = 0;
    port_pad_ex_assign_set(0, PORT_PAD_EX_SHARE_ROW, 2);
    CHECK(!strcmp(g_key[g_keys - 1], "game.padex.p1.share"));
    CHECK(port_pad_ex_row_bit(PORT_PAD_EX_SHARE_ROW) == HOST_PAD_SHARE);
    pad[0].buttons = HOST_PAD_SHARE;
    input_ex_note(pad);
    CHECK(port_pad_ex_assign_codes(0, codes, 3) == 0x20u);
    port_pad_ex_assign_load(3);
    CHECK(g_noted == 1 && g_noted_share == 1 && g_share_note[0] == 2);
    g_replaying = 1, g_has_share_note = 1, g_reads = 0;
    port_pad_ex_assign_load(3); /* a recording with Share alone */
    CHECK(g_reads == 0 && port_pad_ex_assign_get(0, PORT_PAD_EX_SHARE_ROW) == 2 && port_pad_ex_assign_get(1, 0) == 0);
    puts("ok");
    return 0;
}
''', [KIT / "base" / "input_ex.c", KIT / "base" / "input_ex_assign.c"], "c")

    def test_page_list(self):
        """pad_ex_page.c: Share among the rows only when offered; in a list UP past the first, DOWN past the last and BACK
        leave and say so; a page wraps; port_pad_list_top keeps the row in view."""
        self.build_run("pg", r'''
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"
static int g_share_on;
int pad_style(void) { return 0; }
int port_setting_get_int(const char *key, int def) { (void)key; return def; }
void port_setting_set_int(const char *key, int value) { (void)key; (void)value; }
int session_note_replayed(const char *n, void *d, u32 s) { (void)n; (void)d; (void)s; return 0; }
int session_replaying(void) { return 0; }
void session_save_read(const char *n, const void *d, u32 s, int f) { (void)n; (void)d; (void)s; (void)f; }
int port_switch(const char *n, const char *l) { (void)n; (void)l; return g_share_on; }
void game_log_line(const char *fmt, ...) { (void)fmt; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
    static const char *const choices[3] = {"NONE", "A", "B"};
    int set;
    g_share_on = 1;
    port_pad_page_init(choices, 2);
    CHECK(port_pad_page_rows() == 11 && !strcmp(port_pad_page_row_name(0, 10), "SHARE") && !port_pad_page_is_stick(10));
    port_pad_page_enter();
    port_pad_page_open(0);
    CHECK(port_pad_page_press(0, PORT_PAD_PAGE_UP) == 1 && port_pad_page_cursor(0) == 10); /* a page wraps */
    CHECK(port_pad_page_press(0, PORT_PAD_PAGE_RIGHT) == 1 && !strcmp(port_pad_page_row_value(0, 10, &set), "A") && set);
    port_pad_page_keep();
    CHECK(port_pad_ex_assign_get(0, PORT_PAD_EX_SHARE_ROW) == 1);
    port_pad_page_list(1);
    port_pad_page_open_at(0, 99);
    CHECK(port_pad_page_showing(0) && port_pad_page_cursor(0) == 10);
    CHECK(port_pad_page_press(0, PORT_PAD_PAGE_DOWN) == PORT_PAD_PAGE_OUT_DOWN && !port_pad_page_showing(0));
    port_pad_page_open_at(0, 0);
    CHECK(port_pad_page_press(0, PORT_PAD_PAGE_DOWN) == 1 && port_pad_page_cursor(0) == 1);
    CHECK(port_pad_page_press(0, PORT_PAD_PAGE_UP) == 1);
    CHECK(port_pad_page_press(0, PORT_PAD_PAGE_UP) == PORT_PAD_PAGE_OUT_UP && !port_pad_page_showing(0));
    port_pad_page_open_at(0, 3);
    CHECK(port_pad_page_press(0, PORT_PAD_PAGE_BACK) == PORT_PAD_PAGE_OUT_UP && !port_pad_page_showing(0));
    CHECK(port_pad_list_top(0, 5, 6) == 0 && port_pad_list_top(0, 6, 6) == 1 && port_pad_list_top(1, 16, 6) == 11);
    CHECK(port_pad_list_top(11, 5, 6) == 5 && port_pad_list_top(3, 4, 6) == 3);
    puts("ok");
    return 0;
}
''', [KIT / "base" / "pad_ex_page.c", KIT / "base" / "input_ex_assign.c", KIT / "base" / "input_ex.c"], "c")

    def test_page_without_share(self):
        """With pad-share off (a check run, an old recording) the page has the rows it had."""
        self.build_run("pg0", r'''
#include <stdio.h>
#include "base.h"
int pad_style(void) { return 0; }
int port_setting_get_int(const char *key, int def) { (void)key; return def; }
void port_setting_set_int(const char *key, int value) { (void)key; (void)value; }
int session_note_replayed(const char *n, void *d, u32 s) { (void)n; (void)d; (void)s; return 0; }
int session_replaying(void) { return 0; }
void session_save_read(const char *n, const void *d, u32 s, int f) { (void)n; (void)d; (void)s; (void)f; }
int port_switch(const char *n, const char *l) { (void)n; (void)l; return 0; }
void game_log_line(const char *fmt, ...) { (void)fmt; }
int main(void)
{
    static const char *const choices[2] = {"NONE", "A"};
    port_pad_page_init(choices, 1);
    if (port_pad_page_rows() != 10) { printf("fail: %u rows\n", port_pad_page_rows()); return 1; }
    puts("ok");
    return 0;
}
''', [KIT / "base" / "pad_ex_page.c", KIT / "base" / "input_ex_assign.c", KIT / "base" / "input_ex.c"], "c")


if __name__ == "__main__":
    unittest.main()
