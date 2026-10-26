/* The port's half of the lockstep check (tools/lockstep.py runs the emulator and compares). A game frame starts with
 * a read of the SDK timer (session_timer): the game's clock reads it once per frame, so the state at that read is
 * the state the previous frame left. Both halves trace it as it was at that frame's last vertical blank (before the
 * game's handler), so state the game swaps within a frame reads the same. The timer value is the frame's label on
 * both sides. A game that reads no timer and labels its frames with its own frame counter (flag counter) ends each
 * frame at the vertical blank where that counter moved instead (counter_vblank), and no timer value is replayed.
 *   --lockstep-spec FILE   the watched state, from the game's folder: one item per line, "name words address
 *                          [flags]". The address is hex with steps: * reads a pointer there, +X and -X move by hex X;
 *                          the item is that many u32 words from where the steps end. Flags are the compare tool's,
 *                          except key: the key items are the frames' phase (a scene number, say), and frames: the
 *                          game's frame counter (a timer read with no frame ended since the last is no new frame,
 *                          and the emulator's frames that ran several game frames are split), and counter: the
 *                          label item when it is the game's frame counter, not a copy of the timer. "EMU|PORT"
 *                          gives the port its own address; "@rand" is the C library generator's state (one word).
 *   --lockstep-trace FILE  one line per frame: "label emulator-frame word..", hex, "-" for a word that is not in
 *                          guest RAM (a null or host pointer on the way).
 *   --lockstep FILE        the emulator's frames, "label on r l x y rtc jump key..": the port's frames take their
 *                          timer value (the label; offset after a re-pairing; jump, if not 0, for the first read
 *                          within the frame), pad reads and RTC (seconds from 1950, the AICA's) from the paired
 *                          frame. Frames pair one to one; when the key changes, the next emulator frame that starts
 *                          the same key pairs instead (a movie the port does not play, a different number of loading
 *                          frames).
 *   --lockstep-end exit    ends the run (exit code 0) once the drive's last frame is traced; "live" (the default)
 *                          plays on in live time.
 *   --lockstep-watch W@N   logs each write to trace word W through the port frame that trace line N shows (its state
 *                          is that frame's end), with the stack (lockstep_watch.c). */
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "platform.h"
#include "lockstep.h"
#include "lockstep_parts.h"

#define MAX_WORDS 8192u
#define NO_WORD 0xffffffffu /* in g_snap_ok: the word is not in guest RAM */
#define TIMER_UNIT 3125     /* 4 ms of the SDK timer (781,250 Hz): a whole number of milliseconds */
#define LOAD_REACH 4u       /* emulator frames a load may pair away from the paired one */

static LsSpec g_spec;
static FILE *g_trace;
static EmuFrame *g_emu;
static u32 g_nemu, g_ei, g_seg, g_calls, g_unmatched, g_jump_next; /* the first emulator frame a load may pair with */
static u32 g_key[MAX_KEY], g_prev_key[MAX_KEY];
static s32 g_value, g_off; /* the timer value returned last; the port's timer minus the emulator's */
static int g_drive, g_started, g_ended, g_snapped, g_jumped, g_end_exit;
static u32 g_frames_seen;
static int g_scenes; /* scenes begun since the last frame read */
static u32 g_watch_word, g_watch_frame, g_watching; /* --lockstep-watch */
static u32 g_snap[MAX_WORDS], g_snap_ok[MAX_WORDS], g_nwords;

int lockstep_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--lockstep-spec")) {
        if (!lockstep_spec_load(value, &g_spec)) exit(2);
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
    } else if (!strcmp(arg, "--lockstep-end")) {
        g_end_exit = !strcmp(value, "exit");
        if (!g_end_exit && strcmp(value, "live")) exit(2);
    } else if (!strcmp(arg, "--lockstep-watch")) {
        if (sscanf_s(value, "%u@%u", &g_watch_word, &g_watch_frame) != 2) exit(2);
        g_watching = 1;
    } else {
        return ram_frames_option(arg, value) ? 2 : hw_check_option(arg, value);
    }
    return 2;
}

/* Every watched word into g_snap: at the vertical blank, as the emulator's half samples (the game swaps state
 * within a frame, a world for its overlay say, and waits for the vertical blank once the frame is done). */
