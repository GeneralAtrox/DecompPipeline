/* The --input script: scripted presses for automated runs, by vertical blank (the syntax is in input.c's header).
 * Split from input.c, which reads the devices: the press table, the button names and the parse are here. */
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"
#include "game_log.h"

#define MAX_SCRIPT 4096 /* scripted presses kept; later ones are dropped with one log line */
#define SCRIPT_HOLD 6u

typedef struct ScriptPress {
    u32 buttons, at, until, level; /* level: how far a scripted trigger is pressed, 1..255 */
    u32 player;
    int logged;
} ScriptPress;

static ScriptPress g_script[MAX_SCRIPT];
static u32 g_script_n, g_script_dropped;
static u32 g_script_players = 1u; /* bit p: the script names player p + 1 (player 1 is always connected) */

static const struct {
    const char *name;
    u32 bit;
} g_names[] = {
    {"A", HOST_PAD_A},       {"B", HOST_PAD_B},         {"X", HOST_PAD_X},   {"Y", HOST_PAD_Y},
    {"START", HOST_PAD_START}, {"UP", HOST_PAD_UP},     {"DOWN", HOST_PAD_DOWN}, {"LEFT", HOST_PAD_LEFT},
    {"RIGHT", HOST_PAD_RIGHT}, {"L", SCRIPT_L},         {"R", SCRIPT_R},
    {"LB", HOST_PAD_LB},     {"RB", HOST_PAD_RB},       {"L3", HOST_PAD_L3}, {"R3", HOST_PAD_R3},
    {"BACK", HOST_PAD_BACK}, {"GUIDE", HOST_PAD_GUIDE}, {"RSUP", HOST_PAD_RS_UP}, {"RSDOWN", HOST_PAD_RS_DOWN},
    {"RSLEFT", HOST_PAD_RS_LEFT}, {"RSRIGHT", HOST_PAD_RS_RIGHT}, {"LT", SCRIPT_L}, {"RT", SCRIPT_R},
    {"LSUP", SCRIPT_LS_UP},  {"LSDOWN", SCRIPT_LS_DOWN}, {"LSLEFT", SCRIPT_LS_LEFT}, {"LSRIGHT", SCRIPT_LS_RIGHT},
};

int input_scripted(void) { return g_script_n != 0; } /* a scripted run: the console's actions take their defaults */

u32 input_name_bit(const char *name) /* a g_names bit */
{
    for (size_t i = 0; i < sizeof g_names / sizeof g_names[0]; i++)
        if (!_stricmp(name, g_names[i].name)) return g_names[i].bit;
    return 0;
}

/* A button named as --input takes it: the console's own names first (a Saturn's L is its shoulder, not the trigger),
 * then the host's. len: the name's length (it may run on into the rest of a script). */
static u32 button_bit(const char *name, size_t len)
{
    unsigned na;
    const PortAction *act = console_actions(&na);
    for (unsigned i = 0; i < na; i++)
        if (strlen(act[i].name) == len && !_strnicmp(name, act[i].name, len)) return input_name_bit(act[i].dflt);
    for (size_t i = 0; i < sizeof g_names / sizeof g_names[0]; i++)
        if (strlen(g_names[i].name) == len && !_strnicmp(name, g_names[i].name, len)) return g_names[i].bit;
    return 0;
}

u32 input_button_bit(const char *name) { return button_bit(name, strlen(name)); }


/* One --input entry's optional player prefix "Pn.": returns the player (0..3) and moves *p past it. */
static u32 script_player(const char **p)
{
    const char *s = *p;
    if ((s[0] == 'P' || s[0] == 'p') && s[1] >= '1' && s[1] <= '0' + HOST_PADS && s[2] == '.') {
        *p = s + 3;
        return (u32)(s[1] - '1');
    }
    return 0;
}

/* One --input argument; 0 if it is not --input. */
int input_script_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--input") || !value) return 0;
    for (const char *p = value; *p;) {
        const char *entry = p;
        u32 player = script_player(&p);
        size_t n = strcspn(p, "@,="), name = n;
        u32 bit = button_bit(p, name), level = 255;
        char *end = (char *)p + n;
        if (*end == '=' && (bit & (SCRIPT_L | SCRIPT_R))) {
            level = (u32)strtoul(end + 1, &end, 10);
            if (!level || level > 255) bit = 0;
        }
        if (bit && *end == '@' && g_script_n == MAX_SCRIPT) {
            if (!g_script_dropped++)
                port_log("input: --input holds at most %d presses: \"%.*s\" and the ones after it are dropped\n",
                         MAX_SCRIPT, (int)strcspn(entry, ","), entry);
        } else if (bit && *end == '@') {
            u32 at = (u32)strtoul(end + 1, &end, 10), hold = SCRIPT_HOLD;
            if (*end == '+') hold = (u32)strtoul(end + 1, &end, 10);
            g_script[g_script_n].buttons = bit;
            g_script[g_script_n].level = level;
            g_script[g_script_n].at = at;
            g_script[g_script_n].player = player;
            g_script[g_script_n++].until = at + hold;
            g_script_players |= 1u << player;
        } else {
            port_log("input: cannot read the --input entry \"%s\"\n", entry);
        }
        end += strcspn(end, ",");
        p = *end == ',' ? end + 1 : end;
    }
    return 2;
}

/* Player p's scripted presses held now; trig[0] and trig[1]: how far its scripted L and R triggers are pressed. */
u32 input_script_buttons(u32 p, u32 trig[2])
{
    u32 now = port_vblank_count(), b = 0;
    trig[0] = trig[1] = 0;
    for (u32 i = 0; i < g_script_n; i++) {
        ScriptPress *s = &g_script[i];
        if (s->player != p || now < s->at || now >= s->until) continue;
        b |= s->buttons;
        if ((s->buttons & SCRIPT_L) && s->level > trig[0]) trig[0] = s->level;
        if ((s->buttons & SCRIPT_R) && s->level > trig[1]) trig[1] = s->level;
        if (!s->logged) port_log("input: scripted press %08x for player %u at vertical blank %u\n", s->buttons, p + 1,
                                 now);
        s->logged = 1;
    }
    return b;
}

u32 input_script_players(void) { return g_script_players; } /* bit p: the script names player p + 1 */
