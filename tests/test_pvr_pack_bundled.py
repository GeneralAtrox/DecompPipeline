"""Texture packs in a release (port/pvr/pvr_pack.c, port/tools/package_release.py): a release's packs, in packs/ beside
the program: the game's default is found there, an optional pack shows over it, and the menu's Texture pack entry lists
them and switches at once. Only where Visual Studio's 32-bit tools are found, skipped elsewhere. Split from
tests/test_pvr_pack.py, whose driver these use.

usage: python -W error tests/test_pvr_pack_bundled.py
"""
import os
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))

from test_pvr_pack import HAVE_VC, NO_VC, build_driver, flat, run_driver, tp  # noqa: E402


def write_pack(folder, images):
    p = tp.Pack(folder, "test")
    for key, n in images:
        p.add(key, flat(n), n, n)
    return p.close()


@unittest.skipUnless(HAVE_VC, NO_VC)
class BundledPacks(unittest.TestCase):
    """A release carries its packs in packs/ beside the program; game.json's path exists only on the builder's machine.
    The image sizes say whose image a key got: 4 game.json's path, 8 the game's pack beside the program, 16 and 32 an
    optional pack."""

    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        cls.dir = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        rnd = random.Random(5)
        cls.vram = bytearray(rnd.getrandbits(8) for _ in range(0x60000))
        cls.vram += bytes(0x800000 - len(cls.vram))
        cls.cases = [(1 << 27 | 0x0000 // 8, 32, 32, 0), (1 << 27 | 0x4000 // 8, 32, 32, 0), (1 << 27 | 0x8000 // 8, 32, 32, 0)]
        cls.keys = [tp.key(*c[:3], bytes(cls.vram[(c[0] & 0x1fffff) << 3:((c[0] & 0x1fffff) << 3) + 2048])) for c in cls.cases]
        cls.with_game, cls.without = cls.dir / "with_game", cls.dir / "without"
        cls.with_game.mkdir()
        cls.without.mkdir()
        cls.exe = build_driver(cls.with_game, cls.dir / "builder" / "game")
        cls.bare = build_driver(cls.without)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        for folder in (self.dir / "builder", self.with_game / "packs", self.without / "packs"):
            shutil.rmtree(folder, ignore_errors=True)

    def sizes(self, exe, arg=None, check=False, choose=None):
        env = {"PACK_SET": str(choose)} if choose is not None else {}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            return [g[2] for g in run_driver(exe, self.vram, self.cases, arg, check_run=check)]
        finally:
            for k, v in old.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)

    def menu(self, exe, arg=""):
        r = subprocess.run([str(exe), str(exe.parent / "vram.bin"), str(exe.parent / "cases.txt"), arg], capture_output=True,
                           text=True, timeout=120, env=dict(os.environ, PACK_MENU="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stderr.splitlines()
        return [ln.split(" ", 1)[1].split("|")[1] for ln in lines if ln.startswith("choice ")], lines[-1]

    def test_game_pack_is_found_beside_the_program(self):
        k0 = self.keys[0]
        write_pack(self.dir / "builder" / "game", [(k0, 4)])
        self.assertEqual(self.sizes(self.exe), [4, 0, 0])               # a build where it was built: game.json's path
        write_pack(self.with_game / "packs" / "game", [(k0, 8)])
        self.assertEqual(self.sizes(self.exe), [8, 0, 0])               # a release: the one it carries
        shutil.rmtree(self.dir / "builder")
        self.assertEqual(self.sizes(self.exe), [8, 0, 0])               # on a player's machine: only that one exists

    def test_optional_pack_shows_over_the_game_pack(self):
        k0, k1, k2 = self.keys
        write_pack(self.with_game / "packs" / "game", [(k0, 8), (k1, 8)])
        write_pack(self.with_game / "packs" / "HD text", [(k0, 16), (k2, 32)])
        self.assertEqual(self.sizes(self.exe), [8, 8, 0])               # off by default: the game's own only
        self.assertEqual(self.sizes(self.exe, "HD text"), [16, 8, 32])  # its images win; the game's fills the rest
        self.assertEqual(self.sizes(self.exe, "on"), [8, 8, 0])
        self.assertEqual(self.sizes(self.exe, "off"), [0, 0, 0])
        self.assertEqual(self.sizes(self.exe, "gone since saved"), [8, 8, 0])  # a saved name with no folder
        self.assertEqual(self.sizes(self.exe, check=True), [0, 0, 0])   # a check run: the original
        self.assertEqual(self.sizes(self.exe, "HD text", check=True), [16, 8, 32])  # asked for

    def test_a_dir_still_replaces_the_game_pack(self):
        k0, k1, _ = self.keys
        write_pack(self.with_game / "packs" / "game", [(k0, 8), (k1, 8)])
        write_pack(self.dir / "elsewhere", [(k0, 16)])
        self.assertEqual(self.sizes(self.exe, str(self.dir / "elsewhere")), [16, 0, 0])

    def test_menu_lists_the_choices_and_switches_at_once(self):
        k0, k1, k2 = self.keys
        packs = self.with_game / "packs"
        write_pack(packs / "game", [(k0, 8), (k1, 8)])
        write_pack(packs / "Zed", [(k1, 16)])
        write_pack(packs / "HD text", [(k0, 16), (k2, 32)])
        (packs / "no index").mkdir()
        write_pack(packs / "on", [(k0, 16)])                            # --texture-pack would read it as the word
        self.sizes(self.exe)                                            # writes the driver's input files
        self.assertEqual(self.menu(self.exe)[0], ["off", "on", "HD text", "Zed"])  # the game's own isn't offered twice
        self.assertTrue(self.menu(self.exe)[1].endswith("available 1 current 1"))
        self.assertTrue(self.menu(self.exe, "HD text")[1].endswith("current 2"))
        self.assertTrue(self.menu(self.exe, "off")[1].endswith("current 0"))
        self.assertTrue(self.menu(self.exe, "gone since saved")[1].endswith("current 1"))
        self.assertEqual(self.sizes(self.exe, choose=2), [8, 8, 0, 16, 8, 32])  # images read before the switch are dropped
        self.assertEqual(self.sizes(self.exe, "HD text", choose=3), [16, 8, 32, 8, 16, 0])
        self.assertEqual(self.sizes(self.exe, "HD text", choose=1), [16, 8, 32, 8, 8, 0])
        self.assertEqual(self.sizes(self.exe, choose=0), [8, 8, 0, 0, 0, 0])
        self.assertEqual(self.sizes(self.exe, "off", choose=2), [0, 0, 0, 16, 8, 32])

    def test_game_without_a_default_pack(self):
        k0, _, _ = self.keys
        self.sizes(self.bare)
        self.assertEqual(self.menu(self.bare)[0], ["off"])              # nothing to choose: the entry is grey
        self.assertTrue(self.menu(self.bare)[1].endswith("available 0 current 0"))
        write_pack(self.without / "packs" / "HD text", [(k0, 16)])
        self.assertEqual(self.menu(self.bare)[0], ["off", "HD text"])
        self.assertTrue(self.menu(self.bare)[1].endswith("available 1 current 0"))
        self.assertEqual(self.sizes(self.bare), [0, 0, 0])              # shipped, but off until chosen
        self.assertEqual(self.sizes(self.bare, choose=1), [0, 0, 0, 16, 0, 0])


class PackageRelease(unittest.TestCase):
    """tools/package_release.py: what the release ZIP holds, and what it refuses."""

    def setUp(self):
        import package_release
        self.tool = package_release
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        build = self.d / "build"
        build.mkdir()
        (build / "port.exe").write_bytes(b"MZ")
        (build / "SDL3.dll").write_bytes(b"MZ")
        (build / "port.pdb").write_bytes(b"not for players")
        write_pack(self.d / "pad_icons", [(0x11, 4)])
        write_pack(self.d / "sharp", [(0x22, 4)])
        (self.d / "sharp" / "NOTICE.txt").write_text("credits", encoding="utf-8")
        (self.d / "sharp" / "source_scan.png").write_bytes(b"not named by the index")
        self.exe, self.out = build / "port.exe", self.d / "game-port-win32-v1.zip"

    def test_holds_the_program_and_only_what_the_packs_name(self):
        self.assertEqual(self.tool.main(["--exe", str(self.exe), "--out", str(self.out), "--game-pack", str(self.d / "pad_icons"),
                                         "--pack", f"Sharp text={self.d / 'sharp'}"]), 0)
        with zipfile.ZipFile(self.out) as z:
            self.assertEqual(sorted(z.namelist()), sorted([
                "port.exe", "SDL3.dll", "packs/pad_icons/index.txt", f"packs/pad_icons/{0x11:016x}.png",
                "packs/Sharp text/index.txt", f"packs/Sharp text/{0x22:016x}.png", "packs/Sharp text/NOTICE.txt"]))
            self.assertEqual(z.read("packs/Sharp text/NOTICE.txt"), b"credits")

    def test_refuses(self):
        (self.d / "bare").mkdir()
        write_pack(self.d / "bare", [(0x33, 4)])                        # an optional pack without its credits
        broken = self.d / "broken"
        write_pack(broken, [(0x44, 4)])
        (broken / "NOTICE.txt").write_text("credits", encoding="utf-8")
        (broken / f"{0x44:016x}.png").unlink()                          # an image the index names
        for packs in ([("bare", self.d / "bare")], [("broken", broken)], [("on", self.d / "sharp")], [("a/b", self.d / "sharp")],
                      [("pad_icons", self.d / "sharp")], [("x", self.d / "nothing")]):
            with self.subTest(packs=packs), self.assertRaises((ValueError, OSError)):
                self.tool.package(self.exe, self.out, self.d / "pad_icons", packs)
        self.assertFalse(self.out.exists())                             # nothing half made
        self.assertEqual(self.tool.main(["--exe", str(self.d / "none.exe"), "--out", str(self.out)]), 1)


class PadIcons(unittest.TestCase):
    """tools/pad_icons.py draws each style's pictures into the boxes of a texture and writes the variant lines."""

    def test_pictures_stay_in_their_boxes_and_write_variants(self):
        import pad_icons
        top, front = (0, 8, 16, 16), (20, 8, 12, 8)
        for style in ("xbox", "playstation"):
            rgba, n = pad_icons.compose(32, [("top", style) + top, ("front", style) + front], scale=2)
            self.assertEqual((n, len(rgba)), (64, 64 * 64 * 4))
            inside = lambda x, y: (0 <= x < 32 and 16 <= y < 48) or (40 <= x < 64 and 16 <= y < 32)  # noqa: E731
            drawn = [(i // 4 % 64, i // 4 // 64) for i in range(3, len(rgba), 4) if rgba[i]]
            self.assertGreater(len(drawn), 400)                          # a picture, not a blank
            self.assertTrue(all(inside(x, y) for x, y in drawn))         # nothing outside the boxes
            flipped, _ = pad_icons.compose(32, [("top", style) + top], scale=2, flip_v=True)
            plain, _ = pad_icons.compose(32, [("top", style) + top], scale=2)
            row = lambda img, y: img[(y * 64) * 4:(y * 64 + 64) * 4]  # noqa: E731
            self.assertEqual(row(flipped, 16), row(plain, 47))           # upside down inside the box
        a, _ = pad_icons.compose(32, [("top", "xbox") + top])
        b, _ = pad_icons.compose(32, [("top", "playstation") + top])
        self.assertNotEqual(a, b)
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            self.assertEqual(pad_icons.main([str(d), "0123456789abcdef", "--size", "32", "--top", "0,8,16,16",
                                             "--scale", "1"]), 0)
            self.assertEqual(sorted(tp.read_index(d)), [(0x0123456789abcdef, "playstation"),
                                                        (0x0123456789abcdef, "xbox")])
            self.assertEqual(tp.main(["check", str(d)]), 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
