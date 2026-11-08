"""The SH-2's DMA controller (port/saturn/sat_mem.c). With 16-byte units TCR counts longwords, 4 per unit, and Robo Pit's
copy routine sets it to bytes / 4; the kit counted one per unit, so every 16-byte copy moved 4 times its length. The 32-byte
HUD ramp copied to colour RAM ran over the next 96 bytes and painted the fight's health bars in striped cyan (launcher,
2026-11-02). tests/sat_dmac_test.c builds the file at /W4 /WX in 32-bit MSVC and programs the registers as the game does.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_dmac.py.
Nearest existing: tests/test_sat_vdp_fb.py - the same way of building a kit file with a C test, for another chip.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_mem.c").read_text(encoding="utf-8")
PLANTS = {
    "a 16-byte unit counted once in TCR": ("i < n; i += unit == 16u ? 4u : 1u)", "i < n; i++)"),
    "word units moved as longwords": ("ts == 1 ? 2u", "ts == 1 ? 4u"),
    "DMAOR's master enable ignored": (" || !(oc32(0x1b0u) & 1u)", ""),
    "TE not set at the end": ("bswap32(chcr | 2u)", "bswap32(chcr)"),
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_mem.c").write_text(source, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_dmac_test.c"}" /Fe:t.exe',
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
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_mem.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
