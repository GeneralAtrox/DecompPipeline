"""The plug-in backup memory cartridge (port/saturn/sat_bup.c device 1, option "memory-cartridge" in console_sat.c).
Dragon Force's Record (field system menu) needs a cartridge; the kit offered internal memory only. tests/sat_bup_test.c builds
sat_bup.c at /W4 /WX in 32-bit MSVC and calls the BUP services as the game does: with the option off BUP_Init reports no second
device and every call on device 1 says none (the game sees what it saw before); with it on device 1 is a 4 Mbit cartridge
(512-byte blocks, 1024 blocks) with its own file beside the internal one, which it never writes.
Controls: the check must fail on each planted fault. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_bup.py.
Nearest existing: tests/test_sat_bios.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_bup.c").read_text(encoding="utf-8")
PLANTS = {
    "device 1 never found": ("dev == DEV_CART && g_cart ? &g_dev[1] : NULL", "NULL"),
    "cartridge reported with the option off": ('g_cart = port_option("memory-cartridge") > 0;', "g_cart = 1;"),
    "cartridge file is the internal file": ("cart_path(path, g_dev[1].path, sizeof g_dev[1].path);",
                                            'snprintf(g_dev[1].path, sizeof g_dev[1].path, "%s", path);'),
    "cartridge file not made on first use": ("if (d == &g_dev[1]) save_all(d);", ""),
}


def run(vcvars, source, mode, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_bup.c").write_text(source, encoding="utf-8")
        (tmp / "sat_game.h").write_text("/* the generated game header: nothing the library uses */\n")
        (tmp / "d").mkdir()
        (tmp / "e").mkdir()
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_bup_test.c"}" /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99",
                                     f'"{tmp / "t.exe"}" "{(tmp / "d").as_posix()}" on', "if errorlevel 1 exit /b 1",
                                     f'"{tmp / "t.exe"}" "{(tmp / "e").as_posix()}" off']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, SRC, "", "the kit")
    print(out[-2500:])
    assert rc == 0 and out.count("all ok") == 2, out
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_bup.c"
        rc, out = run(vcvars, SRC.replace(old, new), "", name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
