/* The game log's last line, the crash recording with its replay commands, the handlers for the ends nothing else
 * reports, and the start (game_log.h). */
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
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

#define KEEP_CRASH 5 /* crash recordings kept */

static volatile LONG gl_ended;

/* ---- the end ---- */

static void prune_crash_recordings(const char *dir)
{
    char pattern[PATH_CAP], names[32][64];
    int n = 0;
    WIN32_FIND_DATAA fd;
    sprintf_s(pattern, sizeof pattern, "%s\\crash-*.ptrec", dir);
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (n < 32) strncpy_s(names[n++], 64, fd.cFileName, _TRUNCATE);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    for (int pass = 0; n - pass > KEEP_CRASH; pass++) { /* the names sort by time: delete the oldest */
        int oldest = -1;
        for (int i = 0; i < n; i++)
            if (names[i][0] && (oldest < 0 || strcmp(names[i], names[oldest]) < 0)) oldest = i;
        char path[PATH_CAP];
        sprintf_s(path, sizeof path, "%s\\%s", dir, names[oldest]);
        DeleteFileA(path);
        names[oldest][0] = 0;
    }
}

/* Where the player resumes a recording that reaches vertical blank end: 5 s before it (host_resume.cpp does the same). */
static u32 resume_at(u32 end)
{
    u32 back = 5u * (port_console.tick_hz ? port_console.tick_hz : 60u);
    return end > 2u * back ? end - back : end / 2u + 1u;
}
#define RESUME_TEXT \
    "resume: in the next run, Help > Resume before the last crash; or this command line with --replay \"%s\" " \
    "--replay-until %u --fast-until %u --live-input on"

/* The recording of this run, saved, and the commands that replay it to the last frame before the end. */
static void crash_save(void)
{
    char dir[PATH_CAP], st[32], rec[PATH_CAP + 64], rdir[PATH_CAP + 80], header[1024];
    if (!port_log_path()[0]) return;
    log_dir(dir, sizeof dir);
    scene_stamp(st, sizeof st);
    sprintf_s(rec, sizeof rec, "%s\\crash-%s.ptrec", dir, st);
    sprintf_s(rdir, sizeof rdir, "%s\\crash-%s_replay", dir, st);
    recording_header(header, sizeof header);
    if (!session_write(rec, header)) {
        end_line("recording: not saved (--record off, the recording was cut short, or the file cannot be written)");
        return;
    }
    char full[PATH_CAP + 64];
    scene_full_path(rec, full, sizeof full);
    scene_make_dir(rdir);
    char *cmd = scene_replay_command(full, rdir, gl_poll_frame, gl_poll_vblank);
    if (cmd && gl_fault_at) { /* the replay has to raise the same test fault */
        size_t n = strlen(cmd) + 32;
        char *more = malloc(n);
        if (more) {
            sprintf_s(more, n, "%s --fault-at %u", cmd, gl_fault_at);
            free(cmd);
            cmd = more;
        }
    }
    end_line("recording: saved %s (%u pad reads, to vertical blank %u)", full, session_reads(), port_vblank_count());
    if (cmd) {
        end_line("replay to the last frame before the end (vertical blank %u; the frame is in the _replay folder): %s",
                 gl_poll_vblank, cmd);
        end_line("replay on through the end: the same command without --stop-at, with --seconds raised to cover the "
                 "session; it ends on the same FAULT (or exit) one frame later");
    }
    u32 at = resume_at(port_vblank_count());
    end_line(RESUME_TEXT, full, at, at);
    free(cmd);
    prune_crash_recordings(dir);
}

#define CODE_UNKNOWN (-1000) /* the atexit hook is not told the exit code */

void game_log_end(const char *how, int code, int save)
{
    if (!gl_on || InterlockedExchange(&gl_ended, 1)) return;
    char codes[48];
    if (code == CODE_UNKNOWN) sprintf_s(codes, sizeof codes, "exit code not known here");
    else sprintf_s(codes, sizeof codes, "exit code %d", code);
    if (gl_cost.count) {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        end_line("gamelog: cost of the log per presented frame: average %.1f us, longest %.0f us (a snapshot or the "
                 "autosave), over %llu frames", 1e6 * (double)gl_cost.total / (double)f.QuadPart / (double)gl_cost.count,
                 1e6 * (double)gl_cost.longest / (double)f.QuadPart, gl_cost.count);
    }
    end_line("END: %s (%s) after %u vertical blanks, %llu s", how, codes, port_vblank_count(),
             (GetTickCount64() - gl_t0) / 1000u);
    port_log_flush();
    char path[PATH_CAP];
    if (code == CODE_UNKNOWN) {
        if (gl_saved_at && autosave_path(path, sizeof path))
            end_line("recording: kept, the autosave of vertical blank %u is %s", gl_saved_at, path);
    } else if (code == 0) {
        if (autosave_path(path, sizeof path)) DeleteFileA(path);
    } else if (save) {
        __try {
            crash_save();
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            end_line("recording: the save itself faulted (the heap or the recording is damaged)");
        }
    } else if (gl_saved_at && autosave_path(path, sizeof path)) {
        end_line("recording: not saved now; the autosave of vertical blank %u is %s", gl_saved_at, path);
    }
    port_log_flush();
}

