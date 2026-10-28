"""The port kit's sound against the console's (port/runtime/aica.c, port/sdk/ac_digi.c) and the audio check that
measures it (port/tools/audio_check.py, aica_regs.py). The levels, pans and release are the ones Flycast's AICA shows
for Audio64's driver: it writes pan p as DIPAN 16 - p (1..15; 0 as 15) or p (16..31), the chip turns the left side
down by DIPAN's low bits in 3 dB steps (the right side with bit 4 set; 15 off), and a stop is a key off that fades
through the driver's release (RR 31, about 2.7 ms). Synthetic data; the C part only where Visual Studio's 32-bit tools
are found (as tests/test_port_kit.py).
Run: python -W error tests/test_port_audio.py
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

import audio_check  # noqa: E402
import game_config  # noqa: E402
from aica_regs import fields  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

# A constant 16-bit sample (16384) on port 0, looped, at each pan; then a stop and the frames of its release.
AUDIO_MAIN_C = r"""#include <stdio.h>
#include <stdarg.h>
#include "game.h"
#include "aica.h"

s32 acDigiOpen(u32 port, u32 aicaAddress, u32 sizeInBytes, u32 audioType, u32 sampleRate);
s32 acDigiPlayWithParameters(u32 port, u32 volume, u32 pan, u32 dspMixerChannel, u32 dspSendLevel,
                             u32 frequencyOrCents, u32 frequencyOrCentsFlag, u32 eventOffsetInFrames,
                             s16 loopStartInFrames, u32 loopEndInFrames);
s32 acDigiStop(u32 port);

static u8 g_sound[AICA_RAM_SIZE];
void *host_map_fixed(u32 addr, u32 size) { (void)addr, (void)size; return g_sound; }
void port_log(const char *fmt, ...) { (void)fmt; }
void cdda_read(s16 *stereo, u32 frames) { for (u32 i = 0; i < 2u * frames; i++) stereo[i] = 0; }
void audio_start(void) {}
u32 port_vblank_count(void) { return 0; }
void port_wait_vblank(void) {}
u32 port_vblank_phase(int look) { (void)look; return 0; }

int main(void)
{
    static const u32 pans[] = {15, 0, 20, 16, 31};
    s16 out[2 * 200];
    for (u32 i = 0; i < 1000u; i++) {
        g_sound[0x20000u + 2u * i] = 0x00;
        g_sound[0x20000u + 2u * i + 1u] = 0x40;
    }
    acDigiOpen(0, 0x20000u, 2000u, AICA_PCM16, 44100u);
    for (u32 k = 0; k < 5u; k++) {
        acDigiPlayWithParameters(0, 15, pans[k], 0, 0, 44100u, 1u, 0, 0, 0);
        aica_mix(out, 4);
        printf("%d %d ", out[4], out[5]);
    }
    acDigiStop(0);
    aica_mix(out, 200);
    printf("%d %d %d %d\n", out[0], out[2], aica_voice_playing(0), out[2 * 130]);
    return 0;
}
"""

# The movie clock as Flycast's VGA intro recording shows it (tc-clk rec1): the second movie's pair keys on 0.32 of a
# blank in (vertical blank 882.32), and the read position moves on through each blank at 735.68..735.85 samples a
# blank (about every 26 samples), not in a step at the blank. The blank's phase is set by hand; port 0 loops a
# 0x10000-frame ring at 44100 Hz. Prints the position at each quarter of the blank after the key on's, then the
# vertical blanks waited for over 65 reads mid-blank and over 65 reads with the blank's time used up.
CLOCK_MAIN_C = r"""#include <stdio.h>
#include <stdarg.h>
#include "game.h"
#include "aica.h"

s32 acDigiOpen(u32 port, u32 aicaAddress, u32 sizeInBytes, u32 audioType, u32 sampleRate);
s32 acDigiPlayWithParameters(u32 port, u32 volume, u32 pan, u32 dspMixerChannel, u32 dspSendLevel,
                             u32 frequencyOrCents, u32 frequencyOrCentsFlag, u32 eventOffsetInFrames,
                             s16 loopStartInFrames, u32 loopEndInFrames);