static void snap(void)
{
    u32 n = 0;
    for (u32 i = 0; i < g_spec.nitems; i++)
        for (u32 j = 0; j < g_spec.items[i].words && n < MAX_WORDS; j++, n++)
            g_snap_ok[n] = lockstep_item_word(&g_spec.items[i], j, &g_snap[n]) ? 0u : NO_WORD;
    g_nwords = n;
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
    for (u32 i = 0; i < g_spec.nitems; i++) {
        const LsItem *it = &g_spec.items[i];
        for (u32 j = 0; j < it->words; j++, at++) {
            if (!it->key) continue;
            if (live) key[n++] = lockstep_item_word(it, j, &w) ? w : 0xffffffffu;
            else key[n++] = g_snap_ok[at] != NO_WORD ? g_snap[at] : 0xffffffffu;
        }
    }
}

static int key_is(const EmuFrame *e, const u32 *key)
{
    return e->nkey == g_spec.nkey && !memcmp(e->key, key, g_spec.nkey * sizeof *key);
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
    if (!g_spec.frames || !lockstep_item_word(g_spec.frames, 0, &n)) return 1;
    if (g_started && (n == g_frames_seen || (n == g_frames_seen + 1u && g_scenes == 0))) return 0;
    g_scenes = 0;
    g_frames_seen = n;
    return 1;
}

/* When the key has changed to one the paired frame does not have, the next emulator frame that starts it pairs. */
static void pair(const u32 *key)
{
    if (memcmp(key, g_prev_key, g_spec.nkey * sizeof *key) && !key_is(&g_emu[g_ei], key)) {
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
    memcpy(g_prev_key, key, g_spec.nkey * sizeof *key);
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
    if (!g_jumped && g_spec.paused && lockstep_item_word(g_spec.paused, 0, &paused)) load_pair(paused);
    return ++g_value;
}

/* A frame ends: the RAM capture, the write watch and the key of the state it left. */
static void frame_end(u32 label)
{
    g_calls++;
    ram_frames_capture(g_calls, label, !g_drive ? -1 : g_ended ? -2 : (s32)g_ei);
    g_jumped = 0;
    if (g_watching && g_calls - g_watch_frame - 1u <= 1u) { /* trace line N is written as frame N + 2 starts */
        volatile u32 *at = g_calls == g_watch_frame + 1u ? lockstep_word_host(&g_spec, g_watch_word) : NULL;
        lockstep_watch(at, g_watch_frame);
    }
    if (!g_snapped) snap(); /* no vertical blank since the last frame: the state now */
    g_snapped = 0;
    read_key(g_key, 0);
}

/* The drive's last frame is traced: with --lockstep-end exit the run ends here (exit code 0; tools/lockstep.py has
 * nothing more to compare), else it plays on in live time. */
static void drive_ended(void)
{
    port_log("lockstep: the emulator frames end at port frame %u%s\n", g_calls,
             g_end_exit ? "" : "; live time from here");
    if (g_end_exit) port_exit("the lockstep drive ended", 0);
}

s32 lockstep_timer(s32 live)
{
    if ((!g_drive && !g_trace) || port_extra_drawing() || g_spec.counter) return live;
    if (!frame_read()) return g_drive ? in_frame() : live;
    frame_end((u32)g_value);
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
        if (!g_ended++) drive_ended();
        g_value += (s32)(g_emu[g_ei].label - (g_ei ? g_emu[g_ei - 1u].label : g_emu[g_ei].label));
        return g_value;
    }
    s32 next = (s32)g_emu[g_ei + 1u].label + g_off; /* the emulator's timer itself: the game's clock sums deltas */
    g_value = next - g_value > 0 ? next : g_value + 1;    /* never back past the reads within the frame */
    g_ei++;
    return g_value;
}

/* Counter mode (flag counter): the label is the game's own frame counter and the game reads no timer. A frame ends
 * at the vertical blank where the counter has moved, and the snapshot of the vertical blank before is the state it
 * left, as the emulator's half takes it. The frames pair one to one (and by key); no timer value is replayed. */
static void counter_vblank(u32 n)
{
    u32 label = (u32)g_value;
    if (g_started && n == label) return;
    g_value = (s32)n;
    if (!g_started) {
        g_started = 1;
        read_key(g_prev_key, 1);
        return;
    }
    frame_end(n);
    if (!g_drive) {
        trace_frame(label, -1);
        return;
    }
    pair(g_key);
    trace_frame(label, (s32)g_ei);
    if (g_ei + 1u < g_nemu) g_ei++;
    else if (!g_ended++) drive_ended();
}

void lockstep_vblank(void)
{
    u32 n;
    if ((!g_drive && !g_trace) || port_extra_drawing()) return;
    if (g_spec.counter && lockstep_item_word(g_spec.counter, 0, &n)) counter_vblank(n);
    snap();
    g_snapped = 1;
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
    if (memcmp(key, g_prev_key, g_spec.nkey * sizeof *key) && !key_is(e, key) && (at = phase_start(key)) < g_nemu)
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