static void __cdecl on_invalid_parameter(const wchar_t *expr, const wchar_t *fn, const wchar_t *file, unsigned line,
                                         uintptr_t r)
{
    char how[300];
    (void)file;
    (void)line;
    (void)r;
    sprintf_s(how, sizeof how, "CRT invalid parameter in %ls (%ls)", fn ? fn : L"?", expr ? expr : L"?");
    game_log_end(how, 3, 1);
    TerminateProcess(GetCurrentProcess(), 0xC000000Du);
}

static void __cdecl on_purecall(void)
{
    game_log_end("CRT pure virtual call", 3, 1);
    TerminateProcess(GetCurrentProcess(), 3u);
}

static void on_abort(int sig)
{
    (void)sig;
    game_log_end("abort() called", 3, 1);
    TerminateProcess(GetCurrentProcess(), 3u);
}

static BOOL WINAPI on_console(DWORD ev)
{
    char how[120];
    sprintf_s(how, sizeof how, "console control event %lu (Ctrl+C, Ctrl+Break, its console closed, logoff or shutdown)", ev);
    game_log_end(how, 1, 0);
    return FALSE; /* the default handler ends the process */
}

static void on_exit_without_reason(void)
{
    game_log_end("the process exited (exit() or main returned) without a stop message of the build", CODE_UNKNOWN, 0);
}

/* The recording a previous run left (the host moved it aside): the vertical blank it reaches is its last 4 bytes. */
static void previous_recording(void)
{
    FILE *f = NULL;
    u32 end = 0;
    if (!gl_note_rec[0] || fopen_s(&f, gl_note_rec, "rb") || !f) return;
    if (fseek(f, -4, SEEK_END) == 0 && fread(&end, 4, 1, f) == 1) {
        char rdir[PATH_CAP + 16], full[PATH_CAP];
        scene_full_path(gl_note_rec, full, sizeof full);
        sprintf_s(rdir, sizeof rdir, "%s_replay", full);
        game_log_line("gamelog: that run's recording (saved at most %u s before it ended): %s, to vertical blank %u", gl_save / 60u,
                      full, end);
        char *cmd = end ? scene_replay_command(full, rdir, end, end) : NULL;
        if (cmd) game_log_line("gamelog: replay it (the frame number is a guess; drop --dump-frames if it is not reached): %s", cmd);
        u32 at = resume_at(end);
        if (end) game_log_line(RESUME_TEXT, full, at, at);
        free(cmd);
    }
    fclose(f);
}

void game_log_start(void)
{
    char buf[700];
    int argc = 0;
    char *const *argv = host_args(&argc);
    if (!gl_on) return;
    gl_t0 = GetTickCount64();
    gl_started = 1;
    _set_invalid_parameter_handler(on_invalid_parameter);
    _set_purecall_handler(on_purecall);
    signal(SIGABRT, on_abort);
    SetConsoleCtrlHandler(on_console, TRUE);
    atexit(on_exit_without_reason);
    game_log_line("gamelog: on. Lines are [clock +seconds-since-start vVERTICAL-BLANK] text; P1 press A / release A are the "
                  "pad, keyboard: and menu: the player's keys and menu choices, window: the window, NAME: old -> new a "
                  "game value changing, snapshot: all of them, END: the last line of a run");
    game_log_line("gamelog: %s, game %s, snapshot every %u vertical blanks, recording autosaved every %u", port_console.version,
                  port_console.game, gl_every, gl_save);
    buf[0] = 0;
    /* Cut, never overflow: strcat_s ends the run (0xC000000D) on one long argument, such as a scripted --input. */
    const size_t room = sizeof buf - 16;
    int cut = 0;
    for (int i = 1; i < argc && !cut; i++)
        cut = strncat_s(buf, room, argv[i], _TRUNCATE) || strncat_s(buf, room, " ", _TRUNCATE);
    if (cut) strcat_s(buf, sizeof buf, "... (cut)");
    game_log_line("gamelog: arguments: %s", buf);
    if (gl_note[0]) game_log_line("gamelog: %s", gl_note);
    previous_recording();
    if (!scene_field_count) game_log_line("gamelog: this game has no scene_dump.json fields yet: only the actions are logged");
}
