/* The port's half of the lockstep check (tools/lockstep.py runs the emulator and compares). A game frame starts with
 * a read of the SDK timer (session_timer): the game's clock reads it once per frame, so the state at that read is
 * the state the previous frame left. Both halves trace it as it was at that frame's last vertical blank (before the
 * game's handler), so state the game swaps within a frame reads the same. The timer value is the frame's label on
 * both sides.
 *   --lockstep-spec FILE   the watched state, from the game's folder: one item per line, "name words address
 *                          [flags]". The address is hex with steps: * reads a pointer there, +X and -X move by hex X;
 *                          the item is that many u32 words from where the steps end. Flags are the compare tool's,
 *                          except key: the key items are the frames' phase (a scene number, say), and frames: the
 *                          game's frame counter (a timer read with no frame ended since the last is no new frame,
 *                          and the emulator's frames that ran several game frames are split). "EMU|PORT"
 *                          gives the port its own address; "@rand" is the C library generator's state (one word).
 *   --lockstep-trace FILE  one line per frame: "label emulator-frame word..", hex, "-" for a word that is not in
 *                          guest RAM (a null or host pointer on the way).
 *   --lockstep FILE        the emulator's frames, "label on r l x y rtc jump key..": the port's frames take their
 *                          timer value (the label; offset after a re-pairing; jump, if not 0, for the first read
 *                          within the frame), pad reads and RTC (seconds from 1950, the AICA's) from the paired
 *                          frame. Frames pair one to one; when the key changes, the next emulator frame that starts
 *                          the same key pairs instead (a movie the port does not play, a different number of loading
 *                          frames).
 *   --lockstep-watch W@N   logs each write to trace word W through the port frame that trace line N shows (its state
 *                          is that frame's end), with the stack (lockstep_watch.c). */
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "platform.h"
#include "lockstep.h"
#include "lockstep_parts.h"

#define MAX_ITEMS 512u
#define MAX_STEPS 8u
#define LINE_CAP 4096u
#define STEP_DEREF 0x7fffffff
#define MAX_WORDS 8192u
#define NO_WORD 0xffffffffu /* in g_snap_ok: the word is not in guest RAM */
#define TIMER_UNIT 3125     /* 4 ms of the SDK timer (781,250 Hz): a whole number of milliseconds */
#define LOAD_REACH 4u       /* emulator frames a load may pair away from the paired one */

typedef struct Item {
    u32 base, words, steps, key, rand, frames, paused;
    s32 step[MAX_STEPS];
} Item;

static Item g_items[MAX_ITEMS];
static u32 g_nitems, g_nkey;
static FILE *g_trace;
static EmuFrame *g_emu;
static u32 g_nemu, g_ei, g_seg, g_calls, g_unmatched, g_jump_next; /* the first emulator frame a load may pair with */
static u32 g_key[MAX_KEY], g_prev_key[MAX_KEY];
static s32 g_value, g_off; /* the timer value returned last; the port's timer minus the emulator's */
static int g_drive, g_started, g_ended, g_snapped, g_jumped;
static const Item *g_frames; /* the game's frame counter (flag frames), if the spec names one */
static const Item *g_paused; /* the game clock's pause count (flag paused) */
static u32 g_frames_seen;
static int g_scenes; /* scenes begun since the last frame read */
static u32 g_watch_word, g_watch_frame, g_watching; /* --lockstep-watch */
static u32 g_snap[MAX_WORDS], g_snap_ok[MAX_WORDS], g_nwords;

static int guest_word(u32 a, u32 *out)
{
    u32 p = a & 0x1fffffffu;
    if ((a & 3u) || p < 0x0c000000u || p >= 0x0c000000u + GUEST_RAM_SIZE) return 0;
    *out = *(const volatile u32 *)(uintptr_t)(GUEST_RAM_BASE + (p - 0x0c000000u));
    return 1;
}

/* Where an item's words start; 0 if a step leaves guest RAM. */
static int item_at(const Item *it, u32 *at)
{
    u32 a = it->base;
    for (u32 s = 0; s < it->steps; s++) {
        if (it->step[s] == STEP_DEREF) {
            if (!guest_word(a, &a)) return 0;
        } else {
            a += (u32)it->step[s];
        }
    }
    *at = a;
    return 1;
}

