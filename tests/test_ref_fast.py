"""The bounded fast reference recorder (port/tools/ref_fast.py record, ref_bound.py, ram_delta.pack_stream) and
core/divergence.reference, after 2026-11-06 (raw streams of 50 GB per scenario, good references deleted first):
1. pack_stream stores what pack_raw always stored (byte for byte), packs from a pipe as records arrive, drops what
   comes after a stop, and ends cleanly on a record cut short.
2. Bound: the disk cap, free disk, and the projections (size, time) only after the warm-up; the time limit follows
   the vertical blanks; no start without twice the estimate free.
3. record() with Flycast faked (it writes RAMF records into the pipe): a complete run replaces the old reference; a
   short, capped or failed one leaves it as it was, removes its partial folder and says why in NAME.record.json.
4. core/divergence.reference: a complete keyed reference is reused; a failed recording keeps the old one and the
   error carries the recorder's reason.
5. port/tools/divergence.py: noise words that are game globals are reported and never "no divergence"; the watch
   flag (--watch-ram-frame) and its arming line.
Synthetic records; no Flycast, no port build. Run: python -W error tests/test_ref_fast.py
"""
import atexit
import json
import os
import pathlib
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import numpy as np

TOOL = pathlib.Path(__file__).resolve().parents[1]
if not os.environ.get("DECOMP_PROJECT"):  # standalone: a fixture project (tests/test_tooling.py sets its own)
    (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
    _PROJ = tempfile.TemporaryDirectory(prefix="ref_fast_", dir=TOOL / "tests" / ".work")
    (pathlib.Path(_PROJ.name) / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name":
                                                                     "fixture", "binary": "game.bin"}), encoding="utf-8")
    os.environ["DECOMP_PROJECT"] = _PROJ.name
    atexit.register(_PROJ.cleanup)
sys.path[:0] = [str(TOOL), str(TOOL / "port" / "tools"), str(TOOL / "tests")]

import ram_delta  # noqa: E402
import ref_fast  # noqa: E402
from core import divergence as core_div  # noqa: E402
import divergence as kit_div  # noqa: E402  (port/tools/divergence.py)
from test_divergence import LOG, write_raw  # noqa: E402


def rb():
    """ref_bound, imported where used: the checks of the old behaviour (Record, CoreReference) run without it."""
    import ref_bound
    return ref_bound

SIZE, BLOCK = ram_delta.RAM_SIZE, 32


