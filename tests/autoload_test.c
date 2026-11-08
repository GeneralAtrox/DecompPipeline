/* The save's auto-load (port/base/autoload.c) against stubs of the clock, the session and the console: when it starts,
 * what it presses and when, the word it puts in, the game's load it calls (once, with its wait's word set only for the
 * call), the recording's note, and giving the pad back. Run by
 * tests/test_autoload.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "autoload.h"
u32 autoload_buttons(u32 *own); /* declared in host.h, for input.c */

static u32 g_vb, g_poke_addr, g_poke_value, g_pokes, g_word, g_calls, g_call_fn, g_call_arg, g_word_in_call;
static int g_replay, g_note, g_note_on, g_scripted, g_has, g_asked, g_recorded, g_fail;

u32 port_vblank_count(void) { return g_vb; }
void port_log(const char *fmt, ...) { (void)fmt; }
int input_scripted(void) { return g_scripted; }
u32 input_button_bit(const char *name) { return !strcmp(name, "A") ? 0x4u : !strcmp(name, "B") ? 0x2u : !strcmp(name, "UP") ? 0x10u : 0u; }
int session_replaying(void) { return g_replay; }
int session_note_replayed(const char *name, void *buf, u32 size)
{
    if (!g_note || strcmp(name, "*auto-load-call") || size != 1) return 0;
    *(u8 *)buf = (u8)g_note_on;
    return 1;
}
void session_save_read(const char *name, const void *buf, u32 size, int got)
{
    if (!strcmp(name, "*auto-load-call") && size == 1 && got && *(const u8 *)buf == 1) g_recorded++;
}
static int has_save(void) { g_asked++; return g_has; }
static void poke16(u32 addr, u32 value) { g_poke_addr = addr, g_poke_value = value, g_pokes++, g_word = value; }
static u32 peek16(u32 addr) { (void)addr; return g_word; }
static u32 call(u32 fn, u32 arg) { g_calls++, g_call_fn = fn, g_call_arg = arg, g_word_in_call = g_word; return 0; }
static const AutoLoadConsole k_con = {has_save, poke16, peek16, call};

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

/* the menu, then A until the load screen, then A until it returns, then the game's load called with its wait set to 0 */
static const AutoLoadStep k_steps[] = {
    {0x100u, 0, NULL, 0x5000u, 9u, 0, 0},
    {0x200u, 0, "A", 0, 0, 0, 0},
    {0x200u, 1, "B", 0, 0, 0, 0},
    {0x300u, 0, "UP+A", 0, 0, 0, 0},
    {0x400u, 0, NULL, 0x6000u, 0u, 0x700u, 3u},
};

static void start(int play_run, int want, int has, int replay, int note, int note_on, int scripted)
{
    g_vb = 1000u, g_pokes = 0, g_asked = 0, g_recorded = 0, g_calls = 0;
    g_has = has, g_replay = replay, g_note = note, g_note_on = note_on, g_scripted = scripted;
    if (want >= 0) autoload_option("--auto-load", want ? "on" : "off"); /* -1: as it was (the default until set) */
    autoload_start(k_steps, 5, play_run, &k_con);
}

static u32 press(u32 *own) { return autoload_buttons(own); }

int main(void)
{
    u32 own, b;
    check(autoload_get() == 1, "the menu shows it on before anything sets it");
    start(0, -1, 1, 0, 0, 0, 0);
    check(!autoload_fn && !g_asked, "by default off in a check (not a play run)");
    start(1, -1, 1, 0, 0, 0, 1);
    check(!autoload_fn, "by default off in a scripted play run");
    start(1, -1, 1, 0, 0, 0, 0);
    check(autoload_fn == 0x100u, "by default on in a play run with the save");
    check(autoload_option("--auto-load", "on") == 2 && autoload_option("--fps", "60") == 0, "the option");
    start(0, 1, 1, 0, 0, 0, 0);
    check(autoload_fn == 0x100u, "--auto-load on: on in a check too");
    autoload_set(0);
    check(autoload_get() == 0, "the menu's setting");

    start(1, 1, 0, 0, 0, 0, 0);
    check(!autoload_fn && press(&own) == 0 && !own && !g_recorded, "no save: off, nothing pressed or recorded");

    start(1, 0, 1, 0, 0, 0, 0);
    check(!autoload_fn && !g_asked && press(&own) == 0 && !own, "set off: the save is not looked at");

    start(1, 1, 1, 0, 0, 0, 0);
    check(autoload_fn == 0x100u && !autoload_leave && g_recorded == 1, "a save in a play run: on, waits for the menu, recorded");
    b = press(&own);
    check(b == 0 && !own, "while it waits for the menu the pad is the player's");
    g_vb = 1300u;
    autoload_hit();
    check(g_pokes == 1 && g_poke_addr == 0x5000u && g_poke_value == 9u, "the menu entered: the cursor's word put in");
    check(autoload_fn == 0x200u && !autoload_leave, "then the load screen");
    b = press(&own);
    check(b == 0x4u && own, "A pressed at once, the pad held");
    g_vb = 1306u;
    b = press(&own);
    check(b == 0 && own, "released after its hold, the pad still held");
    g_vb = 1320u;
    b = press(&own);
    check(b == 0x4u && own, "pressed again a few blanks on");
    autoload_hit();
    check(autoload_fn == 0x200u && autoload_leave && press(&own) == 0x2u, "the screen entered: B until it returns");
    autoload_hit();
    check(autoload_fn == 0x300u && press(&own) == 0x14u && own, "a step's buttons joined by '+': pressed together");
    check(g_calls == 0, "nothing called before a call step");
    autoload_hit();
    check(autoload_fn == 0x400u && press(&own) == 0 && !own, "a call step presses nothing: the pad is the player's");
    g_word = 50u;
    autoload_hit();
    check(g_calls == 1 && g_call_fn == 0x700u && g_call_arg == 3u, "its step entered: the game's load called once, its argument given");
    check(g_word_in_call == 0u && g_word == 50u, "the wait's word 0 for the call only, put back after it");
    check(!autoload_fn && press(&own) == 0 && !own && g_calls == 1, "done: the pad is the player's, nothing called again");

    start(1, 1, 1, 0, 0, 0, 0);
    autoload_hit();
    g_vb += 3001u;
    b = press(&own);
    check(b == 0 && !own && !autoload_fn, "a step that waits too long gives the pad back");
    g_vb += 20u;
    check(press(&own) == 0 && !own, "and stays given back");

    start(1, 1, 0, 1, 1, 1, 0);
    check(autoload_fn == 0x100u && !g_asked && g_recorded == 1, "a replay with the note: on without a save file");
    start(1, 1, 1, 1, 0, 0, 0);
    check(!autoload_fn && !g_recorded, "a replay without the note (an older recording): off");
    start(1, 1, 1, 1, 1, 0, 0);
    check(!autoload_fn, "a replay whose note says off: off");
    printf(g_fail ? "FAIL\n" : "all ok\n");
    return g_fail;
}
