"""A Saturn port on Phangrade's launcher contract (port/README.md "Launcher contract"): the play run has no image.bin and
writes its saves into USERDATA.

1. gen_saturn.py's game.json keys image_files, image_sha256 and fallback (port/tools/gen_saturn_image.py) become
   SAT_IMAGE_FILES, SAT_IMAGE_SHA256 and SAT_FALLBACK_DEFAULT; bad values stop it.
2. port/saturn/sat_image.c, built at /W4 /WX in 32-bit MSVC with sat_disc.c (tests/sat_image_test.c), makes the code
   image from a synthetic Saturn disc (tests/test_saturn_sh2.py's) byte for byte as overlay_image.py builds image.bin,
   again after the disc is opened a second time; another SHA-256 or a missing file is 5, no disc 4, no table -1.
3. port/saturn/sat_bup.c (tests/sat_save_dir_test.c): --save-dir puts the backup RAM there unless --save names a file,
   in either order; an old save in the current folder is copied there once and never over a newer one.
4. tools/AutoOnboard/onboard_sat.py play_keys writes the three keys from the disc plan and the image.
5. port/tools/package_release.py packs a Saturn build's exe, SDL3.dll and its fonts, never image.bin, a disc or a save.
Fails before: test_sat_launcher.py (no gen_saturn_image.py, no sat_image.c, no sat_bup_save_dir, no play_keys).
The C parts skip when no 32-bit MSVC is installed. Run: python -W error tests/test_sat_launcher.py
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
KIT = ROOT / "port" / "saturn"
sys.path[:0] = [str(ROOT / "port" / "tools"), str(ROOT / "tests"), str(ROOT / "targets" / "saturn_sh2" / "tools"),
                str(ROOT / "tools" / "AutoOnboard"), str(ROOT / "tools")]
import game_config  # noqa: E402
import gen_saturn  # noqa: E402
import gen_saturn_image  # noqa: E402
import overlay_image  # noqa: E402
import package_release  # noqa: E402
from test_saturn_sh2 import FILES, disc_sectors, raw  # noqa: E402

IMAGE = [["/0", "0x06004000"], ["/FIELD/FIELD.BIN", "0x06010000"]]


def expected_image():
    parts = [(int(b, 16), FILES[p], p, False) for p, b in IMAGE]
    return overlay_image.build(parts)[0]


def cl(vcvars, tmp, source, header, libs=""):
    """Builds tests/SOURCE with the kit at /W4 /WX beside a test sat_game.h; the exe's path, or an AssertionError."""
    (tmp / "sat_game.h").write_text("\n".join(header) + "\n", encoding="utf-8")
    inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
    exe = tmp / (source.replace(".c", ".exe"))
    bat = tmp / "b.bat"
    bat.write_text("\r\n".join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / source}" /Fe:"{exe}" {libs}']) + "\r\n")
    r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and exe.is_file(), f"{source} does not build:\n{r.stdout[-3000:]}{r.stderr[-2000:]}"
    return exe


class GenSaturnImage(unittest.TestCase):
    def test_keys_known_to_the_contract(self):
        gen_saturn.check_keys({"image_files": IMAGE, "image_sha256": "0" * 64, "fallback": "on"}, "game.json")

    def test_header_lines(self):
        sha = hashlib.sha256(b"x").hexdigest()
        lines = gen_saturn_image.image_h({"image_files": [["0", "0x06004000"], ["/A/B.BIN", 0x06010000]],
                                          "image_sha256": sha.upper(), "fallback": "on"})
        self.assertEqual(lines, ['#define SAT_IMAGE_FILES {{"/0", 0x06004000u}, {"/A/B.BIN", 0x06010000u}}',
                                 f'#define SAT_IMAGE_SHA256 "{sha}"', '#define SAT_FALLBACK_DEFAULT "on"'])
        self.assertEqual(gen_saturn_image.image_h({"image": "image.bin"}), [])

    def test_bad_values_stop(self):
        for cfg in ({"image_files": IMAGE}, {"image_files": [], "image_sha256": "0" * 64},
                    {"image_files": [["/0"]], "image_sha256": "0" * 64}, {"image_sha256": "0" * 64},
                    {"fallback": "yes"}, {"image_files": [["/0", "zz"]], "image_sha256": "0" * 64}):
            with self.assertRaises(SystemExit, msg=str(cfg)):
                gen_saturn_image.image_h(cfg)


class PlayKeys(unittest.TestCase):
    def test_written_from_the_plan(self):
        import onboard_sat
        with tempfile.TemporaryDirectory() as d:
            gj = pathlib.Path(d) / "game.json"
            gj.write_text(json.dumps({"saturn": {"name": "T", "image": "image.bin"}}), encoding="utf-8")
            plan = {"image": [{"path": "/FIELD/FIELD.BIN", "base": 0x06010000, "first": False},
                              {"path": "/0", "base": 0x06004000, "first": True}]}
            img = expected_image()
            self.assertEqual(onboard_sat.play_keys(gj, plan, img, True), ["image_files", "image_sha256", "fallback"])
            sat = json.loads(gj.read_text(encoding="utf-8"))["saturn"]
            self.assertEqual(sat["image_files"], IMAGE)
            self.assertEqual(sat["image_sha256"], hashlib.sha256(img).hexdigest())
            self.assertEqual((sat["fallback"], sat["image"]), ("on", "image.bin"))
            self.assertEqual(onboard_sat.play_keys(gj, plan, img, True), [])
            self.assertEqual(onboard_sat.play_keys(gj, plan, img, False), ["fallback"])


