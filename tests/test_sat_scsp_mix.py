"""The SCSP's direct pan (port/saturn/sat_scsp_mix.c). DIPAN bit 4 picks the side turned down: 0 the left, 1 the right,
as on the Dreamcast's AICA (port/runtime/aica.c, checked against Flycast), the same Yamaha voice. The mixer had it the other
way round, so every panned voice came out of the other speaker. Measured on Dragon Force's in-game music against Mednafen
from the same presses (2026-11-17, tempo matched): the port leaned 0.85 dB left where Mednafen leans 0.57 dB right, and the
left-right balance in quarter-second windows ran against Mednafen's (correlation -0.31; +0.31 with the sign turned).
tests/sat_scsp_mix_test.c builds the file at /W4 /WX in 32-bit MSVC and mixes one voice at five pans.
Controls: the check must fail on the planted copy of the old sign and on 6 dB pan steps. Skips when no 32-bit MSVC is here.
Run: python -W error tests/test_sat_scsp_mix.py.
Nearest existing: tests/test_sat_smpc.py - the same way of building a kit file with a C test, for the SMPC.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_scsp_mix.c").read_text(encoding="utf-8")
OLD_SIGN = ("s->gl = (r16v >> 8) & 0x10u ? lvl : lvl * side;", "s->gl = (r16v >> 8) & 0x10u ? lvl * side : lvl;")
PLANTS = {
    "the old DIPAN sign (bit 4 set turned the left down)": [OLD_SIGN, ("s->gr = (r16v >> 8) & 0x10u ? lvl * side : lvl;",
                                                                      "s->gr = (r16v >> 8) & 0x10u ? lvl : lvl * side;")],
    "pan steps of 6 dB": [("exp2f(-0.5f * (float)pan)", "exp2f(-1.0f * (float)pan)")],
}
GAME_H = "#define SAT_SOUND_CPU 1\n"


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_scsp_mix.c").write_text(source, encoding="utf-8")
        (tmp / "sat_game.h").write_text(GAME_H, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX /fp:strict {inc} "{ROOT / "tests" / "sat_scsp_mix_test.c"}" /Fe:t.exe',
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
    print(out[-2000:])
    assert rc == 0 and "all ok" in out, out
    for name, edits in PLANTS.items():
        src = SRC
        for old, new in edits:
            assert old in src, f"plant {name}: {old!r} not in the kit"
            src = src.replace(old, new)
        rc, out = run(vcvars, src, name)
        assert rc != 0 and "FAIL" in out, f"plant not caught: {name}\n{out}"
        print(f"plant caught: {name}")
    print("test_sat_scsp_mix: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
