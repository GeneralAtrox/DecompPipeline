"""tools/AutoOnboard/onboard_sat.py, the Saturn adapters of tools/AutoOnboard/autoonboard.py, with no Ghidra, no build and no real disc:
1. load_base finds a code file's load address from its own pointers onto function starts, and refuses data;
2. compose: the largest code file first, an overlapping one left out (Saturn Bomberman's /0 under BOMSS/0KRNL.BIN),
   non-overlapping ones kept together (Robo Pit's 0.BIN and MV.BIN);
3. project, disc and extract end to end on a synthetic cue/bin disc shaped like Saturn Bomberman: the plan, the
   workspace from game_setup.py setup --no-first --file, game-data from satdisc.py extract --all; census then stops as
   heavy work; a second extract reuses both;
4. the boot try's starter game.json: entry and its evidence, the kit's stack when IP.BIN sets none, the frame rate from
   the area code; and the kit run's log read into vertical blanks, stubs and the first missing function;
5. symbols: no .sig files is a hard error naming where they come from;
6. gaps: the hardware blocks a function's pool names, against the kit file that models each;
7. work RAM low blocks inside an imaged file (Dragon Force's /0): found at their run address, translated there, and
   code loaded at run time guarded by its own bytes (gen_saturn.py --guard).
Run: python -W error tests/test_onboard_sat.py   (needs git on PATH for 3)
"""
import json
import pathlib
import random
import shutil
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools" / "AutoOnboard"), str(TOOL / "tools"), str(TOOL / "tests"), str(TOOL / "targets" / "saturn_sh2" / "tools"),
                str(TOOL / "port" / "tools")]
import autoonboard as onboard  # noqa: E402
import onboard_sat as SAT  # noqa: E402
import onboard_stages as S  # noqa: E402
from test_onboard_tool import run, verdicts  # noqa: E402
from test_saturn_sh2 import directory, raw, record  # noqa: E402


def code(base, n, extra=()):
    """n SH-2 functions of uneven size at base: a prologue, an rts, and a pool of two calls into the file (plus
    extra). Uneven, as real code is: evenly spaced functions would also fit at a base one stride away."""
    rng = random.Random(base)
    sizes = [rng.randrange(40, 400, 4) for _ in range(n)]  # SH-2 pools are 4-byte aligned
    offs = [sum(sizes[:i]) for i in range(n)]
    out = bytearray()
    for i in range(n):
        words = sizes[i] // 2 - 6
        w = [0x4F22] + [0x0009] * (words - 4) + [0x4F26, 0x000B, 0x0009]
        pool = [base + offs[(i + 1) % n], base + offs[(i * 5 + 3) % n], extra[i] if i < len(extra) else 0]
        out += struct.pack(f">{words}H", *w) + struct.pack(">3I", *pool)
    return bytes(out)


KRNL = code(0x06006000, 300)  # about 64 KB from 0x06006000
LOADER = code(0x06010000, 64, extra=(0x06006000,))  # about 8 KB at 0x06010000: overlaps KRNL, holds its start
FILES = {"/0": LOADER, "/BOMSS/0KRNL.BIN": KRNL, "/DATA.BIN": bytes(range(256)) * 16}
LBA = {"/0": 30, "/BOMSS/0KRNL.BIN": 40, "/DATA.BIN": 80}


