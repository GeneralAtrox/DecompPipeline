"""VDP1's framebuffer change (port/saturn/sat_vdp.c). FBCR's bit 1 is FCM (manual) and bit 0 FCT (change or erase); the kit
had them the other way round. SGL's 2-vertical-blank frame writes 2 then 3, so Robo Pit's fights swapped every vertical
blank, drew the list every vertical blank and never erased one of the two framebuffers: every other picture piled up old
frames over the sky (stacked stands, trees and robots, seen on the launcher 2026-11-02). tests/sat_vdp_fb_test.c builds
the file at /W4 /WX in 32-bit MSVC and programs the registers as a game does.
Controls: the check must fail on each planted copy of the old behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_vdp_fb.py.
Nearest existing: tests/test_sat_m68k.py - the same way of building a kit file with a C test, for another chip.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_vdp.c").read_text(encoding="utf-8")
PLANTS = {
    "FCM and FCT swapped": ("FBCR_FCT = 1u, FBCR_FCM = 2u", "FBCR_FCT = 2u, FBCR_FCM = 1u"),
    "the list drawn on a frame with no swap": ("    if (!change) return;\n", "    if (!change) { if (g_auto_draw) v1_start_draw(); return; }\n"),
    "FBCR 2 erases at once": ("g_erase_armed = g_manual_pending && (fbcr & FBCR_FCM) && !(fbcr & FBCR_FCT);",
                              "if (g_manual_pending && (fbcr & FBCR_FCM) && !(fbcr & FBCR_FCT)) v1_erase(sat_v1_fb[g_fb_swapped]);"),
    "no erase behind a VBE change": ("if (g_erase_armed || (change && (fbcr & FBCR_FCM) && (tvmr & TVMR_VBE)))", "(void)tvmr; if (g_erase_armed)"),
    "CEF cleared each vertical blank": ("    sat_present();\n    v1_frame_change();", "    sat_present();\n    g_v1_draw_end = 0;\n    v1_frame_change();"),
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_vdp.c").write_text(source, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_vdp_fb_test.c"}" /Fe:t.exe',
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
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_vdp.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
