"""Save states, "save anywhere" (port/base/save_state.c, save_state_slots.c; File > Save state / Load state, F5 / F8, Game > Save states;
stages 2 to 4, 2026-11-18). The user: "As emulators suggest, dont rely on this 'Save States'. It should be kept separate to the in-game
save." tests/save_state_test.c builds both at /W4 /WX in 32-bit MSVC against stubs and checks: the file made and
read back block for block; refused whole when damaged, cut, not a state, of another format, with a block past its end
or missing, or made by another game, build or device layout; the slots in <data>/states written only at the kit's safe
point (save_state_poll), nothing else written in the data folder and the game's own save unchanged; not offered in a
replay, a check or an online match, nor with the option "save-states" off; a load reported to the kit (save_state_poll
returns 1); a request the kit drops; --state-save / --state-load VB:SLOT coming due at that vertical blank, the fixed
clock's too; F5 / F8 on the slot last used, yielding to the player's keyboard layout; the menu's two 8-slot entries and
the note, greyed, with each slot's time.
The rest is checked as text: File's order (Restart, Save state, Load state, the note, Exit), the menu greying an entry
as available() says when it opens and showing the slots' times, the window's F5 / F8, no port's key layout in this
repository using F5 or F8, the option listed, and the Saturn's (stage 4): the open frames' return addresses, r8..r14 and
entry sp in its SH2 block, no backup RAM, the safe point in tc_call on the master, the game thread's resume point, the
test aid's tick and the generator counting a recursive function's levels.
Controls: the check must fail on each planted copy of a wrong behaviour. Skips the C build when no 32-bit MSVC is here.
Run: python -W error tests/test_save_state.py.
Nearest existing: tests/test_autoload.py - the same way of building a base file with a C test.
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

BASE = ROOT / "port" / "base"
SAT = ROOT / "port" / "saturn"
FILES = ("save_state.c", "save_state_slots.c")
SRCS = {f: (BASE / f).read_text(encoding="utf-8") if (BASE / f).exists() else "" for f in FILES}  # "": the code before
NOTE = "Save states are separate from the game's own save. Keep saving in the game too."
PLANTS = {  # name: (old, new) in save_state.c, or (file, old, new)
    "the hash not checked": ("memcmp(hash, data + size - TRAILER, 32))", "0)"),
    "the identity not checked": ("|| !same_field(data + 160, identity(), 96))", ")"),
    "the block count not checked": ("if (n != want || at != end)", "if (at != end)"),
    "offered in a replay": ("save_state_slots.c", "return save_state_has_provider() && !session_replaying() &&", "return save_state_has_provider() &&"),
    "done at once, not at the safe point": ("save_state_slots.c", "    InterlockedExchange(&save_state_pending, (LONG)(kind << 8 | slot));\n",
                                           "    if (kind == SAVE_STATE_SAVE) do_save(slot); else do_load(slot);\n"),
    "F5 taken from the game": ("save_state_slots.c", "&& input_kbm_get(i)) return;", "&& 0) return;"),
    "the option ignored": ("save_state_slots.c", 'port_option("save-states") != 0', "1"),
    "a load not reported": ("save_state_slots.c", "    return do_load((int)(r & 0xff));\n", "    do_load((int)(r & 0xff));\n    return 0;\n"),
    "a test request never due": ("save_state_slots.c", "vblank >= g_test_vb[k]", "vblank > g_test_vb[k] + 1000000u"),
    "the note offered": ("save_state_slots.c", "NULL, NULL, 0, none, 0}, /* always greyed", "NULL, NULL, 0, NULL, 0}, /* always greyed"),
}


def run(vcvars, sources, name):
    work = ROOT / ".work" / "tmp" / "save-state-test"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        tmp = pathlib.Path(tmp)
        for f, text in sources.items():
            (tmp / f).write_text(text, encoding="utf-8")
        data = tmp / "data"
        data.mkdir()
        srcs = " ".join(f'"{p}"' for p in [ROOT / "tests" / "save_state_test.c"] + [tmp / f for f in FILES])
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX /I"{BASE}" {srcs} /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe" "{data}"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def read(p):
    return p.read_text(encoding="utf-8") if p.exists() else ""


def text_checks():
    extra = (BASE / "host_menu_extra.cpp").read_text(encoding="utf-8")
    assert "save_state_menu(&ns)" in extra, "File: no Save state or Load state"
    at = extra.index("g_extra_all.assign(std::begin(k_file), std::end(k_file));")
    assert at < extra.index("save_state_menu(&ns)"), "File: Restart first"
    assert extra.index("save_state_menu(&ns)") < extra.index("if (items) g_extra_all.insert"), "File: the states before the console's"
    menu = (BASE / "host_menu.cpp").read_text(encoding="utf-8")
    attach = menu[menu.index("void menu_attach(void *hwnd)"):]
    assert attach.index('append_extras(file, "File", false);') < attach.index("kExit, L\"E&xit"), "File: Exit last"
    assert "save_state_labels();" in menu and "ModifyMenuW(g_menu, kExtra + 16 * i + n" in menu, "the slots' times not shown"
    assert "MF_BYPOSITION | (e.available() ? MF_ENABLED : MF_GRAYED)" in menu, "an entry not greyed as available() says now"
    win = (BASE / "host_window.cpp").read_text(encoding="utf-8")
    assert "save_state_key(e.key.key == SDLK_F8)" in win and "SDLK_F5 || e.key.key == SDLK_F8" in win, "F5 / F8"
    assert f'#define SAVE_STATE_NOTE "{NOTE}"' in read(BASE / "save_state.h"), "the note's words"
    for p in list(BASE.glob("*.c")) + list(BASE.glob("*.cpp")) + list((ROOT / "port").glob("*/*.c")):
        if p.name in ("save_state_slots.c", "host_window.cpp") or "third_party" in p.parts:
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r'SDLK_F[58]\b|"F[58]=|,F[58]=', t), f"{p.name}: F5 or F8 is already a key there"
    assert "&port_state_options" in read(BASE / "game_options.c"), "Game > Save states not listed"
    sat = read(SAT / "sat_state.c")
    call = read(SAT / "sat_call.c")
    frames = call[call.index("void sat_call_state(SatSt *io)"):]
    assert all(s in frames for s in ("m->site[k]", "m->saved[k]", "m->ftop[k]", "m->stack[k]")), "Saturn: the open frames' resume points"
    assert "pend_visit(&io), sat_call_state(&io);" in sat, "Saturn: the SH2 block"
    assert "if (save_state_pending && cur == &g_cpu[MASTER]) sat_state_point(target, a, site, csp);" in call, "Saturn: the safe point"
    main = read(SAT / "sat_main.c")
    assert "if (!setjmp(sat_state_jb))" in main and "sat_state_resume();" in main, "Saturn: the game thread's resume point"
    assert "save_state_tick(g_vblanks);" in read(SAT / "sat_sys.c"), "Saturn: the test aid never comes due"
    assert "sat_body_out(body((sat_body_in(), r15)," in read(ROOT / "port" / "tools" / "gen_saturn.py"), "Saturn: recursion not counted"
    assert not re.search(r"bup|backup", sat.split("*/", 1)[-1], re.I), "Saturn: the game's own save in a state"
    assert "sat_fns[i].addr" in sat, "Saturn: the identity does not follow the code"


def main():
    text_checks()
    print("text checks ok")
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, SRCS, "the base")
    print(out[-2500:])
    assert rc == 0 and "all ok" in out, out
    for name, plant in PLANTS.items():
        f, old, new = plant if len(plant) == 3 else ("save_state.c",) + plant
        assert SRCS[f].count(old) == 1, f"{name}: the plant's text is gone from {f}"
        rc, out = run(vcvars, dict(SRCS, **{f: SRCS[f].replace(old, new)}), name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