def disc(folder, area=b"U"):
    s = [bytes(2048)] * 90
    ip = bytearray(2048)
    ip[0x00:0x10] = b"SEGA SEGASATURN "
    ip[0x20:0x2A] = b"MK-81070  "
    ip[0x2A:0x30] = b"V1.003"
    ip[0x40:0x40 + len(area)] = area
    ip[0x60:0x70] = b"SATURN BOMBERMAN"
    ip[0xF0:0xF4] = (0x06010000).to_bytes(4, "big")
    s[0] = bytes(ip)
    pvd = bytearray(2048)
    pvd[0], pvd[1:6] = 1, b"CD001"
    pvd[156:190] = record(b"\x00", 20, 2048, True)
    s[16] = bytes(pvd)
    s[20] = directory(20, 20, [record(b"0;1", 30, len(LOADER)), record(b"BOMSS", 21, 2048, True),
                               record(b"DATA.BIN;1", 80, len(FILES["/DATA.BIN"]))])
    s[21] = directory(21, 20, [record(b"0KRNL.BIN;1", 40, len(KRNL))])
    for p, lba in LBA.items():
        for k in range(0, len(FILES[p]), 2048):
            s[lba + k // 2048] = FILES[p][k:k + 2048].ljust(2048, b"\x00")
    (folder / "game (Track 01).bin").write_bytes(b"".join(raw(x, i) for i, x in enumerate(s)))
    (folder / "game (Track 02).bin").write_bytes(bytes(2352 * 4))  # an audio track: a .bin that is not the image
    (folder / "game.cue").write_text('FILE "game (Track 01).bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                                     'FILE "game (Track 02).bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n',
                                     encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="onbsat_", dir=TOOL / "tests" / ".work"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


class Placing(unittest.TestCase):
    def test_main_program_by_identity(self):
        """Saturn Bomberman: /XBAND/XBAND.BIN (an add-on, 668 bytes bigger) and /BOMSS/0KRNL.BIN share 06006000; the
        disc names itself BOMSS (BOMSS_CP.TXT), so KRNL is the image and the add-on's files are left out."""
        placed = [dict(path="/XBAND/H2HLIBUS.BIN", base=0x06002F00, size=8850, names=0),
                  dict(path="/XBAND/XBAND.BIN", base=0x06006000, size=787100, names=5),
                  dict(path="/BOMSS/0KRNL.BIN", base=0x06006000, size=786432, names=22),
                  dict(path="/0", base=0x06010000, size=4096, names=24)]
        img, out = SAT.compose(placed, {"BOMSS", "BOMBERMAN"})
        self.assertEqual([f["path"] for f in img], ["/BOMSS/0KRNL.BIN"])
        self.assertEqual(sorted(f["path"] for f in out), ["/0", "/XBAND/H2HLIBUS.BIN", "/XBAND/XBAND.BIN"])
        img, _ = SAT.compose(placed[:3])  # no identity: the one naming most disc files
        self.assertEqual([f["path"] for f in img], ["/XBAND/H2HLIBUS.BIN", "/BOMSS/0KRNL.BIN"])
        # KRNL holds 26 pointers into H2HLIBUS's jump table (it calls 06002f04 at boot): a library it calls goes in
        holds = lambda m, f: 26 if (m["path"], f["path"]) == ("/BOMSS/0KRNL.BIN", "/XBAND/H2HLIBUS.BIN") else 0  # noqa: E731
        img, out = SAT.compose(placed, {"BOMSS", "BOMBERMAN"}, holds)
        self.assertEqual([f["path"] for f in img], ["/XBAND/H2HLIBUS.BIN", "/BOMSS/0KRNL.BIN"])
        self.assertEqual(img[0]["held"], 26)
        self.assertEqual(sorted(f["path"] for f in out), ["/0", "/XBAND/XBAND.BIN"])
        img, _ = SAT.compose(placed, {"BOMSS"}, lambda m, f: SAT.HELD - 1)  # too few pointers: still an add-on's
        self.assertEqual([f["path"] for f in img], ["/BOMSS/0KRNL.BIN"])

    def test_load_base(self):
        base, votes, runner = SAT.load_base(KRNL)
        self.assertEqual(base, 0x06006000)
        self.assertTrue(SAT.accepted(votes, runner), (votes, runner))
        self.assertEqual(SAT.load_base(LOADER)[0], 0x06010000)
        self.assertIsNone(SAT.load_base(bytes(range(256)) * 16))  # no pointers into work RAM high

    def test_compose(self):
        f = lambda p, b, n, first=False: {"path": p, "base": b, "size": n, "first": first}  # noqa: E731
        img, out = SAT.compose([f("/0", 0x06010000, 0x6A460, True), f("/BOMSS/0KRNL.BIN", 0x06006000, 0xC0000)])
        self.assertEqual([x["path"] for x in img], ["/BOMSS/0KRNL.BIN"])
        self.assertIn("overlaps /BOMSS/0KRNL.BIN (06006000-060c6000)", out[0]["why"])
        img, out = SAT.compose([f("/0.BIN", 0x06004000, 0x212C0, True), f("/RP/MV.BIN", 0x0602D000, 0x61DD4)])
        self.assertEqual(([x["path"] for x in img], out), (["/0.BIN", "/RP/MV.BIN"], []))
        self.assertEqual(SAT.entry_of({"image": img, "ip": {}})[0], 0x06004000)  # the first-read address


class EndToEnd(Base):
    def test_disc_extract_then_census_is_heavy(self):
        disc(self.d)
        rc, _ = run("--project", self.d, "--console", "saturn", "--name", "Test Bomber")
        self.assertEqual(rc, 2)
        v = verdicts(self.d)
        self.assertEqual([v[n]["status"] for n in ("project", "disc", "extract")], ["ok"] * 3, v["extract"]["message"])
        self.assertIn("heavy work", v["census"]["message"])
        self.assertEqual(v["disc"]["numbers"]["entry"], "06006000")
        self.assertIn("holds this address", v["disc"]["message"])
        plan = json.loads((self.d / S.STATE_DIR / SAT.PLAN).read_text(encoding="utf-8"))
        self.assertEqual([(f["path"], f["base"]) for f in plan["image"]], [("/BOMSS/0KRNL.BIN", 0x06006000)])
        self.assertEqual([f["path"] for f in plan["left_out"]], ["/0"])
        self.assertTrue(plan["image"] and next(iter(plan["first_holds"].values())))
        cfg = json.loads((self.d / "decomp" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["image_gaps"], cfg["game_ranges"]), (["06000000-06006000"], [["06006000", f"{0x06006000 + len(KRNL):08x}"]]))
        self.assertEqual((self.d / "decomp" / "binary" / "image.bin").read_bytes()[0x6000:], KRNL)
        data = self.d / "game-data"
        self.assertEqual((data / "BOMSS" / "0KRNL.BIN").read_bytes(), KRNL)
        self.assertEqual(v["extract"]["numbers"]["asset files"], 3)
        rc, _ = run("--project", self.d, "--only", "extract")
        self.assertEqual(rc, 0)
        self.assertEqual(verdicts(self.d)["extract"]["message"].split(";")[0], "reused workspace, game-data")


class BootPieces(Base):
    def plan(self, area="U", first=False, stack=0, whole=False):
        ip = {"first_read_file": "/0", "stack_master": f"0x{stack:08x}", "stack_slave": "0x00000000", "area": area,
              "product": "MK-81070"}
        img = [{"path": "/BOMSS/0KRNL.BIN", "base": 0x06006000, "size": 0xC0000, "first": first}]
        (self.d / S.STATE_DIR).mkdir(parents=True, exist_ok=True)
        (self.d / S.STATE_DIR / SAT.PLAN).write_text(json.dumps({"ip": ip, "image": img,
                                                                 "first_holds": {"06006000": True}}), encoding="utf-8")
        a = onboard.argparse.Namespace(disc=None, here=False, host=None, dry_run=False)
        out = SAT.starter(onboard.Ctx(a, self.d, "saturn", "Test Bomber"))
        return out if whole else out["saturn"]

    def test_starter(self):
        g = self.plan()
        self.assertEqual((g["entry"], g["entry_sp"], g["frame_hz"], g["image_base"], g["image"]),
                         ("0x06006000", "0x060ffc00", 60, "0x06000000", "image.bin"))
        self.assertNotIn("slave_sp", g)
        self.assertEqual(g["boot_dir"], "/BOMSS")  # the loader /0 left the file system there (sat_cd.c's hand-over)
        self.assertNotIn("boot_dir", self.plan(first=True))  # the BIOS starts the first-read file: the root, no hand-over
        # the game's own sound driver runs on the kit's 68000: without it SBL's sound commands are never taken and the
        # game waits on its mailbox for ever (Saturn Bomberman round 5: 0609e434 polling the sound-RAM command slot)
        self.assertIs(g["sound_cpu"], True)
        # SBL's SMPC handler takes the pad only from the hardware's INTBACK report (sat_smpc.c): every new game turns it on
        self.assertEqual(g["smpc_intback"], "hardware")
        self.assertEqual(self.plan(area="E", stack=0x06002000)["entry_sp"], "0x06002000")
        self.assertEqual(self.plan(area="E")["frame_hz"], 50)
        self.assertEqual(self.plan(area="JUE")["frame_hz"], 60)
        sys.path.insert(0, str(TOOL / "port" / "tools"))
        import game_config
        self.assertFalse(set(g) - set(game_config.SATURN_KEYS))  # gen_saturn.py stops on an unknown key

    def test_log_facts(self):
        log = ("port: image image.bin (786432 bytes) at 06000000, entry 06006000, 4893 functions\n"
               "STUB #1 (master): call to 0600a000 has no translated function (returns 0); the guest holds 0 1 2 3\n"
               "STUB #2 (master): call to 0600b000 has no translated function (returns 0); the guest holds 0 0 0 0\n"
               "stubs hit: 2\nexit: watchdog (3) after 1200 vertical blanks\n")
        why, vb, stubs, first, table = SAT.log_facts(log)
        self.assertEqual((why, vb, stubs, table), ("watchdog", 1200, 2, 4893))
        self.assertTrue(first.startswith("STUB #1 (master): call to 0600a000"))
        self.assertEqual(SAT.log_facts(""), (None, 0, 0, None, None))


class Symbols(Base):
    def test_no_sig_files(self):
        (self.d / "reference").mkdir()
        a = onboard.argparse.Namespace(disc=None, here=True, host=None, dry_run=False)
        ctx = onboard.Ctx(a, self.d, "saturn", "X")
        ctx.verdicts = {"census": {"status": "ok"}}
        with self.assertRaises(S.StageError) as e:
            SAT.Symbols().run(ctx)
        self.assertIn("no .sig files", str(e.exception))
        self.assertIn("saturnsigs100.zip", e.exception.supply)


class Gaps(Base):
    def test_kit_section(self):
        ws = self.d / "decomp"
        (ws / "binary").mkdir(parents=True)
        (ws / "state").mkdir()
        body = struct.pack(">4H", 0x4F22, 0x0009, 0x4F26, 0x000B) + struct.pack(">3I", 0x25F80000, 0x20100063,
                                                                                0x02400000)
        (ws / "binary" / "image.bin").write_bytes(bytes(0x6000) + body)
        (ws / "project.json").write_text(json.dumps({"binary": "binary/image.bin"}), encoding="utf-8")
        (ws / "state" / "function_bodies.txt").write_text("06006000 06006000 06006008\n", encoding="utf-8")
        out, n = SAT.kit_section(ws, ["06006000"])
        text = "\n".join(out)
        self.assertIn("- VDP2 registers: 1 address(es); kit: sat_vdp2.c", text)
        self.assertIn("- SMPC: 1 address(es); kit: sat_smpc.c", text)
        self.assertIn("- A-bus cartridge and CS2: 1 address(es); kit: no file models it", text)
        self.assertEqual(n, {"kit gaps": 1, "hardware blocks": 3, "fallback seeds": 0})


class Starts(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_entry_not_at_the_image_start_is_marked(self):
        """Saturn Bomberman with H2HLIBUS at 06002f00: the image starts below the entry 06006000, whose first words are
        a jump stub, so analysis made no function there; the extract marks it (catalog_overrides.entry) and the census
        builds the catalog again when it lacks one."""
        plan = {"ip": {"first_read_file": "/0"}, "first_holds": {},
                "image": [{"path": "/XBAND/H2HLIBUS.BIN", "base": 0x06002F00, "size": 8850, "first": False},
                          {"path": "/BOMSS/0KRNL.BIN", "base": 0x06006000, "size": 786432, "first": False}]}
        self.assertEqual(SAT.starts_of(plan), ["06002f00", "06006000"])
        ws = self.d / "decomp"
        (ws / "state").mkdir(parents=True)
        (ws / "project.json").write_text(json.dumps({"catalog_overrides": {"entry": ["0x060061c4"]}}), encoding="utf-8")
        self.assertEqual(SAT.mark_starts(ws, plan), ["06002f00", "06006000"])
        self.assertEqual(SAT.mark_starts(ws, plan), [])  # once
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["catalog_overrides"]["entry"], ["06002f00", "06006000", "060061c4"])
        self.assertEqual(SAT.missing_starts(ws), [])  # no catalog yet: the census makes one with them
        cat = {"functions": [{"addr": "06002f00"}, {"addr": "060061c4"}, {"addr": "06006174"}]}
        (ws / "state" / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
        self.assertEqual(SAT.missing_starts(ws), ["06006000"])
        cat["functions"].append({"addr": "06006000"})
        (ws / "state" / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
        self.assertEqual(SAT.missing_starts(ws), [])

    def test_sdl3_reused_never_installed_again(self):
        """A fresh build folder takes an installed SDL3 (no vcpkg install, which waited 20 minutes on another card's
        vcpkg root lock): from a moved-aside build, then from the project's cache."""
        old = self.d / "old-krnl-only" / "port" / "build" / "onboard" / "vcpkg_installed" / "x86-windows" / "lib"
        old.mkdir(parents=True)
        (old / "SDL3.lib").write_bytes(b"lib")
        exe = self.d / "port" / "build" / "onboard"
        self.assertEqual(SAT.sdl3_ready(self.d, exe), old.parent.parent)
        self.assertTrue((exe / "vcpkg_installed" / "x86-windows" / "lib" / "SDL3.lib").is_file())
        SAT.sdl3_keep(self.d, exe)
        shutil.rmtree(self.d / "old-krnl-only")
        fresh = self.d / "port" / "build" / "again"
        self.assertEqual(SAT.sdl3_ready(self.d, fresh), self.d / S.STATE_DIR / "sdl3" / "vcpkg_installed")
        self.assertTrue((fresh / "vcpkg_installed" / "x86-windows" / "lib" / "SDL3.lib").is_file())
        self.assertIsNone(SAT.sdl3_ready(self.d / "none", self.d / "none" / "x"))  # none anywhere: build_saturn installs


class Launcher(unittest.TestCase):
    def test_start_game_and_catalogue_entry(self):
        """The project stage writes Start Game.bat in the Core shape (--data-directory data, no --log, start then
        timeout) with Saturn's exe and arguments, and the launcher's catalogue entry; a person's edits are kept."""
        d = pathlib.Path(tempfile.mkdtemp()) / "SaturnBomberman"
        d.mkdir()
        self.addCleanup(shutil.rmtree, d.parent, True)
        self.assertEqual(SAT.launcher_files(d, "Saturn Bomberman"),
                         ["Start Game.bat", f"{S.STATE_DIR}/{SAT.CATALOGUE_ENTRY}"])
        raw = (d / "Start Game.bat").read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))  # CRLF only: cmd.exe
        bat = raw.decode("ascii")
        line = next(ln for ln in bat.splitlines() if ln.startswith('start "" '))
        self.assertIn(r'"%~dp0port\build\onboard\game.exe"', line)
        self.assertIn(r'--image "%~dp0port\build\onboard\image.bin"', line)
        self.assertIn('--disc "%CUE%"', line)
        self.assertIn(r'--data-directory "%~dp0data"', line)
        self.assertNotIn("--log", line)
        self.assertTrue(line.endswith(" %*"), line)  # a check passes --headless ... through
        self.assertIn('cd /d "%~dp0"', bat)
        self.assertIn("timeout /t 6 >nul", bat)
        self.assertIn('for %%c in ("%~dp0*.cue")', bat)
        ent = json.loads((d / S.STATE_DIR / SAT.CATALOGUE_ENTRY).read_text(encoding="utf-8"))
        self.assertEqual({k: ent[k] for k in ("id", "title", "platform", "short", "folder", "logs", "localExecutable")},
                         {"id": "saturnbomberman", "title": "Saturn Bomberman", "platform": "Saturn", "short": "SB",
                          "folder": "SaturnBomberman", "logs": "logs", "localExecutable": "Start Game.bat"})
        self.assertEqual((ent["repository"], ent["executable"]), ("SaturnBomberman_Saturn_NativePort", "game.exe"))
        self.assertNotIn("port", ent)  # only after check-port.ps1 passes on a published release
        (d / "Start Game.bat").write_text("mine", encoding="utf-8")
        self.assertEqual(SAT.launcher_files(d, "Saturn Bomberman"), [])
        self.assertEqual((d / "Start Game.bat").read_text(encoding="utf-8"), "mine")

    def test_boot_writes_them_when_the_project_stage_ran_earlier(self):
        """Saturn Bomberman's project stage ran before launcher_files existed and later runs started past it, so the game
        never got a Start Game.bat. The boot stage writes the missing ones before anything else it does."""
        import types
        d = pathlib.Path(tempfile.mkdtemp()) / "Game"
        (d / S.STATE_DIR).mkdir(parents=True)
        self.addCleanup(shutil.rmtree, d.parent, True)
        ctx = types.SimpleNamespace(project=d, name="Saturn Bomberman", verdicts={}, console="saturn")
        with self.assertRaises(Exception):  # no disc plan or workspace here: boot stops after the launcher files
            SAT.Boot().run(ctx)
        self.assertTrue((d / "Start Game.bat").is_file())
        self.assertTrue((d / S.STATE_DIR / SAT.CATALOGUE_ENTRY).is_file())

    def test_root_bat_in_the_games_own_folder(self):
        """The user: Start Game.bat belongs at the root of the game's folder, not hidden in .work\\auto. A project there
        gets a root bat calling its own by a relative path; a stand-in naming onboard.py is replaced, a person's own root
        bat is kept, and the catalogue entry names the game's folder, not "auto"."""
        g = pathlib.Path(tempfile.mkdtemp()) / "SaturnBomberman"
        self.addCleanup(shutil.rmtree, g.parent, True)
        (g / "GameFiles").mkdir(parents=True)
        (g / "LOCATIONS.md").write_text("x", encoding="utf-8")
        d = g / ".work" / "auto"
        d.mkdir(parents=True)
        (g / "Start Game.bat").write_text("rem stand-in: tools/onboard.py will write this\n", encoding="ascii")
        self.assertIn(str(g / "Start Game.bat"), SAT.launcher_files(d, "Saturn Bomberman"))
        raw = (g / "Start Game.bat").read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn('call "%~dp0.work\\auto\\Start Game.bat"', raw.decode("ascii"))
        self.assertTrue((d / "Start Game.bat").is_file())
        ent = json.loads((d / S.STATE_DIR / SAT.CATALOGUE_ENTRY).read_text(encoding="utf-8"))
        self.assertEqual(ent["folder"], "SaturnBomberman")
        self.assertEqual(SAT.launcher_files(d, "Saturn Bomberman"), [])  # nothing rewritten the second time
        (g / "Start Game.bat").write_text("mine", encoding="ascii")
        SAT.launcher_files(d, "Saturn Bomberman")
        self.assertEqual((g / "Start Game.bat").read_text(encoding="ascii"), "mine")
        # a bat written before the rename names the old entry point: still ours, so it is updated
        (g / "Start Game.bat").write_text("rem Game: written by tools/onboard.py (project stage).\n", encoding="ascii")
        self.assertIn(str(g / "Start Game.bat"), SAT.launcher_files(d, "Saturn Bomberman"))
        self.assertIn(b"written by AutoOnboard (tools/AutoOnboard/autoonboard.py)", (g / "Start Game.bat").read_bytes())
        self.assertIsNone(SAT.game_root(g))  # a project that is the game's folder: its own bat is the root one

    def test_project_stage_is_the_saturn_one(self):
        import onboard_stages
        self.assertIsInstance(onboard_stages.BY_NAME["project"].adapters["saturn"], SAT.Project)


class StartResponds(unittest.TestCase):
    """The boot stage's "start responds" check: two fixed-clock runs, idle and with Start pressed, compared frame by frame."""

    @staticmethod
    def png(v, w=4, h=3):
        import zlib

        def ch(k, d):
            return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d))
        rows = b"".join(b"\x00" + bytes([v]) * (w * 3) for _ in range(h))
        return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + ch(b"IDAT", zlib.compress(rows)) + ch(b"IEND", b""))

    def test_identical_frames_do_not_respond(self):
        f = {2000: self.png(9), 2600: self.png(9)}
        self.assertEqual(SAT.start_responds(f, dict(f)), (False, None))

    def test_a_divergence_after_a_press_responds_with_that_press(self):
        idle = {2000: self.png(9), 2600: self.png(9), 3200: self.png(9)}
        pressed = {2000: self.png(9), 2600: self.png(7), 3200: self.png(5)}
        self.assertEqual(SAT.start_responds(idle, pressed), (True, 2600))

    def test_a_black_or_missing_frame_is_not_a_response(self):
        idle = {2000: self.png(9), 2600: self.png(9)}
        pressed = {2000: self.png(0), 2600: None}
        self.assertEqual(SAT.start_responds(idle, pressed), (False, None))
        self.assertTrue(SAT.png_is_black(self.png(0)))
        self.assertFalse(SAT.png_is_black(self.png(1)))


