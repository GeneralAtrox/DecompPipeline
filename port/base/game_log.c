/* The game log (game_log.h): stamped lines for the player's actions and the game's state, and the last line of a run.
 * It reads the game through the scene map only (scene_dump.h): no write to guest RAM, nothing in a frame. */
#include <windows.h>
#include <signal.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"
#include "game_log.h"
#include "game_log_priv.h"
#include "scene_dump.h"
#include "scene_host.h"
#include "scene_json.h"
#include "session.h"

#define MAX_FIELDS 192u
#define VALUE_CAP 64u        /* bytes of a value compared for a change (a longer string is compared by its start) */
#define DEFAULT_EVERY 600u   /* vertical blanks between snapshots */
#define DEFAULT_SAVE 3600u   /* between autosaves of the recording */
#define NOISE_WINDOW 120u    /* a field that changes more than NOISE_MAX times in this many blanks is summarised */
#define NOISE_MAX 6u
#define SOFT_CAP (8u << 20)  /* state lines stop here, the snapshots go on */
#define HARD_CAP (32u << 20) /* everything stops but the end */

typedef struct Watch {
    u8 last[VALUE_CAP];
    u8 seen, readable;
    char text[96];
    u32 window, changes, hidden;
} Watch;

int gl_on, gl_started;
u32 gl_every = DEFAULT_EVERY, gl_save = DEFAULT_SAVE, gl_poll_frame, gl_poll_vblank, gl_saved_at, gl_fault_at;
static u32 g_next_snap, g_next_save, g_bytes;
ULONGLONG gl_t0;
static Watch g_watch[MAX_FIELDS];
char gl_note[600], gl_note_rec[PATH_CAP];

int game_log_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--game-log")) gl_on = strcmp(value, "off") != 0 && strcmp(value, "0") != 0;
    else if (!strcmp(arg, "--game-log-every")) gl_every = (u32)strtoul(value, NULL, 10) ? (u32)strtoul(value, NULL, 10) : DEFAULT_EVERY;
    else if (!strcmp(arg, "--game-log-save")) gl_save = (u32)strtoul(value, NULL, 10);
    else if (!strcmp(arg, "--fault-at")) gl_fault_at = (u32)strtoul(value, NULL, 10);
    else return 0;
    return 2;
}

int game_log_on(void) { return gl_on; }

void game_log_previous(const char *note, const char *recording)
{
    strncpy_s(gl_note, sizeof gl_note, note ? note : "", _TRUNCATE);
    strncpy_s(gl_note_rec, sizeof gl_note_rec, recording ? recording : "", _TRUNCATE);
}

static void stamp(char *out, size_t cap)
{
    SYSTEMTIME t;
    ULONGLONG ms = GetTickCount64() - gl_t0;
    GetLocalTime(&t);
    sprintf_s(out, cap, "[%02u:%02u:%02u.%03u +%llu.%03llus v%u]", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds,
              ms / 1000u, ms % 1000u, port_vblank_count());
}

/* tier 0: the end, always; 1: actions and snapshots; 2: state changes (they stop first, at the size caps). */
static void emit(int tier, const char *fmt, va_list ap)
{
    static char msg[10000]; /* not on the stack: the end is written from a fault handler too */
    char st[80];
    if (!gl_on) return;
    if (tier >= 2 && g_bytes > SOFT_CAP) {
        if (g_bytes < SOFT_CAP + 4096u) { /* said once */
            g_bytes = SOFT_CAP + 4096u;
            port_log("gamelog: the log passed %u MB: state changes are no longer listed (snapshots go on)\n", SOFT_CAP >> 20);
        }
        return;
    }
    if (tier >= 1 && g_bytes > HARD_CAP) return;
    _vsnprintf_s(msg, sizeof msg, _TRUNCATE, fmt, ap);
    stamp(st, sizeof st);
    g_bytes += (u32)(strlen(st) + strlen(msg) + 2u);
    port_log("%s %s\n", st, msg);
}

