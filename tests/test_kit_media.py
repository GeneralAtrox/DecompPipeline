"""The port kit's media decoders (port/runtime/media: ADX, the Sofdec system stream, MPEG-1 video, the movie player)
on streams this file builds from the formats' descriptions: an ADX file whose samples follow from the format's
prediction by hand, and an MPEG-1 system stream holding a 16x16 movie (an intra picture of flat blocks and a predicted
picture that copies it) with an ADX sound of one second. Synthetic data only; the C part where Visual Studio's 32-bit
tools are found (as tests/test_port_kit.py).
Run: python -W error tests/test_kit_media.py
"""
import math
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

MEDIA = ["adx.c", "media_mix.c", "mpv_tables.c", "mpv_idct.c", "mpv_pic.c", "mpv_mb.c", "sfd.c", "movie.c"]

# The harness: "adx F" decodes an ADX file (once, then looping), "talk F" plays it through the mix at -6 dB, "sfd F"
# demultiplexes and decodes a movie, "movie F" plays one as a port does (vertical blanks, mix, frames drawn).
MAIN_C = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "platform.h"
#include "media.h"
#include "sfd.h"

static PortVblankFn g_task;
static void *g_task_arg;
static u32 g_vb, g_shows, g_hides;
static u8 g_shown[3];
void port_log(const char *fmt, ...) { (void)fmt; }
u32 port_vblank_count(void) { return g_vb; }
void port_add_vblank_task(PortVblankFn fn, void *arg) { g_task = fn, g_task_arg = arg; }
void audio_start(void) {}
void gfx_movie_show(const u8 *y, const u8 *cb, const u8 *cr, u32 ys, u32 cs, u32 w, u32 h)
{
    (void)ys, (void)cs;
    g_shows++;
    g_shown[0] = y[0], g_shown[1] = cb[0], g_shown[2] = cr[0];
    if (w != 16 || h != 16) printf("size %u %u\n", w, h);
}
void gfx_movie_hide(void) { g_hides++; }

static u8 *load(const char *path, u32 *n)
{
    FILE *f = fopen(path, "rb");
    u8 *p;
    fseek(f, 0, SEEK_END);
    *n = (u32)ftell(f);
    fseek(f, 0, SEEK_SET);
    p = malloc(*n + 64);
    fread(p, 1, *n, f);
    fclose(f);
    return p;
}

