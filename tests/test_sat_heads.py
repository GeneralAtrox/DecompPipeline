"""New head types in the game's own part choice (port/saturn/sat_heads.c, game.json heads): the user asked to pick Robo
Pit's animal heads in the game as head types after its own, "Type N is the rabbit head, Type P would be the next head etc...
when you get to Z, you go to A1" (2026-11-08). gen_saturn_heads.py turns game.json heads into sat_game.h lines;
tests/sat_heads_test.c builds sat_heads.c at /W4 /WX in 32-bit MSVC on a small game and checks the order both ways, the
host part swapped only while a robot with a new type is built (its model and the ring's), the panel's name, its restore
and its count of types, the kit's save beside the
game's, ROBOT MAKING and the start switch.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_heads.py.
Nearest existing: tests/test_sat_part.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
import gen_saturn_heads  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_heads.c").read_text(encoding="utf-8")
CFG = {"part_choice": {"object": "0x00220000", "choices": [["cat", "", ""], ["dog", "", ""], ["owl", "", ""]]},
       "heads": {"records": "0x060a0000", "size": 90, "count": 4, "body": 2, "used": 76, "name": 65, "name_len": 10, "host": 5,
                 "names": "0x060b0000", "entry": 18, "label_len": 8, "setup": "0x06001000", "ring": "0x06001100",
                 "node": "0x06001200", "next": "0x06001300", "prev": "0x06001400", "panel": "0x06001500",
                 "making": "0x06001600", "ring_node": "0x06001700", "offered": "0x06001800", "file": "ROBO_PIT___", "types": [["TYPE G", "cat"], ["TYPE H", "dog"], ["TYPE A1", "owl"]]}}
PLANTS = {
    "the step past the last original not taken": ("wr16(g_field, (u16)H.host), set(i, next ? 1 : SAT_HEADS_N);", ";"),
    "the last new type not followed by the first original": (
        "else wr16(g_field, (u16)avail_end(a->r[1], (u32)(s16)a->r[2], !next)), set(i, 0);", "else set(i, 0);"),
    "the host part not swapped": ("(a->r[1] & 0x1fffffffu) == SAT_HEADS_OBJECT", "a->r[1] == 0"),
    "the model swapped outside a robot's build": ("k = g_sp > 0 && g_sp <= 16 ? g_stack[g_sp - 1] : 0;", "k = g_stack[0];"),
    "the panel's name not put back": ("wr8(H.names + H.host * H.entry + n, g_name_was[n]);", "(void)g_name_was[n];"),
    "a renamed robot taken from the kit's save": ("if (!p8(rec(i) + H.used) || !same_name(i, e->name)) return 0;",
                                                  "if (!p8(rec(i) + H.used)) return 0;"),
    "the ring's own copy not swapped": ("fn == H.node || fn == H.ring_node", "fn == H.node"),
    "the panel's count without the new types": ("return r + SAT_HEADS_N;", "return r;"),
    "the count raised outside the panel": ("fn == H.offered && g_panel > 0 &&", "fn == H.offered &&"),
    "ROBOT MAKING keeps a kept type": ("if (i >= 0) set((u32)i, 0);", "(void)i;"),
    "a write before the game's read drops the kit's save": ("if (!g_known) return;", ";"),
    "the recording's switch ignored": ("if (session_note_replayed(NOTE, &on, 1)) on = on != 0;", "if (0) on = 0;"),
    "the busy-wait detector's reads used": ("static u32 p8(u32 a) { return", "static u32 p8(u32 a) { return rd8(a) + 0 * "),
}


def run(vcvars, source, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_heads.c").write_text(source, encoding="utf-8")
        (tmp / "sat_game.h").write_text("\n".join(gen_saturn_heads.heads_h(CFG)) + "\n", encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        srcs = f'"{ROOT / "tests" / "sat_heads_test.c"}" "{tmp / "sat_heads.c"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f"cl /nologo /W4 /WX /D_CRT_SECURE_NO_WARNINGS {inc} {srcs} /Fe:t.exe",
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def main():
    assert gen_saturn_heads.heads_h({}) == [], "a game with no heads"
    for bad in ({"types": [["TYPE G", "fox"]]}, {"types": [["TYPE TOOLONG", "cat"]]}, {"file": "SHORT"}):
        try:
            gen_saturn_heads.heads_h(dict(CFG, heads=dict(CFG["heads"], **bad)))
            raise AssertionError(f"heads built with {bad}")
        except SystemExit:
            pass
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, SRC, "the kit")
    print(out[-2500:])
    assert rc == 0 and "all ok" in out, out
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_heads.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
