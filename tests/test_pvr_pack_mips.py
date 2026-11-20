"""Stored mip chains (port/pvr/pvr_pack_png.c FILE.mips, port.exe --pack-mips DIR), through the renderer's lookup
compiled by tests/test_pvr_pack.py's driver:
1. --pack-mips writes FILE.mips for every image but an over line's, and a run that reads them gets exactly the levels a
   run without them builds (odd and even edges, transparent texels);
2. --pack-mips-check builds every chain again and finds what a run reads back equal; a second --pack-mips writes nothing;
3. an image changed after its mips were stored is not given them: its chain is built from the new image;
4. tools/pack_builds.py runs the build's port.exe --pack-mips after a pack is built, and a release ships the files;
5. images kicked early (pvr_pack_hint, as kmLoadTexture does) and loaded on the loader's thread give the same levels;
6. an over line's image is kicked by the hint too and merged on the loader's thread: the image where its alpha is not
   0, else the game's texel (it was read and its chain built on the drawing thread at its first draw); a texture whose
   bytes the game's own library copies (port_texture_written, no kmLoadTexture) is kicked by the write that reaches its
   surface's last texel byte (pvr_pack_surface, pvr_pack_written), a write short of it kicks nothing;
7. --pack-mips stores the chain of a line off by its if= setting (its run is a check run, where every setting is off).
Only where Visual Studio's 32-bit tools are found, skipped elsewhere. Run: python -W error tests/test_pvr_pack_mips.py.
Nearest existing: tests/test_pvr_pack.py - same driver, split out because that file is at its size cap.
"""
import os
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_pvr_pack import HAVE_VC, NO_VC, build_driver, png_rgba, run_driver, tp  # noqa: E402

import pack_builds  # noqa: E402
import package_release  # noqa: E402


def image(rnd, w, h):
    px = bytearray()
    for i in range(w * h):
        a = 0 if i % 7 == 0 else (255 if i % 3 else rnd.randrange(1, 255))
        px += bytes((rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), a))
    return px


