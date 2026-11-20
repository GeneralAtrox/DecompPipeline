/* Save states: the slots (<data>\states\slotN.state), the requests the kit does at its safe point, F5 / F8, File's
 * entries, Game > Save states and the test aid --state-save / --state-load (save_state.h). The file itself: save_state.c.
 * The game's own save is never touched here. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include "save_state.h"
#include "host.h"
#include "game_log.h"
#include "session.h"
#include "perf.h"

const char *host_online_locked(void); /* host_online.cpp: why a match locks the entries that change the game, or NULL */

volatile long save_state_pending; /* kind << 8 | slot, 0: none */
static int g_slot;                /* F5 / F8's: the slot last saved or loaded */
static int g_test;                /* --state-save / --state-load given: asked in any run, a check's too */
static u32 g_test_vb[2];
static int g_test_slot[2], g_test_due[2];

static void slot_path(int slot, char *out, size_t cap, int make_dir)
{
    const char *data = host_data_folder();
    snprintf(out, cap, "%s\\states", data && *data ? data : ".");
    if (make_dir) CreateDirectoryA(out, NULL);
    size_t n = strlen(out);
    snprintf(out + n, cap - n, "\\slot%d.state", slot + 1);
}

static void do_save(int slot)
{
    u8 *data;
    u32 size;
    char path[MAX_PATH], tmp[MAX_PATH + 8];
    if (!save_state_encode(slot, &data, &size)) {
        game_log_line("state: slot %d not saved (the game cannot be written down at this moment)", slot + 1);
        return;
    }
    slot_path(slot, path, sizeof path, 1);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = NULL;
    if (fopen_s(&f, tmp, "wb")) f = NULL;
    int ok = f && fwrite(data, 1, size, f) == size;
    if (f) ok = fclose(f) == 0 && ok;
    ok = ok && MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    if (!ok) DeleteFileA(tmp);
    free(data);
    game_log_line(ok ? "state: saved slot %d (%u KB, vblank %u) to %s" : "state: slot %d not saved (%u KB, vblank %u): %s could not be written",
                  slot + 1, size >> 10, port_vblank_count(), path);
    if (ok) g_slot = slot;
}

static int do_load(int slot)
{
    char path[MAX_PATH];
    const char *why = NULL;
    slot_path(slot, path, sizeof path, 0);
    FILE *f = NULL;
    if (fopen_s(&f, path, "rb") || !f) {
        game_log_line("state: slot %d is empty", slot + 1);
        return 0;
    }
    u8 *data = NULL;
    long size = fseek(f, 0, SEEK_END) == 0 ? ftell(f) : -1;
    if (size > 0 && size < (1L << 30) && (data = (u8 *)malloc((size_t)size)) && fseek(f, 0, SEEK_SET) == 0 &&
        fread(data, 1, (size_t)size, f) != (size_t)size) {
        free(data);
        data = NULL;
    }
    fclose(f);
    SaveStateIn *in = data ? save_state_decode(data, (u32)size, &why) : NULL;
    free(data);
    if (!in) {
        game_log_line("state: slot %d refused: %s", slot + 1, why ? why : "could not be read");
        return 0;
    }
    int ok = save_state_apply(in);
    if (!ok) {
        game_log_line("state: slot %d refused by the console (nothing was changed)", slot + 1);
    } else {
        game_log_line("state: loaded slot %d (made at vblank %u)", slot + 1, save_state_vblank(in));
        g_slot = slot;
        perf_loaded(); /* --perf-from load+N: the measured segment starts N blanks from here */
    }
    save_state_free(in);
    return ok;
}

/* Game > Save states: a live option (never recorded: it changes nothing the game does), on by default. Off, nothing is
 * ever asked, so the game runs as the original exactly. */
static int provider(void) { return save_state_has_provider(); }
static const PortOption k_options[] = {
    {"save-states", "Game", "&Save states (File > Save state / Load state, F5 / F8)", PORT_MENU_CHECK, NULL, NULL, 0u, 1u, 0u,
     0u, provider},
};
const PortOptions port_state_options = {k_options, 1u};

int save_state_slot(void) { return g_slot; }

int save_state_offered(void)
{
    return save_state_has_provider() && !session_replaying() && !host_fixed_clock() && !host_online_locked() &&
           port_option("save-states") != 0;
}

void save_state_request(int kind, int slot)
{
    if (slot < 0 || slot >= SAVE_STATE_SLOTS || (kind != SAVE_STATE_SAVE && kind != SAVE_STATE_LOAD)) return;
    if (!save_state_offered()) {
        game_log_line("state: not offered here (a replay, a check, an online match, or a console without states)");
        return;
    }
    InterlockedExchange(&save_state_pending, (LONG)(kind << 8 | slot));
}

