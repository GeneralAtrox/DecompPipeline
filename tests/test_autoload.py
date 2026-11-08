"""The save's auto-load (port/base/autoload.c; Game > Auto-load save): the user asked (2026-11-04) that Robo Pit load the
save by itself, "When you load into Robopit, it should auto load your save file", through the game's own load path, with no
change when there is no save or the setting is off; and (2026-11-07) "We do not need inputs to load the save, just load the
save file by the function": a step may call the game's own load function once, nothing pressed. tests/autoload_test.c builds autoload.c at /W4 /WX in 32-bit MSVC
against stubs and checks when it starts (by default only in an unscripted play run with the game's file in the save, or
as the recording says), what it presses and when, the word it puts in, the load it calls, and giving the pad back. The rest is checked as
text and through gen_saturn.py: player 1's pad takes the presses and drops the player's while a step holds it
(input.c), the Saturn kit reports a step's function on entry and return (sat_call.c), looks for the file only when the
setting wants it (sat_bup.c), and game.json auto_load turns into the steps.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips the C build when no 32-bit MSVC is here.
Run: python -W error tests/test_autoload.py.
Nearest existing: tests/test_sat_part.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
import gen_saturn  # noqa: E402

BASE = ROOT / "port" / "base"
SRC = (BASE / "autoload.c").read_text(encoding="utf-8")
PLANTS = {
    "on without the game's file": ("&& n && con->has_save()", "&& n"),
    "on in a scripted run": ("play_run && !input_scripted()", "play_run"),
    "the recording's note ignored": ("if (session_note_replayed(NOTE, &on, 1)) g_on = on != 0;", "if (0) g_on = 0;"),
    "on in a replay without the note": ("else if (session_replaying()) g_on = 0;", "else if (0) g_on = 0;"),
    "not kept in the recording": ("if (on) session_save_read(NOTE, &on, 1, 1);", ";"),
    "the cursor's word not put in": ("if (s->poke) g_con->poke16(s->poke, s->value);", ";"),
    "the game's load not called": ("u32 r = g_con->call(s->call, s->arg);", "u32 r = 0;"),
    "the wait's word not put back": ("if (s->poke) g_con->poke16(s->poke, old);", ";"),
    "a return taken as an entry": ("autoload_leave = s && s->leave;", "autoload_leave = 0;"),
    "the press held all the time": ("t % EVERY < HOLD ? g_press : 0u", "g_press"),
    "the pad kept after giving up": ("        autoload_fn = 0;\n        return 0;", "        return 0;"),
    "the player's pad not held off": ("    *own = 1;\n", "    *own = 0;\n"),
    "only the last of a step's buttons": ("bits |= input_button_bit(one)", "bits = input_button_bit(one)"),
}


def run(vcvars, source, name, strict=False):  # strict: /WX (a plant may leave a parameter unused)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "autoload.c").write_text(source, encoding="utf-8")
        inc = f'/I"{BASE}"'
        srcs = f'"{ROOT / "tests" / "autoload_test.c"}" "{tmp / "autoload.c"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f"cl /nologo /W4 {'/WX ' if strict else ''}{inc} {srcs} /Fe:t.exe",
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def text_checks():
    inp = (BASE / "input.c").read_text(encoding="utf-8")
    assert "own = 0, load = autoload_buttons(&own)" in inp, "input.c: player 1's pad does not take the auto-load's presses"
    assert "if (own) s[0] = (PadState){.b = load}, keys = 0; else s[0].b |= load;" in inp, "input.c: the player's pad not held off"
    call = (ROOT / "port" / "saturn" / "sat_call.c").read_text(encoding="utf-8")
    assert "if (f->addr == autoload_fn && !autoload_leave) autoload_hit();" in call, "sat_call.c: a step's entry not reported"
    assert "if (f->addr == autoload_fn && autoload_leave) autoload_hit();" in call, "sat_call.c: a step's return not reported"
    assert call.index("autoload_fn && !autoload_leave") < call.index("r = run_frame(f, top, a, 1);") < call.index(
        "autoload_fn && autoload_leave"), "sat_call.c: the entry must be reported before the function runs, the return after"
    bup = (ROOT / "port" / "saturn" / "sat_bup.c").read_text(encoding="utf-8")
    assert "autoload_start(k_auto, sizeof k_auto / sizeof k_auto[0], play_run, &k_con);" in bup, "sat_bup.c: no start"
    assert "a.r[0] = arg;\n    return tc_call(fn, &a);" in bup, "sat_bup.c: the game's load not called with its argument"
    main = (ROOT / "port" / "saturn" / "sat_main.c").read_text(encoding="utf-8")
    assert "(used = autoload_option(a, v)) == 0" in main and "sat_bup_auto_load(" in main, "sat_main.c: not wired"
    assert gen_saturn.auto_load_h(None) == [], "a game with no auto_load"
    al = {"file": "ROBO_PIT___", "steps": [{"enter": "0x060400f8", "poke": ["0x060b1250", 9]}, {"leave": "0x0604ea28", "press": "A"},
                                            {"enter": "0x060400f8", "poke": ["0x0609c908", 0], "call": ["0x0604f394", 0]}]}
    assert gen_saturn.auto_load_h(al) == [
        '#define SAT_AUTO_LOAD_FILE "ROBO_PIT___"',
        '#define SAT_AUTO_LOAD_STEPS {{0x060400f8u, 0, NULL, 0x060b1250u, 9u, 0x00000000u, 0u}, '
        '{0x0604ea28u, 1, "A", 0x00000000u, 0u, 0x00000000u, 0u}, {0x060400f8u, 0, NULL, 0x0609c908u, 0u, 0x0604f394u, 0u}}'], \
        gen_saturn.auto_load_h(al)
    try:
        gen_saturn.auto_load_h(dict(al, file="ROBO_PIT"))
        raise AssertionError("a file name that is not 11 characters was taken")
    except SystemExit:
        pass


def main():
    text_checks()
    print("text checks ok")
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, SRC, "the base", strict=True)
    print(out[-2500:])
    assert rc == 0 and "all ok" in out, out
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from autoload.c"
        rc, out = run(vcvars, SRC.replace(old, new), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
