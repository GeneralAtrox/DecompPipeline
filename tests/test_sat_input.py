"""The Saturn pad on the host's (port/saturn/sat_input.c): game.json "confirm" and "cancel" (sat_game.h SAT_CONFIRM,
SAT_CANCEL; AutoOnboard's menu probe finds them) put the game's own move-on and back-out buttons on the host's A and B
(keyboard Z and X), and the buttons they displace take the host buttons confirm and cancel had, so all thirteen Saturn
actions keep one host button each. Saturn Bomberman confirms with C and cancels with B (the probe on build box A, 2026-11-17).
A game.json without them keeps today's table (Robo Pit, Dragon Force).
tests/sat_input_test.c builds the file at /W4 /WX in 32-bit MSVC with a sat_game.h written here and prints the table.
Skips when no 32-bit MSVC is installed here. Run: python -W error tests/test_sat_input.py.
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
SATURN = {"A": "A", "B": "B", "C": "RB", "X": "X", "Y": "Y", "Z": "LB", "L": "LT", "R": "RT", "START": "START", "UP": "UP",
          "DOWN": "DOWN", "LEFT": "LEFT", "RIGHT": "RIGHT"}
OLD = dict(SATURN, C="RT", Z="LT", L="LB", R="RB")  # no "controller" key (Robo Pit)
CTL = "#define SAT_CONTROLLER_SATURN 1"


def pad(confirm=None, cancel=None):
    return [CTL] + ([f'#define SAT_CONFIRM "{confirm}"'] if confirm else []) + ([f'#define SAT_CANCEL "{cancel}"'] if cancel else [])


CASES = [
    ("no controller key: the old table", [], OLD),
    ("controller saturn, no confirm or cancel: unchanged", pad(), SATURN),
    ("Saturn Bomberman: confirm C on host A, Saturn A on C's RB; cancel B stays on B", pad("C", "B"), dict(SATURN, C="A", A="RB")),
    ("confirm B, cancel A: swapped", pad("B", "A"), dict(SATURN, A="B", B="A")),
    ("confirm Z only", pad("Z"), dict(SATURN, Z="A", A="LB")),
    ("cancel Y only", pad(None, "Y"), dict(SATURN, Y="B", B="Y")),
    ("confirm C, cancel Z", pad("C", "Z"), dict(SATURN, C="A", A="RB", Z="B", B="LB")),
    ("the old table, confirm C cancel B", [f'#define SAT_CONFIRM "C"', f'#define SAT_CANCEL "B"'], dict(OLD, C="A", A="RT")),
]


def table(vcvars, defines):
    """{action: host button} console_actions gives, built with these sat_game.h lines."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_game.h").write_text("\n".join(defines) + "\n", encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text("\n".join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                  f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_input_test.c"}" /Fe:t.exe',
                                  "if errorlevel 1 exit /b 99", f'"{tmp / "t.exe"}"']) + "\n")
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"{defines}: does not build or run:\n{r.stdout}{r.stderr}"
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("kit:"))
    return dict(p.split("=") for p in line[4:].split())


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    bad = 0
    for what, defines, want in CASES:
        got = table(vcvars, defines)
        ok = got == want and sorted(got.values()) == sorted(want.values())
        bad += not ok
        print("ok  " if ok else "FAIL", what, "" if ok else f"got {got}")
    assert not bad, f"{bad} layout(s) wrong"
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
