"""The port's half of the lockstep check (port/runtime/lockstep.c) in behaviour: the runtime's lockstep files built
with a driver (DRIVER_C, STUBS_C) that maps guest RAM where the port does and runs a script of guest writes,
vertical blanks and timer reads. Counter mode (spec flag counter): a synthetic game loop that bumps its frame counter
and never reads the timer ends one frame per counter change, traced as the vertical blank before it left the state,
and pairs by key when driven. Timer mode: a fixed scenario traces and replays exactly what the timer-label path did
before counter mode existed (TIMER_GOLDEN, from lockstep.c at 2492861).
Only where Visual Studio's 32-bit tools are found: config.local.json "vcvars" (a git worktree has none) or $VCVARS32
names them. Without them these tests and the kit's compile tests skip, and NO_VC says so on stderr: a skip is not
a pass.
Run: python -W error tests/test_port_kit.py (it imports these)
"""
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))

import game_config  # noqa: E402
import lockstep  # noqa: E402

RUNTIME = ("lockstep.c", "lockstep_spec.c", "lockstep_emu.c")
BASE = 0x8c100000  # the synthetic game's state, in guest RAM


# The driver and the runtime's other parts it stands in for (C sources live in targets/ and port/ only, so here)
DRIVER_C = r"""/* The lockstep check's behaviour driver: the runtime's lockstep files over guest RAM where
 * the port maps it, run from a script, one step per line: "w ADDR VALUE" a guest write, "v" a vertical blank,
 * "s" a scene's begin, "t LIVE" an SDK timer read (prints what the game gets), "p" a pad read (prints it). The
 * arguments after the script are the port's --lockstep options. */
#include <stdio.h>
#include <string.h>
#include "port.h"
#include "lockstep.h"

void *guest_alloc(void); /* stubs.c */

int main(int argc, char **argv)
{
    FILE *f = NULL;
    char line[256];
    if (argc < 2 || !guest_alloc()) return 3;
    for (int i = 2; i + 1 < argc; i += 2)
        if (!lockstep_option(argv[i], argv[i + 1])) return 2;
    if (fopen_s(&f, argv[1], "r") || !f) return 2;
    while (fgets(line, sizeof line, f)) {
        unsigned a = 0, v = 0;
        if (line[0] == 'w' && sscanf_s(line + 1, "%x %x", &a, &v) == 2) {
            *(volatile u32 *)(uintptr_t)a = v;
        } else if (line[0] == 'v') {
            lockstep_vblank();
        } else if (line[0] == 's') {
            lockstep_scene();
        } else if (line[0] == 't' && sscanf_s(line + 1, "%x", &v) == 1) {
            printf("timer %x %x\n", v, (u32)lockstep_timer((s32)v));
        } else if (line[0] == 'p') {
            HostPad pad;
            memset(&pad, 0, sizeof pad);
            int given = lockstep_pad(&pad);
            printf("pad %d %x\n", given, pad.buttons);
        }
    }
    fclose(f);
    lockstep_close();
    return 0;
}
"""
STUBS_C = r"""/* What the lockstep files take from the rest of the runtime, for the behaviour driver: logs to stdout,
 * guest RAM at its port address, and no RAM capture, hardware check or write watch. */
#include <stdarg.h>
#include <stdio.h>
#include <windows.h>

void *guest_alloc(void)
{
    return VirtualAlloc((void *)(UINT_PTR)0x8c000000u, 0x01000000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
}

void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("log: ", stdout);
    vprintf(fmt, ap);
    va_end(ap);
}

void lockstep_close(void);

void port_exit(const char *why, int code)
{
    port_log("EXIT: %s\n", why);
    lockstep_close();
    fflush(stdout);
    ExitProcess((UINT)code);
}

int port_extra_drawing(void) { return 0; }
static volatile unsigned g_rand_word = 0x12345678u;
unsigned port_rand_state(void) { return g_rand_word; }
volatile unsigned *port_rand_word(void) { return &g_rand_word; }
int ram_frames_option(const char *arg, const char *value) { return arg && value && 0; }
void ram_frames_capture(unsigned frame, unsigned label, int emu) { (void)frame, (void)label, (void)emu; }
void ram_frames_close(void) {}
int hw_check_option(const char *arg, const char *value) { return arg && value && 0; }
void hw_check_begin(void) {}
void hw_check_close(void) {}
void lockstep_watch(volatile unsigned *at, unsigned frame) /* says which word it was armed on */
{
    if (at) port_log("watch frame %u on %s\n", frame, at == &g_rand_word ? "rand" : "guest");
}
void lockstep_entry(const unsigned *fn, const unsigned *guest, unsigned count, unsigned words, unsigned off, unsigned n,
                    volatile unsigned *frames)
{
    (void)fn, (void)guest, (void)words, (void)off, (void)n, (void)frames;
    printf("entry armed %u\n", count);
}
void lockstep_entry_frame(unsigned line, unsigned label) { printf("entry frame %u %x\n", line, label); }
static void fixture_fn(void) {}
static const struct { unsigned addr; void (*fn)(void); const char *name; } g_fixture_fn = {0x8c0e0000u, fixture_fn,
                                                                                         "Fx_Entry"};
const void *port_dispatch_find(unsigned addr) /* one game function, for --lockstep-entry */
{
    return addr == g_fixture_fn.addr ? &g_fixture_fn : NULL;
}
"""