void game_log_line(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    emit(1, fmt, ap);
    va_end(ap);
}

static void state_line(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    emit(2, fmt, ap);
    va_end(ap);
}

void end_line(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    emit(0, fmt, ap);
    va_end(ap);
}

/* ---- the pad ---- */

static const struct {
    u32 bit;
    const char *name;
} k_buttons[] = {{HOST_PAD_C, "C"},   {HOST_PAD_B, "B"},       {HOST_PAD_A, "A"},         {HOST_PAD_START, "START"},
                 {HOST_PAD_UP, "UP"}, {HOST_PAD_DOWN, "DOWN"}, {HOST_PAD_LEFT, "LEFT"},  {HOST_PAD_RIGHT, "RIGHT"},
                 {HOST_PAD_Z, "Z"},   {HOST_PAD_Y, "Y"},       {HOST_PAD_X, "X"},
                 {HOST_PAD_LB, "LB"}, {HOST_PAD_RB, "RB"},     {HOST_PAD_L3, "L3"},       {HOST_PAD_R3, "R3"},
                 {HOST_PAD_BACK, "BACK"}, {HOST_PAD_GUIDE, "GUIDE"}, {HOST_PAD_RS_UP, "RS-UP"},
                 {HOST_PAD_RS_DOWN, "RS-DOWN"}, {HOST_PAD_RS_LEFT, "RS-LEFT"}, {HOST_PAD_RS_RIGHT, "RS-RIGHT"}};

const char *game_log_button(unsigned bit)
{
    for (size_t i = 0; i < sizeof k_buttons / sizeof k_buttons[0]; i++)
        if (k_buttons[i].bit == bit) return k_buttons[i].name;
    return "?";
}

/* The stick as a direction word (the stick's y is negative up). */
static const char *stick_dir(int x, int y)
{
    int l = x < -40, r = x > 40, u = y < -40, d = y > 40;
    if (u) return l ? "up-left" : r ? "up-right" : "up";
    if (d) return l ? "down-left" : r ? "down-right" : "down";
    return l ? "left" : r ? "right" : "center";
}

void game_log_key(unsigned slot, int down, const char *name, unsigned bit)
{
    static unsigned char was[64];
    if (!gl_on || slot >= sizeof was || was[slot] == (down != 0)) return;
    was[slot] = (unsigned char)(down != 0);
    game_log_line("keyboard: %s %s (P1 %s)", down ? "press" : "release", name,
                  (bit & GAME_LOG_TRIGGER_L) ? "L trigger" : (bit & GAME_LOG_TRIGGER_R) ? "R trigger" : game_log_button(bit));
}

void game_log_pads(const HostPad *pad, unsigned connected)
{
    static HostPad prev[HOST_PADS];
    static unsigned had = 1;
    static const char *dir[HOST_PADS];
    if (!gl_on) return;
    for (unsigned p = 0; p < HOST_PADS; p++) {
        unsigned now = (connected >> p) & 1u;
        if (now != ((had >> p) & 1u)) game_log_line("P%u controller %s", p + 1, now ? "connected" : "gone");
        if (now) {
            u32 d = pad[p].buttons ^ prev[p].buttons;
            for (size_t i = 0; i < sizeof k_buttons / sizeof k_buttons[0]; i++)
                if (d & k_buttons[i].bit)
                    game_log_line("P%u %s %s", p + 1, (pad[p].buttons & k_buttons[i].bit) ? "press" : "release",
                                  k_buttons[i].name);
            const char *sd = stick_dir(pad[p].x, pad[p].y);
            if (sd != dir[p] && (dir[p] || strcmp(sd, "center"))) game_log_line("P%u stick %s", p + 1, sd);
            dir[p] = sd;
            if ((pad[p].l > 128) != (prev[p].l > 128))
                game_log_line("P%u trigger L %s", p + 1, pad[p].l > 128 ? "press" : "release");
            if ((pad[p].r > 128) != (prev[p].r > 128))
                game_log_line("P%u trigger R %s", p + 1, pad[p].r > 128 ? "press" : "release");
        }
        prev[p] = now ? pad[p] : (HostPad){0};
    }
    had = connected | 1u;
}

