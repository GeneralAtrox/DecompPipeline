/* --kbm-inject: a test's keys and mouse instead of the real devices, read through the same layout (input_kbm.c):
 *   --kbm-inject NAME@VBLANK[+N],...   a key or mouse button held from that vertical blank for N (default 6);
 *                                      WHEELUP/WHEELDOWN one notch; MOUSEMIDDLE a click;
 *                                      MDRAG=DX:DY@VBLANK[+N] the middle button held N reads while the mouse moves DX, DY;
 *                                      MOUSEMOVE=DX:DY@VBLANK[+N] the mouse moved DX, DY pixels (right, down) before
 *                                      each of N reads from that blank, no button held: mouse steer (input_kbm_steer.c),
 *                                      e.g. MOUSEMOVE=6:-3@6000+20 (right and forward for 20 reads);
 *                                      MOUSEPOS=X:Y@VBLANK the pointer put at X, Y of the game's 640x480 screen (the
 *                                      menu pointer, input_kbm_pointer.c), e.g. MOUSEPOS=320:200@900,MOUSELEFT@904+4;
 *                                      TEXT=STRING@VBLANK typed into the game's open text field (native_game.h
 *                                      pg_native_text): \b \n \t \e \\ are Backspace, Enter, Tab, Esc and a backslash,
 *                                      \xHH a byte; no ',' in it, and its last '@' ends it: TEXT=héllo\b\n@20;
 *                                      FOCUSLOST@VBLANK the window's focus lost (not given back in that run).
 * A notch, a click, a move or a text goes at the first read at or after its blank (a pad is not read every blank). */
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "host.h"
#include "input_kbm_priv.h"

typedef struct Inject {
    char name[24], text[64]; /* text: TEXT='s, its escapes undone */
    u32 from, count, reads;  /* reads: MOUSEMOVE's reads done; TEXT's length */
    float dx, dy;
    int state; /* 0 not started, 1 running, 2 done */
} Inject;
#define MAX_INJECT 64
static Inject g_inj[MAX_INJECT];
static unsigned g_inj_n;

/* TEXT=...@V: the text, its escapes undone, into j (cleared); the entry's last '@', or NULL. */
static const char *text_entry(Inject *j, const char *value, size_t len)
{
    const char *at = NULL, *c;
    u32 n = 0;
    for (size_t i = 5; i < len; i++)
        if (value[i] == '@') at = value + i;
    if (!at) return NULL;
    memset(j, 0, sizeof *j);
    memcpy(j->name, "TEXT", 4);
    for (c = value + 5; c < at && n + 1 < sizeof j->text; c++) {
        char out = *c;
        if (*c == '\\' && c + 1 < at) {
            char e = *++c;
            out = e == 'b' ? '\b' : e == 'n' ? '\n' : e == 't' ? '\t' : e == 'e' ? (char)27 : e;
            if (e == 'x' && c + 2 < at) {
                char hex[3] = {c[1], c[2], 0};
                out = (char)strtoul(hex, NULL, 16), c += 2;
            }
        }
        j->text[n++] = out;
    }
    j->reads = n;
    return at;
}

int kbm_inject_option(const char *value)
{
    while (*value) {
        size_t len = strcspn(value, ",");
        Inject *j = g_inj_n < MAX_INJECT ? &g_inj[g_inj_n] : NULL;
        int text = j && len > 5 && !strncmp(value, "TEXT=", 5);
        const char *at = text ? text_entry(j, value, len) : memchr(value, '@', len);
        size_t name = text ? 4 : at ? (size_t)(at - value) : len;
        char *eq;
        if (!j || !at || name >= sizeof j->name) {
            port_log("input: --kbm-inject: \"%.*s\" is not NAME@VBLANK[+N]\n", (int)len, value);
            return 2;
        }
        if (!text) memset(j, 0, sizeof *j), memcpy(j->name, value, name);
        if (!text && (eq = strchr(j->name, '=')) != NULL) { /* MDRAG=DX:DY, MOUSEMOVE=DX:DY, MOUSEPOS=X:Y */
            j->dx = (float)atof(eq + 1);
            j->dy = strchr(eq, ':') ? (float)atof(strchr(eq, ':') + 1) : 0.0f;
            *eq = 0;
        }
        j->from = (u32)strtoul(at + 1, NULL, 10);
        j->count = 6;
        {
            const char *plus = memchr(at, '+', len - (size_t)(at - value));
            if (plus) j->count = (u32)strtoul(plus + 1, NULL, 10);
        }
        if (!j->count) j->count = 1;
        g_inj_n++;
        value += len + (value[len] == ',');
    }
    return 2;
}

int input_kbm_injected(void) { return g_inj_n != 0; }

int kbm_injected_held(const char *name, u32 vb)
{
    for (unsigned i = 0; i < g_inj_n; i++)
        if (kbm_same(g_inj[i].name, name) && vb >= g_inj[i].from && vb - g_inj[i].from < g_inj[i].count) return 1;
    return 0;
}

/* Turns the injection's mouse entries (and its text and focus) into the same calls the window's events make. */
void kbm_inject_mouse(u32 vb)
{
    for (unsigned i = 0; i < g_inj_n; i++) {
        Inject *j = &g_inj[i];
        /* a notch or a click goes at the first read at or after its blank (a pad is not read every blank) */
        int started = vb >= j->from, on = started && vb - j->from < j->count;
        if (j->state == 2 || !started) continue;
        if (kbm_same(j->name, "WHEELUP") || kbm_same(j->name, "WHEELDOWN")) {
            input_kbm_mouse_wheel(kbm_same(j->name, "WHEELUP") ? 1 : -1);
            j->state = 2;
        } else if (kbm_same(j->name, "MOUSEMIDDLE")) {
            input_kbm_mouse_button(1, 1), input_kbm_mouse_button(1, 0);
            j->state = 2;
        } else if (kbm_same(j->name, "MDRAG")) {
            if (j->state == 0) input_kbm_mouse_button(1, 1), j->state = 1;
            if (on) input_kbm_mouse_motion(j->dx / (float)j->count, j->dy / (float)j->count);
            else input_kbm_mouse_button(1, 0), j->state = 2;
        } else if (kbm_same(j->name, "MOUSEPOS")) {
            input_kbm_mouse_position(j->dx, j->dy);
            j->state = 2;
        } else if (kbm_same(j->name, "TEXT")) {
            host_text_put(j->text, (int)j->reads);
            j->state = 2;
        } else if (kbm_same(j->name, "FOCUSLOST")) {
            input_kbm_focus_lost();
            j->state = 2;
        } else if (kbm_same(j->name, "MOUSEMOVE")) {
            input_kbm_mouse_motion(j->dx, j->dy); /* N reads, however often the game reads its pad */
            j->state = ++j->reads < j->count ? 1 : 2;
        } else {
            j->state = on ? 1 : 2;
        }
    }
}
