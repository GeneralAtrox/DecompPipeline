"""The SMPC's INTBACK peripheral report (port/saturn/sat_smpc.c). SBL's handler (Saturn Bomberman, 0x0609d0aa) takes a
first peripheral packet only with SR & 0xc0 = 0xc0 and a later one with 0x80, counts SR bit 4 as the reset button held,
and asks for the next packet by inverting IREG0 bit 7, so its CONTINUE alternates 0x80 and 0x00. The kit gave SR 0x90
(PDL and RESB swapped) and took only a CONTINUE with bit 7 set, so the game dropped every pad report and Start at the title
did nothing (the user's launcher run, 2026-11-16: 4,763 lines of "INTBACK CONTINUE with nothing pending", which were
SBL's closing BREAK 0xc0). tests/sat_smpc_test.c builds the file at /W4 /WX in 32-bit MSVC and writes the registers as
the game does.
Both modes are built: game.json "saturn" "smpc_intback": "hardware" (SAT_SMPC_HARDWARE) and without the key, which keeps the
older report (SR 0x90, only a CONTINUE with bit 7 set).
Controls: the check must fail on each planted copy of a wrong behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_smpc.py.
Nearest existing: tests/test_sat_cd_play.py - the same way of building a kit file with a C test, for the CD block.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_smpc.c").read_text(encoding="utf-8")
PLANTS = {
    "PDL and RESB swapped (SR 0x90)": ("#define SR_PDL 0x40u", "#define SR_PDL 0x10u"),
    "CONTINUE only with bit 7 set": ("((value ^ g_cont) & 0x80u)", "(value & 0x80u)"),
    "a BREAK with bit 7 taken as CONTINUE": ("g_wait && (value & 0x40u)", "g_wait && (value & 0x40u) && !(value & 0x80u)"),
    "a long report not split": ("    if (n > 32u) n = 32u;\n", "    if (n > 64u) n = 64u;\n"),
    "0-byte mode ignored": ("& 3u) == 3u) continue;", "& 3u) == 4u) continue;"),
    "no NPE on the status reply": ("(u8)(SR_PDL | (want_peri ? SR_NPE : 0u))", "(u8)SR_PDL"),
}


def run(vcvars, source, name, hardware=True):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_smpc.c").write_text(source, encoding="utf-8")
        split = " /DSAT_SMPC_HARDWARE" if hardware else ""
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX{split} {inc} "{ROOT / "tests" / "sat_smpc_test.c"}" /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, SRC, "the kit, smpc_intback hardware")
    print(out[-2000:])
    assert rc == 0 and "all ok" in out, out
    rc, out = run(vcvars, SRC, "the kit, no smpc_intback key", hardware=False)
    print(out[-2000:])
    assert rc == 0 and "all ok" in out and "old: " in out, out
    old, new = "#define SR_PDL 0x10u", "#define SR_PDL 0x40u"
    assert SRC.count(old) == 1
    rc, out = run(vcvars, SRC.replace(old, new), "old report with SR 0x40", hardware=False)
    assert rc != 0 and "FAIL" in out, f"planted change of the old report was not caught:\n{out}"
    print("caught: the old report's SR changed")
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_smpc.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