class BootTranslation(unittest.TestCase):
    def test_new_catalog_translates_again(self):
        """Boot reuses its translation only for the same function_bodies.txt: after a census added 06006000 the old C
        lacked it and the entry call was a stub."""
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        tx, bodies = d / "tx", d / "function_bodies.txt"
        (tx / "c").mkdir(parents=True)
        (tx / "c" / "f.c").write_text("x", encoding="utf-8")
        bodies.write_text("06006174 06006180\n", encoding="utf-8")
        self.assertFalse(SAT.tx_current(tx, bodies))  # C without a stamp: an older boot's
        SAT.tx_stamp(tx, bodies)
        self.assertTrue(SAT.tx_current(tx, bodies))
        bodies.write_text("06006000 06006006\n06006174 06006180\n", encoding="utf-8")
        self.assertFalse(SAT.tx_current(tx, bodies))
        tr = d / "translator"
        (tr / "sh2translate").mkdir(parents=True)
        (tr / "sh2translate" / "emit.py").write_text("v1", encoding="utf-8")
        SAT.tx_stamp(tx, bodies, tr)
        self.assertTrue(SAT.tx_current(tx, bodies, tr))
        (tr / "sh2translate" / "emit.py").write_text("v2", encoding="utf-8")  # a translator fix: every C file may change
        self.assertFalse(SAT.tx_current(tx, bodies, tr))


    def test_translation_takes_code_the_list_lacks(self):
        """Boot asks overlay.py for the census's list plus the code it reaches that the list lacks (--pointers): Saturn
        Bomberman's handlers in KRNL's table at 060b5364 (060342f8 first) were STUBs. Another flag set translates
        again."""
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        a = SAT.tx_args("img.bin", "function_bodies.txt", "tx")
        self.assertEqual(a[a.index("--bodies") + 1], "function_bodies.txt")
        self.assertIn("--pointers", a)
        self.assertEqual(a[a.index("--range") + 1:a.index("--range") + 3], ["0x06000000", "0x06100000"])
        tx, bodies = d / "tx", d / "function_bodies.txt"
        (tx / "c").mkdir(parents=True)
        (tx / "c" / "f.c").write_text("x", encoding="utf-8")
        bodies.write_text("06006174 06006174 06006180\n", encoding="utf-8")
        SAT.tx_stamp(tx, bodies, flags=("--simplify", SAT.SIMPLIFY))  # an older boot's flags: no --pointers
        self.assertFalse(SAT.tx_current(tx, bodies))
        SAT.tx_stamp(tx, bodies)
        self.assertTrue(SAT.tx_current(tx, bodies))


