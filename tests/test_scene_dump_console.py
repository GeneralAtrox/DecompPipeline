"""The scene dump's console members (base/console.h PortConsole replay_fps, no_replay, flags PORT_CONSOLE_FCAP) and the frame
capture (base/frame_capture.c): scene_dump.c and frame_capture.c built at /W4 /WX with stubs beside them and run, once as a
console that sets none of them and once as one that sets all three.

1. A console that leaves them out (every console but the Xbox 360's) gets the replay command it got before, byte for byte
   (--fixed-clock 1, the time limit at 150 frames a second), and its dump names no frame capture: nothing is armed, even by
   a direct fcap_arm, and no _frame folder is made.
2. replay_fps and no_replay: the command drops --fixed-clock 1, the limit is at replay_fps, and the dump says why.
3. PORT_CONSOLE_FCAP: a dump arms the capture, a record is kept, and the next frame writes frame.jsonl, summary.json and
   the console's final picture into <dump>_frame, then disarms.

usage: python -W error tests/test_scene_dump_console.py
"""
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))
from test_port_buttons import BuildRun  # noqa: E402

STUBS = r'''
#include <direct.h>
#include <io.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "frame_capture.h"
#include "scene_dump.h"
#include "scene_host.h"
#include "scene_json.h"
#include "session.h"
static u32 g_vb;
u32 port_vblank_count(void) { return g_vb; }
int port_extra_drawing(void) { return 0; }
void port_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); }
void port_log_flush(void) {}
const char *port_log_path(void) { return ""; }
void port_exit(const char *why, int code) { printf("exit: %s\n", why); exit(code); }
static void put(Json *j, const char *s, u32 n)
{
    if (j->n + n + 1 > j->cap) {
        j->cap = (j->n + n + 1) * 2;
        j->p = (char *)realloc(j->p, j->cap);
        if (!j->p) exit(3);
    }
    memcpy(j->p + j->n, s, n);
    j->n += n;
    j->p[j->n] = 0;
}
void json_put(Json *j, const char *s) { put(j, s, (u32)strlen(s)); }
void json_f(Json *j, const char *fmt, ...)
{
    char tmp[4096];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    if (n > 0) put(j, tmp, (u32)n);
}
void json_str(Json *j, const char *s, u32 max) { put(j, "\"", 1); put(j, s, (u32)strnlen(s, max)); put(j, "\"", 1); }
const SceneField scene_fields[1] = {{0}};
const u32 scene_field_count = 0;
const char *const scene_task_keys[1] = {0};
const u32 scene_task_count = 0;
const char scene_root[] = "root";
const SceneField *scene_field(const char *key) { (void)key; return NULL; }
void scene_field_value(Json *j, const SceneField *f, int text) { (void)j; (void)f; (void)text; }
void scene_file_list(Json *j, int edit) { (void)edit; json_put(j, "[]"); }
void scene_log_lists(Json *j, const char *log, const char *indent) { (void)log; (void)indent; json_put(j, "    \"log_tail\": []"); }
int scene_clipboard(const char *utf8) { (void)utf8; return 0; }
char *scene_read_file(const char *path, u32 max, u32 *size) { (void)path; (void)max; *size = 0; return NULL; }
void scene_stamp(char *out, u32 cap) { strcpy_s(out, cap, "STAMP"); }
int scene_make_dir(const char *dir) { _mkdir(dir); return _access(dir, 0) == 0; }
void scene_exe_path(char *out, u32 cap) { strcpy_s(out, cap, "EXE"); }
const char *scene_full_path(const char *path, char *out, u32 cap) { strcpy_s(out, cap, path); return out; }
const char *scene_command_line(void) { return "cmd"; }
const char *scene_env(const char *name, char *out, u32 cap) { (void)name; (void)out; (void)cap; return NULL; }
char *const *host_args(int *argc) { static char *a[1] = {"exe"}; *argc = 1; return a; }
const char *host_arg(const char *name) { (void)name; return NULL; }
const char *host_window_state(void) { return "focused"; }
const char *host_data_folder(void) { return "."; }
int input_device_count(void) { return 0; }
const char *input_device_desc(int i) { (void)i; return ""; }
u32 session_reads(void) { return 0; }
int session_replaying(void) { return 0; }
int session_write(const char *path, const char *header) { (void)path; (void)header; return 1; }
void console_view(PortView *v) { memset(v, 0, sizeof *v); v->out_w = 640; v->out_h = 480; }
void console_replay_args(struct Json *j, int after) { (void)j; (void)after; }
void console_scene_extra(struct Json *j) { (void)j; }
void console_watch(u32 word, u32 tick) { (void)word; (void)tick; }
int console_poke(u32 addr, u8 value) { (void)addr; (void)value; return 0; }
static int g_shots;
int console_capture_bmp(const char *path)
{
    FILE *f = NULL;
    if (fopen_s(&f, path, "wb") || !f) return 0;
    fputs("BM", f);
    fclose(f);
    g_shots++;
    return 1;
}
static char *read_all(const char *path)
{
    static char buf[1 << 16];
    FILE *f = NULL;
    size_t n;
    if (fopen_s(&f, path, "rb") || !f) return NULL;
    n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    return buf;
}
static int there(const char *path) { return _access(path, 0) == 0; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
'''

