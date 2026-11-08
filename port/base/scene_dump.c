/* "Copy agent prompt" (scene_dump.h): the JSON an agent gets, with the frame and the session recording beside it.
 *   task          the game and the scene fields its map names for the task, on one line
 *   problem       empty: the user says what is wrong
 *   how_to_reach  the port.exe command that replays the session to this frame (session.c), and how to compare
 *   scene         the vertical blank, the frame, the pad reads, and the map's fields read from guest RAM
 *   files         what to read and edit in this scene (the map's rules), relative to files.root
 *   capture       the files written; build: the version, the executable, the arguments and the launcher
 *   host          the input devices, the output, the frame rate, and the log's warnings and last lines
 * Options: --scene-dump-at V (a dump at vertical blank V), --scene-dir DIR (where; default scene_dumps in the data
 * folder), --stop-at V (exit, code 0, once the frame at vertical blank V is presented and dumped: replays),
 * --watch-ram A@V1:V2 (A hex: logs each write to guest word A, with the stack, in vertical blanks V1 to V2; the same
 * debug-register watch as --lockstep-watch, for a replay that has no lockstep: tools/lockstep.py names the stack). */
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"
#include "scene_host.h"
#include "scene_json.h"
#include "session.h"

#define PATH_CAP 520u

static int g_requested;
static u32 g_at, g_stop;
static u32 g_watch_word, g_watch_from, g_watch_to, g_watch_on; /* --watch-ram */
static char g_dir[PATH_CAP];

int scene_dump_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--scene-dump-at")) g_at = (u32)strtoul(value, NULL, 10);
    else if (!strcmp(arg, "--scene-dir")) strcpy_s(g_dir, sizeof g_dir, value);
    else if (!strcmp(arg, "--stop-at")) g_stop = (u32)strtoul(value, NULL, 10);
    else if (!strcmp(arg, "--watch-ram")) {
        if (sscanf_s(value, "%x@%u:%u", &g_watch_word, &g_watch_from, &g_watch_to) != 3) exit(2);
    }
    else return 0;
    return 2;
}

void scene_dump_request(void) { g_requested = 1; }

/* An argument for the replay command: the option and its value from this run, if it had one (a file: absolute, so
 * the command works from any folder). */
void scene_carry(Json *c, const char *opt, int file)
{
    char full[PATH_CAP];
    const char *v = host_arg(opt);
    if (v) json_f(c, file ? " %s \"%s\"" : " %s %s", opt, file ? scene_full_path(v, full, sizeof full) : v);
}

/* The command that replays the recording rec to the frame presented at vertical blank stop, as plain text (the caller
 * frees it); the game log (game_log.c) prints it after a crash. */
char *scene_replay_command(const char *rec, const char *rdir, u32 frame, u32 stop)
{
    Json c = {0};
    char exe[PATH_CAP];
    scene_exe_path(exe, sizeof exe);
    json_f(&c, "\"%s\"", exe);
    PortView v;
    console_view(&v);
    console_replay_args(&c, 0); /* the console's own options before the replay (the disc) */
    json_f(&c, " --replay \"%s\" --fixed-clock 1 --present tick --headless --size %ux%u", rec, v.out_w, v.out_h);
    if (v.warp) json_put(&c, " --warp");
    console_replay_args(&c, 1); /* and after it */
    json_f(&c, " --record off --seconds %u --stop-at %u --log \"%s\\port.log\" --dump-dir \"%s\" --dump-frames %u",
           stop / 150u + 60u, stop, rdir, rdir, frame);
    return c.p;
}

static void strings(Json *j, char *const *list, int n)
{
    json_put(j, "[");
    for (int i = 0; i < n; i++) {
        if (i) json_put(j, ", ");
        json_str(j, list[i], 2048);
    }
    json_put(j, "]");
}