int main(int argc, char **argv)
{
    u32 n, got;
    u8 *p = load(argv[2], &n);
    static s16 pcm[2 * 4096];
    if (argc < 3) return 2;
    if (!strcmp(argv[1], "adx")) {
        AdxStream s;
        memset(&s, 0, sizeof s);
        if (!adx_parse(p, n, &s.h)) return printf("not adx\n"), 1;
        printf("%u %u %u %u %u %u %u %d %d\n", s.h.data, s.h.channels, s.h.rate, s.h.samples, s.h.loop,
               s.h.loop_start, s.h.loop_end, s.h.c1, s.h.c2);
        adx_open(&s, p, n, 1);
        s.loops = 0;
        got = adx_read(&s, pcm, 4096);
        printf("%u %d", got, adx_ended(&s));
        for (u32 i = 0; i < got; i++) printf(" %d", pcm[2 * i]);
        printf("\n");
        adx_open(&s, p, n, 1);
        got = adx_read(&s, pcm, 200);
        printf("%u %u %d", got, s.loop_count, adx_ended(&s));
        for (u32 i = 0; i < got; i++) printf(" %d", pcm[2 * i]);
        printf("\n");
    } else if (!strcmp(argv[1], "talk")) {
        MediaTalk *t = media_talk_new();
        u8 *b = media_talk_load(t, n);
        memcpy(b, p, n);
        media_talk_loop(t, 0);
        if (!media_talk_start(t, n)) return 1;
        media_talk_volume(t, -60);
        memset(pcm, 0, sizeof pcm);
        media_mix(pcm, 4096);
        printf("%d %u %d %d %d\n", media_talk_state(t), media_talk_pos(t), media_gain(-60), pcm[0], pcm[1]);
    } else if (!strcmp(argv[1], "sfd")) {
        SfdDemux d;
        Mpv m;
        const MpvFrame *f;
        u32 st, sz;
        AdxInfo h;
        memset(&d, 0, sizeof d);
        memset(&m, 0, sizeof m);
        printf("%u", sfd_parse(&d, p, n, 1));
        printf(" %u %u %d\n", d.vlen, d.alen, adx_parse(d.audio, d.alen, &h));
        while (sfd_unit(&d, &st, &sz)) {
            const u8 *u = d.video + st;
            if (u[3] == 0xb3) {
                int ok = mpv_sequence(&m, u, sz);
                printf("seq %d %u %u %u/%u\n", ok, m.width, m.height, m.rate_num, m.rate_den);
            }
            else if (u[3] == 0x00 && (f = mpv_decode(&m, u, sz)) != NULL)
                printf("pic %u %u %u %u\n", f->y[0], f->y[15 * 16 + 15], f->cb[0], f->cr[63]);
            sfd_compact(&d);
        }
        if ((f = mpv_flush(&m)) != NULL) printf("pic %u %u %u %u\n", f->y[0], f->y[15 * 16 + 15], f->cb[0], f->cr[63]);
        printf("errors %u\n", m.errors);
    } else {
        MediaMovie *mv = media_movie_new();
        u8 *b = media_movie_load(mv, n);
        u32 ended = 0, first = 0;
        memcpy(b, p, n);
        if (!media_movie_start(mv, n)) return 1;
        for (g_vb = 1; g_vb < 200 && !ended; g_vb++) {
            g_task(g_task_arg);
            media_mix(pcm, 735);
            if (g_vb & 1) media_movie_frame(); /* the game draws every other blank */
            if (!first && media_movie_pictures(mv) == 2) first = g_vb;
            if (media_movie_state(mv) == MEDIA_TALK_PLAYEND) ended = g_vb;
        }
        printf("%u %u %u %u %u %u %u\n", first, ended, media_movie_pictures(mv), g_shows,
               g_shown[0], g_shown[1], g_shown[2]);
        media_movie_stop(mv);
        printf("%d %u\n", media_movie_state(mv), g_hides);
    }
    return 0;
}
"""


class Bits:
    """MSB-first bit writer."""

    def __init__(self):
        self.v, self.n = 0, 0

    def put(self, value, width):
        self.v, self.n = self.v << width | (value & ((1 << width) - 1)), self.n + width
        return self

    def code(self, s):
        for c in s:
            self.put(int(c), 1)
        return self

    def bytes(self):
        pad = -self.n % 8
        return (self.v << pad).to_bytes((self.n + pad) // 8, "big")


def adx_coefs(cutoff, rate):
    z = math.cos(2 * math.pi * cutoff / rate)
    a, b = math.sqrt(2) - z, math.sqrt(2) - 1
    c = (a - math.sqrt((a + b) * (a - b))) / b
    return math.floor(c * 8192 + 0.5), math.floor(c * c * -4096 + 0.5)


def adx_file(frames, samples, loop=None, channels=1, rate=44100):
    """An ADX version 3 file: frames as (scale, 32 codes) per channel, interleaved; loop (start, end) in samples."""
    co = 0x32
    h = bytearray(co + 4)
    h[0:2] = b"\x80\x00"
    h[2:4] = co.to_bytes(2, "big")
    h[4:8] = bytes([3, 18, 4, channels])
    h[8:12] = rate.to_bytes(4, "big")
    h[12:16] = samples.to_bytes(4, "big")
    h[16:18] = (500).to_bytes(2, "big")
    h[18] = 3
    if loop:
        for i, v in enumerate((1, loop[0], co + 4 + loop[0] // 32 * 18 * channels, loop[1],
                               co + 4 + -(-loop[1] // 32) * 18 * channels)):
            h[0x18 + 4 * i:0x1c + 4 * i] = v.to_bytes(4, "big")
    h[co - 2:co + 4] = b"(c)CRI"
    for scale, codes in frames:
        h += scale.to_bytes(2, "big") + bytes((codes[i] & 15) << 4 | (codes[i + 1] & 15) for i in range(0, 32, 2))
    return bytes(h) + b"\x80\x01" + bytes(16)  # the end marker


def adx_decode(frames, c1, c2):
    """The format's samples: code * scale + (c1 * h1 + c2 * h2) >> 12, clipped to 16 bits."""
    out, h1, h2 = [], 0, 0
    for scale, codes in frames:
        for c in codes:
            s = max(-32768, min(32767, c * scale + ((c1 * h1 + c2 * h2) >> 12)))
            out.append(s)
            h1, h2 = s, h1
    return out


