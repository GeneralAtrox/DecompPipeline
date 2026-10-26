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
unsigned port_rand_state(void) { return 0x12345678u; }
int ram_frames_option(const char *arg, const char *value) { return arg && value && 0; }
void ram_frames_capture(unsigned frame, unsigned label, int emu) { (void)frame, (void)label, (void)emu; }
void ram_frames_close(void) {}
int hw_check_option(const char *arg, const char *value) { return arg && value && 0; }
void hw_check_begin(void) {}
void hw_check_close(void) {}
void lockstep_watch(volatile unsigned *at, unsigned frame) { (void)at, (void)frame; }
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

    def test_rand_with_counter_is_an_error(self):
        """The counter flag on @rand (no guest word to watch) is a spec error."""
        (self.out / "spec.txt").write_text("rng 1 @rand counter\n", encoding="utf-8")
        (self.out / "script.txt").write_text("v\n", encoding="utf-8")
        r = subprocess.run([str(self.out / "lockstep.exe"), str(self.out / "script.txt"), "--lockstep-spec",
                            str(self.out / "spec.txt")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)


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


class LockstepCompare(unittest.TestCase):
    """tools/lockstep.py compare and report on synthetic frames: a sample whose frames word is the draw's or one more
    (taken at a lockstep_sample_pcs PC, after the frame counted itself) is compared; one further off is set apart,
    and a check that sets apart more than SET_APART_MAX of its frames fails loudly."""
    ITEMS = [{"name": "label", "words": 1, "addr": "0", "flags": {"label", "time"}},
             {"name": "frames", "words": 1, "addr": "4", "flags": {"frames"}},
             {"name": "x", "words": 1, "addr": "8", "flags": set()}]

    def check(self, ahead, port_x=lambda i: i):
        n = 120
        emu = [(100 + i, 2 * i + 2, "%08x %08x %08x" % (100 + i, i + ahead, i)) for i in range(n)]
        frames = [{"label": 100 + i, "jump": None, "draw": (100 + i, i, 0, 0, 0, 0, 0)} for i in range(n)]
        port = ((100 + i, i, "%08x %08x %08x" % (100 + i, i + ahead, port_x(i))) for i in range(n))
        res = lockstep.compare(self.ITEMS, emu, frames, port)
        self.assertEqual(res["traced"], n)  # read to the end past the first divergence
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


HAVE_VC = os.name == "nt" and bool(game_config.vcvars32())
NO_VC = ('no 32-bit Visual Studio tools found: set VCVARS32 to vcvars32.bat (or config.local.json "vcvars"); the '
         "runtime compile and lockstep behaviour tests are SKIPPED, not passed")
if not HAVE_VC:
    sys.stderr.write(f"*** {NO_VC}\n")
    LockstepCounter = unittest.skip(NO_VC)(LockstepCounter)
    LockstepTimer = unittest.skip(NO_VC)(LockstepTimer)

if __name__ == "__main__":
    unittest.main()