static void document(Json *j, const char *base, u32 frame, int bmp, int rec)
{
    char exe[PATH_CAP], env[PATH_CAP], path[PATH_CAP], rdir[PATH_CAP];
    int argc = 0;
    char *const *argv = host_args(&argc);
    PortView v;
    scene_exe_path(exe, sizeof exe);
    sprintf_s(rdir, sizeof rdir, "%s_replay", base);
    json_put(j, "{\n  \"format\": \"dcport_scene_dump_v1\",\n  \"task\": ");
    Json task = {0};
    json_f(&task, "%s", port_console.game);
    for (u32 i = 0; i < scene_task_count; i++) {
        const SceneField *f = scene_field(scene_task_keys[i]);
        if (!f) continue;
        json_f(&task, "%s%s ", i ? "; " : ": ", f->key);
        scene_field_value(&task, f, 1);
    }
    json_str(j, task.p, 4096);
    free(task.p);
    json_put(j, ",\n  \"problem\": \"\",\n  \"how_to_reach\": [\n    ");
    if (rec) {
        sprintf_s(path, sizeof path, "%s.ptrec", base);
        char *cmd = scene_replay_command(path, rdir, frame, port_vblank_count());
        json_str(j, cmd, 8192);
        free(cmd);
        json_f(j, ",\n    \"That replays this session from boot (the .ptrec recording) on the fixed clock, without a "
                  "window, to frame %u (vertical blank %u), and writes it as frame_%06u.png in the _replay folder: "
                  "it matches capture.screenshot (the .bmp) pixel for pixel. It stops there (exit code 0); add "
                  "--scene-dump-at %u for this dump again, or drop --headless and --stop-at and add --live-input on to watch and play on (the real keyboard and pads are read only then).\"",
               frame, port_vblank_count(), frame, port_vblank_count());
    } else {
        json_put(j, "\"No session recording (--record off, or it was cut short): reach the scene by playing.\"");
    }
    json_put(j, ",\n    ");
    json_str(j, "Play it live: build.command_line.", 64);
    json_put(j, "\n  ],\n  \"scene\": {\n");
    json_f(j, "    \"vblank\": %u,\n    \"frame\": %u,\n    \"pad_reads\": %u,\n    \"replaying\": %s", port_vblank_count(),
           frame, session_reads(), session_replaying() ? "true" : "false");
    for (u32 i = 0; i < scene_field_count; i++) {
        json_f(j, ",\n    \"%s\": ", scene_fields[i].key);
        scene_field_value(j, &scene_fields[i], 0);
    }
    console_scene_extra(j); /* the console's own state (a Saturn's VDP registers) */
    json_put(j, "\n  },\n  \"files\": {\n    \"root\": ");
    json_str(j, scene_root, PATH_CAP);
    json_put(j, ",\n    \"read\": ");
    scene_file_list(j, 0);
    json_put(j, ",\n    \"edit\": ");
    scene_file_list(j, 1);
    json_put(j, ",\n    \"note\": \"edit is a suggestion: trim it. Never hand-edit decompiled (readable) C.\"\n  },\n");
    json_put(j, "  \"capture\": {\n    \"json\": ");
    sprintf_s(path, sizeof path, "%s.json", base);
    json_str(j, path, PATH_CAP);
    sprintf_s(path, sizeof path, "%s.bmp", base);
    json_put(j, ",\n    \"screenshot\": ");
    json_str(j, bmp ? path : "", PATH_CAP);
    sprintf_s(path, sizeof path, "%s.ptrec", base);
    json_put(j, ",\n    \"recording\": ");
    json_str(j, rec ? path : "", PATH_CAP);
    json_put(j, ",\n    \"replay_folder\": ");
    json_str(j, rdir, PATH_CAP);
    json_put(j, ",\n    \"log\": ");
    json_str(j, port_log_path()[0] ? scene_full_path(port_log_path(), path, sizeof path) : "", PATH_CAP);
    json_put(j, "\n  },\n  \"build\": {\n    \"version\": ");
    json_str(j, port_console.version, 256);
    json_put(j, ",\n    \"exe\": ");
    json_str(j, exe, PATH_CAP);
    json_put(j, ",\n    \"command_line\": ");
    json_str(j, scene_command_line(), 8192);
    json_put(j, ",\n    \"arguments\": ");
    strings(j, argv + 1, argc - 1); /* the defaults, the saved settings, then the command line (host_main.cpp) */
    json_put(j, ",\n    \"launcher\": ");
    json_str(j, scene_env("PHANGRADE_SETTINGS", env, sizeof env) ? "Phangrade" : "", 16);
    json_put(j, "\n  },\n  \"host\": {\n    \"input_devices\": [");
    for (int i = 0; i < input_device_count(); i++) {
        if (i) json_put(j, ", ");
        json_str(j, input_device_desc(i), 256);
    }
    console_view(&v);
    json_f(j, "],\n    \"output\": \"%ux%u\",\n    \"frame_size\": \"%ux%u\",\n    \"window\": ", v.out_w, v.out_h,
           v.frame_w, v.frame_h);
    json_str(j, v.headless ? "headless" : host_window_state(), 32);
    json_f(j, ",\n    \"present\": \"%s\",\n    \"frames_a_second\": %.1f,\n", v.interp ? "interp" : "tick",
           (double)v.presented_fps);
    scene_log_lists(j, port_log_path(), "    ");
    json_put(j, "\n  }\n}\n");
}

