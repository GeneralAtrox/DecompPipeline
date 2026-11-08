"""VDP1's floor shadows (port/saturn/sat_vdp1_list.c). VDP1 has no depth buffer, so Robo Pit's shadow quads that sort after
a robot's feet darken them, on the console too; the user asked for the shadow under the robot (2026-11-04). A game names
its floor-shadow quads in game.json (ground_shadows: draw mode and texture), and play runs draw them before the rest of
the list. tests/sat_ground_shadows_test.c builds the walk and the renderer at /W4 /WX in 32-bit MSVC and draws a foot,
a shadow reached through a jump, and a mesh sprite that is not a shadow, in both orders.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_ground_shadows.py.
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
SRC = (KIT / "sat_vdp1_list.c").read_text(encoding="utf-8")
GAME_H = "#define SAT_GROUND_SHADOW_PMOD 0x0128u\n#define SAT_GROUND_SHADOW_SRCA 0xa4ecu\n"
PLANTS = {
    "the shadows drawn again in the main pass": (": !ground_shadow(cmd, ctrl));", ": 1);"),
    "the shadow pass without the local coordinates": ("(ctrl & 15u) >= 8u || ground_shadow(cmd, ctrl)", "ground_shadow(cmd, ctrl)"),
    "every mesh sprite taken for a shadow": (" && lw(cmd + 8u) == SAT_GROUND_SHADOW_SRCA;", ";"),
    "the setting ignored": ("if (sat_v1_ground_shadows > 0) {", "if (sat_v1_ground_shadows > 1) {"),
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_vdp1_list.c").write_text(source, encoding="utf-8")
        (tmp / "sat_game.h").write_text(GAME_H, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        srcs = f'"{ROOT / "tests" / "sat_ground_shadows_test.c"}" "{KIT / "sat_vdp1.c"}" "{tmp / "sat_vdp1_list.c"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f"cl /nologo /W4 /WX {inc} {srcs} /Fe:t.exe",
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
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_vdp1_list.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
