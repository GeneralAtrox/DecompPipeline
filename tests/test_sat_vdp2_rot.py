"""VDP2's rotation plane RBG0 (port/saturn/sat_vdp2_rot.c, composed by sat_vdp2.c). The kit had no RBG0, so Robo Pit's
fight showed the blue back screen where the arena's grass and dirt floor is (the launcher, 2026-11-02; Mednafen draws the
floor). tests/sat_vdp2_rot_test.c builds both files at /W4 /WX in 32-bit MSVC, programs the registers, a map and a
rotation parameter table as the fight does, and reads dots of the composed picture; then the windows and NBG0's line
scroll (sat_vdp2_win.c: Robo Pit's sky shows only above the horizon through window 0). Each picture is composed in one band
and again in four (base/bands.c), and the two must match.
Controls: the check must fail on each planted mistake. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_vdp2_rot.py.
Nearest existing: tests/test_sat_vdp_fb.py - the same way of building a kit file with a C test, for VDP1's framebuffers.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
FILES = ("sat_vdp2.c", "sat_vdp2_rot.c", "sat_vdp2_win.c")
SRC = {n: (KIT / n).read_text(encoding="utf-8") for n in FILES}
PLANTS = {
    "RBG0 not composed": ("sat_vdp2.c", "if (g_n[n].on && g_n[n].prio", "if (n < 4 && g_n[n].on && g_n[n].prio"),
    "over mode 2 wraps": ("sat_vdp2_rot.c", "if (g_r.ovr == 2 &&", "if (g_r.ovr == 9 &&"),
    "Px..Cz read unsigned": ("sat_vdp2_rot.c", "return sx(vw(t + o), 14); }", "return (s64)(vw(t + o) & 0x3fffu); }"),
    "RBG0's map as 2x2 planes": ("sat_vdp2_rot.c", "g->maps = 4;", "g->maps = 2;"),
    "the scale ignored": ("sat_vdp2_rot.c", "s64 ax = kx * bx", "s64 ax = 65536 * bx"),
    "X stepped by dX, not kx dX": ("sat_vdp2_rot.c", "dax = kx * l->dxr,", "dax = l->dxr,"),
    "the coefficient read once a line though it changes along it": ("sat_vdp2_rot.c", "each = g_r.kte && g_r.dkax;", "each = 0;"),
    "a transparent coefficient drawn": ("sat_vdp2_rot.c", "if (k & 0x80000000u) return 0;", "(void)0;"),
    "a row between the bands left out": ("sat_vdp2.c", "y1 = h * (band + 1u) / bands,", "y1 = h * (band + 1u) / bands - (band + 1u < bands),"),
    "the matrix ignored for Xp": ("sat_vdp2_rot.c", "l->xp = g_r.a * ox", "l->xp = 65536 * ox"),
    "windows not applied": ("sat_vdp2.c", "if (each[i] && sat_win_hide(&wl, n + 1, x)) continue;", "(void)0;"),
    "a line half in a window taken as all in it": ("sat_vdp2_win.c", "can[d][0] = !(a <= l->xw[d] && l->xw[d] - a >= n - 1u);", "can[d][0] = 0;"),
    "windows tested per dot only where they hide every dot": ("sat_vdp2_win.c", "*each = l->on[layer] && any;", "*each = 0;"),
    "a window's area bit ignored": ("sat_vdp2_win.c", "in0 ^ (int)(ctrl & 1u)", "in0"),
    "a window's lines ignored": ("sat_vdp2_win.c", "if (y < g_w.ys[d] || y > g_w.ye[d] || xe < xs) {", "if (xe < xs) {"),
    "the line window table ignored": ("sat_vdp2_win.c", "if (g_w.lw[d]) {", "if (0) {"),
    "window X in dots, not half dots": ("sat_vdp2_win.c", "if (!g_w.hires) xs >>= 1, xe >>= 1;", "(void)0;"),
    "line scroll replaces the scroll": ("sat_vdp2_win.c", "(vw(a + 2u) >> 8)) + p[n].scx;", "(vw(a + 2u) >> 8));"),
    "line scroll every line, not every 2^LSS": ("sat_vdp2_win.c", "(y >> lss) * g_w.lsstep[n]", "y * g_w.lsstep[n]"),
    "widened: RBG0's x unsigned for X": ("sat_vdp2_rot.c", "bx = l->xsp + l->dxr * x,", "bx = l->xsp + l->dxr * (s64)x0,"),
    "widened: RBG0's x unsigned for Y": ("sat_vdp2_rot.c", "by = l->ysp + l->dyr * x;", "by = l->ysp + l->dyr * (s64)x0;"),
    "widened: no window taken as one at x -1": ("sat_vdp2_win.c", "l->xs[d] = 0x80000000u;", "l->xs[d] = 0xffffffffu;"),
    "widened: windows stop at the screen's edges": ("sat_vdp2_win.c", "if (sat_wide_now) xs =", "if (0) xs ="),
    "widened: the picture stretched, not wider": ("sat_vdp2.c", "x = 0u - g_ox; wx < g_ww;", "x = 0u; wx < g_ww;"),
}


def run(vcvars, srcs, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        for n, s in srcs.items():
            (tmp / n).write_text(s, encoding="utf-8")
        inc = f'/I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} sat_vdp2.c sat_vdp2_rot.c sat_vdp2_win.c "{ROOT / "port" / "base" / "bands.c"}" "{ROOT / "tests" / "sat_vdp2_rot_test.c"}" /Fe:t.exe',
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
    for name, (f, old, new) in PLANTS.items():
        assert SRC[f].count(old) == 1, f"{name}: the plant's text is gone from {f}"
        rc, out = run(vcvars, dict(SRC, **{f: SRC[f].replace(old, new)}), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
