"""Guest task switching (port/saturn/sat_task.c). A function resumed by a longjmp runs again from its start, so its
prologue pushes r8..r14 again over the words its epilogue pops: the kit ran it with the longjmp's r8..r14 (the
setjmp-time values), and KRNL's yield 06006d36 handed Saturn Bomberman's main task r13 = 26006020 instead of its own
pointer 06006d36, so the task's next yield was STUB 26006020 (round 6, 2026-11-16). tests/sat_task_test.c builds the
file at /W4 /WX in 32-bit MSVC with KRNL's own setjmp and longjmp words and checks the caller's r13 after a resume.
Controls: the check must fail on each planted copy of a wrong behaviour; the first plant is the kit before the fix.
Skips when no 32-bit MSVC is installed here.
With a window the thread is already a fiber when the kit first switches (port/base/host_modal.cpp): ConvertThreadToFiber
then fails, and the NULL the kit kept as the CPU's own fiber crashed Dragon Force's windowed launch in SwitchToFiber after
8 vertical blanks (2026-11-16). The test runs again with the thread made a fiber first (argument "fiber"), and every
ConvertThreadToFiber in port/saturn must ask IsThreadAFiber first.
Run: python -W error tests/test_sat_task.py.
Nearest existing: tests/test_sat_dmac.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

KIT = ROOT / "port" / "saturn"
SRC = (KIT / "sat_task.c").read_text(encoding="utf-8")
PLANTS = {
    "a resumed function starts with the longjmp's r8..r14 (the kit before the fix)":
        ("        for (int i = 0; i < 7; i++) c->r[8 + i] = p->entry_r[i];", ""),
    "the setjmp does not return the longjmp's value on the resume": ("return f->replay_val;", "return 0;"),
    "a buffer the setjmp saved is started as a new task": ("b->prw == prw && b->spw == spw", "0"),
    "a thread a window already made a fiber gets a NULL fiber (the kit before 2026-11-16)":
        ("IsThreadAFiber() ? GetCurrentFiber() : ConvertThreadToFiber(NULL)", "ConvertThreadToFiber(NULL)"),
}


def run(vcvars, source, name, args=()):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "sat_task.c").write_text(source, encoding="utf-8")
        inc = f'/I"{tmp}" /I"{KIT}" /I"{ROOT / "port" / "base"}" /I"{ROOT / "targets" / "saturn_sh2" / "runtime"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f'cl /nologo /W4 /WX {inc} "{ROOT / "tests" / "sat_task_test.c"}" /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99"]) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
        assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
        try:  # a planted fault can leave a task fiber starting over for ever: a hang is a FAIL, not a stall
            r = subprocess.run([str(tmp / "t.exe"), *args], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return 1, "FAIL: hung (30 s)"
    return r.returncode, r.stdout


def main():
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skip: no 32-bit MSVC here")
        return 0
    bare = [f"{f.name}: {ln.strip()}" for f in sorted(KIT.glob("*.c")) for ln in f.read_text(encoding="utf-8").splitlines()
            if "ConvertThreadToFiber(" in ln and "IsThreadAFiber()" not in ln]
    assert not bare, f"ConvertThreadToFiber without IsThreadAFiber (NULL when a window made the thread a fiber): {bare}"
    for args in ((), ("fiber",)):
        rc, out = run(vcvars, SRC, "the kit", args)
        print(out[-1500:])
        assert rc == 0 and "all ok" in out, f"the kit {args}: rc {rc}\n{out}"
    for name, (old, new) in PLANTS.items():
        assert SRC.count(old) == 1, f"{name}: the plant's text is gone from sat_task.c"
        rc, out = run(vcvars, SRC.replace(old, new), name, ("fiber",))
        assert rc != 0 and ("FAIL" in out or "all ok" not in out), f"planted '{name}' was not caught:\n{out}"
        print("caught:", name)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
