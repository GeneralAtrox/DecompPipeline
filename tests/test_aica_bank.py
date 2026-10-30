"""port/tools/aica_bank.py on synthetic data: the AICA's ADPCM (a sine encoded here with the same quantiser comes
back within the step size; the decoder's steps and clamps by hand), PCM forms, a built DTPK bank (sample table, the
last-index count, rates from the program records' transposes) and SPSD streams (mono, stereo blocks), and WAV output.
Run: python -W error tests/test_aica_bank.py
"""
import math
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
import wave

KIT = pathlib.Path(__file__).resolve().parents[1] / "port"
sys.path.insert(0, str(KIT / "tools"))

import aica_bank  # noqa: E402

DIFF = (1, 3, 5, 7, 9, 11, 13, 15)
SCALE = (0xe6, 0xe6, 0xe6, 0xe6, 0x133, 0x199, 0x200, 0x266)


def encode(samples):
    """A plain ADPCM encoder for the tests: the nibble whose step lands nearest, tracking the decoder's state."""
    s, q, nibs = 0, 0x7f, []
    for x in samples:
        best = None
        for n in range(16):
            d = q * DIFF[n & 7] // 8
            v = max(-32768, min(32767, s - d if n & 8 else s + d))
            if best is None or abs(v - x) < abs(best[1] - x):
                best = (n, v)
        n, s = best
        q = max(0x7f, min(0x6000, q * SCALE[n & 7] >> 8))
        nibs.append(n)
    if len(nibs) & 1:
        nibs.append(0)
    return bytes(nibs[i] | nibs[i + 1] << 4 for i in range(0, len(nibs), 2))


def sine(n, period=64, amp=12000):
    return [int(amp * math.sin(2 * math.pi * i / period)) for i in range(n)]


def make_dtpk(samples, transposes):
    """A bank with the given (form, data) samples and one program record per sample with its transpose."""
    nprog = len(samples)
    prog = 0x60
    table = prog + 0x50 + 0x40 * nprog
    data_at = table + 4 + 16 * len(samples)
    head = bytearray(b"DTPK" + bytes(0x1c))
    head += struct.pack("<8I", 0, 0, 0, 0, prog, 0, 0, table)
    head += bytes(prog - len(head))
    p = bytearray(0x50)
    struct.pack_into("<H", p, 0x10, nprog - 1)
    for i, tr in enumerate(transposes):
        r = bytearray(0x40)
        r[0], r[1], r[2], r[3] = i, 0x80, i, 1
        struct.pack_into("<b", r, 10, tr)
        p += r
    t = bytearray(struct.pack("<I", len(samples) - 1))
    off, body = data_at, bytearray()
    for form, data in samples:
        t += struct.pack("<IHHII", off | form << 24, 0, 0, 0, len(data))
        body += data
        off += len(data)
    blob = head + p + t + body
    struct.pack_into("<I", blob, 8, len(blob))
    return bytes(blob)


def make_spsd(data, stereo, rate=22050, form=3):
    h = bytearray(0x40)
    h[0:4] = b"SPSD"
    h[8], h[9] = form, 1 if stereo else 0
    struct.pack_into("<I", h, 0x0c, len(data))
    struct.pack_into("<H", h, 0x2a, rate)
    return bytes(h) + data


class Adpcm(unittest.TestCase):
    def test_sine_round_trip(self):
        x = sine(4000)
        y = aica_bank.adpcm_decode(encode(x))
        err = max(abs(a - b) for a, b in zip(x[200:], y[200:]))
        self.assertLess(err, 1500)
        self.assertEqual(len(y), 4000)

    def test_steps_by_hand(self):
        # 0x07: +127*15/8 = 238, quant 127*0x266>>8 = 304; 0x0f: -(304*15//8 = 570) -> -332, quant 727
        self.assertEqual(aica_bank.adpcm_decode(bytes([0xf7])), [238, -332])
        # nibble 0 shrinks the quantiser to its floor of 127: +15 each time
        self.assertEqual(aica_bank.adpcm_decode(bytes([0x00]), frames=1), [15])

    def test_clamps(self):
        y = aica_bank.adpcm_decode(bytes([0x77]) * 200)
        self.assertEqual(max(y), 32767)
        y = aica_bank.adpcm_decode(bytes([0xff]) * 200)
        self.assertEqual(min(y), -32768)

    def test_state_runs_on(self):
        data = encode(sine(1000))
        st = aica_bank.AdpcmState()
        a = aica_bank.adpcm_decode(data[:250], state=st) + aica_bank.adpcm_decode(data[250:], state=st)
        self.assertEqual(a, aica_bank.adpcm_decode(data))


