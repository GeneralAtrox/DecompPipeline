"""Clearer text (port/saturn/sat_text.c, the VDP1 scan in sat_vdp1.c, A3 in sat_text_a3.c). Dragon Force's lettering is
redrawn sharp at the window's resolution from each lettering sprite's own dots, on by default, and with it off the picture
must be the original's exactly (the user, 2026-11-11: clearer content on by default, off is the original). The game's
framebuffer must never change: the composer shows a copy with the lettering taken out, and the lettering goes to the
presenter as an image. tests/sat_text_test.c builds the files at /W4 /WX in 32-bit MSVC, draws a list with a lettering
sprite and a sprite over half of it, on and off, in one band and in several, then composes and posts; again with a
play run's maker thread, where the frame that first shows the lettering must not make its image (a first-sight frame
cost up to 10 ms: images are made off the shown frame, the lettering's own dots standing in until ready).
tests/sat_text_v2_test.c builds the VDP2 path (sat_text_v2.c through the composer sat_vdp2.c): lettering cells of a scroll
plane (side by side, and one shown flipped) over another plane, composed on and off, in one run and with the maker thread.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_text.py.
Nearest existing: tests/test_sat_ground_shadows.py - builds the same VDP1 files with a C test; it has no lettering.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
TEXT = ("sat_text.c", "sat_text_a3.c", "sat_text_v2.c")
SPRITES = TEXT + ("sat_vdp1.c", "sat_vdp1_list.c")
PLANES = TEXT + ("sat_vdp2.c", "sat_vdp2_rot.c", "sat_vdp2_win.c")
FILES = tuple(dict.fromkeys(SPRITES + PLANES))
PLANTS = {
    "the lettering left in the shown copy": ("sat_text.c", "if (a != b && cur == a)", "if (a != b && cur == a && i < 0)"),
    "a dot drawn over later taken out too": ("sat_text.c", "if (a != b && cur == a)", "if (a != b && cur != b)"),
    "the scan draws": ("sat_vdp1.c", "if (g_scan || x1 < -g_ox ||", "if (x1 < -g_ox ||"),
    "the staircase kept on the grid": ("sat_text_a3.c", "zig[i] = sg[i * 5 + 4] == 1 &&", "zig[i] = 0 &&"),
    "the option ignored": ("sat_text.c", 'port_option("clear-text") == 1', 'port_option("clear-text") >= 0'),
    "the image made on the frame that shows it": ("sat_text.c", "    if (!async()) {\n        SatTextItem", "    if (1) {\n        SatTextItem"),
    "nothing standing in": ("sat_text.c", "if (g_on[y * w + x]) px[", "if (0) px["),
    "a plane's lettering left in the shown picture": ("sat_vdp2.c", "sat_text_v2_layer[id] == (u8)n) {", "sat_text_v2_layer[id] == (u8)n && 0) {"),
    "the planes taken apart with the option off": ("sat_text_v2.c", "if (!SAT_G(text_cells) || !sat_text_wanted() ||", "if (!SAT_G(text_cells) ||"),
    "a cell's flip ignored": ("sat_text_v2.c", "nm->hf ? cs - 1u - x : x", "x"),
    "cells side by side not joined": ("sat_text_v2.c", "while (c1 + 1 < cols && grid[r][c1 + 1] == tag) c1++;", "(void)0;"),
    "a bitmap's lettering left in the shown picture": ("sat_vdp2.c", "b->lt = sat_text_v2_bm >> ", "b->lt = 0u & sat_text_v2_bm >> "),
    "a bitmap's tiles never asked about": ("sat_text_v2.c", "grid[r][c] = bm_empty(g, m) ? 0 :", "grid[r][c] = 1 ? 0 :"),
    "the game's no for a picture ignored": ("sat_text_v2.c", "(NULL)) <= 0) return 0;", "(NULL)) < -1) return 0;"),
    "a plane's lettering colour not kept": ("sat_vdp2.c", "= mk || lt ? pick(&tw, back) : rgb;", "= mk ? pick(&tw, back) : rgb;"),
}


def run(vcvars, srcs, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        for n, s in srcs.items():
            (tmp / n).write_text(s, encoding="utf-8")
        (tmp / "sat_game.h").write_text("", encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bands = f' "{ROOT / "port" / "base" / "bands.c"}"'
        spr = " ".join(f'"{tmp / n}"' for n in SPRITES) + bands + f' "{ROOT / "tests" / "sat_text_test.c"}"'
        pl = " ".join(f'"{tmp / n}"' for n in PLANES) + bands + f' "{ROOT / "tests" / "sat_text_v2_test.c"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f"cl /nologo /W4 /WX {inc} {spr} /Fe:t.exe", "if errorlevel 1 exit /b 99",
                                     f"cl /nologo /W4 /WX {inc} {pl} /Fe:v.exe", "if errorlevel 1 exit /b 99",
                                     f'"{tmp}\\t.exe" || exit /b 1', f'"{tmp}\\t.exe" async || exit /b 1',
                                     f'"{tmp}\\v.exe" || exit /b 1', f'"{tmp}\\v.exe" async']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    missing = [n for n in FILES if not (KIT / n).is_file()]
    assert not missing, "FAIL: the kit has no Clearer text (" + ", ".join(missing) + " missing)"
    src = {n: (KIT / n).read_text(encoding="utf-8") for n in FILES}
    rc, out = run(vcvars, src, "the kit")
    print(out[-2000:])
    assert rc == 0 and "all ok" in out, out
    for name, (f, old, new) in PLANTS.items():
        assert src[f].count(old) == 1, f"{name}: the plant's text is gone from {f}"
        rc, out = run(vcvars, dict(src, **{f: src[f].replace(old, new)}), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
