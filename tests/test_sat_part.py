"""A model part the player picks (port/saturn/sat_part.c, game.json part_choice): the user asked for Robo Pit's bunny head
to come as animals too (2026-11-04), chosen from the menu, with the original unchanged. gen_saturn.py turns a part_choice
into sat_part_data.h; tests/sat_part_test.c builds sat_part.c at /W4 /WX in 32-bit MSVC against a small original and
one version made here, and checks the original changes nothing, the version is put together and pointed at once the
models are loaded, the recording's choice, and a wrong original refused.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_part.py.
Nearest existing: tests/test_sat_ground_shadows.py - the same way of building a kit file with a C test.
"""
import base64
import pathlib
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
import gen_saturn  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_part.c").read_text(encoding="utf-8")
EDIT = (struct.pack(">4H", 2, 3, 1, 0) + struct.pack(">2H", 0x1111, 0x2222) + struct.pack(">9i", 10, 11, 12, 20, 21, 22, 30, 31, 32)
        + struct.pack(">3i4HHBx", 1, 2, 3, 3, 4, 5, 5, 0x3333, 1))
CHOICE = {"title": "&Head", "object": "0x00220000", "pdata": "0x00210000", "points": 5, "polygons": 3, "drop_points": 2,
          "original": "&Original", "choices": [["fox", "&Fox", base64.b64encode(EDIT).decode()]]}
PLANTS = {
    "the dropped points kept": ("memcpy(g_ram + 20, pts + 12u * SAT_PART_DROP, 12u * base);", "memcpy(g_ram + 20, pts, 12u * base);"),
    "the normals' direction ignored": ("s >= 0.0 ? 1.0 : -1.0", "1.0"),
    "the pointers not rebased": ("put32(g_ram, PART_LO + 20u)", "put32(g_ram, 20u)"),
    "the kept colours not changed": ("put16(a + 6, be16(cols + 2u * k));", ";"),
    "the version's flag not taken": ("a[0] = f[22];", ";"),
    "the original recorded as a choice": ("    if (g_pick) {\n        strncpy_s", "    if (1) {\n        strncpy_s"),
    "the recording's choice ignored": ("if (session_note_replayed(PART_NOTE, name, sizeof name)) {", "if (0) {"),
    "a different original taken": ("!= SAT_PART_POINTS", "== 0"),
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_part.c").write_text(source, encoding="utf-8")
        (tmp / "sat_part_data.h").write_text(gen_saturn.part_choice_h(CHOICE), encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        srcs = f'"{ROOT / "tests" / "sat_part_test.c"}" "{tmp / "sat_part.c"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f"cl /nologo /W4 /WX {inc} {srcs} /Fe:t.exe",
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def main():
    assert gen_saturn.part_choice_h(None).count("#define SAT_PART_N 0") == 1, "a game with no part_choice"
    assert "sat_part_e1" in gen_saturn.part_choice_h(dict(CHOICE, max_points=6)), "a version of exactly max_points"
    try:
        gen_saturn.part_choice_h(dict(CHOICE, max_points=5))
        raise AssertionError("a version over max_points was built")
    except SystemExit:
        pass
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, SRC, "the kit")
    print(out[-1500:])
    assert rc == 0 and "all ok" in out, out
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_part.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
