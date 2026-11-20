"""The counts taken from the swap chain's frame-latency object (port/base/show_count.c). The user (2026-11-12, Toy
Commander, a 16-vehicle battle): "Rendering stopped". The game ran on (ticks, input, sound) and nothing was presented
again: present.c kept the counts as one flag that the tick's thread and the thread that shows frames both read and
wrote with no lock, a count taken beside the other thread's was lost, and the swap chain has only two.
tests/show_count_test.c builds show_count.c alone at /W4 /WX in 32-bit MSVC, with a semaphore of 2 as the object and
the other thread's step run in the middle of this thread's wait (both interleavings), then two real threads.
As text: present.c keeps no flag of its own, a test present (a hidden window) uses no count, and a second with nothing
presented is logged and ended by one Present.
Controls: the check must fail on each planted copy of the old flag. Skips the C build when no 32-bit MSVC is here.
Run: python -W error tests/test_show_count.py.
Nearest existing: tests/test_kbm.py - the same way of building a kit file with a C test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402

BASE = ROOT / "port" / "base"
SRC_PATH = BASE / "show_count.c"
TAKE = "if (SHOW_COUNT_WAIT((HANDLE)latency, (DWORD)wait_ms) == WAIT_OBJECT_0) InterlockedIncrement(&g_held);"
PLANTS = {
    "two counts taken, kept as one flag": ("InterlockedIncrement(&g_held);", "g_held = 1;"),
    "a failed look wipes the other thread's count": (
        TAKE, "g_held = SHOW_COUNT_WAIT((HANDLE)latency, (DWORD)wait_ms) == WAIT_OBJECT_0;"),
}


def run(vcvars, source, name, strict=False):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "show_count.c").write_text(source, encoding="utf-8")
        srcs = f'"{ROOT / "tests" / "show_count_test.c"}" "{tmp / "show_count.c"}"'
        bat = tmp / "b.bat"
        bat.write_text(chr(10).join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                                     f"cl /nologo /W4 {'/WX ' if strict else ''}/DSHOW_COUNT_WAIT=test_wait "
                                     f'/I"{BASE}" {srcs} /Fe:t.exe',
                                     "if errorlevel 1 exit /b 99", f'"{tmp}\\t.exe"']) + chr(10))
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    assert r.returncode != 99, f"{name}: does not build:\n{r.stdout}{r.stderr}"
    return r.returncode, r.stdout


def text_checks():
    assert SRC_PATH.is_file(), "port/base/show_count.c: missing"
    pres = (BASE / "present.c").read_text(encoding="utf-8")
    assert "g_can_show" not in pres, "present.c: a flag of its own for the counts again (two threads write it)"
    assert "WaitForSingleObject(g_gfx.latency" not in pres, "present.c: takes a count past show_count.c"
    for needle, why in [
        ("int gfx_can_show(void) { return show_count_take(g_gfx.latency, 0); }", "gfx_can_show not through show_count"),
        ("void gfx_show_wait(void) { show_count_take(g_gfx.latency, 2); }", "gfx_show_wait not through show_count"),
        ("if (!test) show_count_used(g_gfx.latency);", "a test present (a hidden window) uses up a count"),
        ("(!gfx_can_show() && !show_overdue())", "a second with nothing presented is not ended"),
        ('"gfx: nothing presented for %.1f s: the display takes no frame', "the log has no line for a stopped picture"),
    ]:
        assert needle in pres, f"present.c: {why}"
    assert pres.index("int test = occluded;") < pres.index("IDXGISwapChain1_Present(g_gfx.swap, test ?"), \
        "present.c: the count's use is not decided by the present that was made"


def main():
    text_checks()
    print("text checks: ok")
    vcvars = game_config.vcvars32()
    if vcvars is None:
        print("skipped: no 32-bit MSVC here")
        return 0
    src = SRC_PATH.read_text(encoding="utf-8")
    rc, out = run(vcvars, src, "the base", strict=True)
    print(out)
    assert rc == 0, "the base fails its own check"
    missed = []
    for name, (old, new) in PLANTS.items():
        assert old in src, f"plant {name!r}: its text is gone from show_count.c"
        rc, out = run(vcvars, src.replace(old, new), name)
        print(f"plant {name}: {'caught' if rc else 'MISSED'}")
        print("  " + out.strip().replace(chr(10), chr(10) + "  "))
        if not rc:
            missed.append(name)
    assert not missed, f"plants not caught: {missed}"
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