class LowRamBlocks(unittest.TestCase):
    """Dragon Force's /0 (06010000) carries, at file offset 0xaffa4, code that runs at 00210000 (copied there at boot);
    the plan imaged only the file at 06010000, so the first call into the block (00210000) was a STUB. The disc stage
    finds the block and its run address from the file's own pointers; boot translates it there; each function of code
    loaded at run time checks its own bytes first (gen_saturn.py --guard), so it never runs over another overlay."""

    def file(self):
        high = code(0x06010000, 40, extra=(0x00210000,))  # the program at its base calls into the block
        pad = bytes(range(256)) * 8  # data between them, as /0's graphics
        off = len(high) + len(pad)
        block = code(0x00210000, 80, extra=(0x06010000,))  # runs at 00210000, calls back into the program
        return high + pad + block, off, len(block)

    def test_block_found_at_its_run_address(self):
        data, off, size = self.file()
        f = {"path": "/0", "base": 0x06010000, "size": len(data), "first": True}
        segs = SAT.segments([f], {"/0": data})
        self.assertEqual(len(segs), 1)
        s = segs[0]
        self.assertEqual((s["offset"], s["size"], s["base"]), (off, size, 0x00210000))
        self.assertIn("00210000", s["entries"])
        self.assertIn("06010000", s["calls"])
        plan = {"image": [f], "ip": {"first_read_file": "/0"}, "segments": segs}
        self.assertIn("06010000", SAT.starts_of(plan))
        self.assertEqual(SAT.segments([f], {"/0": data[:off]}), [])  # the program alone: no block

    def test_boot_translates_the_block_and_guards_overlays(self):
        a = SAT.seg_args("seg.bin", 0x00210000, 0x1000, "ents.txt", "tx")
        self.assertEqual(a[a.index("--load") + 1], "0x00210000")
        self.assertEqual(a[a.index("--range") + 1:a.index("--range") + 3], ["0x00210000", "0x00211000"])
        img = [{"path": "/0", "base": 0x06010000, "size": 0x1000}]
        self.assertTrue(SAT.overlaid({"image": img, "left_out": [{"path": "/GAME/DF.BIN", "base": 0x06010000,
                                                                     "size": 0x800}]}))
        self.assertFalse(SAT.overlaid({"image": img, "left_out": []}))

    def test_guarded_function_checks_its_bytes(self):
        import gen_saturn
        data = bytes.fromhex("4f22e400d3712341d2712241d1712142") + bytes(16)
        text = "u32 fn_00210000(u32 a0, u32 a1, u32 a2, u32 a3) {\n    return 0u;\n}\n"
        got = gen_saturn.guard(text, 0x00210000, [(0x00210000, data)])
        self.assertIn("{ if (!sat_resident(0x00210000u, 0x4f22e400u, 0xd3712341u, 0xd2712241u, 0xd1712142u)) "
                      "return sat_absent(0x00210000u);", got)
        self.assertEqual(gen_saturn.guard(text, 0x00210000, [(0x06010000, data)]), text)  # outside every guarded file


