"""The BIOS services (port/saturn/sat_bios.c). SYS_SETUINT/SYS_GETUINT (0x06000310/0x06000314) keep the user-handler table
apart from the vector table SYS_SETSINT writes. They were aliased to SETSINT/GETSINT, so SBL's CD stream library
(Saturn Bomberman, 0x060a5398: SETSINT(0x4b, 0x060a54c0) then SETUINT(0x4b, 0)) lost its DMA-end handler, the stream
stalled, the STAGE START jingle never ended and the round never began (no movement, timer at 6:00, no music; 2026-11-17).
tests/sat_bios_test.c builds the file at /W4 /WX in 32-bit MSVC and calls the services through their work-RAM pointers.
Controls: the check must fail on the planted old aliasing. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_bios.py.
Nearest existing: tests/test_sat_smpc.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_bios.c").read_text(encoding="utf-8")
PLANTS = {
    "SETUINT aliased to SETSINT": ("{0x06000310u, set_user_vector}", "{0x06000310u, set_scu_vector}"),
    "GETUINT aliased to GETSINT": ("{0x06000314u, get_user_vector}", "{0x06000314u, get_scu_vector}"),
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_bios.c").write_text(source, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_bios_test.c"}" /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99", f'"{tmp / "t.exe"}"']) + chr(10))
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
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_bios.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
