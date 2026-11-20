"""The keyboard and mouse layout (port/base/input_kbm.c; Input > Keyboard and mouse). The user (2026-11-11, Toy Commander):
the controls "dont work well with keyboard + Mouse"; the base had no mouse at all. tests/kbm_test.c builds input_kbm.c at
/W4 /WX in 32-bit MSVC against stubs, twice: with the kit's layout (which must be the keyboard as it always was: Z/X/A/S,
Enter, the arrows as d-pad and stick, I/K/J/L, Q/W/Shift/Space triggers, E/R) and with a game's own layout and key ramp
(game.json keys, key_ramp). Both check the mouse: the buttons, wheel notches (one press each, let go between), the middle
click (a press, and the look centred) and the middle drag (the right stick, kept after the button is let go), losing the
focus, the player's changes, and --kbm-inject, which drives a headless test run. And mouse steer (input_kbm_steer.c; the
user: "In a aircraft i think we should use mouse movement as rotation"): off until the game asks, then the mouse moving with
the middle button up turns the left stick (right x > 0, forward up), by the sensitivity, added to the stick keys', back to
the middle within three reads of a still mouse, Invert mouse Y, the middle drag kept for the look, nothing with the setting
off, the menu open or the focus lost, the window keeping the pointer only while it steers; and MOUSEMOVE injected.
The rest is checked as text: input.c reads the layout only live or injected, sets HOST_PAD_MOUSE_LOOK and the right stick
before the pad is recorded, and has no key table of its own; the window passes the mouse's events; the menu keeps every
change; game.json keys and key_ramp become PORT_KEYS and PORT_KEY_RAMP.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips the C build when no 32-bit MSVC is here.
Run: python -W error tests/test_kbm.py.
Nearest existing: tests/test_autoload.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

BASE = ROOT / "port" / "base"
SRC_PATH = BASE / "input_kbm.c"
SRCS = ("input_kbm.c", "input_kbm_inject.c", "input_kbm_steer.c", "input_kbm_pointer.c", "input_kbm_mouse.c")
GAME_CONFIG = ('#define PORT_KEYS "W=RT,S=LT,A=LSLEFT,D=LSRIGHT,Q=X,UP=UP,DOWN=DOWN,LEFT=LEFT,RIGHT=RIGHT,'
               'MOUSELEFT=A,MOUSERIGHT=B,WHEELUP=Y,WHEELDOWN=Y"\n#define PORT_KEY_RAMP 4\n')
PLANTS = {  # name: (old, new), in input_kbm.c, or (file, old, new)
    "the wheel's notches run together": ("#define PULSE_OFF 2 ", "#define PULSE_OFF 0 "),
    "a drag taken for a click": ("g_mid_moved < CLICK_SLOP) {", "1) {"),
    "the look let go with the button": ("    g_button[which] = down != 0;\n",
                                        "    g_button[which] = down != 0;\n    if (which == 1 && !down) g_look[0] = g_look[1] = 0.0f;\n"),
    "the game's layout ignored": ("    load_layout(PORT_KEYS);", "    load_layout(k_kit_keys);"),
    "left wins over right": ("if (right) out->dirs |= HOST_PAD_RIGHT", "if (right && !left) out->dirs |= HOST_PAD_RIGHT"),
    "the player's choice not taken": ("    if (s) s->now = (u32)mask;\n", "    if (s) s->dflt = (u32)mask;\n"),
    "no key ramp": ("    if (g_key_ramp <= 0) return 128;", "    return 128;"),
    "the injection's keys not held": ("input_kbm_inject.c", "vb - g_inj[i].from < g_inj[i].count) return 1;", "0) return 1;"),
    "a notch between two reads lost": ("input_kbm_inject.c", "if (j->state == 2 || !started) continue;", "if (j->state == 2 || !on) continue;"),
    "the focus kept the buttons": ("    g_button[0] = g_button[1] = g_button[2] = 0;\n", ""),
    "the arrows lose the d-pad": ('"UP=UP+LSUP,DOWN', '"UP=LSUP,DOWN'),
    # mouse steer
    "steer on before the game asks": ("input_kbm_steer.c", "static int g_want, g_setting", "static int g_want = 1, g_setting"),
    "the game's off ignored": ("input_kbm_steer.c", "    g_want = on != 0;\n", "    g_want = 1;\n"),
    "the setting ignored": ("input_kbm_steer.c", "return g_want && g_setting && kbm_window_ok() &&", "return g_want && kbm_window_ok() &&"),
    "no sensitivity": ("input_kbm_steer.c", "* (float)g_sens /", "* (float)SENS_DEFAULT /"),
    "invert ignored": ("input_kbm_steer.c", "((a && g_invert) ?", "((a && 0) ?"),
    "the stick never quite back": ("input_kbm_steer.c", "s > -STEER_SNAP) s = 0.0f;", "s > -STEER_SNAP) s = s;"),
    "the moves add up for ever": ("input_kbm_steer.c", "        g_acc[a] = 0.0f;\n        out[a]", "        out[a]"),
    "forward is down": ("out->y = stick(out->y + sy)", "out->y = stick(out->y - sy)"),
    "the keys' stick replaced": ("out->x = stick(out->x + sx)", "out->x = stick(sx)"),
    "the middle drag steers too": ("    if (!g_button[1]) { /* the middle button up: mouse steer's, if it is on */\n        kbm_steer_motion(dx, dy);\n",
                                   "    kbm_steer_motion(dx, dy);\n    if (!g_button[1]) { /* the middle button up: mouse steer's, if it is on */\n"),
    "the menu keeps steering": ("int kbm_window_ok(void) { return !g_unfocused && !g_menu_open; }", "int kbm_window_ok(void) { return !g_unfocused; }"),
    "the focus lost keeps steering": ("    g_click_pending = 0;\n    g_unfocused = 1;\n", "    g_click_pending = 0;\n"),
    "MOUSEMOVE once": ("input_kbm_inject.c", "j->state = ++j->reads < j->count ? 1 : 2;", "j->state = 2;"),
    # the menu pointer
    "a resting pointer moves the selection": ("input_kbm_pointer.c", "    g_moved = 0; /* a pointer resting where the menu opens moves nothing */\n", ""),
    "the hover not followed": ("input_kbm_pointer.c", "if (at >= 0 && at != g_hover) g_target = at, g_steps = 0;", "if (0) g_target = at, g_steps = 0;"),
    "confirmed before the selection is there": ("input_kbm_pointer.c", "        if (at >= 0) g_click = at, g_target = -1, g_steps = 0;",
                                                "        if (at >= 0) g_click = g_menu.selected, g_target = -1, g_steps = 0;"),
    "a click off the entries confirms": ("input_kbm_pointer.c", "        if (at >= 0) g_click = at, g_target = -1, g_steps = 0;",
                                         "        g_click = at >= 0 ? at : g_menu.selected, g_target = -1, g_steps = 0;"),
    "the long way round": ("input_kbm_pointer.c", "if (d > n / 2) d -= n;", "if (0) d -= n;"),
    "never gives up": ("input_kbm_pointer.c", "++g_steps > g_menu.count + 1", "++g_steps > 1000"),
    "a row stepped up and down": ("input_kbm_pointer.c", "    if (g_menu.horizontal) return input_name_bit(dir > 0 ? \"RIGHT\" : \"LEFT\");\n", ""),
    "keys do not stop the pointer": ("input_kbm_pointer.c", "    if (keys_pressed) g_target = -1;", "    if (0) g_target = -1;"),
    "the held click leaks to the layout": ("input_kbm_pointer.c", "    return down && !g_left_eaten;", "    return down;"),
    "the menu never lapses": ("input_kbm_pointer.c", "if (g_fresh > 0 && --g_fresh == 0)", "if (0)"),
    "the wheel the layout's in a menu": ("    if (kbm_pointer_wheel(notches)) return; /* a menu is up: the notches step its selection */\n", ""),
    "the focus lost keeps the click": ("    g_unfocused = 1;\n    kbm_pointer_forget();\n", "    g_unfocused = 1;\n"),
    "steer in a menu": ("input_kbm_steer.c", " && !kbm_pointer_on(); }", "; }"),
    "the entry's press ignored": ("input_kbm_pointer.c", "start(g_menu.press[goal] ? g_menu.press[goal] : g_menu.confirm",
                                 "start(0 ? g_menu.press[goal] : g_menu.confirm"),
    "MOUSEPOS ignored": ("input_kbm_inject.c", "            input_kbm_mouse_position(j->dx, j->dy);\n", ""),
    # the game's own text field and mouse (input_kbm_mouse.c, kbm_game_test.c)
    "the field open, keys still press": ("default: held = g_text ? 0 : keys ?", "default: held = keys ?"),
    "TEXT= not typed": ("input_kbm_inject.c", "            host_text_put(j->text, (int)j->reads);\n", ""),
    "the game's mouse presses the layout": ("    if (kbm_game_mouse()) { /* the game's own (input_kbm_mouse.c), not the layout's */\n"
                                            "        kbm_game_button(which, down);\n        return;\n    }\n", ""),
    "the game's moves lost": ("input_kbm_mouse.c", "    if (g_m.captured) g_m.dx += dx, g_m.dy += dy;",
                              "    if (g_m.captured) g_m.dx = dx, g_m.dy = dy;"),
    # focus_lost and menu_open release it, and each read releases it again while the window is not the player's: one
    # plant for both, where the letting go happens
    "the game's mouse never let go": ("input_kbm_mouse.c", "    g_m.captured = 0, g_m.buttons = 0, g_swallow = 0;\n", ""),
    "the recapturing click is the game's": ("input_kbm_mouse.c", "g_m.captured = 1, g_swallow |= bit;",
                                            "g_m.captured = 1, g_m.buttons |= bit, g_m.pressed |= bit;"),
    "the moves never cleared": ("input_kbm_mouse.c", "    g_m.dx = g_m.dy = 0.0f, g_m.pressed = 0, g_m.wheel = 0;\n", ""),
}


def run(vcvars, sources, name, game, strict=False):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        for f, text in sources.items():
            (tmp / f).write_text(text, encoding="utf-8")
        if game:
            (tmp / "port_config.h").write_text(GAME_CONFIG, encoding="utf-8")
        srcs = f'"{ROOT / "tests" / "kbm_test.c"}" "{ROOT / "tests" / "kbm_game_test.c"}" ' +" ".join(f'"{tmp / f}"' for f in sources)
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f"cl /nologo /W4 {'/WX ' if strict else ''}{'/DGAME ' if game else ''}"
                                     f'/I"{tmp}" /I"{BASE}" {srcs} /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def text_checks():
    assert SRC_PATH.is_file(), "port/base/input_kbm.c: missing (the base has no keyboard and mouse layout)"
    inp = (BASE / "input.c").read_text(encoding="utf-8")
    body = inp[inp.index("u32 host_pads_read"):]
    for needle, why in [
        ("s[0] = read_keyboard(SDL_GetKeyboardState(NULL), &kbm);", "the real keyboard read only live"),
        ("} else if (input_kbm_injected()) {\n        s[0] = read_keyboard(NULL, &kbm);", "an injected test reads no real input"),
        ("pad[0].buttons |= HOST_PAD_MOUSE_LOOK;", "the mouse look not marked on the pad"),
        ("if (kbm.look && !s[0].rx && !s[0].ry) s[0].rx = kbm.rx, s[0].ry = kbm.ry;", "the look not the right stick"),
    ]:
        assert needle in body, f"input.c: {why}"
    assert body.index("HOST_PAD_MOUSE_LOOK") < body.index("session_pad(&pad[0])"), "input.c: the look marked after recording"
    assert "g_key_map" not in inp and "SDL_SCANCODE_Z" not in inp, "input.c: a key table of its own beside the layout"
    assert '"--kbm-", 6)) return input_kbm_option' in inp, "input.c: --kbm-* not passed on"
    win = (BASE / "host_window.cpp").read_text(encoding="utf-8")
    winm = win + (BASE / "host_mouse.cpp").read_text(encoding="utf-8")  # the mouse's events moved there
    for needle in ("input_kbm_mouse_button(which", "input_kbm_mouse_motion(e.motion.xrel", "input_kbm_mouse_wheel(notches)",
                   "input_kbm_focus_lost();", "input_kbm_focus_gained();", "input_kbm_capture_wanted() != 0 &&",
                   "SDL_SetWindowRelativeMouseMode(g_window, want);"):
        assert needle in winm, f"host_window.cpp/host_mouse.cpp: {needle} missing"
    mouse = (BASE / "host_mouse.cpp").read_text(encoding="utf-8")
    for needle in ("pointer_at(e.motion.windowID, e.motion.x, e.motion.y);", "gfx_screen_point(", "input_kbm_mouse_position(at[0], at[1]);"):
        assert needle in mouse, f"host_mouse.cpp: the menu pointer: {needle} missing"
    pump = win[win.index('extern "C" void gfx_pump(void)'):]
    assert "host::mouse_capture_sync();" in pump[:pump.index("\n}")], "host_window.cpp: the pointer not kept or let go as the game turns steer"
    assert win.count("SDL_SetWindowRelativeMouseMode(") == 1, "host_window.cpp: the pointer kept or let go outside mouse_capture_sync"
    menu = (BASE / "host_menu.cpp").read_text(encoding="utf-8") + (BASE / "host_menu_kbm.cpp").read_text(encoding="utf-8")
    assert 'L"&Keyboard and mouse"' in menu and 'settings_set(key, value);' in menu, "host_menu.cpp: no menu, or not kept"
    for needle, why in [('L"Mouse &steering', "no Mouse steering"), ('L"Mouse se&nsitivity"', "no Mouse sensitivity"),
                        ('L"&Invert mouse Y"', "no Invert mouse Y"), ("settings_set(input_kbm_steer_key(which)", "not kept"),
                        ("input_kbm_menu_open(msg == WM_ENTERMENULOOP);", "the menu does not let go of the pointer")]:
        assert needle in menu, f"host_menu.cpp: mouse steer: {why}"
    base_h = (BASE / "base.h").read_text(encoding="utf-8")
    host_h = (BASE / "host.h").read_text(encoding="utf-8")
    assert "void input_kbm_mouse_steer(int on);" in base_h, "base.h: a game cannot turn mouse steer on"
    assert "#define HOST_PAD_MOUSE_LOOK 0x40000000u" in base_h and "0x40000000u" not in host_h, \
        "the look's bit is one no device or script has (kit 2.10.0), so old recordings have it clear"
    assert "events_read();" in body[body.index("session_pad_other"):], \
        "input.c: the game's text, mouse and menu picks not taken at the pad read"
    ev = inp[inp.index("static void events_read(void)"):]
    assert "if (port_extra_drawing()) return;" in ev[:ev.index("\n}")], "input.c: events taken in an in-between frame"
    gc = "".join((ROOT / "port" / "tools" / f).read_text(encoding="utf-8") for f in ("game_config.py", "play_keys.py"))
    assert '"key_ramp"' in gc and "#define PORT_KEYS " in gc and "#define PORT_KEY_RAMP " in gc, "game_config.py: keys"


def main():
    text_checks()
    print("text checks: ok")
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skipped: no 32-bit MSVC here")
        return 0
    src = {f: (BASE / f).read_text(encoding="utf-8") for f in SRCS}
    for game in (False, True):
        rc, out = run(vcvars, src, f"the base ({'game' if game else 'kit'} layout)", game, strict=True)
        print(out)
        assert rc == 0, f"the base fails its own check ({'game' if game else 'kit'} layout)"
    missed = []
    for name, plant in PLANTS.items():
        f, old, new = plant if len(plant) == 3 else ("input_kbm.c",) + plant
        assert src[f].count(old) == 1, f"plant {name!r}: its text is not once in {f}"
        rcs = [run(vcvars, dict(src, **{f: src[f].replace(old, new)}), name, game)[0] for game in (False, True)]
        print(f"plant {name}: {'caught' if any(rcs) else 'MISSED'}")
        if not any(rcs):
            missed.append(name)
    assert not missed, f"plants not caught: {missed}"
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