def records(n, seed=1, vb_step=2):
    """n RAMF records as Flycast writes them: the first in full, then a few changed blocks each (vertical blanks)."""
    rng, ram, out = np.random.default_rng(seed), bytearray(SIZE), []
    ram[0x100000:0x110000] = rng.integers(0, 256, 0x10000, dtype=np.uint8).tobytes()
    for i in range(n):
        if i == 0:
            runs = [(0, SIZE)]
        else:
            starts = sorted({int(x) * BLOCK for x in rng.integers(0, SIZE // BLOCK, 5)} | {0x200000 + 64 * i})
            for s in starts:
                ram[s:s + 4] = struct.pack("<I", i * 7 + s)
            runs = [(s, BLOCK) for s in starts]
        body = b"".join(struct.pack("<II", s, n_) for s, n_ in runs) + b"".join(bytes(ram[s:s + n_]) for s, n_ in runs)
        out.append(struct.pack("<IIIiI", ram_delta.RAMF, i, 0x1000 + i, i * vb_step, len(runs)) + body)
    return out


def old_pack(raw, dest, keyframe):
    """pack_raw as it was before pack_stream (Writer.add on the whole RAM)."""
    w, ram = ram_delta.Writer(dest, keyframe), bytearray(SIZE)
    for frame, label, emu, starts, lengths, data in ram_delta.read_raw(raw):
        at = 0
        for s, n in zip(starts.tolist(), lengths.tolist()):
            ram[s:s + n] = data[at:at + n]
            at += n
        w.add(ram, f"{frame} {emu} {label:08x}", (starts, lengths))
    return w.close()


class Pack(unittest.TestCase):
    def test_same_bytes_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "a.raw").write_bytes(b"".join(records(12)))
            old = old_pack(d / "a.raw", d / "old", 5)
            new = ram_delta.pack_stream(d / "a.raw", d / "new", 5)
            for ext in (".zram", ".zidx"):
                self.assertEqual((d / f"old{ext}").read_bytes(), (d / f"new{ext}").read_bytes())
            self.assertEqual((old["changed_bytes"], old["block_bytes"]), (new["changed_bytes"], new["block_bytes"]))
            self.assertEqual((new["stream"]["vb_first"], new["stream"]["vb_last"], new["stream"]["stopped"]), (0, 22, None))
            self.assertIn("8c200000", new["stream"]["regions_kb_per_frame"])

    def test_stop_drains_and_cut_record_ends(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            data = b"".join(records(10))
            (d / "a.raw").write_bytes(data + records(11)[10][:30])  # a record cut short (a killed writer)
            self.assertEqual(ram_delta.pack_stream(d / "a.raw", d / "all", 300)["frames"], 10)
            seen = []
            stats = ram_delta.pack_stream(d / "a.raw", d / "cap", 300, stop=lambda: "cap" if len(seen.append(1) or seen) > 4 else None)
            self.assertEqual((stats["frames"], stats["stream"]["stopped"], len(seen)), (4, "cap", 5))

    @unittest.skipUnless(sys.platform == "win32", "a Windows named pipe")
    def test_pipe(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            s = rb().Stream(d / "ram", 300)

            def flycast():  # the CRT's "wb" open, as Flycast's std::ofstream(trunc) does it
                with open(s.name, "wb") as f:
                    for r in records(20):
                        f.write(r)
            t = threading.Thread(target=flycast)
            t.start()
            t.join(30)
            stats = s.finish(30)
            self.assertIsNone(s.error)
            self.assertEqual(stats["frames"], 20)
            r = ram_delta.Reader(d / "ram")
            self.assertEqual(len(r), 20)
            r.close()
            nobody = rb().Stream(d / "none", 300)  # Flycast never opened it: finish() does not hang
            self.assertEqual(nobody.finish(30)["frames"], 0)


class Bounds(unittest.TestCase):
    def test_bound(self):
        used, free = [0], [100e9]
        b = rb().Bound(10000, 1000, lambda: used[0], lambda: free[0], cap_gb=1.0, t0=0.0, warm_s=60, every=5)
        used[0] = 0.5e9
        self.assertIsNone(b(500, 10))  # far over the cap at this pace, but before the warm-up
        self.assertIsNone(b(600, 12))  # asked again within `every`
        used[0] = 0.05e9
        self.assertIsNone(b(3000, 100))  # 30 vb/s: 333 s, 0.17 GB
        self.assertEqual(b.seen["projected_s"], 333)
        self.assertIn("too slow", b(3000, 400))  # 7.5 vb/s: 1333 s over 1000
        used[0] = 0.4e9
        self.assertIn("would need about 1.3 GB", b(3000, 410))  # the size is checked first
        used[0] = 1.1e9
        self.assertIn("over the disk cap", b(100, 420))
        used[0], free[0] = 0, 1e9
        self.assertIn("under 2.0 GB free", b(100, 430))

    def test_limits(self):
        self.assertEqual(rb().time_limit(16400, 1800), 1800)
        self.assertEqual(rb().time_limit(1500, 1800, 10), 270)
        self.assertAlmostEqual(rb().check_disk(".", 1000, free=1e9), 1000 * rb().EST_KB_PER_VB * 1024)
        with self.assertRaises(SystemExit):
            rb().check_disk(".", 16400, free=1e9)  # 1.07 GB estimated, 2.15 GB needed
        with self.assertRaises(SystemExit):
            rb().check_disk(".", 10 ** 7, max_gb=5, free=9e9)  # the cap bounds the estimate: 10 GB needed


@unittest.skipUnless(sys.platform == "win32", "a Windows named pipe")
class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="rec_", dir=TOOL / "tests" / ".work"))
        self.game, self.ref = self.tmp / "game", self.tmp / "game" / "build" / "reference" / "div_boot"
        self.game.mkdir()
        (self.game / "lockstep.spec").write_text("label 1 8c0fc0e8 label time\n", encoding="utf-8")
        self.ref.mkdir(parents=True)
        (self.ref / "manifest.json").write_text('{"old": true}', encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_emu(self, n, reach):
        def emu_run(game, spec, out, seconds, inputs, exe, lua_vars=None, entries=(), drive=None):
            pipe = next(e.split("=", 1)[1] for e in entries if e.startswith("research:RamFramesPath="))
            with open(pipe, "wb") as f:
                for r in records(n):
                    f.write(r)
            new = self.ref.with_name("div_boot.new") / "ram.zram"
            t = time.monotonic()
            while (not new.exists() or not new.stat().st_size) and time.monotonic() - t < 10:
                time.sleep(0.05)  # the packer has the frames
            drive.vb = reach
            drive.stop = drive.guard(reach, time.monotonic())
            return 1.0
        return emu_run

    def record(self, n, reach, vblanks=20, **kw):
        with mock.patch.object(ref_fast.lockstep, "emu_run", self.fake_emu(n, reach)), \
                mock.patch.object(ref_fast, "load_game", lambda g: {"disc": "x"}), \
                mock.patch.object(ref_fast, "label_address", lambda p: 0x8C0FC0E8):
            return ref_fast.record(self.game, "div_boot", "", "8c04f380", vblanks, every_vb=0, keyframe=5,
                                   exe="flycast.exe", key={"k": 1}, **kw)

    def old_kept(self, msg, why):
        self.assertIn(why, str(msg.exception))
        self.assertTrue((self.ref / "manifest.json").exists())
        self.assertEqual(json.loads((self.ref / "manifest.json").read_text(encoding="utf-8")), {"old": True})
        self.assertFalse(self.ref.with_name("div_boot.new").exists())
        run = json.loads(self.ref.with_name("div_boot.record.json").read_text(encoding="utf-8"))
        self.assertFalse(run["ok"])
        self.assertIn(why, run["reason"])

    def test_complete_replaces(self):
        m = self.record(12, 22)
        got = json.loads((self.ref / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((got["complete"], got["divergence_key"], got["reached_vb"], m["ram"]["frames"]), (True, {"k": 1}, 22, 12))
        self.assertFalse(self.ref.with_name("div_boot.old").exists())
        self.assertTrue(json.loads(self.ref.with_name("div_boot.record.json").read_text(encoding="utf-8"))["ok"])

    def test_short_run_keeps_old(self):
        with self.assertRaises(SystemExit) as msg:
            self.record(12, 10)
        self.old_kept(msg, "vertical blank 10 of 20")

    def test_cap_keeps_old(self):
        with self.assertRaises(SystemExit) as msg:
            self.record(12, 22, max_gb=1e-6)
        self.old_kept(msg, "over the disk cap")

    def test_failed_run_keeps_old(self):
        with mock.patch.object(ref_fast.lockstep, "emu_run", side_effect=RuntimeError("Flycast did not start")), \
                mock.patch.object(ref_fast, "load_game", lambda g: {"disc": "x"}), \
                mock.patch.object(ref_fast, "label_address", lambda p: 0x8C0FC0E8), self.assertRaises(RuntimeError):
            ref_fast.record(self.game, "div_boot", "", "8c04f380", 20, every_vb=0, exe="flycast.exe")
        self.assertTrue((self.ref / "manifest.json").exists())  # the old code deleted it before recording
        self.assertEqual(json.loads((self.ref / "manifest.json").read_text(encoding="utf-8")), {"old": True})
        self.assertFalse(self.ref.with_name("div_boot.new").exists())

    def test_interrupted_swap_restored(self):
        os.replace(self.ref, self.ref.with_name("div_boot.old"))
        rb().restore(self.ref)
        self.assertTrue((self.ref / "manifest.json").exists())


class CoreReference(unittest.TestCase):
    def test_reuse_and_failure_keeps_old(self):
        with tempfile.TemporaryDirectory(dir=TOOL / "tests" / ".work") as d:
            game, exe = pathlib.Path(d), pathlib.Path(d) / "flycast.exe"
            exe.write_bytes(b"MZ")
            sc, top = {"input": "", "vblanks": 100}, {"frame_pc": "8c04f380"}
            ref = game / "build" / "reference" / "div_boot"
            ref.mkdir(parents=True)
            want = {"input": "", "vblanks": 100, "frame_pc": "8c04f380", "card": None, "build": core_div.build_key(exe)}
            (ref / "manifest.json").write_text(json.dumps({"divergence_key": want, "reached_vb": 100}), encoding="utf-8")
            with mock.patch.object(core_div, "bounded", side_effect=AssertionError("recorded again")):
                self.assertEqual(core_div.reference(game, "boot", sc, top, exe, 600, lambda m: None), (ref, 0.0))
            sc["vblanks"] = 200  # stale: recorded again, and the recorder fails

            def failed(cmd, timeout, log_path):
                ref.with_name("div_boot.new").mkdir()
                ref.with_name("div_boot.record.json").write_text('{"reason": "too slow: 3.0 vertical blanks a second"}',
                                                                 encoding="utf-8")
                self.assertIn("--key", cmd)
                return 1
            with mock.patch.object(core_div, "bounded", failed), mock.patch.object(core_div, "wait_ram", lambda log: None), \
                    self.assertRaises(RuntimeError) as e:
                core_div.reference(game, "boot", sc, top, exe, 600, lambda m: None)
            self.assertTrue((ref / "manifest.json").exists())
            self.assertIn("too slow", str(e.exception))
            self.assertIn("the old reference kept", str(e.exception))
            self.assertEqual(json.loads((ref / "manifest.json").read_text(encoding="utf-8"))["reached_vb"], 100)
            self.assertFalse(ref.with_name("div_boot.new").exists())


class DivergenceReport(unittest.TestCase):
    def test_noise_globals_and_watch(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            hidden, sdk = 0x8C020000, 0x8C100004  # a global wrong from frame 1 on; an unnamed work area
            ref = [(1, 10, {hidden: 1, sdk: 1}), (2, 12, {}), (3, 14, {})]
            port = [(1, 0, {hidden: 2, sdk: 2}), (2, 1, {}), (3, 2, {})]
            for name, frames in (("ref", ref), ("port", port)):
                write_raw(d / f"{name}.raw", frames)
                ram_delta.pack_raw(d / f"{name}.raw", d / name, 300)
            out, names = {}, [(0x8C020000, "g_fx")]
            first, _, noise = kit_div.first_divergence(d / "ref", d / "port", None, 2, [], names, noise_out=out,
                                                       span=(0x8C000000, 0x8D000000))
            self.assertEqual((first, noise), (None, 2))  # the old silent "no divergence"
            self.assertEqual(kit_div.noise_globals(out["words"], names), {"count": 1, "words": [
                {"addr": "8c020000", "name": "g_fx"}]})
            self.assertIsNone(kit_div.noise_globals([sdk], names))
            (d / "port.log").write_text(LOG, encoding="ascii")
            self.assertEqual((kit_div.armed(d / "port.log", 4), kit_div.armed(d / "port.log", 5)), (True, False))
        res = {"status": "noise", "noise_globals": {"count": 1, "words": [{"addr": "8c020000", "name": "g_fx"}]}}
        self.assertIn("not zero divergence: 8c020000 g_fx", core_div.summary_line(res))
        self.assertEqual(kit_div.watch_args([0x8C020000, 0x8C030010], 7), ["--watch-ram-frame", "8c020000,8c030010@7"])


if __name__ == "__main__":
    unittest.main()