FRAMES = [(100, [7] + [0] * 31), (40, [(-8 + i % 16) for i in range(32)]), (2000, [7, 7, 7, 7] + [0] * 28),
          (3, [-1, 1] * 16)]


def mpeg1_movie(sound):
    """An MPEG-1 system stream: a 16x16 30 fps video of an intra picture whose blocks are flat (DC only: Y 144,
    Cb 120, Cr 152) and a predicted picture of one macroblock moved by (0, 0), not coded; and the sound."""
    seq = b"\x00\x00\x01\xb3" + Bits().put(16, 12).put(16, 12).put(1, 4).put(5, 4).put(0x3ffff, 18).put(1, 1) \
        .put(2, 10).put(0, 3).bytes()
    gop = b"\x00\x00\x01\xb8" + Bits().put(0, 1).put(0, 5).put(0, 6).put(1, 1).put(0, 12).put(1, 1).put(0, 1).bytes()
    pic_i = b"\x00\x00\x01\x00" + Bits().put(0, 10).put(1, 3).put(0xffff, 16).put(0, 1).bytes()
    # slice 1, quantiser 8; macroblock increment 1, intra; luma DC +16 (size 5), 0, 0, 0; Cb -8 (size 4); Cr +24
    mb = Bits().put(8, 5).put(0, 1).code("1").code("1")
    mb.code("1110").code("10000").code("10")
    for _ in range(3):
        mb.code("100").code("10")
    mb.code("1110").code("0111").code("10")
    mb.code("11110").code("11000").code("10")
    slice_i = b"\x00\x00\x01\x01" + mb.bytes()
    pic_p = b"\x00\x00\x01\x00" + Bits().put(1, 10).put(2, 3).put(0xffff, 16).put(0, 1).put(1, 3).put(0, 1).bytes()
    # macroblock increment 1, motion compensated and not coded, vector (0, 0)
    slice_p = b"\x00\x00\x01\x01" + Bits().put(8, 5).put(0, 1).code("1").code("001").code("1").code("1").bytes()
    video = seq + gop + pic_i + slice_i + pic_p + slice_p + b"\x00\x00\x01\xb7"
    pack = b"\x00\x00\x01\xba" + Bits().code("0010").put(0, 3).put(1, 1).put(0, 15).put(1, 1).put(0, 15).put(1, 1) \
        .put(1, 1).put(1000, 22).put(1, 1).bytes()

    def packet(sid, data):
        return b"\x00\x00\x01" + bytes([sid]) + (len(data) + 1).to_bytes(2, "big") + b"\x0f" + data

    return pack + packet(0xe0, video) + pack + packet(0xc0, sound) + b"\x00\x00\x01\xb9"