static int parse_item(char *line, Item *it)
{
    char *ctx = NULL, *name = strtok_s(line, " \t\r\n", &ctx), *words, *addr, *p, *flag;
    if (!name || name[0] == '#') return 0;
    words = strtok_s(NULL, " \t\r\n", &ctx);
    addr = strtok_s(NULL, " \t\r\n", &ctx);
    if (!words || !addr) return -1;
    memset(it, 0, sizeof *it);
    while ((flag = strtok_s(NULL, " \t\r\n", &ctx)) != NULL) {
        if (!strcmp(flag, "key")) it->key = 1;
        if (!strcmp(flag, "frames")) it->frames = 1;
        if (!strcmp(flag, "paused")) it->paused = 1;
        if (!strcmp(flag, "emu")) return 0; /* the emulator's only */
    }
    it->words = (u32)strtoul(words, NULL, 0);
    if ((p = strchr(addr, '|')) != NULL) addr = p + 1;
    if (!strcmp(addr, "@rand")) {
        it->rand = 1;
        return 1;
    }
    it->base = (u32)strtoul(addr, &p, 16);
    while (*p) {
        if (it->steps == MAX_STEPS) return -1;
        if (*p == '*') {
            it->step[it->steps++] = STEP_DEREF;
            p++;
        } else if (*p == '+' || *p == '-') {
            s32 sign = *p == '-' ? -1 : 1;
            it->step[it->steps++] = sign * (s32)strtoul(p + 1, &p, 16);
        } else {
            return -1;
        }
    }
    if (it->key && g_nkey + it->words > MAX_KEY) return -1;
    if (it->key) g_nkey += it->words;
    if (it->frames) g_frames = it;
    if (it->paused) g_paused = it;
    return 1;
}

static int load_spec(const char *path)
{
    FILE *f = NULL;
    char line[LINE_CAP];
    u32 n = 0;
    if (fopen_s(&f, path, "r") || !f) {
        port_log("lockstep: cannot open the spec %s\n", path);
        return 0;
    }
    while (fgets(line, sizeof line, f)) {
        n++;
        if (g_nitems == MAX_ITEMS) break;
        int r = parse_item(line, &g_items[g_nitems]);
        if (r < 0) {
            port_log("lockstep: %s line %u is not \"name words address [flags]\"\n", path, n);
            fclose(f);
            return 0;
        }
        g_nitems += (u32)r;
    }
    fclose(f);
    port_log("lockstep: %u watched items, %u key words\n", g_nitems, g_nkey);
    return 1;
}

int lockstep_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--lockstep-spec")) {
        if (!load_spec(value)) exit(2);
    } else if (!strcmp(arg, "--lockstep-trace")) {
        if (fopen_s(&g_trace, value, "w") || !g_trace) {
            port_log("lockstep: cannot write %s\n", value);
            exit(2);
        }
        setvbuf(g_trace, NULL, _IOFBF, 1u << 20);
    } else if (!strcmp(arg, "--lockstep")) {
        g_nemu = lockstep_load_emu(value, &g_emu);
        g_drive = g_nemu != 0;
        if (!g_drive) exit(2);
    } else if (!strcmp(arg, "--lockstep-watch")) {
        if (sscanf_s(value, "%u@%u", &g_watch_word, &g_watch_frame) != 2) exit(2);
        g_watching = 1;
    } else {
        return ram_frames_option(arg, value) ? 2 : hw_check_option(arg, value);
    }
    return 2;
}

/* An item's word j; 0 if it is not in guest RAM. */
static int item_word(const Item *it, u32 j, u32 *w)
{
    u32 at;
    if (it->rand) {
        *w = port_rand_state();
        return j == 0;
    }
    return item_at(it, &at) && guest_word(at + 4u * j, w);
}

/* Trace word n in host memory as it is now; NULL if it is not in guest RAM. */
static volatile u32 *word_host(u32 n)
{
    u32 at, w;
    for (u32 i = 0; i < g_nitems; n -= g_items[i++].words) {
        const Item *it = &g_items[i];
        if (n >= it->words) continue;
        if (it->rand || !item_at(it, &at) || !guest_word(at += 4u * n, &w)) return NULL;
        return (volatile u32 *)(uintptr_t)(GUEST_RAM_BASE + ((at & 0x1fffffffu) - 0x0c000000u));
    }
    return NULL;
}

/* Every watched word into g_snap: at the vertical blank, as the emulator's half samples (the game swaps state
 * within a frame, a world for its overlay say, and waits for the vertical blank once the frame is done). */