/* ---- the game's fields ---- */

static int field_int(const SceneField *f, const u8 *p, s32 *v)
{
    if (!p || f->kind > SCENE_UINT) return 0;
    if (f->size == 1) *v = f->kind == SCENE_INT ? *(const s8 *)p : *p;
    else if (f->size == 2) *v = f->kind == SCENE_INT ? *(const s16 *)p : *(const u16 *)p;
    else *v = *(const s32 *)p;
    return 1;
}

/* The value as the log shows it: NAME (n) for a named value, else the number, a float, or the string. */
static void value_text(const SceneField *f, char *out, size_t cap)
{
    const u8 *p = scene_field_bytes(f);
    s32 v;
    if (!p) {
        strcpy_s(out, cap, "?");
    } else if (f->kind == SCENE_STRING) {
        sprintf_s(out, cap, "\"%.*s\"", (int)strnlen((const char *)p, f->size), (const char *)p);
    } else if (f->kind == SCENE_FLOAT) {
        sprintf_s(out, cap, "%.4g", f->size == 8 ? *(const double *)p : (double)*(const f32 *)p);
    } else if (field_int(f, p, &v)) {
        const char *name = NULL;
        for (u32 i = 0; i < f->name_count && !name; i++)
            if (f->names[i].value == v) name = f->names[i].name;
        if (name) sprintf_s(out, cap, "%s (%d)", name, v);
        else sprintf_s(out, cap, f->kind == SCENE_UINT && f->size == 4 ? "%u" : "%d", v);
    } else {
        strcpy_s(out, cap, "?");
    }
}

static int nonzero(const SceneField *f)
{
    const u8 *p = scene_field_bytes(f);
    if (!p) return 0;
    for (u32 i = 0; i < f->size; i++)
        if (p[i]) return 1;
    return 0;
}

static void counts_text(char *out, size_t cap)
{
    out[0] = 0;
    for (u32 c = 0; c < scene_count_count; c++) {
        int n = 0;
        char one[96];
        for (u32 k = 0; k < scene_counts[c].of_count; k++) {
            const SceneField *f = scene_field(scene_counts[c].of[k]);
            n += f && nonzero(f);
        }
        sprintf_s(one, sizeof one, "%s%s=%d", out[0] ? " " : "", scene_counts[c].key, n);
        strcat_s(out, cap, one);
    }
}

/* Every field and count, in lines of a readable length. */
static void snapshot(const char *what)
{
    char line[420], item[160], val[96];
    u32 part = 0;
    line[0] = 0;
    counts_text(line, sizeof line);
    for (u32 i = 0; i < scene_field_count; i++) {
        value_text(&scene_fields[i], val, sizeof val);
        sprintf_s(item, sizeof item, "%s=%s", scene_fields[i].key, val);
        if (strlen(line) + strlen(item) + 2 > 360) {
            game_log_line("%s (%u): %s", what, ++part, line);
            line[0] = 0;
        }
        if (line[0]) strcat_s(line, sizeof line, " ");
        strcat_s(line, sizeof line, item);
    }
    if (!part) game_log_line("%s: %s", what, line);
    else if (line[0]) game_log_line("%s (%u): %s", what, part + 1, line);
}

