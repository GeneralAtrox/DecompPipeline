"""Resuming a recording a little before its end (port/base/session.c --replay-until, Help > Resume before the last
crash in host_resume.cpp): the real file compiled at /W4 /WX with a driver that records 300 vertical blanks of a pad
that changes every 10 and a timer that goes up 7 a blank, then replays the file.
1. A plain replay gives the recording's pad and timer to the end.
2. --replay-until 200: the recording's pad and timer before blank 200; from 200 the live pad, and the timer goes on
   from the replay's last value by the live clock's steps (never back), with one log line saying where it handed over.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_session_resume.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "session.h"
s32 session_timer(s32 live); /* platform.h, which this driver leaves out */
static u32 g_vb;
u32 port_vblank_count(void) { return g_vb; }
int port_extra_drawing(void) { return 0; }
int input_live(void) { return 1; }
int console_lockstep_pad(HostPad *pad) { (void)pad; return 0; }
s32 console_lockstep_timer(s32 live) { return live; }
int console_lockstep_time(HostTime *t) { (void)t; return 0; }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("log: ");
    vprintf(fmt, ap);
    va_end(ap);
}
int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    if (!strcmp(argv[1], "record")) {
        for (g_vb = 0; g_vb < 300u; g_vb++) {
            HostPad pad = {g_vb / 10u, 0, 0, 0, 0};
            session_pad(&pad);
            session_timer(1000 + (s32)g_vb * 7);
        }
        return session_write(argv[2], "fixture") ? 0 : 1;
    }
    if (argc > 3) session_option("--replay-until", argv[3]);
    if (session_option("--replay", argv[2]) != 2 || !session_replaying()) return 3;
    for (g_vb = 0; g_vb < 300u; g_vb++) {
        HostPad pad = {0xf000u + g_vb, 0, 0, 0, 0}; /* the live pad */
        session_pad(&pad);
        printf("%u %u %d\n", g_vb, pad.buttons, session_timer(50 + (s32)g_vb));
    }
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class Resume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "resume_main.c").write_text(DRIVER_C, encoding="utf-8")
        srcs = " ".join(f'"{p}"' for p in [KIT / "base" / "session.c", d / "resume_main.c"])
        cl = f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "base"}" {srcs} /Fe:resume.exe'
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        cls.rec = d / "fixture.ptrec"
        r = subprocess.run([str(d / "resume.exe"), "record", str(cls.rec)], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise RuntimeError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def replay(self, *until):
        r = subprocess.run([str(self.dir / "resume.exe"), "replay", str(self.rec), *until], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [tuple(int(v) for v in ln.split()) for ln in r.stdout.splitlines() if not ln.startswith("log:")]
        return rows, [ln for ln in r.stdout.splitlines() if ln.startswith("log: session: the replay ends")]

    def test_a_plain_replay_gives_the_recording_to_the_end(self):
        rows, ends = self.replay()
        self.assertEqual(len(rows), 300)
        for vb, buttons, timer in rows:
            self.assertEqual((buttons, timer), (vb // 10, 1000 + vb * 7), vb)
        self.assertEqual(ends, [])

    def test_replay_until_hands_over_to_the_live_pad_and_the_timer_goes_on(self):
        rows, ends = self.replay("200")
        self.assertEqual(len(rows), 300)
        for vb, buttons, timer in rows[:200]:
            self.assertEqual((buttons, timer), (vb // 10, 1000 + vb * 7), vb)
        last = 1000 + 199 * 7
        for vb, buttons, timer in rows[200:]:
            self.assertEqual(buttons, 0xF000 + vb, vb)
            self.assertEqual(timer, last + (vb - 200), vb)  # from the replay's value, by the live clock's steps
        self.assertEqual(len(ends), 1)
        self.assertIn("vertical blank 200 (--replay-until)", ends[0])
        self.assertIn("live input", ends[0])


if __name__ == "__main__":
    unittest.main()