def build(d):
    (d / "port_config.h").write_text(game_config.port_config_h(
        {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
         "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
    (d / "media_main.c").write_text(MAIN_C, encoding="utf-8")
    rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
    media = KIT / "runtime" / "media"
    srcs = " ".join(f'"{p}"' for p in [media / f for f in MEDIA] + [d / "media_main.c"])
    cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /wd4996 /I"{d}" /I"{media}" '
          f'/I"{KIT / "runtime"}" /I"{rd}" {srcs} /Fe:media.exe')
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                       errors="replace", timeout=300)
    return r, d / "media.exe"


@unittest.skipUnless(HAVE_VC, NO_VC)
class KitMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        cls.d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        r, cls.exe = build(cls.d)
        if r.returncode != 0 or "warning" in r.stdout.lower():
            shutil.rmtree(cls.d, ignore_errors=True)
            raise AssertionError("the media files do not compile at /W4 /WX:\n" + r.stdout[-3000:] + r.stderr[-1000:])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def run_exe(self, mode, name, data):
        (self.d / name).write_bytes(data)
        run = subprocess.run([str(self.exe), mode, str(self.d / name)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return run.stdout.splitlines()

    def test_adx_samples_and_loop(self):
        """The header's fields and the cut-off's coefficients (500 Hz at 44,100: 7334, -3283); every sample as the
        format's prediction gives it, clipped, ending at the header's count; looping 32..96 plays frames 0, 1, 2,
        then 1, 2 again and on, the prediction carrying on from the loop's end (as the stream continues there)."""
        c1, c2 = adx_coefs(500, 44100)
        self.assertEqual((c1, c2), (7334, -3283))
        want = adx_decode(FRAMES, c1, c2)
        out = self.run_exe("adx", "a.adx", adx_file(FRAMES, 120, loop=(32, 96)))
        self.assertEqual(out[0].split(), ["54", "1", "44100", "120", "1", "32", "96", "7334", "-3283"])
        once = [int(x) for x in out[1].split()]
        self.assertEqual(once[:2], [120, 1])
        self.assertEqual(once[2:], want[:120])
        looped = [int(x) for x in out[2].split()]
        self.assertEqual(looped[:3], [200, 2, 0])
        self.assertEqual(looped[3:], adx_decode([FRAMES[i] for i in (0, 1, 2, 1, 2, 1, 2)], c1, c2)[:200])

    def test_talk_volume(self):
        """A talk at -6 dB (-60 in 0.1 dB steps, 1.15 gain 16423) plays the samples scaled, mono on both sides,
        and ends (ADXT's PLAYEND) after its last sample."""
        want = adx_decode(FRAMES, *adx_coefs(500, 44100))
        out = self.run_exe("talk", "t.adx", adx_file(FRAMES, 128))[0].split()
        self.assertEqual(out[:3], ["5", "128", "16423"])
        self.assertEqual([int(out[3]), int(out[4])], [want[0] * 16423 >> 15] * 2)

    def test_movie_demux_and_pictures(self):
        """The system stream splits into its video and sound (the ADX file whole); the sequence reads as 16x16 at
        30 fps; the intra picture's flat blocks decode to their DC values and the predicted picture copies it."""
        sound = adx_file([(0, [0] * 32)] * 1379, 44100)
        out = self.run_exe("sfd", "m.sfd", mpeg1_movie(sound))
        total, vlen, alen, is_adx = (int(x) for x in out[0].split())
        self.assertEqual((alen, is_adx), (len(sound), 1))
        self.assertGreater(vlen, 0)
        self.assertEqual(out[1:], ["seq 1 16 16 30/1", "pic 144 144 120 152", "pic 144 144 120 152", "errors 0"])
        self.assertEqual(total, len(mpeg1_movie(sound)))

    def test_movie_plays_to_its_sound(self):
        """Played as a port plays it: the second picture (due at 1/30 s) is shown at the first drawn frame past
        that time on the sound's clock (vertical blank 3: two blanks, 1470 samples), and the movie ends when its one
        second of sound has played out (vertical blank 60 or 61); stopping it takes its picture away."""
        sound = adx_file([(0, [0] * 32)] * 1379, 44100)
        out = self.run_exe("movie", "p.sfd", mpeg1_movie(sound))
        first, ended, pictures, shows, y, cb, cr = (int(x) for x in out[0].split())
        self.assertEqual((first, pictures, y, cb, cr), (3, 2, 144, 120, 152))
        self.assertIn(ended, (60, 61, 62))
        self.assertGreaterEqual(shows, 2)
        self.assertEqual(out[1].split(), ["0", "1"])


if __name__ == "__main__":
    unittest.main()