int save_state_poll(void)
{
    LONG r = save_state_pending ? InterlockedExchange(&save_state_pending, 0) : 0;
    if (!r || !(save_state_offered() || (g_test && save_state_has_provider()))) return 0;
    if (r >> 8 == SAVE_STATE_SAVE) {
        do_save((int)(r & 0xff));
        return 0;
    }
    return do_load((int)(r & 0xff));
}

void save_state_drop(const char *why)
{
    LONG r = InterlockedExchange(&save_state_pending, 0);
    if (r) game_log_line("state: slot %d not %s: %s", (int)(r & 0xff) + 1, r >> 8 == SAVE_STATE_SAVE ? "saved" : "loaded", why);
}

/* --state-save VB:SLOT, --state-load VB:SLOT (slot 1..8): a test aid, asked at that vertical blank in any run */
int save_state_test_option(const char *arg, const char *value)
{
    int k = !strcmp(arg, "--state-save") ? 0 : !strcmp(arg, "--state-load") ? 1 : -1;
    unsigned vb = 0;
    int slot = 0;
    if (k < 0) return 0;
    if (!value || sscanf_s(value, "%u:%d", &vb, &slot) != 2 || slot < 1 || slot > SAVE_STATE_SLOTS) {
        game_log_line("state: %s %s: not VB:SLOT (slot 1..%d)", arg, value ? value : "", SAVE_STATE_SLOTS);
        return value ? 2 : 1;
    }
    g_test = 1, g_test_vb[k] = vb, g_test_slot[k] = slot - 1, g_test_due[k] = 1;
    return 2;
}

void save_state_tick(u32 vblank)
{
    for (int k = 0; g_test && k < 2; k++)
        if (g_test_due[k] && vblank >= g_test_vb[k] && !save_state_pending) {
            g_test_due[k] = 0;
            game_log_line("state: --state-%s at vblank %u, slot %d", k ? "load" : "save", vblank, g_test_slot[k] + 1);
            InterlockedExchange(&save_state_pending, (LONG)((k ? SAVE_STATE_LOAD : SAVE_STATE_SAVE) << 8 | g_test_slot[k]));
        }
}

/* F5 / F8, unless the player's keyboard layout gives the key to the game (Input > Keyboard and mouse) */
void save_state_key(int load)
{
    const char *key = load ? "F8" : "F5";
    for (unsigned i = 0; i < input_kbm_count(); i++)
        if (!_stricmp(input_kbm_name(i), key) && input_kbm_get(i)) return;
    game_log_line("keyboard: %s (%s state, slot %d)", key, load ? "Load" : "Save", g_slot + 1);
    save_state_request(load ? SAVE_STATE_LOAD : SAVE_STATE_SAVE, g_slot);
}

/* ---- File's entries (host_menu_extra.cpp puts them after Restart) ---- */
static char g_label[SAVE_STATE_SLOTS][64];
static const char *const g_choice[SAVE_STATE_SLOTS] = {g_label[0], g_label[1], g_label[2], g_label[3],
                                                       g_label[4], g_label[5], g_label[6], g_label[7]};

void save_state_labels(void)
{
    for (int i = 0; i < SAVE_STATE_SLOTS; i++) {
        char path[MAX_PATH], when[32] = "empty";
        struct _stat64 st;
        struct tm t;
        slot_path(i, path, sizeof path, 0);
        if (_stat64(path, &st) == 0 && localtime_s(&t, &st.st_mtime) == 0) strftime(when, sizeof when, "%d %b %H:%M", &t);
        snprintf(g_label[i], sizeof g_label[i], "Slot &%d   %s%s", i + 1, when, i == g_slot ? "   (F5 / F8)" : "");
    }
}

static int none(void) { return 0; }
static void save_pick(int n) { save_state_request(SAVE_STATE_SAVE, n); }
static void load_pick(int n) { save_state_request(SAVE_STATE_LOAD, n); }
static void note_pick(int n) { (void)n; }
static const PortMenuItem k_items[] = {
    {"File", "&Save state", PORT_MENU_SUBMENU, NULL, none, save_pick, g_choice, NULL, SAVE_STATE_SLOTS, save_state_offered, 0},
    {"File", "&Load state", PORT_MENU_SUBMENU, NULL, none, load_pick, g_choice, NULL, SAVE_STATE_SLOTS, save_state_offered, 0},
    {"File", SAVE_STATE_NOTE, PORT_MENU_COMMAND, NULL, none, note_pick, NULL, NULL, 0, none, 0}, /* always greyed: a note */
};

const PortMenuItem *save_state_menu(unsigned *count)
{
    save_state_labels();
    *count = sizeof k_items / sizeof k_items[0];
    return k_items;
}
