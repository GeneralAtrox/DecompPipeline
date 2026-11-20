"""The Xbox 360 kit's list of guest threads (runtime/xb_call.c): the watchdog's report (xb_threads_dump) reads each thread's call stack from
that thread's own storage, which Windows frees when the thread ends. A thread that has ended must leave the list before its storage goes, so the
dump says it ended instead of reading freed memory (an access violation in xb_threads_dump ended runs the watchdog stopped, with no report).

No game data: the list is cut from xb_call.c and compiled with stand-in threads; 40 of them end before the dump, one is still running.
Nearest existing: tests/test_x360_replay_race.py - the same cut-and-compile pattern, for the command buffers, not the thread list."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

RT = X360 / "port" / "runtime"

HARNESS = r'''
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int u32;
static void port_log(const char *f, ...) { va_list v; va_start(v, f); vprintf(f, v); va_end(v); }
static __declspec(thread) u32 s_stack[256];
static __declspec(thread) u32 s_stack_arg[256][3];
static __declspec(thread) unsigned s_depth;
/* the Core's sampler (base/perf.h), which tc_call_inner's registration also tells: stand-ins, on, so that line runs */
static int g_perf_on = 1;
#define PERF_ANY (-1)
#define perf_thread_stack(s, d, n) ((void)(s), (void)(d), (void)(n))
#define perf_thread(w) ((void)(w))
CUT
static void reg(void) { REG }
static HANDLE go;
static DWORD WINAPI worker(LPVOID p)
{
    u32 tag = (u32)(UINT_PTR)p;
    s_stack[0] = 0x82000000u | tag, s_stack_arg[0][0] = tag, s_depth = 1;
    reg();
    if (!tag) WaitForSingleObject(go, 60000);
    return 0;
}
int main(void)
{
    HANDLE live, t;
    unsigned long ids[64];
    unsigned i, n, dead = 0;
    go = CreateEventA(NULL, TRUE, FALSE, NULL);
    live = CreateThread(NULL, 0, worker, (LPVOID)0, 0, NULL);
    while (!s_thread_n) Sleep(1);
    for (i = 1; i <= 40; i++) {   /* threads that end before the dump, their storage freed */
        t = CreateThread(NULL, 0, worker, (LPVOID)(UINT_PTR)i, 0, NULL);
        WaitForSingleObject(t, 60000);
        CloseHandle(t);
    }
    for (i = 0; i < 2000; i++) free(memset(malloc(64 + i % 4096), 0xA5, 64 + i % 4096));   /* the freed blocks reused */
    xb_threads_dump();
    n = xb_thread_ids(ids, 64);
    for (i = 0; i < n; i++) dead += !ids[i];
    printf("ids %u dead %u live %d\n", n, dead, ids[0] == GetThreadId(live));
    SetEvent(go);
    WaitForSingleObject(live, 60000);
    printf("done\n");
    return 0;
}
'''


def cut():
    """The thread list from xb_call.c: its declarations, every function up to xb_thread_ids, and the registration in tc_call_inner"""
    src = (RT / "xb_call.c").read_text(encoding="utf-8")
    parts = [re.search(r"^static struct \{ const u32 \*stack;.*$", src, re.M).group(0), "static volatile long s_thread_n;",
             "static __declspec(thread) int s_listed;"]
    for decl in ("static CRITICAL_SECTION s_threads_cs;", "static INIT_ONCE s_threads_once = INIT_ONCE_STATIC_INIT;",
                 "static DWORD s_threads_fls = FLS_OUT_OF_INDEXES;"):
        if decl in src:
            parts.append(decl)
    for sig in ("static void WINAPI thread_gone(", "static BOOL CALLBACK threads_init(", "static void thread_listed(void)",
                "void xb_threads_dump(void)", "unsigned xb_thread_ids("):
        if sig in src:
            start = src.index(sig)
            parts.append(src[start:src.index("\n}\n", start) + 3])
    at = src.index("if (!s_listed)", src.index("static u64 tc_call_inner("))
    rest = src[at + len("if (!s_listed)"):]
    if rest.lstrip().startswith("{"):
        depth, j = 0, rest.index("{")
        for j in range(j, len(rest)):
            depth += {"{": 1, "}": -1}.get(rest[j], 0)
            if not depth:
                break
        reg = "if (!s_listed)" + rest[:j + 1]
    else:
        reg = "if (!s_listed)" + rest[:rest.index(";") + 1]
    return "\n".join(parts), reg


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class ThreadList(unittest.TestCase):
    def test_ended_threads_leave_the_dump(self):
        d = tmpdir("x360tl_")
        try:
            code, reg = cut()
            (d / "t.c").write_text(HARNESS.replace("CUT", code).replace("REG", reg), encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            r = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=120)
            got = r.stdout.splitlines()
            self.assertEqual(r.returncode, 0, f"the dump crashed: exit {r.returncode:#x}\n" + r.stdout[-2000:])
            self.assertEqual(sum("has ended" in g for g in got), 40, r.stdout[-2000:])
            stacks = [g for g in got if "stack (innermost first)" in g]
            self.assertEqual(len(stacks), 1, r.stdout[-2000:])   # only the running thread's stack, with its own values
            self.assertTrue(stacks[0].endswith("stack (innermost first): 82000000; [00000000 00000000 00000000]"), stacks[0])
            self.assertIn("ids 41 dead 40 live 1", got)
            self.assertEqual(got[-1], "done")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
