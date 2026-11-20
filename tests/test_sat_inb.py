"""In-between frames over a VDP1 framebuffer the CPU wrote into (port/saturn/sat_inb.c, sat_vdp.c). Dragon Force's NOW
LOADING screen writes its text straight into the framebuffer over a list that draws nothing; the in-between frames erased
the framebuffer and drew the list again, so two of every three pictures shown in a play run were black: the loading screen
flickered at 144 a second (the user, 2026-11-17). tests/sat_inb_test.c builds both files at /W4 /WX in 32-bit MSVC and
programs them as the game does. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_inb.py.
Nearest existing: tests/test_sat_vdp_fb.py - builds sat_vdp.c alone with sat_inb.c stubbed out, so it cannot see in-between frames.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        inc = f'/I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_inb_test.c"}" /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    print(r.stdout[-2000:])
    assert r.returncode != 99, f"does not build:\n{r.stdout}{r.stderr}"
    assert r.returncode == 0 and "all ok" in r.stdout, r.stdout + r.stderr
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
