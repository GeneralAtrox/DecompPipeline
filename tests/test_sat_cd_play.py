"""The CD block's play and seek (port/saturn/sat_cd.c). A seek to 0xffffff pauses where the play is and a play from 0xffffff
resumes it, keeping the play's start, end and repeat count (a mode of 0xff keeps the mode). Robo Pit's pause menu sends
exactly that pair; the kit took the seek's 0xffffff as a position past the disc's end, so the music went silent after the
first pause (the user's launcher run, 2026-11-04). Fast loads (the user's black pause after skipping the intro,
2026-11-04): a data play fills the buffer in a frame unless its first sector starts a Sega FILM movie, and only when
asked. tests/sat_cd_play_test.c builds the file at /W4 /WX in 32-bit MSVC and writes the commands to the registers as
the game does.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_cd_play.py.
Nearest existing: tests/test_sat_dmac.py - the same way of building a kit file with a C test, for the SH-2's DMA.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_cd.c").read_text(encoding="utf-8")
PLANTS = {
    "seek 0xffffff taken as a position": ("if (pos24 != 0xffffffu && (pos24 & 0x800000u)) g_pos", "if (pos24 & 0x800000u) g_pos"),
    "a resume restarts the loop at the pause": ("g_play_start = start;", "g_play_start = at, (void)start;"),
    "mode 0xff read as endless repeat": ("mode == 0xffu ? g_repeat : (int)(mode & 15u)", "(int)(mode & 15u)"),
    "end 0xffffff read as a count": ("if (epos != 0xffffffu && (fad ||", "if ((fad ||"),
    "fast loads taken when not asked": ("return sat_cd_fast_loads > 0 && t", "return t"),
    "a movie loaded fast": (' && memcmp(raw + 16, "FILM", 4) != 0;', ";"),
    "fast loads asked but not given": ("g_quick = quick(start);", "g_quick = 0, (void)quick(start);"),
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_cd.c").write_text(source, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_cd_play_test.c"}" /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, SRC, "the kit")
    print(out[-1500:])
    assert rc == 0 and "all ok" in out, out
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_cd.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