@unittest.skipUnless(HAVE_VC, NO_VC)
class StoredMips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = pathlib.Path(__file__).resolve().parent / ".work"
        cls.dir = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.exe = build_driver(cls.dir)
        rnd = random.Random(11)
        cls.vram = bytearray(rnd.getrandbits(8) for _ in range(0x20000)) + bytes(0x800000 - 0x20000)
        cls.cases = [(tp.VQ | 1 << 27 | 0x0000 // 8, 64, 64, 0), (1 << 27 | 0x8000 // 8, 32, 64, 0),
                     (tp.NON_TWIDDLED | 1 << 27 | 0x18000 // 8, 16, 8, 0)]
        cls.sizes = [(37, 21), (64, 32), (16, 8)]  # odd edges, even edges; the last is an over line's

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def keys(self):
        return [tp.key(c[0], c[1], c[2], self.vram[(c[0] & 0x1fffff) << 3:], c[3]) for c in self.cases]

    def pack(self, name, seed=3):
        rnd, d = random.Random(seed), self.dir / name
        shutil.rmtree(d, ignore_errors=True)
        p = tp.Pack(d)
        for i, (k, (w, h)) in enumerate(zip(self.keys(), self.sizes)):
            p.add(k, image(rnd, w, h), w, h, over=i == 2)
        p.close()
        return d

    def tool(self, d, check=False):
        env = dict(os.environ, PACK_MIPS=str(d), **({"PACK_MIPS_CHECK": "1"} if check else {}))
        r = subprocess.run([str(self.exe), "-", "-"], capture_output=True, text=True, timeout=120, env=env)
        return r.returncode, r.stdout

    def levels(self, d, out):
        out.mkdir(exist_ok=True)
        got = run_driver(self.exe, self.vram, self.cases, pack=d, out=out)
        return got, [(out / f"{n}_after.rgba").read_bytes() for n in range(len(self.cases))]

    def test_stored_equal_built(self):
        d = self.pack("p1")
        built = self.levels(d, self.dir / "o_built")
        rc, text = self.tool(d)
        self.assertEqual(rc, 0, text)
        self.assertIn("pack mips: 2 written, 0 already stored, 1 over lines (built at load), 0 failed", text)
        self.assertEqual(sorted(f.name for f in d.glob("*.mips")), sorted(f"{k:016x}.png.mips" for k in self.keys()[:2]))
        self.assertEqual(self.levels(d, self.dir / "o_stored"), built)
        self.assertEqual(built[0][0][1], 6)  # 37x21, 18x10, 9x5, 4x2, 2x1, 1x1
        rc, text = self.tool(d, check=True)
        self.assertEqual(rc, 0, text)
        self.assertEqual(text.count("already stored read back = built"), 2, text)
        self.assertIn("0 written, 2 already stored", text)
        # the files ship with the pack
        names = [a for a, _ in package_release.pack_files("HD", d, notice=False)]
        self.assertIn(f"packs/HD/{self.keys()[0]:016x}.png.mips", names)

    def test_changed_image_is_built(self):
        d = self.pack("p2")
        self.assertEqual(self.tool(d)[0], 0)
        k = self.keys()[0]
        w, h = self.sizes[0]
        (d / f"{k:016x}.png").write_bytes(tp.png(image(random.Random(99), w, h), w, h))
        fresh = self.dir / "p2_fresh"
        shutil.rmtree(fresh, ignore_errors=True)
        shutil.copytree(d, fresh, ignore=shutil.ignore_patterns("*.mips"))
        self.assertEqual(self.levels(d, self.dir / "o_changed"), self.levels(fresh, self.dir / "o_fresh"))
        rc, text = self.tool(d)
        self.assertIn("1 written, 1 already stored", text)

    def test_hinted_same_pixels(self):
        d = self.pack("p3")
        self.assertEqual(self.tool(d)[0], 0)
        plain = self.levels(d, self.dir / "o_plain")
        with unittest.mock.patch.dict(os.environ, {"PACK_HINT": "1"}):
            self.assertEqual(self.levels(d, self.dir / "o_hinted"), plain)

    def hinted(self, how, d, out):
        r = subprocess.run([str(self.exe), str(self.dir / "vram.bin"), str(self.dir / "cases.txt"), str(d), str(out)],
                           capture_output=True, text=True, timeout=120, env=dict(os.environ, PACK_HINT=how, PACK_LOG="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stderr

    def test_kicked_by_library_write(self):
        d, out = self.pack("p6"), self.dir / "o_write"
        self.levels(d, out)
        log = self.hinted("w", d, out)
        self.assertIn("3 images kicked, 3 at their load, 0 at their first draw", log)
        self.assertIn("(built (an over line's image)): kicked at blank 0 (load)", log)
        self.assertIn("3 images kicked, 0 at their load, 3 at their first draw", self.hinted("s", d, out))

    def test_over_line_kicked_by_hint(self):
        d, out = self.pack("p4"), self.dir / "o_over"
        got = self.levels(d, out)  # writes vram.bin and cases.txt beside the driver
        log = self.hinted("1", d, out)
        self.assertIn("3 images kicked, 3 at their load, 0 at their first draw", log)
        self.assertIn("(built (an over line's image)): kicked at blank 0 (load)", log)
        k, (w, h) = self.keys()[2], self.sizes[2]
        _, _, png = png_rgba((d / f"{k:016x}.png").read_bytes())
        game = (out / "2_before.rgba").read_bytes()
        want = b"".join(png[i:i + 4] if png[i + 3] else game[i:i + 4] for i in range(0, w * h * 4, 4))
        self.assertEqual((out / "2_after.rgba").read_bytes()[:w * h * 4], want)
        self.assertEqual(got[0][2][1], 5)  # 16x8, 8x4, 4x2, 2x1, 1x1: the over image's whole chain, as before

    def test_if_line_gets_mips(self):
        d, k = self.pack("p5"), self.keys()[0]
        idx = d / "index.txt"
        idx.write_text(idx.read_text(encoding="utf-8").replace(f"{k:016x}.png", f"{k:016x}.png if=game.hd", 1), encoding="utf-8")
        with unittest.mock.patch.dict(os.environ, {"PACK_CHECK_OFF": "game.hd"}):
            rc, text = self.tool(d)
        self.assertEqual(rc, 0, text)
        self.assertIn("pack mips: 2 written, 0 already stored, 1 over lines (built at load), 0 failed", text)
        self.assertTrue((d / f"{k:016x}.png.mips").is_file())


class PackBuildsStores(unittest.TestCase):
    def test_runs_port_exe(self):
        with tempfile.TemporaryDirectory() as t:
            b = pathlib.Path(t)
            (b / "packs" / "HD").mkdir(parents=True)
            (b / "packs" / "HD" / "index.txt").write_text("")
            self.assertTrue(pack_builds.store_mips(b / "packs" / "HD", b, None).startswith("not stored"))
            (b / "port.exe").write_bytes(b"")
            seen = []
            line = pack_builds.store_mips(b / "packs" / "HD", b, lambda cmd, t: (seen.append(cmd), (0, "x\npack mips: 1 written"))[1])
            self.assertEqual(line, "pack mips: 1 written")
            self.assertEqual(seen[0][1:], ["--headless", "--fixed-clock", "1", "--pack-mips", str(b / "packs" / "HD")])


if __name__ == "__main__":
    unittest.main()
