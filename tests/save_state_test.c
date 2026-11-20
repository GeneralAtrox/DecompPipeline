/* Save states (port/base/save_state.c) against stubs of the console, the session, the log and the keyboard layout: the
 * file made and read back, every kind of bad or foreign state refused whole, the slots in <data>\states, nothing else
 * written in the data folder (the game's own save is never touched), not offered in a replay, a check or an online match,
 * F5 / F8 yielding to the player's layout, and the slots' labels. Run by tests/test_save_state.py; argv[1]: an empty
 * folder to be the data folder. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <bcrypt.h>
#include "save_state.h"
#include "host.h"
#pragma comment(lib, "bcrypt.lib")

static const char *g_data;
static int g_replay, g_fixed, g_f5_bound, g_fail, g_loads, g_opt = 1;
static const char *g_online;
static char g_last[512];
static const char *g_ident = "test kit 1";
static u8 g_got_a[16], g_got_b[8];

const PortConsole port_console = {"testcon", "Test", "Test Game", "test kit 1", 320u, 224u, 60u, 0u};
u32 port_vblank_count(void) { return 1234u; }
const char *host_data_folder(void) { return g_data; }
int session_replaying(void) { return g_replay; }
int host_fixed_clock(void) { return g_fixed; }
const char *host_online_locked(void) { return g_online; }
void game_log_line(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_last, sizeof g_last, fmt, ap);
    va_end(ap);
}
unsigned input_kbm_count(void) { return 2; }
const char *input_kbm_name(unsigned i) { return i ? "f5" : "Z"; }
int port_option(const char *id) { return !strcmp(id, "save-states") ? g_opt : -1; }
u32 input_kbm_get(unsigned i) { return i ? (g_f5_bound ? 4u : 0u) : 1u; }

#define CHECK(c, what) do { if (!(c)) { printf("FAIL: %s (line %d; last log: %s)\n", what, __LINE__, g_last); g_fail = 1; } } while (0)

static const char *ident(void) { return g_ident; }
static int save(SaveStateOut *o)
{
    static const u8 a[16] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
    return save_state_block(o, "A", 1, a, sizeof a) && save_state_begin(o, "BLOCKB", 2) && save_state_put(o, "abc", 3) &&
           save_state_put(o, "defgh", 5) && save_state_end(o);
}
static int load(const SaveStateIn *in)
{
    u32 la, lb;
    const u8 *a = save_state_find(in, "A", 1, &la), *b = save_state_find(in, "BLOCKB", 2, &lb);
    if (!a || la != 16 || !b || lb != 8 || save_state_find(in, "A", 2, NULL) || save_state_find(in, "C", 1, NULL)) return 0;
    memcpy(g_got_a, a, 16), memcpy(g_got_b, b, 8);
    g_loads++;
    return 1;
}
static const SaveStateProvider k_p = {ident, save, load};

static void rehash(u8 *d, u32 size)
{
    BCRYPT_ALG_HANDLE alg;
    BCRYPT_HASH_HANDLE h;
    BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL, 0);
    BCryptCreateHash(alg, &h, NULL, 0, NULL, 0, 0);
    BCryptHashData(h, d, size - 32, 0);
    BCryptFinishHash(h, d + size - 32, 32, 0);
    BCryptDestroyHash(h);
    BCryptCloseAlgorithmProvider(alg, 0);
}

static int refused(const u8 *d, u32 size, const char *want)
{
    const char *why = NULL;
    SaveStateIn *in = save_state_decode(d, size, &why);
    save_state_free(in);
    return !in && why && strstr(why, want);
}

static int count_files(const char *dir)
{
    char pat[MAX_PATH];
    WIN32_FIND_DATAA f;
    int n = 0;
    snprintf(pat, sizeof pat, "%s\\*", dir);
    HANDLE h = FindFirstFileA(pat, &f);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do n += strcmp(f.cFileName, ".") && strcmp(f.cFileName, "..");
    while (FindNextFileA(h, &f));
    FindClose(h);
    return n;
}

int main(int argc, char **argv)
{
    u8 *d;
    u32 size;
    char path[MAX_PATH], save_path[MAX_PATH], states[MAX_PATH];
    if (argc < 2) return 2;
    g_data = argv[1];

    /* a console with no provider: nothing offered, nothing made */
    CHECK(!save_state_offered(), "offered with no provider");
    CHECK(!save_state_encode(0, &d, &size), "a state made with no provider");

    /* the game's own save: a file in the data folder that a state must never touch */
    snprintf(save_path, sizeof save_path, "%s\\game.bup", g_data);
    FILE *f = fopen(save_path, "wb");
    fwrite("SAVEDATA", 1, 8, f), fclose(f);

    save_state_provider(&k_p);
    CHECK(save_state_offered(), "not offered in a play run");
    CHECK(save_state_encode(2, &d, &size), "no state made");
    SaveStateIn *in = save_state_decode(d, size, NULL);
    CHECK(in && load(in), "the state read back differs");
    CHECK(!memcmp(g_got_b, "abcdefgh", 8) && g_got_a[15] == 16, "the blocks' bytes differ");
    save_state_free(in);

    /* bad or foreign: refused whole */
    u8 *bad = (u8 *)malloc(size);
    memcpy(bad, d, size), bad[size / 2] ^= 1;
    CHECK(refused(bad, size, "hash"), "a damaged state taken");
    CHECK(refused(d, 100, "short"), "a cut state taken");
    memcpy(bad, d, size), bad[0] = 'X', rehash(bad, size);
    CHECK(refused(bad, size, "not a save state"), "a file that is not a state taken");
    memcpy(bad, d, size), bad[8] = 2, rehash(bad, size);
    CHECK(refused(bad, size, "format"), "another format version taken");
    memcpy(bad, d, size), bad[280 + 12] = 0xff, rehash(bad, size); /* block A's length */
    CHECK(refused(bad, size, "blocks"), "a block running past the file taken");
    memcpy(bad, d, size), bad[272] = 3, rehash(bad, size); /* the block count */
    CHECK(refused(bad, size, "blocks"), "a missing block taken");
    g_ident = "test kit 2";
    CHECK(refused(d, size, "another build"), "a state from another device layout or code taken");
    g_ident = "test kit 1";
    memcpy(bad, d, size), bad[96 + 9] = '0', rehash(bad, size); /* the build's version line: "test kit 0" */
    CHECK(refused(bad, size, "another build"), "a state from another build taken");
    memcpy(bad, d, size), bad[32] = 'O', rehash(bad, size); /* the game: "Oest Game" */
    CHECK(refused(bad, size, "another game"), "a state from another game taken");
    CHECK(refused(d, size, "") == 0, "a good state refused");
    free(bad), free(d);

    /* the slots: asked, then done at the kit's safe point */
    save_state_request(SAVE_STATE_SAVE, 2);
    snprintf(path, sizeof path, "%s\\states\\slot3.state", g_data);
    CHECK(GetFileAttributesA(path) == INVALID_FILE_ATTRIBUTES, "saved before the safe point");
    save_state_poll();
    CHECK(GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES, "slot 3 not written");
    CHECK(strstr(g_last, "saved slot 3"), "the save not logged");
    snprintf(states, sizeof states, "%s\\states", g_data);
    CHECK(count_files(states) == 1, "something besides slot3.state in the states folder");
    CHECK(count_files(g_data) == 2, "something besides states and the game's save in the data folder");
    memset(g_got_b, 0, 8);
    save_state_request(SAVE_STATE_LOAD, 2);
    CHECK(save_state_poll() == 1, "a load not reported to the kit");
    CHECK(g_loads == 2 && !memcmp(g_got_b, "abcdefgh", 8) && strstr(g_last, "loaded slot 3"), "slot 3 not loaded");
    save_state_request(SAVE_STATE_LOAD, 5), save_state_poll();
    CHECK(g_loads == 2 && strstr(g_last, "slot 6 is empty"), "an empty slot loaded");
    f = fopen(save_path, "rb");
    char back[16] = {0};
    fread(back, 1, sizeof back, f), fclose(f);
    CHECK(!strcmp(back, "SAVEDATA"), "the game's own save changed");

    /* F5 / F8: the slot last used, unless the player's layout gives the key to the game */
    save_state_key(0);
    CHECK(strstr(g_last, "F5 (Save state, slot 3)") && save_state_slot() == 2, "F5 not taken");
    save_state_poll();
    g_f5_bound = 1, g_last[0] = 0;
    save_state_key(0), save_state_poll();
    CHECK(!g_last[0], "F5 taken while the player's layout gives it to the game");
    g_f5_bound = 0;
    save_state_key(1), save_state_poll();
    CHECK(g_loads == 3, "F8 did not load");

    /* not offered in a replay, a check or an online match: nothing done */
    g_replay = 1;
    CHECK(!save_state_offered(), "offered in a replay");
    save_state_request(SAVE_STATE_LOAD, 2), save_state_poll();
    g_replay = 0, g_fixed = 1;
    CHECK(!save_state_offered(), "offered in a check");
    g_fixed = 0, g_online = "locked";
    CHECK(!save_state_offered(), "offered in an online match");
    save_state_poll();
    g_online = NULL;
    CHECK(g_loads == 3, "a load done in a replay, check or match");

    /* Game > Save states off: not offered, nothing done (the original game) */
    CHECK(port_state_options.n == 1 && !strcmp(port_state_options.items[0].id, "save-states") &&
          port_state_options.items[0].dflt == 1u && port_state_options.items[0].original == 0u, "the option: on by default, off is the original");
    g_opt = 0;
    CHECK(!save_state_offered(), "offered with the option off");
    save_state_request(SAVE_STATE_LOAD, 2);
    CHECK(save_state_poll() == 0 && g_loads == 3, "a load done with the option off");
    g_opt = 1;

    /* the kit drops a request it finds no moment for */
    save_state_request(SAVE_STATE_SAVE, 6), save_state_drop("no moment");
    CHECK(!save_state_pending && strstr(g_last, "slot 7 not saved: no moment"), "a dropped request not logged or still waiting");

    /* --state-save / --state-load VB:SLOT: due at that vertical blank, the fixed clock's too */
    g_fixed = 1;
    CHECK(save_state_test_option("--state-save", "10:3") == 2 && save_state_test_option("--state-load", "12:3") == 2, "the test flags");
    CHECK(save_state_test_option("--other", "1:1") == 0, "another flag taken");
    snprintf(path, sizeof path, "%s\\states\\slot3.state", g_data);
    DeleteFileA(path); /* the test aid uses slot 3, the slot last used: its saves move F5 / F8 like any other, and the menu checks below expect slot 3 */
    save_state_tick(9);
    CHECK(!save_state_pending, "the test save asked early");
    save_state_tick(10);
    CHECK(save_state_pending && save_state_poll() == 0, "the test save not asked at its blank");
    CHECK(GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES, "the test save not written on the fixed clock");
    save_state_tick(12);
    CHECK(save_state_poll() == 1 && g_loads == 4, "the test load not done on the fixed clock");
    save_state_tick(13);
    CHECK(!save_state_pending, "a test request asked twice");
    CHECK(save_state_test_option("--state-save", "10:9") == 2 && strstr(g_last, "not VB:SLOT"), "a bad slot taken");
    g_fixed = 0;

    /* the menu: Save state, Load state (8 slots each), then the note, greyed */
    unsigned n = 0;
    const PortMenuItem *m = save_state_menu(&n);
    CHECK(n == 3 && m[0].kind == PORT_MENU_SUBMENU && m[1].kind == PORT_MENU_SUBMENU && m[0].n_choices == 8, "the entries");
    CHECK(!strcmp(m[2].label, SAVE_STATE_NOTE) && m[2].available && !m[2].available(), "the note not there or not greyed");
    CHECK(strstr(m[0].choices[2], "Slot &3") && strstr(m[0].choices[2], "(F5 / F8)") && !strstr(m[0].choices[2], "empty"), "slot 3's label");
    CHECK(strstr(m[1].choices[0], "empty") && !strstr(m[1].choices[0], "F5"), "slot 1's label");
    m[0].set(0), save_state_poll();
    CHECK(strstr(g_last, "saved slot 1"), "the menu's save");

    printf(g_fail ? "FAIL\n" : "all ok\n");
    return g_fail;
}