static void snap(void)
{
    u32 n = 0;
    for (u32 i = 0; i < g_nitems; i++)
        for (u32 j = 0; j < g_items[i].words && n < MAX_WORDS; j++, n++)
            g_snap_ok[n] = item_word(&g_items[i], j, &g_snap[n]) ? 0u : NO_WORD;
    g_nwords = n;
}

void lockstep_vblank(void)
{
    if ((!g_drive && !g_trace) || port_extra_drawing()) return;
    snap();
    g_snapped = 1;
}

/* A scene begins: on the console the flip is waited for here, so a read after a frame counted itself and before
 * the next scene (the game's clock put back as a loop ends) is still that frame's; the port waits for the flip as
 * the frame ends (frame_read). */
void lockstep_scene(void)
{
    if ((!g_drive && !g_trace) || port_extra_drawing()) return;
    g_scenes++;
    hw_check_begin();
}

/* The key words of the snapshot, or of the state now (live). */
static void read_key(u32 *key, int live)
{
    u32 n = 0, at = 0, w;
    for (u32 i = 0; i < g_nitems; i++) {
        const Item *it = &g_items[i];
        for (u32 j = 0; j < it->words; j++, at++) {
            if (!it->key) continue;
            if (live) key[n++] = item_word(it, j, &w) ? w : 0xffffffffu;
            else key[n++] = g_snap_ok[at] != NO_WORD ? g_snap[at] : 0xffffffffu;
        }
    }
}

static int key_is(const EmuFrame *e, const u32 *key)
{
    return e->nkey == g_nkey && !memcmp(e->key, key, g_nkey * sizeof *key);
}

static void trace_frame(u32 label, s32 emu)
{
    if (!g_trace) return;
    fprintf(g_trace, "%08x %d", label, emu);
    for (u32 n = 0; n < g_nwords; n++) {
        if (g_snap_ok[n] != NO_WORD) fprintf(g_trace, " %08x", g_snap[n]);
        else fputs(" -", g_trace);
    }
    fputc('\n', g_trace);
    if (!(g_calls & 255u)) fflush(g_trace);
}

/* The emulator frame that starts the key's phase, after the current phase's start; g_nemu if none. */
static u32 phase_start(const u32 *key)
{
    for (u32 m = g_seg + 1u; m < g_nemu; m++)
        if (key_is(&g_emu[m], key) && !key_is(&g_emu[m - 1u], key)) return m;
    return g_nemu;
}

/* 1 if a frame has ended since the last frame read: the game's frame counter moved (or the spec names none). A read
 * within a frame (a clock restored after a load, a timed wait) is no new frame. */
static int frame_read(void)
{
    u32 n;
    if (!g_frames || !item_word(g_frames, 0, &n)) return 1;
    if (g_started && (n == g_frames_seen || (n == g_frames_seen + 1u && g_scenes == 0))) return 0;
    g_scenes = 0;
    g_frames_seen = n;
    return 1;
}

/* When the key has changed to one the paired frame does not have, the next emulator frame that starts it pairs. */
static void pair(const u32 *key)
{
    if (memcmp(key, g_prev_key, g_nkey * sizeof *key) && !key_is(&g_emu[g_ei], key)) {
        u32 m = phase_start(key);
        if (m < g_nemu) {
            /* the port's timer from here: the emulator's plus what the frames between (a movie) left over, in
             * whole TIMER_UNITs, so the game's clock keeps the emulator's remainder and no frame spans the gap */
            s32 off = (s32)((u32)g_value - g_emu[m].label);
            g_off = (off >= 0 ? off + TIMER_UNIT / 2 : off - TIMER_UNIT / 2) / TIMER_UNIT * TIMER_UNIT;
            port_log("lockstep: port frame %u pairs with emulator frame %u (key)\n", g_calls, m);
            g_ei = m;
            g_seg = m;
        } else if (!g_unmatched++) {
            port_log("lockstep: no emulator phase for the key at frame %u; pairing on one to one\n", g_calls);
        }
    }
    memcpy(g_prev_key, key, g_nkey * sizeof *key);
}

/* A load: the emulator frame within LOAD_REACH whose time jumps (a load starts in it). The read that pauses the
 * clock's frame (paused) takes the time after the load; a read just before, while the clock runs, takes the load
 * frame's start (the game may set its clock from it; the emulator's start includes what it did first, a disc read). */
