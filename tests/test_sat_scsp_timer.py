"""The SCSP's timers against the sound driver (port/saturn/sat_scsp.c). The 68000 ran a step of 16 samples' clocks before
the step's samples were made and saw a timer's interrupt only after the step, so a driver that reloads its timer in the
handler lost the rest of the step at every tick: Dragon Force's sequenced music ran slow against Mednafen (2026-11-17).
tests/sat_scsp_timer_test.c builds sat_scsp.c (SAT_SOUND_CPU) at /W4 /WX in 32-bit MSVC with a stand-in driver that
reloads timer A on each interrupt, and counts the ticks in one second of sound at four timer settings.
Controls: the check must fail on the planted old step. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_scsp_timer.py.
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
SRC = (KIT / "sat_scsp.c").read_text(encoding="utf-8")
PLANTS = {
    "a step not ended at a timer's overflow (the old 16-sample step)": [("        if (step > due) step = due;", "        (void)due;")],
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_scsp.c").write_text(source, encoding="utf-8")
        (tmp / "sat_game.h").write_text("#define SAT_SOUND_CPU 1\n", encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_scsp_timer_test.c"}" /Fe:t.exe',
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
    print("test_sat_scsp_timer: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