s32 acDigiGetCurrentReadPosition(u32 port, u32 *position);

static u8 g_sound[AICA_RAM_SIZE];
static u32 g_phase, g_waits;
void *host_map_fixed(u32 addr, u32 size) { (void)addr, (void)size; return g_sound; }
void port_log(const char *fmt, ...) { (void)fmt; }
void cdda_read(s16 *stereo, u32 frames) { for (u32 i = 0; i < 2u * frames; i++) stereo[i] = 0; }
void audio_start(void) {}
u32 port_vblank_count(void) { return 0; }
void port_wait_vblank(void) { g_waits++; }
u32 port_vblank_phase(int look) { (void)look; return g_phase; }

int main(void)
{
    static s16 out[2 * 735];
    u32 pos = 0, waits[2];
    acDigiOpen(0, 0x20000u, 0x20000u, AICA_PCM16, 44100u);
    g_phase = 20972u; /* 0.32 of the blank */
    acDigiPlayWithParameters(0, 15, 16, 0, 0, 44100u, 1u, 0, 0, 0);
    aica_next_frames(735u);
    aica_mix(out, 735);
    for (u32 k = 0; k <= 4u; k++) {
        g_phase = k * 16384u;
        acDigiGetCurrentReadPosition(0, &pos);
        printf("%u ", pos);
    }
    for (u32 k = 0; k < 2u; k++) {
        g_phase = k ? 65536u : 40000u;
        g_waits = 0;
        for (u32 i = 0; i <= 64u; i++) acDigiGetCurrentReadPosition(0, &pos);
        waits[k] = g_waits;
    }
    printf("%u %u\n", waits[0], waits[1]);
    return 0;
}
"""


def build_audio(d, main_c):
    """Compiles aica.c, ac_digi.c and main_c at /W4 /WX in d; returns (the compile, audio.exe's path)."""
    (d / "port_config.h").write_text(game_config.port_config_h(
        {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
         "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
    (d / "game.h").write_text('#ifndef GAME_H\n#define GAME_H\n#include "rd_rt.h"\ntypedef void (*SdkProc)(void);\n#endif\n', encoding="utf-8")
    (d / "audio_main.c").write_text(main_c, encoding="utf-8")
    rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
    srcs = " ".join(f'"{p}"' for p in [KIT / "runtime" / "aica.c", KIT / "sdk" / "ac_digi.c", d / "audio_main.c"])
    cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
          f'/I"{rd}" {srcs} /Fe:audio.exe')
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                       errors="replace", timeout=300)
    return r, d / "audio.exe"


def work_dir():
    return pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))