class Overlays(unittest.TestCase):
    """Dragon Force's battle (DF.BIN) and its FIELD0..7.BIN load over /0 at 06010000 and READY0..7.BIN into work RAM low;
    none had a translation, so the battle ran only in the SH-2 interpreter, and the interpreter's branches to /0's
    translations nested until "call depth over 400" (the user's run, 2026-11-17). Boot now translates each such file at
    its own address and gen_saturn.py --overlay gives each its own names, guarded with its own bytes, beside /0's."""

    def plan(self):
        img = [{"path": "/0", "base": 0x06010000, "size": 0x4000, "first": True}]
        return {"image": img, "left_out": [{"path": "/GAME/DF.BIN", "base": 0x06010000, "size": 0x2000},
                                           {"path": "/X/OTHER.BIN", "base": 0x06080000, "size": 0x100}],
                "low_ram": ["/GAME/READY6.BIN"]}

    def test_overlay_files_found_with_their_entries(self):
        df, ready = code(0x06010000, 30), code(0x00290000, 60)
        got = SAT.overlay_files(self.plan(), {"/GAME/DF.BIN": df, "/GAME/READY6.BIN": ready,
                                               "/X/OTHER.BIN": code(0x06080000, 4)})
        self.assertEqual([(o["path"], o["tag"], o["base"], o["size"]) for o in got],
                         [("/GAME/READY6.BIN", "READY6", 0x00290000, len(ready)), ("/GAME/DF.BIN", "DF", 0x06010000, len(df))])
        df_ents = got[1]["entries"]
        self.assertEqual(df_ents[0], "06010000")
        self.assertGreater(len(df_ents), 10)  # its own pointers on its own function starts
        self.assertTrue(all(0x06010000 <= int(a, 16) < 0x06010000 + len(df) for a in df_ents))
        self.assertEqual(SAT.overlay_files(self.plan(), {}), [])  # no files extracted: nothing to translate

    def test_gen_saturn_keeps_one_translation_per_overlay(self):
        import gen_saturn
        fn = "u32 fn_06010000(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    u32 r0 = 0;\n    return %su;\n}\n"
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            main, ovl, out = tmp / "main", tmp / "df", tmp / "gen"
            main.mkdir(), ovl.mkdir()
            (main / "06010000.c").write_text(fn % "1", encoding="utf-8")
            (ovl / "06010000.c").write_text(fn % "2", encoding="utf-8")
            (tmp / "0.bin").write_bytes(bytes.fromhex("4f22e4011111111122222222333333334444"))
            (tmp / "df.bin").write_bytes(bytes.fromhex("2fe6e40255555555666666667777777788"))
            (tmp / "game.json").write_text(json.dumps({"saturn": {"name": "T", "image": "image.bin", "image_base": "0x06000000",
                                                                  "entry": "0x06010000", "entry_sp": "0x06004000"}}), encoding="utf-8")
            gen_saturn.main(["--game", str(tmp / "game.json"), "--tx", str(main), "--out", str(out),
                             "--guard", f"{tmp / '0.bin'}@06010000", "--overlay", f"DF={ovl}@{tmp / 'df.bin'}@06010000"])
            table = (out / "sat_table.c").read_text(encoding="utf-8")
            units = "".join(f.read_text(encoding="utf-8") for f in out.glob("txchunk_*.c"))
        self.assertIn("{0x06010000u, fn_06010000, ", table)
        self.assertIn("{0x06010000u, fn_06010000_DF, ", table)
        self.assertLess(table.index("fn_06010000, "), table.index("fn_06010000_DF, "))  # the image's first
        self.assertIn("{0x4f22e401u, 0x11111111u, 0x22222222u, 0x33333333u}", table)
        self.assertIn("{0x2fe6e402u, 0x55555555u, 0x66666666u, 0x77777777u}", table)
        self.assertIn("u32 fn_06010000_DF(", units)
        self.assertIn("if (!sat_resident(0x06010000u, 0x2fe6e402u, 0x55555555u, 0x66666666u, 0x77777777u)) return "
                      "sat_absent(0x06010000u);", units)
        self.assertEqual(units.count("u32 fn_06010000("), 1)


