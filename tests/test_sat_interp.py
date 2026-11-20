"""The SH-2 interpreter fallback (port/saturn/sat_interp.c, --fallback on). A call from translated code to an address with
no translated function was a STUB that returned 0 and the game stalled: five of Saturn Bomberman's eight onboarding rounds
(2026-11-16) were such a stop. tests/sat_interp_test.c builds the kit's real sat_call.c with sat_interp.c at /W3 /WX in
32-bit MSVC, plants untranslated SH-2 code in RAM and checks: off, a STUB returning 0; on, the code runs, a translated
callee wins, nested untranslated code runs, r8 comes back, DIV1 and DMULS.L are right, a guarded overlay translation
whose code is not in RAM (gen_saturn.py --guard, sat_absent) runs the code that is there, and five addresses are counted;
--fallback check (translated function run both ways from the same state) finds the one planted difference and no other.
A jump from an open frame (tc_tail_sp) to an untranslated task longjmp goes to sat_task.c's hook first, as tc_call does:
interpreted, Saturn Bomberman's KRNL longjmp 060061e6 switched no fiber and every run stopped at vertical blank 6 (2026-11-17).
An interpreted branch to an address whose translation is another overlay's (its guard fails) is declined and runs on
in the interpreter: Dragon Force's battle loop (DF.BIN's bf/s back to /0's 06010cb0) nested a call per turn and stopped
at "call depth over 400" at vertical blank 12,120 (2026-11-17).
Controls: the check must fail on each planted copy of a wrong behaviour; the first plant is the kit before the change.
Skips when no 32-bit MSVC is installed here.
Run: python -W error tests/test_sat_interp.py.
Nearest existing: tests/test_sat_task.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
FILES = {n: (KIT / n).read_text(encoding="utf-8") for n in ("sat_call.c", "sat_interp.c")}
PLANTS = {
    "a missing function is a STUB that returns 0 (the kit before the change)":
        ("sat_call.c", "        if (sat_fallback_on && sat_fallback_ok(target)) return fallback_run(", "        if (0) return fallback_run("),
    "a translated function does not win over the interpreter": ("sat_interp.c", "if (translated(tgt) && call_out(&x, tgt)) {", "if (0) {"),
    "a call out of the interpreter does not hand r1..r14 to translated C":
        ("sat_interp.c", "    for (int i = 1; i < 15; i++) c->r[i] = x->R[i];\n    c->sp = x->R[15];", "    c->sp = x->R[15];"),
    "DIV1 ignores the old Q bit": ("sat_interp.c", "if (oq == M) {", "if (M == 0) {"),
    "an absent overlay translation stays a STUB with the fallback on":
        ("sat_call.c", "    if (sat_fallback_on && sat_fallback_ok(addr)) { /* the interpreter runs the code that is there",
         "    if (0) { /* the interpreter runs the code that is there"),
    "the interpreter fetches its code through the bus (each fetch a guest read)":
        ("sat_interp.c", "static u32 fetch(u32 pc) { return code16(pc & 0xdfffffffu); }", "static u32 fetch(u32 pc) { return rd16(pc & 0xdfffffffu); }"),
    "a jump from an open frame to a task longjmp is interpreted, skipping the task hook (the kit at 6ffdb1647)":
        ("sat_call.c", "    if (sat_task_on && sat_task_call(t, a, &r)) return r;\n", ""),
    "an interpreted branch to a translation not in memory nests a call per turn (the kit before the decline, ead17b73b)":
        ("sat_call.c", "    if (sat_interp_declines(addr)) return 0;", "    if (0) return 0;"),
    "an address with a translation per overlay always runs the first (one table row per address)":
        ("sat_call.c", "            if (sat_fn_resident(&sat_fns[i])) return &sat_fns[i];", "            if (0) return &sat_fns[i];"),
    "--fallback check never reports a difference": ("sat_call.c", "        if (!rd && !nh && !nl) {", "        if (1) {"),
}


def run(vcvars, files, name):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        for n, text in files.items():
            (tmp / n).write_text(text, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W3 /WX {inc} "{ROOT / "tests" / "sat_interp_test.c"}" /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99"]) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
        assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
        try:  # a planted fault can loop the interpreter: a hang is a FAIL, not a stall
            r = subprocess.run([str(tmp / "t.exe")], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return 1, "FAIL: hung (30 s)"
    return r.returncode, r.stdout


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    rc, out = run(vcvars, FILES, "the kit")
    print(out[-2500:])
    assert rc == 0 and "all ok" in out, out
    for name, (fn, old, new) in PLANTS.items():
        assert FILES[fn].count(old) == 1, f"{name}: the plant's text is gone from {fn}"
        rc, out = run(vcvars, {**FILES, fn: FILES[fn].replace(old, new)}, name)
        assert rc != 0 and "FAIL" in out, f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("test_sat_interp: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