static void watch_fields(u32 v)
{
    for (u32 i = 0; i < scene_field_count && i < MAX_FIELDS; i++) {
        const SceneField *f = &scene_fields[i];
        Watch *w = &g_watch[i];
        if (!f->log) continue;
        const u8 *p = scene_field_bytes(f);
        u32 n = f->size < VALUE_CAP ? f->size : VALUE_CAP;
        if (v - w->window >= NOISE_WINDOW) { /* a window over: say what was held back */
            if (w->hidden) state_line("%s: %u more changes in %u vertical blanks, now %s", f->key, w->hidden, v - w->window, w->text);
            w->window = v;
            w->changes = w->hidden = 0;
        }
        if (w->seen && (p != NULL) == (w->readable != 0) && (!p || !memcmp(p, w->last, n))) continue;
        char now[96];
        value_text(f, now, sizeof now);
        if (w->seen) {
            if (++w->changes > NOISE_MAX) w->hidden++;
            else state_line("%s: %s -> %s", f->key, w->text, now);
        }
        w->seen = 1;
        w->readable = p != NULL;
        if (p) memcpy(w->last, p, n);
        strcpy_s(w->text, sizeof w->text, now);
    }
}

/* The recording to a file beside the log, by a temporary name so a kill mid-write leaves the last whole one. */
void log_dir(char *out, size_t cap)
{
    const char *path = port_log_path();
    const char *slash = strrchr(path, '\\');
    const char *alt = strrchr(path, '/');
    if (alt > slash) slash = alt;
    if (!slash) strcpy_s(out, cap, ".");
    else {
        size_t n = (size_t)(slash - path);
        if (n >= cap) n = cap - 1;
        memcpy(out, path, n);
        out[n] = 0;
    }
}

int autosave_path(char *out, size_t cap)
{
    char dir[PATH_CAP];
    if (!port_log_path()[0]) return 0;
    log_dir(dir, sizeof dir);
    sprintf_s(out, cap, "%s\\autosave.ptrec", dir);
    return 1;
}

void recording_header(char *out, size_t cap)
{
    sprintf_s(out, cap, "%s\nframe %u vblank %u\n", port_console.version, gl_poll_frame, port_vblank_count());
}

static void autosave(void)
{
    char path[PATH_CAP], tmp[PATH_CAP + 8], header[1024];
    if (!autosave_path(path, sizeof path)) return;
    sprintf_s(tmp, sizeof tmp, "%s.tmp", path);
    recording_header(header, sizeof header);
    if (session_write(tmp, header) && MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING)) gl_saved_at = port_vblank_count();
    else DeleteFileA(tmp);
}

/* What the log costs a frame (the poll: field watch, snapshot, autosave), reported in the END line. */
GameLogCost gl_cost;

static void poll_frame(unsigned frame);

void game_log_poll(unsigned frame)
{
    if (gl_fault_at && port_vblank_count() >= gl_fault_at) {
        port_log("fault-at: writing to address 0x40 at vertical blank %u (--fault-at)\n", port_vblank_count());
        *(volatile u32 *)0x40 = 1u;
    }
    if (!gl_on || !gl_started || port_extra_drawing()) return;
    LARGE_INTEGER t0, t1;
    QueryPerformanceCounter(&t0);
    poll_frame(frame);
    QueryPerformanceCounter(&t1);
    gl_cost.total += (unsigned long long)(t1.QuadPart - t0.QuadPart);
    if ((unsigned long long)(t1.QuadPart - t0.QuadPart) > gl_cost.longest)
        gl_cost.longest = (unsigned long long)(t1.QuadPart - t0.QuadPart);
    gl_cost.count++;
}

static void poll_frame(unsigned frame)
{
    u32 v = port_vblank_count();
    gl_poll_frame = frame;
    gl_poll_vblank = v;
    if (!g_next_snap) { /* the first frame: where the game starts */
        watch_fields(v);
        snapshot("start state");
        g_next_snap = v + gl_every;
        g_next_save = v + gl_save;
        return;
    }
    watch_fields(v);
    if (v >= g_next_snap) {
        g_next_snap = v + gl_every;
        snapshot("snapshot");
    }
    if (gl_save && v >= g_next_save) {
        g_next_save = v + gl_save;
        autosave();
    }
}