class SaturnController(Base):
    """A new Saturn game gets the Saturn pad, not the Dreamcast's: game.json "controller": "saturn" (the layout in port/saturn/sat_input.c;
    the name needs a base hook, not made) and the keyboard's "keys" (the user, 2026-11-16: Saturn
    Bomberman's port.log said "pad: controller dreamcast, pictures on, showing dreamcast")."""

    def starter(self):
        return BootPieces.plan(self)  # a Saturn game's starter "saturn" object

    def kit_actions(self):
        """{action: the host button it reads} of port/saturn/sat_input.c's Saturn table."""
        import re
        text = (TOOL / "port" / "saturn" / "sat_input.c").read_text(encoding="utf-8")
        block = text.split("#ifdef SAT_CONTROLLER_SATURN")[1].split("#else")[0]
        return dict(re.findall(r'\{"(\w+)", "(\w+)"\}', block))

    def test_starter_names_the_saturn_pad(self):
        g = self.starter()
        self.assertEqual(g["controller"], "saturn")
        import game_config
        self.assertIn("controller", game_config.SATURN_KEYS)  # port/CONTRACT.md lists it, so gen_saturn takes it

    def test_the_saturn_layout_is_the_documented_one(self):
        acts = self.kit_actions()
        self.assertEqual(acts,
                         {"A": "A", "B": "B", "C": "RB", "X": "X", "Y": "Y", "Z": "LB", "L": "LT", "R": "RT", "START": "START",
                          "UP": "UP", "DOWN": "DOWN", "LEFT": "LEFT", "RIGHT": "RIGHT"})
        # the extra modern buttons start unassigned: no action takes a stick click, Back, Guide or the right stick
        taken = set(acts.values())
        self.assertFalse(taken & {"L3", "R3", "BACK", "GUIDE", "RSUP", "RSDOWN", "RSLEFT", "RSRIGHT"})

    def test_the_keyboard_keeps_each_action(self):
        """Every key reaches the action it always did, through the new layout (old kit: E R = L R, W Space = C, Q LShift = Z)."""
        by_source = {s: n for n, s in self.kit_actions().items()}
        keys = self.starter_object()["keys"]
        for key, action in {"E": "L", "R": "R", "W": "C", "SPACE": "C", "Q": "Z", "LSHIFT": "Z", "Z": "A", "X": "B", "A": "X",
                            "S": "Y", "RETURN": "START", "KP_ENTER": "START"}.items():
            self.assertEqual(by_source[keys[key]], action, key)

    def starter_object(self):
        return BootPieces.plan(self, whole=True)

    def test_an_older_project_gains_the_controller_and_the_keys(self):
        gj = self.d / "game.json"
        gj.write_text(json.dumps({"saturn": {"name": "Mine", "entry": "0x06006000"}}), encoding="utf-8")
        start = self.starter_object()
        added = SAT.add_missing_keys(gj, start)
        self.assertCountEqual(added, [k for k in start["saturn"] if k not in ("name", "entry")] + ["keys"])
        self.assertIn("controller", added)
        cfg = json.loads(gj.read_text(encoding="utf-8"))
        self.assertEqual((cfg["saturn"]["controller"], cfg["keys"]["RETURN"]), ("saturn", "START"))
        self.assertEqual(cfg["saturn"]["entry"], "0x06006000")  # what was there stays
        gj.write_text(json.dumps({"saturn": {}, "keys": {"W": "RT"}}), encoding="utf-8")  # a layout the game set stays
        SAT.add_missing_keys(gj, self.starter_object())
        self.assertEqual(json.loads(gj.read_text(encoding="utf-8"))["keys"], {"W": "RT"})