def build(out, runtime=KIT / "runtime"):
    """lockstep.exe in out from the driver, the stubs and runtime's lockstep files; the build log on a failure."""
    (out / "port_config.h").write_text(game_config.port_config_h(
        {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
         "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
    (out / "driver.c").write_text(DRIVER_C, encoding="utf-8")
    (out / "stubs.c").write_text(STUBS_C, encoding="utf-8")
    srcs = [out / "driver.c", out / "stubs.c"] + [runtime / n for n in RUNTIME if (runtime / n).is_file()]
    rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
    cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{out}" /I"{runtime}" /I"{KIT / "runtime"}" /I"{rd}" '
          + " ".join(f'"{p}"' for p in srcs) + f' /Fe"{out / "lockstep.exe"}" /link /LARGEADDRESSAWARE '
          '/DYNAMICBASE:NO /BASE:0x5a000000')
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=out, capture_output=True, text=True,
                       errors="replace", timeout=300)
    return r.stdout[-3000:] + r.stderr[-1000:] if r.returncode or "warning" in r.stdout.lower() else ""


def run(out, spec, script, emu=None, extra=()):
    """(stdout lines, trace lines) of the driver on spec and script (and the emulator's frames, if driven)."""
    (out / "spec.txt").write_text(spec, encoding="utf-8")
    (out / "script.txt").write_text(script, encoding="utf-8")
    args = [str(out / "lockstep.exe"), str(out / "script.txt"), "--lockstep-spec", str(out / "spec.txt"),
            "--lockstep-trace", str(out / "trace.txt")] + list(extra)
    if emu is not None:
        (out / "emu.txt").write_text(emu, encoding="utf-8")
        args += ["--lockstep", str(out / "emu.txt")]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise AssertionError(f"driver rc {r.returncode}: {r.stdout[-2000:]}")
    return ([x.replace(str(out), "OUT") for x in r.stdout.splitlines()],
            (out / "trace.txt").read_text(encoding="utf-8").splitlines())


def w(off, value):
    return f"w {BASE + off:08x} {value:x}\n"


COUNTER_SPEC = (f"label 1 {BASE:08x} counter\nscene 1 {BASE + 4:08x} key\nstate 2 {BASE + 8:08x}\n"
                f"rng 1 @rand\n")
# (scene, vertical blanks) per game frame: two blanks in one frame, and a wait of three, end no frame between them
COUNTER_FRAMES = [(1, 1), (1, 1), (1, 2), (3, 1), (3, 3), (3, 1), (3, 1)]


def counter_script():
    """The synthetic game: each frame writes its state and scene, bumps the counter and waits its blanks; no timer
    read anywhere. The pad is read once per frame. Returns (script, the state each frame left)."""
    s, left = "v\n", []
    for k, (scene, blanks) in enumerate(COUNTER_FRAMES, 1):
        s += "p\n" + w(8, 0x100 + k) + w(12, 0x200 + k) + w(4, scene) + w(0, k)
        s += "v\n" * blanks
        left.append((k, scene, 0x100 + k, 0x200 + k))
    return s + "v\n", left


class LockstepCounter(unittest.TestCase):
    out = None

    @classmethod
    def setUpClass(cls):
        cls.out = pathlib.Path(tempfile.mkdtemp())
        log = build(cls.out)
        if log:
            raise AssertionError(log)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_traced_frames(self):
        """One trace line per counter change, labelled with the counter it ended and holding the state of the blank
        before the change; the timer is never read, and the spec load says the frames end at the counter."""
        script, left = counter_script()
        log, trace = run(self.out, COUNTER_SPEC, script)
        self.assertIn("log: lockstep: 4 watched items, 1 key words, frames end where the counter label changes", log)
        self.assertFalse([x for x in log if x.startswith("timer")])
        want = [f"{0:08x} -1 {0:08x} {0:08x} {0:08x} {0:08x} {0x12345678:08x}"]
        want += [f"{k:08x} -1 {k:08x} {sc:08x} {a:08x} {b:08x} {0x12345678:08x}" for k, sc, a, b in left[:-1]]
        self.assertEqual(trace, want)

    def test_driven_pairs_by_key(self):
        """Driven: the pads come from the paired emulator frame, one to one, and a scene the port skips (the
        emulator's scene 2, a movie) pairs the port's next scene with the emulator frame that starts it."""
        keys = [1, 1, 1, 2, 2, 3, 3, 3, 3, 3]
        emu = "".join(f"{i:x} {0x1000 + i:x} 0 0 0 0 0 0 {k:x}\n" for i, k in enumerate(keys))
        script, _ = counter_script()
        log, trace = run(self.out, COUNTER_SPEC, script, emu)
        pads = [x.split()[2] for x in log if x.startswith("pad")]
        self.assertEqual(pads, [f"{0x1000 + i:x}" for i in (0, 1, 2, 3, 4, 6, 7)])
        self.assertEqual([x.split()[1] for x in trace], ["0", "1", "2", "3", "5", "6", "7"])
        self.assertIn("log: lockstep: port frame 5 pairs with emulator frame 5 (key)", log)

    def test_gated_item(self):
        """Flag if=scene:3: state is read only in the frames whose scene word is 3 (a level's tables, which a movie's
        memory reuses), else "-", as a word outside guest RAM; the other items are read as before."""
        script, left = counter_script()
        spec = COUNTER_SPEC.replace(f"state 2 {BASE + 8:08x}", f"state 2 {BASE + 8:08x} if=scene:3")
        _, trace = run(self.out, spec, script)
        want = [f"{0:08x} -1 {0:08x} {0:08x} - - {0x12345678:08x}"]
        want += [f"{k:08x} -1 {k:08x} {sc:08x} " + (f"{a:08x} {b:08x}" if sc == 3 else "- -") + f" {0x12345678:08x}"
                 for k, sc, a, b in left[:-1]]
        self.assertEqual(trace, want)
        _, trace = run(self.out, spec.replace("if=scene:3", "if=scene[0]:1"), script)
        self.assertEqual([x.split()[4] for x in trace], ["-", f"{0x101:08x}", f"{0x102:08x}", f"{0x103:08x}", "-",
                                                         "-", "-"])

    def test_a_gate_on_no_earlier_item_is_an_error(self):
        """if= naming no item before it, a word past its end, or a value that is not hex: a spec error, on the port
        and in the compare tool alike."""
        for flag in ("if=later:3", "if=scene[1]:3", "if=scene:x3", "if=scene"):
            spec = (f"label 1 {BASE:08x} counter\nscene 1 {BASE + 4:08x} key\nstate 2 {BASE + 8:08x} {flag}\n"
                    f"later 1 {BASE + 16:08x}\n")
            (self.out / "spec.txt").write_text(spec, encoding="utf-8")
            (self.out / "script.txt").write_text("v\n", encoding="utf-8")
            r = subprocess.run([str(self.out / "lockstep.exe"), str(self.out / "script.txt"), "--lockstep-spec",
                                str(self.out / "spec.txt")], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, flag)
            with self.assertRaises(SystemExit, msg=flag):
                lockstep.parse_spec(self.out / "spec.txt")
        (self.out / "spec.txt").write_text(COUNTER_SPEC.replace(f"{BASE + 8:08x}", f"{BASE + 8:08x} if=scene:3"),
                                           encoding="utf-8")
        self.assertEqual(lockstep.parse_spec(self.out / "spec.txt")[2]["flags"], {"if=scene:3"})

    def test_rand_with_counter_is_an_error(self):
        """The counter flag on @rand (no guest word to watch) is a spec error."""
        (self.out / "spec.txt").write_text("rng 1 @rand counter\n", encoding="utf-8")
        (self.out / "script.txt").write_text("v\n", encoding="utf-8")
        r = subprocess.run([str(self.out / "lockstep.exe"), str(self.out / "script.txt"), "--lockstep-spec",
                            str(self.out / "spec.txt")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)

    def test_watch_on_rand(self):
        """--lockstep-watch on the @rand word arms the write watch on rand's own state (host memory), as on a guest
        word; trace word 4 is rng, 2 is state[0]."""
        script, _ = counter_script()
        for word, on in ((4, "rand"), (2, "guest")):
            log, _ = run(self.out, COUNTER_SPEC, script, extra=("--lockstep-watch", f"{word}@2"))
            self.assertEqual([x for x in log if x.startswith("log: watch")], [f"log: watch frame 2 on {on}"])


WATCH_C = r"""/* lockstep_watch.c's debug registers on a host word (where rand keeps its state): two writes are logged,
 * each with the function that made it on the stack, and none after the watch stops. */
#include <stdarg.h>
#include <stdio.h>
#include "port.h"
#include "lockstep_parts.h"

static volatile u32 g_word;

void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}

__declspec(noinline) void step_word(u32 v) { g_word = g_word * 3u + v; }

int main(void)
{
    printf("step_word %08x\n", (u32)(uintptr_t)step_word);
    lockstep_watch(&g_word, 7);
    step_word(5);
    step_word(6);
    lockstep_watch(NULL, 7);
    step_word(9);
    return g_word == 72u ? 0 : 1;
}
"""


class LockstepWatchHost(unittest.TestCase):
    def test_host_word_writes_logged(self):
        """The watch logs each write to a host word with the instruction after it inside the writer."""
        out = pathlib.Path(tempfile.mkdtemp())
        try:
            (out / "watch.c").write_text(WATCH_C, encoding="utf-8")
            build(out)  # port_config.h
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{out}" /I"{KIT / 'runtime'}" /I"{rd}" "{out / 'watch.c'}" '
                  f'"{KIT / "runtime" / "lockstep_watch.c"}" /Fe"{out / "watch.exe"}"')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=out, capture_output=True,
                               text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            r = subprocess.run([str(out / "watch.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout)
            fn = int(r.stdout.split()[1], 16)
            hits = [x.split() for x in r.stdout.splitlines() if " write " in x]
            self.assertEqual([h[5] for h in hits], ["00000005", "00000015"])
            self.assertTrue(all(0 < int(h[7], 16) - fn < 0x40 for h in hits), hits)
        finally:
            shutil.rmtree(out, ignore_errors=True)


ENTRY_C = r"""/* lockstep_watch.c's execute breakpoints (--lockstep-entry) on a host function: each call logs its argument's
 * words and the words at a pointer inside it, and no call after the entry stops. */
#include <stdarg.h>
#include <stdio.h>
#include "port.h"
#include "lockstep_parts.h"

static volatile u32 g_sum;
static u32 g_tail[2] = {0x77u, 0x88u};
static u32 g_arg[3] = {0x11u, 0x22u, 0u};
static volatile u32 g_frames = 5u;

void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}

__declspec(noinline) void take(u32 *arg) { g_sum += arg[0]; }

int main(void)
{
    const u32 fn = (u32)(uintptr_t)take, guest = 0x8c012340u;
    g_arg[2] = (u32)(uintptr_t)g_tail;
    lockstep_entry(&fn, &guest, 1, 2, 8, 2, &g_frames);
    lockstep_entry_frame(41, 0xabcdu);
    take(g_arg);
    g_arg[0] = 0x33u;
    take(g_arg);
    lockstep_entry(NULL, NULL, 0, 0, 0, 0, NULL);
    take(g_arg);
    return g_sum == 0x77u ? 0 : 1;
}
"""


class LockstepEntryHost(unittest.TestCase):
    def test_calls_logged(self):
        """Each call of the armed function logs line, guest address, label, frames, the argument's words and the
        words at the pointer inside it; the call after the entry stops is not logged."""
        out = pathlib.Path(tempfile.mkdtemp())
        try:
            (out / "entry.c").write_text(ENTRY_C, encoding="utf-8")
            build(out)  # port_config.h
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{out}" /I"{KIT / 'runtime'}" /I"{rd}" "{out / 'entry.c'}" '
                  f'"{KIT / "runtime" / "lockstep_watch.c"}" /Fe"{out / "entry.exe"}"')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=out, capture_output=True,
                               text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            r = subprocess.run([str(out / "entry.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout)
            calls = [x.split() for x in r.stdout.splitlines() if x.startswith("lockstep entry: line")]
            self.assertEqual([c[3:10] + c[12:] for c in calls],
                             [["41", "fn", "8c012340", "label", "0000abcd", "frames", "5", "00000011", "00000022",
                               "|", "00000077", "00000088"],
                              ["41", "fn", "8c012340", "label", "0000abcd", "frames", "5", "00000033", "00000022",
                               "|", "00000077", "00000088"]])
            self.assertIn("lockstep entry: 2 calls logged", r.stdout)
        finally:
            shutil.rmtree(out, ignore_errors=True)


TIMER_SPEC = (f"clock 1 {BASE:08x} frames\npaused 1 {BASE + 4:08x} paused\nscene 1 {BASE + 8:08x} key\n"
              f"state 2 {BASE + 12:08x}\nheld 1 {BASE + 0x20:08x}*+4\n")
TIMER_EMU = "".join(f"{0x10000 + 0x32dc * i:x} {0x2000 + i:x} 0 0 0 0 {0x40000000 + i:x} {j:x} {k:x}\n"
                    for i, (k, j) in enumerate([(1, 0), (1, 0), (1, 0), (2, 0), (2, 0), (3, 0), (3, 0),
                                                (3, 0x30000), (3, 0), (3, 0), (3, 0)]))


def timer_script():
    """A game that reads the SDK timer as each frame starts: frames counted by its clock, a read within a frame,
    a skipped scene (the emulator's 2), a load (the clock paused around a timer jump) and a pointer item."""
    s = w(0x20, BASE + 0x40) + w(0x44, 0x77) + w(8, 1)
    clock = 0
    for k in range(1, 10):
        s += f"t {0x5000 + 0x3000 * k:x}\n"
        if k in (3, 6):
            s += f"t {0x5100 + 0x3000 * k:x}\n"  # a read within the frame
        if k == 4:
            s += w(8, 3)  # the port skips scene 2
        if k == 6:
            s += w(4, 1) + f"t {0x5200 + 0x3000 * k:x}\n" + w(4, 0)  # a load: paused around a read
        clock += 1
        s += "p\n" + w(0, clock) + w(12, 0x300 + k) + w(16, 0x400 + k) + w(0x44, 0x70 + k) + "s\nv\n"
    return s + f"t {0x5000 + 0x3000 * 10:x}\n"


TIMER_GOLDEN = (  # from lockstep.c at 2492861, before counter mode
    ["log: lockstep: 5 watched items, 1 key words", "log: lockstep: driving from OUT\\emu.txt: 11 emulator frames",
     "timer 8000 10000", "pad 1 2000", "timer b000 132dc", "pad 1 2001", "timer e000 165b8", "timer e100 165b9",
     "pad 1 2002", "timer 11000 19894", "pad 1 2003", "log: lockstep: port frame 5 pairs with emulator frame 5 (key)",
     "timer 14000 1cf80", "pad 1 2006", "timer 17000 2025c", "timer 17100 2025d", "timer 17200 29e58", "pad 1 2007",
     "timer 1a000 29e59", "pad 1 2008", "timer 1d000 29e5a", "pad 1 2009", "timer 20000 29e5b", "pad 1 200a",
     "log: lockstep: the emulator frames end at port frame 10; live time from here", "timer 23000 2d137"],
    ["00010000 0 00000001 00000000 00000001 00000301 00000401 00000071",
     "000132dc 1 00000002 00000000 00000001 00000302 00000402 00000072",
     "000165b9 2 00000003 00000000 00000001 00000303 00000403 00000073",
     "00019894 5 00000004 00000000 00000003 00000304 00000404 00000074",
     "0001cf80 6 00000005 00000000 00000003 00000305 00000405 00000075",
     "00029e58 7 00000006 00000000 00000003 00000306 00000406 00000076",
     "00029e59 8 00000007 00000000 00000003 00000307 00000407 00000077",
     "00029e5a 9 00000008 00000000 00000003 00000308 00000408 00000078",
     "00029e5b 10 00000009 00000000 00000003 00000309 00000409 00000079"])


class LockstepTimer(unittest.TestCase):
    def test_timer_label_path_unchanged(self):
        """Timer mode (no counter flag) replays and traces the scenario as lockstep.c did before counter mode."""
        out = pathlib.Path(tempfile.mkdtemp())
        try:
            log = build(out)
            self.assertEqual(log, "")
            got = run(out, TIMER_SPEC, timer_script(), TIMER_EMU)
            self.assertEqual(got, TIMER_GOLDEN)
            # --lockstep-end exit (tools/lockstep.py): the same trace, and the run ends where live time would start
            log, trace = run(out, TIMER_SPEC, timer_script(), TIMER_EMU, ["--lockstep-end", "exit"])
            self.assertEqual(log, TIMER_GOLDEN[0][:-2] + ["log: lockstep: the emulator frames end at port frame 10",
                                                          "log: EXIT: the lockstep drive ended"])
            self.assertEqual(trace, TIMER_GOLDEN[1])
        finally:
            shutil.rmtree(out, ignore_errors=True)


class LockstepEntryLabel(unittest.TestCase):
    def test_entry_calls_carry_the_label_their_frame_reads(self):
        """--lockstep-entry: the calls in a frame are logged under the label that frame's first timer read returned,
        as the console's LS_ENTRY reads the game's copy of it (until tc-ls2 the port gave the label of the frame
        before: TC's intro movie looked one frame in step with the console when it was one frame behind)."""
        out = pathlib.Path(tempfile.mkdtemp())
        try:
            self.assertEqual(build(out), "")
            log, _ = run(out, TIMER_SPEC, timer_script(), TIMER_EMU, ["--lockstep-entry", "8c0e0000@1-3:1:0:0"])
            frames = [(i, x.split()) for i, x in enumerate(log) if x.startswith("entry frame")]
            self.assertEqual([f[2] for _, f in frames], ["0", "1", "2", "3"])
            for i, f in frames:  # the read that starts the frame (the driver prints it once the read returns)
                read = next(x for x in log[i:] if x.startswith("timer"))
                self.assertEqual(f[3], read.split()[2], log)
            self.assertEqual([f[3] for _, f in frames], ["10000", "132dc", "165b8", "19894"])
            self.assertIn("entry armed 0", log)  # off after line 3
        finally:
            shutil.rmtree(out, ignore_errors=True)


class LockstepCompare(unittest.TestCase):
    """tools/lockstep.py compare and report on synthetic frames: a sample whose frames word is the draw's or one more
    (taken at a lockstep_sample_pcs PC, after the frame counted itself) is compared; one further off is set apart,
    and a check that sets apart more than SET_APART_MAX of its frames fails loudly."""
    ITEMS = [{"name": "label", "words": 1, "addr": "0", "flags": {"label", "time"}},
             {"name": "frames", "words": 1, "addr": "4", "flags": {"frames"}},
             {"name": "x", "words": 1, "addr": "8", "flags": set()}]

    def check(self, ahead, port_x=lambda i: i, traced=120):
        n = 120
        emu = [(100 + i, 2 * i + 2, "%08x %08x %08x" % (100 + i, i + ahead, i)) for i in range(n)]
        frames = [{"label": 100 + i, "jump": None, "draw": (100 + i, i, 0, 0, 0, 0, 0)} for i in range(n)]
        port = ((100 + i, i, "%08x %08x %08x" % (100 + i, i + ahead, port_x(i))) for i in range(traced))
        res = lockstep.compare(self.ITEMS, emu, frames, port)
        self.assertEqual(res["traced"], traced)  # read to the end past the first divergence
        with contextlib.redirect_stdout(io.StringIO()) as text:
            rc = lockstep.report(res, emu)
        return res, rc, text.getvalue()

    def test_sample_after_the_frame_counted_itself(self):
        res, rc, _ = self.check(1, lambda i: i + 1000 * (i >= 60))
        self.assertEqual((res["first"][0], res["loads"], rc), (60, 1, 1))  # loads: the first sample (the boot)

    def test_set_apart_fails_loudly(self):
        res, rc, text = self.check(2)
        self.assertEqual((res["first"], res["loads"], rc), (None, 120, 2))
        self.assertIn("CHECK BLIND: 120 of 120 compared frames set apart", text)
        res, rc, text = self.check(0)  # the draw's own word: compared, no divergence
        self.assertEqual((res["loads"], rc), (1, 0))
        self.assertNotIn("CHECK BLIND", text)

    def test_a_port_that_stops_short_fails(self):
        """A port that exits before the drive's end (a crash at frame 14, TC 2026-10-27) is no verdict, not a pass."""
        res, rc, text = self.check(0, traced=14)
        self.assertEqual((res["first"], res["last"], res["driven"], rc), (None, 13, 120, 3))
        self.assertIn("PORT STOPPED SHORT: its trace ends at game frame 13 of the drive's 120", text)
        self.assertIn("no divergence in the frames traced", text)
        res, rc, text = self.check(0, traced=0)
        self.assertEqual((res["last"], rc), (None, 3))
        res, rc, text = self.check(0, lambda i: i + 1000 * (i >= 5), traced=14)  # a divergence first: still 1
        self.assertEqual((res["first"][0], rc), (5, 1))
        self.assertIn("PORT STOPPED SHORT", text)


class LockstepDrive(unittest.TestCase):
    """drive_file: a loading frame (build_frames' jump) drives the port with the sample it ends with, its jump's, as
    compare pairs it: there the key is already the next phase's, so the port re-pairs on the frame itself."""
    ITEMS = [{"name": "label", "words": 1, "addr": "0", "flags": {"label", "time"}},
             {"name": "scene", "words": 1, "addr": "4", "flags": {"key"}},
             {"name": "pad", "words": 8, "addr": "8", "flags": {"emu", "pad"}},
             {"name": "rtc", "words": 2, "addr": "a0710000", "flags": {"emu", "io", "rtc"}}]

    def test_loading_frame_takes_its_jump_sample(self):
        emu = [(lab, vb, " ".join(["%08x" % lab, "%08x" % key] + ["00000000"] * 9 + ["%08x" % rtc]))
               for lab, vb, key, rtc in ((100, 1, 1, 7), (200, 2, 1, 8), (300, 60, 2, 9), (400, 60, 2, 9))]
        frames = [{"label": 100, "jump": None, "draw": None}, {"label": 200, "jump": 300, "draw": None},
                  {"label": 400, "jump": None, "draw": None}]
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "drive.txt"
            lockstep.drive_file(self.ITEMS, emu, frames, out)
            rows = [line.split() for line in out.read_text().splitlines()]
        self.assertEqual([(r[0], r[6], r[7], r[8]) for r in rows],
                         [("00000064", "7", "0", "00000001"), ("000000c8", "9", "12c", "00000002"),
                          ("00000190", "9", "0", "00000002")])


class LockstepScenes(unittest.TestCase):
    """build_frames over steps with each frame's scene counts frames as the port does (lockstep.c frame_read): a frame
    that counted itself and began no scene runs on into the next (its label), as does a load's (its jump); a counter
    reset ends a frame; and a one-word difference planted in such a frame is still reported."""
    ITEMS = LockstepCompare.ITEMS

    def test_frames_as_the_port_counts_them(self):
        steps = [(100, 1, 1), (200, 2, 1), (300, 3, 0), (310, 4, 1), (400, 5, 1), (600, 6, 1), (700, 0, 0),
                 (800, 1, 1)]
        steps = [(lab, n, 0, 0, 0, 0, 0, scene) for lab, n, scene in steps]
        labels = [100, 200, 300, 310, 400, 500, 600, 700, 800]
        count = {d[0]: d[1] for d in steps}
        emu = [(lab, i + 1, "%08x %08x %08x" % (lab, count.get(lab, 0), i)) for i, lab in enumerate(labels)]
        frames = lockstep.build_frames(emu, steps)
        self.assertEqual([(f["label"], f["jump"]) for f in frames],
                         [(100, None), (200, None), (310, None), (400, None), (500, 600), (700, None), (800, None)])
        self.assertEqual(lockstep.sample_of(emu, frames), {0: 0, 1: 1, 2: 3, 3: 4, 4: 6, 5: 7, 6: 8})
        at = lockstep.sample_of(emu, frames)
        port = lambda plant: ((f["label"], gi, emu[at[gi]][2][:-8] + "%08x" % (plant if gi == 2 else at[gi]))
                              for gi, f in enumerate(frames))
        self.assertIsNone(lockstep.compare(self.ITEMS, emu, frames, port(3))["first"])
        planted = lockstep.compare(self.ITEMS, emu, frames, port(33))
        self.assertEqual((planted["first"][:3], planted["diffs"]), ((2, 310, 3), ["x[0] port 00000021 emu 00000003"]))

    def test_a_run_on_frame_into_a_load(self):  # TC mission1 on a VGA cable: the counter reset a step earlier
        steps = [(lab, n, 0, 0, 0, 0, 0, scene) for lab, n, scene in ((100, 1, 1), (200, 2, 0), (400, 3, 1))]
        emu = [(lab, i + 1, "%08x" % lab) for i, lab in enumerate((100, 200, 300, 400))]
        frames = lockstep.build_frames(emu, steps)  # 300: the vertical blank within the load
        self.assertEqual([(f["label"], f["jump"]) for f in frames], [(100, None), (300, 400)])

    # Toy Commander's intro, as Flycast recorded it: (label, frame counter, scene[, unsampled]) per step; the load's
    # label (a movie opening) reaches no sample PC, so before tc-ls2 its scene was not recorded.
    M2_VGA = [(0x008f1dd3, 1, 1), (0x008f82c5, 2, 0), (0x008fe7ba, 2, 1, 1), (0x0094fba5, 3, 1), (0x00954088, 4, 1)]
    M2_TV = [(0x008f04eb, 0x1f, 1), (0x008f69db, 1, 0), (0x008fced1, 1, 1, 1), (0x0094e2b5, 2, 1), (0x0095279c, 3, 1)]

    def frames_of(self, rows, labels):
        steps = [(r[0], r[1], 0, 0, 0, 0, 0) + tuple(r[2:]) for r in rows]
        emu = [(lab, i + 1, "%08x" % lab) for i, lab in enumerate(labels)]
        return [(f["label"], f["jump"]) for f in lockstep.build_frames(emu, steps)]

    def test_a_load_that_begins_a_scene_after_a_run_on(self):
        """VGA, movie 1 -> 2: the draw-less frame 8f82c5 counted itself (a run-on), then the load 8fe7ba opened movie 2
        and began its first scene: the port's next timer read sees the counter one on with a scene, so its frame ends
        there (frame_read), and the movie's first frame 94fba5 is a frame of its own. Without the load's scene the
        drive made 8fe7ba a load ending at 94fba5 (jump), and the port ran one frame behind from there: an extra
        movie frame at the intro's end (all 10 ls_vga stopped at port frame 2312)."""
        labels = [0x008f1dd3, 0x008f82c5, 0x008fe7ba, 0x0094fba5, 0x00954088]
        self.assertEqual(self.frames_of(self.M2_VGA, labels),
                         [(0x008f1dd3, None), (0x008fe7ba, None), (0x0094fba5, None), (0x00954088, None)])
        unrecorded = [r for r in self.M2_VGA if len(r) < 4]  # a recording made before tc-ls2: as it was
        self.assertEqual(self.frames_of(unrecorded, labels),
                         [(0x008f1dd3, None), (0x008fe7ba, 0x0094fba5), (0x00954088, None)])

    def test_the_counter_wrap_keeps_a_load_with_a_scene_within_its_frame(self):
        """TV, the same place: the counter wrapped (1f -> 1) at the draw-less frame 8f69db, so that is a frame of its
        own, and at the load's end the counter has not moved: the load's scene stays within the frame, which starts at
        the load's label and ends at the movie's first frame (jump), as before the scene was recorded."""
        labels = [0x008f04eb, 0x008f69db, 0x008fced1, 0x0094e2b5, 0x0095279c]
        want = [(0x008f04eb, None), (0x008f69db, None), (0x008fced1, 0x0094e2b5), (0x0095279c, None)]
        self.assertEqual(self.frames_of(self.M2_TV, labels), want)
        self.assertEqual(self.frames_of([r for r in self.M2_TV if len(r) < 4], labels), want)

    def test_the_intro_end_run_on_into_a_load_is_unchanged(self):
        """VGA, the intro's end: the run-on 3a23e5a, then a load with no scene of its own (the level's first scene is
        under 3a4f699): no unsampled step, and the frames are as before."""
        rows = [(0x03a1d9e2, 1, 1), (0x03a23e5a, 2, 0), (0x03a4f699, 3, 1), (0x03a504ae, 4, 1)]
        labels = [0x03a1d9e2, 0x03a23e5a, 0x03a2a529, 0x03a4f699, 0x03a504ae]
        self.assertEqual(self.frames_of(rows, labels), [(0x03a1d9e2, None), (0x03a2a529, 0x03a4f699),
                                                         (0x03a504ae, None)])

    def test_a_step_sample_is_never_the_frame_before(self):
        emu = [(100 + i, 2 * i + 2, "%08x %08x %08x" % (100 + i, i, min(i, 59))) for i in range(120)]
        for width, first, drawn in ((7, 61, 1), (8, 60, 0)):  # at 60 the emulator shows the port's frame before
            frames = [{"label": 100 + i, "jump": None, "draw": (100 + i, i, 0, 0, 0, 0, 0, 1)[:width]}
                      for i in range(120)]
            port = ((100 + i, i, "%08x %08x %08x" % (100 + i, i, i)) for i in range(120))
            res = lockstep.compare(self.ITEMS, emu, frames, port)
            self.assertEqual((res["first"][0], res["drawn"]), (first, drawn))


HAVE_VC = os.name == "nt" and bool(game_config.vcvars32())
NO_VC = ('no 32-bit Visual Studio tools found: set VCVARS32 to vcvars32.bat (or config.local.json "vcvars"); the '
         "runtime compile and lockstep behaviour tests are SKIPPED, not passed")
if not HAVE_VC:
    sys.stderr.write(f"*** {NO_VC}\n")
    LockstepCounter = unittest.skip(NO_VC)(LockstepCounter)
    LockstepTimer = unittest.skip(NO_VC)(LockstepTimer)
    LockstepWatchHost = unittest.skip(NO_VC)(LockstepWatchHost)

if __name__ == "__main__":
    unittest.main()
