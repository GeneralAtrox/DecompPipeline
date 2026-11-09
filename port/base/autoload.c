/* Auto-load: when the game's save is there, its own load path is driven at boot the way a player choosing Load would
 * drive it (Game > Auto-load save; --auto-load on|off; on in an unscripted play run, off in checks). The game names the
 * path as steps (game.json auto_load, which the console's generator turns into AutoLoadStep): each waits for the game
 * to enter or leave one of its functions, pressing a button every few blanks until then, and may set a word the game
 * reads there (a menu's remembered cursor, so a press that lands early cannot pick another item). A step may instead
 * call the game's own load function once, as its menu does, so nothing is pressed (Robo Pit). While a step presses,
 * the player's buttons are ignored; a step that waits over a minute gives the pad back. The choice is kept in the
 * recording at start (the presses are pad reads like any other), so a replay drives the same path and an older
 * recording, which has no note, replays with it off.
 * Nearest existing: input_script.c - presses at fixed vertical blanks for scripted runs; a load's screens come after
 * loads whose length varies, so these wait on the game's own functions instead. */
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"
#include "session.h"
#include "autoload.h"

#define NOTE "*auto-load-call" /* renamed when steps could call: a recording made before keeps its presses as pad reads */
enum { EVERY = 20, HOLD = 6, GIVE_UP = 3000 };

u32 autoload_fn;
int autoload_leave;
static const AutoLoadStep *g_steps, *g_then;
static unsigned g_n, g_at, g_nthen;
static int g_then_on; /* the steps running are the ones after the load */
static const char *g_lead; /* pressed while a load step waits without a press */
static int g_want = -1, g_on;
static u32 g_since, g_press;
static const AutoLoadConsole *g_con;

int autoload_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--auto-load") || !value) return 0;
    g_want = strcmp(value, "off") != 0;
    return 2;
}

int autoload_available(void) { return g_steps != NULL; }
int autoload_get(void) { return g_want != 0; }
void autoload_set(int on) { g_want = on != 0; }

/* a step's buttons: one name, or several joined by '+' pressed together ("UP+A": a prompt whose cursor UP moves and
 * whose A reads it later in the same frame) */
static u32 buttons(const char *names)
{
    char one[16];
    u32 bits = 0;
    while (names && *names) {
        size_t n = strcspn(names, "+");
        if (n < sizeof one) memcpy(one, names, n), one[n] = 0, bits |= input_button_bit(one);
        names += n + (names[n] == '+');
    }
    return bits;
}

/* the step at g_at: its event for the console, its buttons */
static void arm(void)
{
    if (g_at >= g_n && g_then) {
        port_log("auto-load: the save is loaded (vertical blank %u); %u step(s) after it follow\n", port_vblank_count(), g_nthen);
        g_steps = g_then, g_n = g_nthen, g_at = 0, g_then = NULL, g_then_on = 1;
    }
    const AutoLoadStep *s = g_at < g_n ? &g_steps[g_at] : NULL;
    autoload_fn = s ? s->fn : 0u;
    autoload_leave = s && s->leave;
    g_press = s ? buttons(s->press ? s->press : g_then_on ? NULL : g_lead) : 0u;
    g_since = port_vblank_count();
    if (!s) port_log(g_then_on ? "auto-load: the steps after the load are done (vertical blank %u)\n" : "auto-load: the save is loaded (vertical blank %u)\n", g_since);
}

void autoload_start(const AutoLoadStep *steps, unsigned n, int play_run, const AutoLoadConsole *con)
{
    u8 on = 0;
    g_steps = steps, g_n = n, g_con = con, g_at = 0, autoload_fn = 0, autoload_leave = 0; /* a start begins afresh */
    if (session_note_replayed(NOTE, &on, 1)) g_on = on != 0;
    else if (session_replaying()) g_on = 0;
    else g_on = (g_want < 0 ? play_run && !input_scripted() : g_want) && n && con->has_save();
    on = (u8)g_on;
    if (on) session_save_read(NOTE, &on, 1, 1);
    port_log("auto-load: %s\n", g_on ? "on: the save is there" : g_want == 0 ? "off" : "off (no save, or not a play run)");
    if (g_on) arm();
    else if (g_then) port_log("auto-load: off, so the %u step(s) after it do not run\n", g_nthen);
}

void autoload_then(const char *lead, const AutoLoadStep *steps, unsigned n)
{
    g_then = n ? steps : NULL, g_nthen = n, g_lead = lead;
    if (!g_on || !g_steps || g_then_on) return;
    if (g_at >= g_n) arm(); /* the load is already done */
    else if (!g_steps[g_at].press) g_press = buttons(g_lead); /* the step waiting now takes the lead press */
}

void autoload_hit(void)
{
    const AutoLoadStep *s = &g_steps[g_at];
    u32 old = s->poke && s->call ? g_con->peek16(s->poke) : 0u;
    if (s->poke) g_con->poke16(s->poke, s->value);
    port_log("auto-load: step %u, %s %08x at vertical blank %u\n", g_at + 1u, s->leave ? "left" : "entered", s->fn, port_vblank_count());
    if (s->call) {
        u32 r = g_con->call(s->call, s->arg);
        if (s->poke) g_con->poke16(s->poke, old);
        port_log("auto-load: the game's load %08x(%u) answered %u%s\n", s->call, s->arg, r, r ? ": not loaded, the game goes on as new" : ": loaded");
    }
    g_at++;
    arm();
}

u32 autoload_buttons(u32 *own)
{
    u32 t = port_vblank_count() - g_since;
    *own = 0;
    if (!autoload_fn || !g_press) return 0;
    if (t > GIVE_UP) {
        port_log("auto-load: step %u waited %u blanks for %08x; the pad is the player's again\n", g_at + 1u, t, autoload_fn);
        autoload_fn = 0;
        return 0;
    }
    *own = 1;
    return t % EVERY < HOLD ? g_press : 0u;
}