class MenuProbe(Base):
    """The boot stage's menu probe finds the game's own confirm and cancel from frames (onboard_sat.py Prober), writes
    them to game.json "saturn", and gen_saturn.py puts them in sat_game.h, where sat_input.c puts them on host A and B.
    Saturn Bomberman (build box A, 2026-11-17): on GAME MODE / OPTION, A and C move on and B goes elsewhere; one level in,
    B returns to the menu's picture."""

    @staticmethod
    def frames(**at):
        return {int(k[1:]): v for k, v in at.items()}

    def test_menu_buttons_from_frames(self):
        f = self.frames
        idle = f(v690=b"menu", v790=b"m1", v890=b"m2")
        runs = {b: idle for b in SAT.PAD_BUTTONS}
        runs = dict(runs, A=f(v690=b"menu", v790=b"next1", v890=b"next2"), C=f(v690=b"menu", v790=b"next1", v890=b"next2"),
                    B=f(v690=b"menu", v790=b"title", v890=b"title"))
        moved, backed = SAT.menu_buttons(idle, runs, 700, {b"title"}, is_black=lambda x: False)
        self.assertEqual((moved, backed), (["A", "C"], ["B"]))
        self.assertEqual(SAT.pick(moved, SAT.CONFIRM_ORDER), "C")
        self.assertEqual(SAT.pick(backed, SAT.CANCEL_ORDER, "C"), "B")
        # a frame before the press does not count, nor a black or missing one
        runs["X"] = f(v690=b"other", v790=b"")
        self.assertNotIn("X", SAT.menu_buttons(idle, runs, 700, set(), is_black=lambda x: False)[0])
        runs["Y"] = f(v790=b"dark")
        self.assertNotIn("Y", SAT.menu_buttons(idle, runs, 700, set(), is_black=lambda x: x == b"dark")[0])

    def test_host_key_check_compares_frames_after_the_press(self):
        a = self.frames(v690=b"x", v790=b"n1", v890=b"n2")
        self.assertTrue(SAT.same_after(a, self.frames(v690=b"other", v790=b"n1", v890=b"n2"), 700))
        self.assertFalse(SAT.same_after(a, self.frames(v790=b"n1", v890=b"no"), 700))
        self.assertFalse(SAT.same_after({}, {}, 700))

    def test_probe_input(self):
        self.assertEqual(SAT.probe_input([2000, 2600], [("C", 3300), ("B", 4000)]),
                         "START@2000+10,START@2600+10,C@3300+10,B@4000+10")

    def test_set_buttons_writes_once_and_keeps_a_persons_choice(self):
        gj = self.d / "game.json"
        gj.write_text(json.dumps({"saturn": {"name": "G"}, "keys": {"Z": "A"}}), encoding="utf-8")
        self.assertEqual(SAT.set_buttons(gj, {"confirm": "C", "cancel": "B"}), ["cancel", "confirm"])
        cfg = json.loads(gj.read_text(encoding="utf-8"))
        self.assertEqual((cfg["saturn"]["confirm"], cfg["saturn"]["cancel"], cfg["keys"]), ("C", "B", {"Z": "A"}))
        gj.write_text(json.dumps({"saturn": {"confirm": "A"}}), encoding="utf-8")
        self.assertEqual(SAT.set_buttons(gj, {"confirm": "C", "cancel": None}), [])
        self.assertEqual(json.loads(gj.read_text(encoding="utf-8"))["saturn"], {"confirm": "A"})

    def test_gen_saturn_writes_confirm_and_cancel(self):
        import gen_saturn
        import game_config
        self.assertIn("confirm", game_config.SATURN_KEYS)
        self.assertIn("cancel", game_config.SATURN_KEYS)
        self.assertEqual(gen_saturn.pad_h({"controller": "saturn", "confirm": "C", "cancel": "B"}),
                         ["#define SAT_CONTROLLER_SATURN 1", '#define SAT_CONFIRM "C"', '#define SAT_CANCEL "B"'])
        self.assertEqual(gen_saturn.pad_h({"controller": "saturn"}), ["#define SAT_CONTROLLER_SATURN 1"])
        self.assertEqual(gen_saturn.pad_h({}), [])
        for bad in ({"confirm": "START"}, {"cancel": "b"}, {"confirm": "B", "cancel": "B"}):
            with self.assertRaises(SystemExit):
                gen_saturn.pad_h(bad)