class PackageRelease(unittest.TestCase):
    def test_exe_and_dlls_only(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            for name in ("game.exe", "SDL3.dll", "image.bin", "Game (USA).cue", "Game (USA) (Track 1).bin",
                         "saturn_backup.bin", "port.log"):
                (d / name).write_bytes(b"x")
            (d / "fonts").mkdir()
            for name in ("Carlito-Bold.ttf", "OFL-Carlito.txt", "notes.md"):
                (d / "fonts" / name).write_bytes(b"x")
            names = package_release.package(d / "game.exe", d / "out.zip")
            with zipfile.ZipFile(d / "out.zip") as z:
                self.assertEqual(sorted(z.namelist()), sorted(names))
            self.assertEqual(sorted(names), ["SDL3.dll", "fonts/Carlito-Bold.ttf", "fonts/OFL-Carlito.txt", "game.exe"])


@unittest.skipIf(game_config.vcvars32() is None, "no 32-bit MSVC here")
class KitC(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="sat_launcher_"))
        (self.tmp / "disc.bin").write_bytes(b"".join(raw(s, i) for i, s in enumerate(disc_sectors())))
        (self.tmp / "game.cue").write_text('FILE "disc.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n',
                                           encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def image(self, cfg, cue=True):
        head = ["#define SAT_IMAGE_BASE 0x06000000u", '#define SAT_GAME_NAME "Test game"'] + gen_saturn_image.image_h(cfg)
        exe = cl(game_config.vcvars32(), self.tmp, "sat_image_test.c", head, "bcrypt.lib")
        out = self.tmp / "image.out"
        out.unlink(missing_ok=True)
        r = subprocess.run([str(exe), str(self.tmp / "game.cue") if cue else "-", str(out)], capture_output=True,
                           text=True, timeout=60)
        return r.stdout, out

    def test_image_from_the_disc(self):
        want = expected_image()
        text, out = self.image({"image_files": IMAGE, "image_sha256": hashlib.sha256(want).hexdigest()})
        self.assertIn("code 0", text)
        self.assertEqual(out.read_bytes(), want)
        self.assertIn("again 0 same", text)

    def test_refusals(self):
        sha = hashlib.sha256(expected_image()).hexdigest()
        self.assertIn("code 5", self.image({"image_files": IMAGE, "image_sha256": "0" * 64})[0])
        self.assertIn("code 5", self.image({"image_files": IMAGE + [["/NOPE.BIN", "0x06020000"]], "image_sha256": sha})[0])
        self.assertIn("code 4", self.image({"image_files": IMAGE, "image_sha256": sha}, cue=False)[0])
        self.assertIn("code -1", self.image({})[0])

    def test_save_dir(self):
        exe = cl(game_config.vcvars32(), self.tmp, "sat_save_dir_test.c", ["/* the generated game header: unused */"])
        start, saves = self.tmp / "start", self.tmp / "data" / "saves"
        start.mkdir()
        (self.tmp / "data").mkdir()
        old = b"SBUP" + bytes(4)
        (start / "saturn_backup.bin").write_bytes(old)
        (start / "saturn_backup_cart.bin").write_bytes(old)

        def run(*args):
            return subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=60, cwd=start).stdout

        text = run("dir", saves)
        self.assertIn(f"where {saves}\\saturn_backup.bin", text)
        self.assertEqual((saves / "saturn_backup.bin").read_bytes(), old)
        self.assertEqual((saves / "saturn_backup_cart.bin").read_bytes(), old)
        self.assertEqual(text.count("from the current folder copied to"), 2, text)
        (saves / "saturn_backup.bin").write_bytes(b"SBUP" + bytes([0, 0, 0, 0]) + b"newer")
        text = run("dir", saves)
        self.assertNotIn("copied", text)
        self.assertTrue((start / "saturn_backup.bin").read_bytes() == old, "the old file is kept")
        self.assertIn(f"where {self.tmp / 'mine.bin'}", run("named", self.tmp / "mine.bin", saves))
        self.assertIn(f"where {self.tmp / 'mine.bin'}", run("dirfirst", saves, self.tmp / "mine.bin"))
        fresh = self.tmp / "data" / "fresh"
        (start / "saturn_backup.bin").unlink()
        (start / "saturn_backup_cart.bin").unlink()
        run("dir", fresh)
        self.assertTrue((fresh / "saturn_backup.bin").is_file(), "the first write makes the saves folder")


if __name__ == "__main__":
    unittest.main()
