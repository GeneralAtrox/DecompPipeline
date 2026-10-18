"""The Dreamcast port kit (port/): game.json loading, the generated per-game files, stub replacement by defined
names, the kit holding no game material, and (only where Visual Studio's 32-bit tools are found) the runtime
compiling at /W4 /WX. Synthetic data; no Ghidra, no game workspace.
Run: python -W error tests/test_port_kit.py (also from tests/test_tooling.py)
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))

import game_config  # noqa: E402
import gen_port  # noqa: E402

GAME = {"name": "Fixture Game", "decomp": "../decomp", "entry": {"name": "Fx_Main", "addr": "8c011000"},
        "image_base": "8c010000", "sdk": ["sy_heap"], "sources": ["src/own.c"], "hardware": {"0x8C012000": "Fx_Hw"},
        "console": {"country": "0x32", "language": 1, "broadcast": "0x31"}}
# Game material the kit must never hold: guest addresses in RAM (P1 0x8c..., P0 0x0c...; only the bases are kept),
# the games' names and the entry functions they configure.
GUEST_CODE = re.compile(r"(?<![0-9a-fA-F])[08]c[0-9a-f]{6}(?![0-9a-fA-F])", re.I)
ALLOWED_ADDRS = {"8c000000", "0c000000"}  # guest RAM and its P0 mirror
GAME_WORDS = ("Toy Commander", "ToyCommander", "1GUTH", "Spawn", "Game_Main")
CODE = {".c", ".h", ".cpp", ".hpp", ".hlsl", ".py", ".txt"}


def kit_files(kit=KIT):
    return sorted(p for p in kit.rglob("*") if p.is_file() and (p.suffix in CODE or p.name == "CMakeLists.txt")
                  and "__pycache__" not in p.parts)


def game_material(files, kit=KIT):
    """[(relative path, line no, what)] for each guest code address, game word or runtime game.h include."""
    hits = []
    for p in files:
        rel = p.relative_to(kit).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in GUEST_CODE.finditer(line):
                if m.group(0).lower() not in ALLOWED_ADDRS:
                    hits.append((rel, i, m.group(0)))
            hits += [(rel, i, w) for w in GAME_WORDS if w in line]
            if rel.startswith("runtime/") and re.match(r'\s*#\s*include\s+"game\.h"', line):
                hits.append((rel, i, "game.h"))
    return hits


class GameConfig(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "src").mkdir()
        (self.dir / "src" / "own.c").write_text("void Fx_Hw(void) {}\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, **over):
        (self.dir / "game.json").write_text(json.dumps(dict(GAME, **over)), encoding="utf-8")
        return game_config.load(self.dir)

    def test_resolved(self):
        cfg = self.load()
        self.assertEqual(cfg["entry"], {"name": "Fx_Main", "addr": "8c011000"})
        self.assertEqual(cfg["hardware"], {"8c012000": "Fx_Hw"})
        self.assertEqual(cfg["decomp"], (self.dir.parent / "decomp").resolve())
        self.assertEqual([p.name for p in cfg["sdk"]], ["sy_heap.c"])
        self.assertEqual(cfg["console"], {"country": 0x32, "language": 1, "broadcast": 0x31})
        self.assertEqual((cfg["boot_file"], cfg["disc"], cfg["must_work"]), ("", None, None))

    def test_errors(self):
        for over in ({"sdk": ["no_such_module"]}, {"image_base": "8c01"}, {"sources": ["src/missing.c"]},
                     {"console": {"country": 1}}, {"surprise": 1}, {"entry": {"name": "1bad", "addr": "8c011000"}},
                     {"boot_file": "../X.BIN"}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)
        (self.dir / "game.json").write_text(json.dumps({k: v for k, v in GAME.items() if k != "entry"}),
                                            encoding="utf-8")
        with self.assertRaises(SystemExit):
            game_config.load(self.dir)

    def test_generated(self):
        cfg = self.load(boot_file="1ST_READ.BIN")
        h = game_config.port_config_h(cfg)
        for line in ('#define PORT_ENTRY_NAME "Fx_Main"', "#define PORT_ENTRY_ADDR 0x8c011000u",
                     "#define PORT_IMAGE_BASE 0x8c010000u", '#define PORT_BOOT_FILE "1ST_READ.BIN"',
                     "#define PORT_CONSOLE_COUNTRY 0x32"):
            self.assertIn(line, h)
        self.assertIn("void port_game_entry(void) { Fx_Main(); }", game_config.port_entry_c(cfg))
        cm = game_config.port_files_cmake(cfg, pathlib.Path("W:/r/src"), ["8c011000"], pathlib.Path("W:/r/include"),
                                          pathlib.Path("W:/rt"))
        self.assertIn('  "W:/r/src/8c011000.c"', cm)
        self.assertIn(f'  "{(KIT / "sdk" / "sy_heap.c").as_posix()}"', cm)
        self.assertIn(f'  "{(self.dir / "src" / "own.c").resolve().as_posix()}"', cm)
        cm = game_config.port_files_cmake(cfg, pathlib.Path("W:/r/src"), ["8c011000"], pathlib.Path("W:/r/include"),
                                          pathlib.Path("W:/rt"), {"8c011000": ["Fx_Draw=Fx_DrawWrap"]})
        self.assertIn('set_source_files_properties("W:/r/src/8c011000.c" PROPERTIES COMPILE_DEFINITIONS '
                      '"Fx_Draw=Fx_DrawWrap")', cm)

    def test_wrap(self):
        self.assertEqual(self.load(wrap={"0x8C011800": "Fx_DrawWrap"})["wrap"], {"8c011800": "Fx_DrawWrap"})
        for over in ({"wrap": {"8c011800": "1bad"}}, {"wrap": {"8c012000": "Fx_Hw2"}}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)
        src = self.dir / "rsrc"
        src.mkdir()
        (src / "8c011000.c").write_text("void Fx_Main(void) { Fx_Draw(); }\n", encoding="utf-8")
        (src / "8c011800.c").write_text("void Fx_Draw(void) { Fx_Draw2(); }\n", encoding="utf-8")
        (src / "8c011900.c").write_text("void Fx_Other(void) { Fx_Drawn(); }\n", encoding="utf-8")
        names = {"8c011000": "Fx_Main", "8c011800": "Fx_Draw", "8c011900": "Fx_Other"}
        files = sorted(names)
        got = gen_port.wrap_defines(src, files, {"8c011800": "Fx_DrawWrap"}, names, {"Fx_DrawWrap"})
        self.assertEqual(got, {"8c011000": ["Fx_Draw=Fx_DrawWrap"]})  # not its own file, not Fx_Drawn
        with self.assertRaises(SystemExit):  # the game's sources must define the wrapper
            gen_port.wrap_defines(src, files, {"8c011800": "Fx_DrawWrap"}, names, set())
        self.assertEqual(gen_port.wrapper_proto("void Fx_Draw(void);", "Fx_Draw", "Fx_DrawWrap"),
                         "void Fx_DrawWrap(void);")

    def test_defined_names(self):
        f = self.dir / "src" / "defs.c"
        f.write_text("s32 Fx_Open(const char *name)\n{\n    return 0;\n}\nstatic void helper(void) {}\n"
                     "void *Fx_Alloc(u32 size) { return 0; }\n    s32 indented(void);\nu32 proto_only(void);\n"
                     "s32 Fx_Long(u32 a,\n            u32 b)\n{\n    return 0;\n}\nu32 long_proto(u32 a,\n"
                     "               u32 b);\n", encoding="utf-8")
        # not the prototypes; a parameter list may run over lines
        self.assertEqual(gen_port.defined_names([f]), {"Fx_Open", "Fx_Alloc", "Fx_Long"})


class KitIsGameFree(unittest.TestCase):
    def test_no_game_material(self):
        files = kit_files()
        self.assertTrue(any(p.parent.name == "runtime" for p in files))
        self.assertEqual(game_material(files), [])

    def test_material_is_found(self):
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            (d / "runtime").mkdir()
            (d / "runtime" / "x.c").write_text('#include "game.h"\nu32 a = 0x8c02796au, b = 0x8c000000u;\n'
                                               "/* Toy Commander */\n", encoding="utf-8")
            hits = game_material(kit_files(d), d)
            self.assertEqual(sorted(h[2] for h in hits), sorted(["game.h", "8c02796a", "Toy Commander"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_sizes(self):
        self.assertEqual([p.name for p in kit_files() if len(p.read_text(encoding="utf-8").splitlines()) > 400], [])


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class RuntimeCompiles(unittest.TestCase):
    def test_runtime_w4_wx(self):
        """Every runtime file compiles at the port's flags with zero warnings (a synthetic port_config.h)."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            srcs = " ".join(f'"{p}"' for p in sorted((KIT / "runtime").glob("*.c")))
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /c /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "host"}" /I"{rd}" {srcs}')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_host_w4_wx(self):
        """The C++ host files compile at the port's C++ flags with zero warnings. Those that include SDL3 need the
        game build's vcpkg headers (manifest mode), so the build checks them, as it does runtime/gfx."""
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            files = [p for p in sorted((KIT / "host").glob("*.cpp")) if "<SDL3/" not in p.read_text(encoding="utf-8")]
            self.assertTrue(files)
            srcs = " ".join(f'"{p}"' for p in files)
            cl = (f'cl /nologo /c /std:c++20 /W4 /WX /permissive- /utf-8 /EHsc /arch:SSE2 /DNOMINMAX '
                  f'/DWIN32_LEAN_AND_MEAN /O2 /I"{KIT / "host"}" {srcs}')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