class GameJson(unittest.TestCase):
    def test_older_game_json_gains_new_starter_keys(self):
        """Saturn Bomberman's port/game.json predates boot_dir; boot adds the starter's missing keys and keeps the rest."""
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        gj = d / "game.json"
        gj.write_text(json.dumps({"saturn": {"name": "Mine", "entry": "0x06006000"}, "other": 1}), encoding="utf-8")
        start = {"saturn": {"name": "Saturn Bomberman", "entry": "0x06006000", "boot_dir": "/BOMSS"}}
        self.assertEqual(SAT.add_missing_keys(gj, start), ["boot_dir"])
        cfg = json.loads(gj.read_text(encoding="utf-8"))
        self.assertEqual(cfg, {"saturn": {"name": "Mine", "entry": "0x06006000", "boot_dir": "/BOMSS"}, "other": 1})
        self.assertEqual(SAT.add_missing_keys(gj, start), [])


class HeadlessFloor(unittest.TestCase):
    def test_a_run_that_stalls_early_fails(self):
        """Saturn Bomberman at b6130d3af stopped at vertical blank 6 and the boot stage passed its headless run."""
        self.assertFalse(SAT.ran_enough("watchdog", 6))
        self.assertFalse(SAT.ran_enough("watchdog", 0))
        self.assertFalse(SAT.ran_enough(None, 5000))
        self.assertTrue(SAT.ran_enough("watchdog", 1590))  # a busy build box A, 2026-11-16


if __name__ == "__main__":
    unittest.main()