class Pcm(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(aica_bank.pcm_decode(struct.pack("<3h", 1, -2, 30000), aica_bank.PCM16), [1, -2, 30000])
        self.assertEqual(aica_bank.pcm_decode(bytes([1, 0xff, 0x80]), aica_bank.PCM8), [256, -256, -32768])
        self.assertEqual(aica_bank.frames_of(10, aica_bank.ADPCM), 20)
        self.assertEqual(aica_bank.rate_of(-12), 22050)
        self.assertEqual(aica_bank.rate_of(-24), 11025)


class Dtpk(unittest.TestCase):
    def setUp(self):
        self.x = sine(2000)
        self.pcm = struct.pack("<100h", *range(100))
        self.blob = make_dtpk([(3, encode(self.x)), (0, self.pcm)], [-24, -12])

    def test_table(self):
        s = aica_bank.dtpk_samples(self.blob)
        self.assertEqual([r["form"] for r in s], ["adpcm", "pcm16"])
        self.assertEqual([r["frames"] for r in s], [2000, 100])
        self.assertEqual([r["rate"] for r in s], [11025, 22050])
        self.assertEqual(aica_bank.dtpk_sample_pcm(self.blob, s[1]), list(range(100)))

    def test_unnamed_sample_rate_is_none(self):
        blob = make_dtpk([(0, self.pcm), (0, self.pcm)], [-12])
        rates = [r["rate"] for r in aica_bank.dtpk_samples(blob)]
        self.assertEqual(rates[0], 22050)

    def test_not_a_bank(self):
        with self.assertRaises(ValueError):
            aica_bank.dtpk_samples(b"RIFF" + bytes(100))

    def test_dump(self):
        d = tempfile.mkdtemp()
        try:
            listing = aica_bank.dump(self.blob, d)
            self.assertEqual(len(listing["samples"]), 2)
            with wave.open(str(pathlib.Path(d) / "001.wav")) as w:
                self.assertEqual((w.getframerate(), w.getnframes(), w.getnchannels()), (22050, 100, 1))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Spsd(unittest.TestCase):
    def test_mono(self):
        x = sine(3000)
        info, ch = aica_bank.spsd_pcm(make_spsd(encode(x), False, 32000))
        self.assertEqual((info["channels"], info["rate"], info["frames"]), (1, 32000, 3000))
        self.assertEqual(ch[0], aica_bank.adpcm_decode(encode(x)))

    def test_stereo_blocks(self):
        left, right = encode(sine(0x8000, 50)), encode(sine(0x8000, 80, 6000))
        blk = aica_bank.SPSD_BLOCK
        data = b"".join(left[o:o + blk] + right[o:o + blk] for o in range(0, len(left), blk))
        info, ch = aica_bank.spsd_pcm(make_spsd(data, True))
        self.assertEqual(info["channels"], 2)
        self.assertEqual(ch[0], aica_bank.adpcm_decode(left))
        self.assertEqual(ch[1], aica_bank.adpcm_decode(right))

    def test_wav(self):
        d = tempfile.mkdtemp()
        try:
            data = encode(sine(0x4000)) * 2
            aica_bank.dump(make_spsd(data, True), d)
            with wave.open(str(pathlib.Path(d) / "stream.wav")) as w:
                self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()), (2, 22050, 0x4000))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