static void load_pair(u32 paused)
{
    u32 best = g_nemu;
    for (u32 d = 0; d <= LOAD_REACH && best == g_nemu; d++) {
        if (g_ei + d < g_nemu && g_ei + d >= g_jump_next && g_emu[g_ei + d].jump) best = g_ei + d;
        else if (d <= g_ei && g_ei - d >= g_jump_next && g_emu[g_ei - d].jump) best = g_ei - d;
    }
    if (best == g_nemu || (!paused && best <= g_ei)) return;
    s32 to = (s32)(paused ? g_emu[best].jump : g_emu[best].label) + g_off;
    if (best != g_ei) port_log("lockstep: port frame %u pairs with emulator frame %u (load%s)\n", g_calls, best,
                               paused ? "" : " start");
    g_ei = best;
    if (paused) g_jump_next = best + 1u;
    g_jumped = (int)paused;
    if (to - g_value > 1) g_value = to - 1;
}

/* A read within a frame: the time moves on a little, so timed waits end. The key is checked too (a load enters its
 * phase within the frame), and a read around a load pairs it (load_pair). */
static s32 in_frame(void)
{
    u32 key[MAX_KEY], paused;
    read_key(key, 1);
    pair(key);
    if (!g_jumped && g_paused && item_word(g_paused, 0, &paused)) load_pair(paused);
    return ++g_value;
}

s32 lockstep_timer(s32 live)
{
    if ((!g_drive && !g_trace) || port_extra_drawing()) return live;
    if (!frame_read()) return g_drive ? in_frame() : live;
    g_calls++;
    ram_frames_capture(g_calls, (u32)g_value, !g_drive ? -1 : g_ended ? -2 : (s32)g_ei);
    g_jumped = 0;
    if (g_watching && g_calls - g_watch_frame - 1u <= 1u) /* trace line N is written as frame N + 2 starts */
        lockstep_watch(g_calls == g_watch_frame + 1u ? word_host(g_watch_word) : NULL, g_watch_frame);
    if (!g_snapped) snap(); /* no vertical blank since the last frame: the state now */
    g_snapped = 0;
    read_key(g_key, 0);
    if (!g_drive) {
        if (g_started) trace_frame((u32)g_value, -1);
        g_started = 1;
        g_value = live;
        return live;
    }
    if (!g_started) {
        g_started = 1;
        g_value = (s32)g_emu[0].label;
        memcpy(g_prev_key, g_key, sizeof g_key);
        return g_value;
    }
    pair(g_key);
    trace_frame((u32)g_value, (s32)g_ei);
    if (g_ei + 1u >= g_nemu) {
        if (!g_ended++) port_log("lockstep: the emulator frames end at port frame %u; live time from here\n", g_calls);
        g_value += (s32)(g_emu[g_ei].label - (g_ei ? g_emu[g_ei - 1u].label : g_emu[g_ei].label));
        return g_value;
    }
    s32 next = (s32)g_emu[g_ei + 1u].label + g_off; /* the emulator's timer itself: the game's clock sums deltas */
    g_value = next - g_value > 0 ? next : g_value + 1;    /* never back past the reads within the frame */
    g_ei++;
    return g_value;
}

int lockstep_pad(HostPad *pad)
{
    if (!g_drive || port_extra_drawing()) return 0;
    const EmuFrame *e = &g_emu[g_ei];
    pad->buttons = e->on;
    pad->r = e->r;
    pad->l = e->l;
    pad->x = e->x;
    pad->y = e->y;
    return 1;
}

/* The RTC (lockstep_rtc_date). A frame that has entered a new phase already (a level seeding rand from the clock as
 * it starts) reads the clock of the emulator frame that entered it: the frames between (a movie the port does not
 * play) have not been paired yet. */
int lockstep_time(HostTime *t)
{
    if (!g_drive || port_extra_drawing()) return 0;
    u32 key[MAX_KEY], at;
    const EmuFrame *e = &g_emu[g_ei];
    read_key(key, 1);
    if (memcmp(key, g_prev_key, g_nkey * sizeof *key) && !key_is(e, key) && (at = phase_start(key)) < g_nemu)
        e = &g_emu[at];
    lockstep_rtc_date(e->rtc, t);
    return 1;
}

void lockstep_close(void)
{
    if (g_trace) fclose(g_trace);
    g_trace = NULL;
    hw_check_close();
    ram_frames_close();
}

s32 lockstep_frame(void) { return g_drive && g_started && !g_ended ? (s32)g_ei : -1; }