static void dump(u32 frame)
{
    char dir[PATH_CAP], stamp[32], base[PATH_CAP], path[PATH_CAP], env[PATH_CAP];
    Json j = {0};
    FILE *f = NULL;
    sprintf_s(path, sizeof path, "%s", g_dir[0] ? g_dir : host_data_folder());
    if (!g_dir[0]) strcat_s(path, sizeof path, "\\scene_dumps");
    scene_full_path(path, dir, sizeof dir);
    if (!scene_make_dir(dir)) {
        port_log("scene: cannot make the folder %s\n", dir);
        return;
    }
    port_log_flush(); /* the dump quotes the log's tail */
    scene_stamp(stamp, sizeof stamp);
    sprintf_s(base, sizeof base, "%s\\%s", dir, stamp);
    for (int n = 2; n < 100; n++) { /* two dumps in one second */
        sprintf_s(path, sizeof path, "%s.json", base);
        if (fopen_s(&f, path, "rb") || !f) break;
        fclose(f);
        f = NULL;
        sprintf_s(base, sizeof base, "%s\\%s_%d", dir, stamp, n);
    }
    sprintf_s(path, sizeof path, "%s.bmp", base);
    int bmp = console_capture_bmp(path);
    sprintf_s(path, sizeof path, "%s.ptrec", base);
    char header[1024];
    sprintf_s(header, sizeof header, "%s\nframe %u vblank %u\n", port_console.version, frame, port_vblank_count());
    int rec = session_write(path, header);
    sprintf_s(path, sizeof path, "%s_replay", base);
    if (rec) scene_make_dir(path);
    document(&j, base, frame, bmp, rec);
    sprintf_s(path, sizeof path, "%s.json", base);
    if (!j.p || fopen_s(&f, path, "wb") || !f || fwrite(j.p, 1, j.n, f) != j.n) {
        port_log("scene: cannot write %s\n", path);
    }
    if (f) fclose(f);
    /* The clipboard holds a prompt: one instruction line (as Phangrade's crash prompt leads), the JSON, then
     * Phangrade's context for the run. */
    Json prompt = {0};
    json_f(&prompt, "The game \"%s\" (%s port) shows a problem at the frame captured below. Read the user's "
                    "description of it, then investigate with this scene dump (how_to_reach replays the session to "
                    "this frame).\n\n", port_console.game, port_console.name);
    if (j.p) json_put(&prompt, j.p);
    const char *ctx = scene_env("PHANGRADE_AGENT_CONTEXT", env, sizeof env);
    u32 size = 0;
    char *extra = ctx ? scene_read_file(ctx, 1u << 20, &size) : NULL; /* read now: Phangrade rewrites it */
    if (extra && size) {
        json_put(&prompt, "\nPhangrade context for this run:\n");
        json_put(&prompt, extra);
    }
    free(extra);
    int clip = j.p && prompt.p && scene_clipboard(prompt.p);
    free(prompt.p);
    port_log("scene: dumped %s.json at frame %u (vertical blank %u)%s%s%s\n", base, frame, port_vblank_count(),
             clip ? ", copied to the clipboard" : ", NOT copied to the clipboard", bmp ? "" : ", no frame image",
             rec ? "" : ", no session recording");
    free(j.p);
}

void scene_dump_poll(u32 frame)
{
    if (g_at && port_vblank_count() >= g_at) {
        g_at = 0;
        g_requested = 1;
    }
    if (g_watch_word) {
        u32 v = port_vblank_count();
        if (v >= g_watch_from && v <= g_watch_to) { /* re-armed each frame: the hit limit is per frame */
            console_watch(g_watch_word, v);
            g_watch_on = 1;
        } else if (g_watch_on && v > g_watch_to) {
            console_watch(0, v);
            g_watch_on = 0;
            g_watch_word = 0;
        }
    }
    if (port_extra_drawing()) return;
    if (g_requested) {
        g_requested = 0;
        dump(frame);
    }
    if (g_stop && port_vblank_count() >= g_stop) port_exit("--stop-at reached", 0);
}
