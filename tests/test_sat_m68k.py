"""The Saturn kit's 68000 core (port/saturn/sat_m68k*.c, the sound driver's CPU): builds it with sat_m68k_test.c at /W4 /WX
in 32-bit MSVC and runs the hand-assembled programs (ALU, flags, divide, DBcc loop, stack and BSR, shifts, EXT, memory,
bit test, MOVEM). Skips when no 32-bit MSVC is installed here (the Saturn builds run on build box B).
Run: python -W error tests/test_sat_m68k.py."""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402


def main():
    VCVARS = game_config.vcvars32()
    if VCVARS is None:
        print("skip: no 32-bit MSVC here")
        return 0
    kit = ROOT / "port" / "saturn"
    with tempfile.TemporaryDirectory() as tmp:
        srcs = " ".join(f'"{kit / n}"' for n in ("sat_m68k.c", "sat_m68k_g0.c", "sat_m68k_g4.c", "sat_m68k_g8.c"))
        inc = f'/I"{kit}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = pathlib.Path(tmp) / "b.bat"
        lines = ["@echo off", f'call "{VCVARS}" >nul', f'cd /d "{tmp}"',
                 f'cl /nologo /W4 /WX {inc} /D_CRT_SECURE_NO_WARNINGS {srcs} "{ROOT / "tests" / "sat_m68k_test.c"}" /Fe:t.exe',
                 "if errorlevel 1 exit /b 1", f'"{tmp}\\t.exe"']  # vcvars32 turns off the current-directory lookup
        bat.write_text(chr(10).join(lines) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    print(r.stdout[-1500:])
    assert r.returncode == 0 and "all ok" in r.stdout, r.stdout + r.stderr
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