class AudioClock(unittest.TestCase):
    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_position_moves_within_the_blank(self):
        """A key on 0.32 of a blank in plays from there, and the read position then follows the time through the next
        blank at the recording's rate (within a sample of 735.735 a blank from the key on); a game polling it mid-blank
        never waits, and only reads with the blank's time used up (the position can move no further) wait for it."""
        d = work_dir()
        try:
            r, exe = build_audio(d, CLOCK_MAIN_C)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0)
            got = [int(x) for x in run.stdout.split()]
            self.assertEqual(len(got), 7, run.stdout)
            for k, pos in enumerate(got[:5]):
                want = (1 + k / 4 - 0.32) * 735.735
                self.assertLessEqual(abs(pos - want), 1.5, f"quarter {k}: {pos} != {want:.1f} ({got})")
            self.assertEqual(got[5:], [0, 1])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class AudioLevels(unittest.TestCase):
    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_pan_law_and_release(self):
        """aica.c and ac_digi.c compile at /W4 /WX; Audio64 pan 15 turns the left side down one 3 dB step (DIPAN 1),
        pan 0 turns it off (DIPAN 15), pan 20 the right side four steps, 16 is the centre and 31 the left only; a stop
        fades in about 2.7 ms (119 frames, 0.79 dB a frame) and then the voice stops."""
        d = work_dir()
        try:
            r, exe = build_audio(d, AUDIO_MAIN_C)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0)
            got = [int(x) for x in run.stdout.split()]
            want = [11599, 16384, 0, 16384, 16384, 4116, 16384, 16384, 16384, 0, 16384, 14930, 0, 0]
            self.assertEqual(len(got), len(want), run.stdout)
            for g, w in zip(got, want):
                self.assertLessEqual(abs(g - w), 2, f"{got} != {want}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class AudioCheck(unittest.TestCase):
    def test_regs_decode(self):
        """A voice's register bytes decode into the manual's fields (little-endian registers at 4-byte steps)."""
        b = bytearray(0x80)
        b[0x00:0x02] = (0x0100 | 0x0200 | 0x0012).to_bytes(2, "little")      # PCMS 2, LPCTL 1, SA high 0x12
        b[0x04:0x06] = (0x3456).to_bytes(2, "little")
        b[0x10:0x12] = (31 | 0 << 6 | 0 << 11).to_bytes(2, "little")         # AR 31
        b[0x14:0x16] = (31 | 7 << 5).to_bytes(2, "little")                   # RR 31, DL 7
        b[0x24:0x26] = (11 << 8 | 19).to_bytes(2, "little")                  # DISDL 11, DIPAN 19
        f = fields(b.hex())
        self.assertEqual((f["PCMS"], f["LPCTL"], f["SA"], f["AR"], f["RR"], f["DL"], f["DISDL"], f["DIPAN"]),
                         (2, 1, 0x123456, 31, 31, 7, 11, 19))

    def test_pairs_and_stops(self):
        """Key ons pair as each voice's n-th on both sides whatever lies between them; a key off's shift is measured
        from its voice's last paired start, so a timeline gap before it cancels and a late stop shows."""
        keys = [("K", 1, 100, 0, "-"), ("K", 2, 500, 1, "-"), ("K", 3, 900, 0, "-"), ("F", 4, 1500, 0, "-")]
        port = [(10, 0, 15, 16, 65536, 100), (400, 1, 15, 16, 65536, 100), (5000, 0, 15, 16, 65536, 100)]
        offs = [(5300, 0, 0, 0, 0, 0)]
        self.assertEqual([(e, p, ch) for e, p, ch, _, _ in audio_check.pairs(keys, 0, port)],
                         [(100, 10, 0), (500, 400, 1), (900, 5000, 0)])
        self.assertEqual(audio_check.stop_shifts(keys, 0, port, offs), [(1500, 0, 300 - 600)])

    def test_pairs_by_sound(self):
        """--pair sound: a port key on pairs with the emulator's next key on of the same step and loop end, on
        whatever voice; a voice only the emulator keys (a stream) shifts nothing, two sounds may swap their order, and
        a sound the emulator never keys stays unpaired. Key offs are measured from each pair's own start."""
        def regs(octave, lea):
            b = bytearray(0x80)
            b[0x0c:0x0e] = lea.to_bytes(2, "little")
            b[0x18:0x1a] = (octave << 11).to_bytes(2, "little")
            return b.hex()
        a, b, stream = regs(14, 100), regs(15, 200), regs(0, 8192)
        keys = [("K", 1, 50, 0, stream), ("K", 2, 1000, 6, a), ("K", 3, 1100, 7, b), ("F", 4, 1400, 6, a),
                ("K", 5, 1500, 8, a)]
        port = [(10, 0, 12, 0, 16384, 100), (5, 1, 12, 0, 32768, 200), (500, 2, 12, 0, 16384, 100),
                (600, 3, 12, 0, 65536, 300)]
        offs = [(420, 0, 0, 0, 0, 0)]
        paired, shifts, unpaired = audio_check.sound_pairs(keys, 0, port, offs)
        self.assertEqual([(e, p, ch) for e, p, ch, _, _ in paired], [(1000, 10, 6), (1100, 5, 7), (1500, 500, 8)])
        self.assertEqual(shifts, [(1000, 6, 410 - 400)])
        self.assertEqual(unpaired, 1)


if __name__ == "__main__":
    unittest.main()