DEFAULT = STUBS + r'''
const PortConsole port_console = {"box", "Box", "Game", "v1", 320u, 240u, 60u, 2u};
int main(void)
{
    const char *want = "\"EXE\" --replay \"r.ptrec\" --fixed-clock 1 --present tick --headless --size 640x480 --record off "
                       "--seconds 80 --stop-at 3000 --log \"rd\\port.log\" --dump-dir \"rd\" --dump-frames 5";
    scene_dump_option("--scene-dir", "sdc_default"); /* each test its own folder: they share the build folder */
    char *cmd = scene_replay_command("r.ptrec", "rd", 5, 3000);
    CHECK(cmd && !strcmp(cmd, want));
    free(cmd);
    g_vb = 10;
    scene_dump_request();
    scene_dump_poll(9);
    const char *j = read_all(".\\sdc_default\\STAMP.json");
    CHECK(j && strstr(j, "That replays this session from boot") && strstr(j, "--fixed-clock 1"));
    CHECK(!strstr(j, "frame_capture") && !strstr(j, "cannot reach this frame"));
    CHECK(!there(".\\sdc_default\\STAMP_frame") && !g_fcap_on && g_shots == 1);
    fcap_arm(".\\sdc_default"); /* a kit calling it directly: still nothing */
    CHECK(!g_fcap_on);
    fcap_rec("\"k\":\"draw\"");
    CHECK(fcap_next() == 0);
    g_vb = 11;
    scene_dump_poll(10);
    CHECK(g_shots == 1 && !there(".\\sdc_default\\summary.json"));
    puts("ok");
    return 0;
}
'''

FCAP = STUBS + r'''
const PortConsole port_console = {"box", "Box", "Game", "v1", 320u, 240u, 60u, PORT_CONSOLE_FCAP, 2u, "its threads are the host's"};
int main(void)
{
    const char *want = "\"EXE\" --replay \"r.ptrec\" --present tick --headless --size 640x480 --record off "
                       "--seconds 1560 --stop-at 3000 --log \"rd\\port.log\" --dump-dir \"rd\" --dump-frames 5";
    scene_dump_option("--scene-dir", "sdc_fcap");
    char *cmd = scene_replay_command("r.ptrec", "rd", 5, 3000);
    CHECK(cmd && !strcmp(cmd, want));
    free(cmd);
    g_vb = 10;
    scene_dump_request();
    scene_dump_poll(9);
    const char *j = read_all(".\\sdc_fcap\\STAMP.json");
    CHECK(j && strstr(j, "cannot reach this frame yet for Game: its threads are the host's"));
    CHECK(!strstr(j, "That replays this session from boot") && strstr(j, "\"frame_capture\": \"sdc_fcap\\STAMP_frame\""));
    CHECK(there(".\\sdc_fcap\\STAMP_frame") && g_fcap_on);
    fcap_rec("\"k\":\"draw\",\"prim\":%u", 4u);
    fcap_rec("\"k\":\"present\"");
    g_vb = 11;
    scene_dump_poll(10);
    CHECK(!g_fcap_on && g_shots == 2);
    const char *r = read_all(".\\sdc_fcap\\STAMP_frame\\frame.jsonl");
    CHECK(r && !strcmp(r, "{\"n\":0,\"k\":\"draw\",\"prim\":4}\n{\"n\":1,\"k\":\"present\"}\n"));
    const char *s = read_all(".\\sdc_fcap\\STAMP_frame\\summary.json");
    CHECK(s && strstr(s, "\"frame\": 10") && strstr(s, "\"records\": 2") && strstr(s, "\"final\": \"final.bmp\""));
    CHECK(there(".\\sdc_fcap\\STAMP_frame\\final.bmp"));
    puts("ok");
    return 0;
}
'''

FILES = [KIT / "base" / "scene_dump.c", KIT / "base" / "frame_capture.c"]


class SceneDumpConsole(BuildRun, unittest.TestCase):
    def test_a_console_without_the_members_is_unchanged(self):
        self.build_run("sdc_default", DEFAULT, FILES, "c")

    def test_replay_fps_no_replay_and_the_frame_capture(self):
        self.build_run("sdc_fcap", FCAP, FILES, "c")


if __name__ == "__main__":
    unittest.main()
