"""The thread of in-between frames' Present never waits for the display holding gfx_lock (port/base/present.c,
--present-nowait). The user's laptop (2026-11-13, Toy Commander game 09b2487 on kit a2c44a92, 144 Hz, vsync on): each
Present on that thread waited up to a refresh (longest Present 7.0 ms) holding gfx_lock, and the game's thread waited
400-700 times in 5 s for it, 1.7-4.5 s in all: 300-480 ticks in 10 s, not 600. Now that Present comes back at once
(DXGI_PRESENT_DO_NOT_WAIT) and the thread makes it again holding no lock between tries.
Checks, as text: the Present is made with DO_NOT_WAIT; one that came back uses no count; the retry holds no lock between
tries and comes before the thread's hold; the thread turns it on; the log counts the Presents that would have waited;
CONTRACT.md lists --present-nowait.
Controls: each planted copy of the waiting Present must fail the check.
Run: python -W error tests/test_present_nowait.py.
Nearest existing: tests/test_show_count.py - the frame-latency object's counts, not the Present's wait.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

PLANTS = {
    "the Present waits again": ("flags | (nowait ? DXGI_PRESENT_DO_NOT_WAIT : 0u)", "flags"),
    "a refused Present uses a count": ("        g_pending = 1;\n        return;", "        g_pending = 1;"),
    "the retry holds no lock between tries": ("if (gfx_lock_wanted() || gfx_present_retry()) pace_sleep_us(250u);",
                                              "gfx_lock(); while (gfx_present_retry()) pace_sleep_us(250u);"),
}


def check(pres, thread):
    assert "flags | (nowait ? DXGI_PRESENT_DO_NOT_WAIT : 0u)" in pres, "present.c: the Present can still wait for the display"
    i = pres.index("if (hr == DXGI_ERROR_WAS_STILL_DRAWING) {")
    j = pres.index("if (!test) show_count_used(g_gfx.latency);")
    assert i < j and "return;" in pres[i:j], "present.c: a Present that came back uses a count (none was queued)"
    assert "if (gfx_lock_wanted() || gfx_present_retry()) pace_sleep_us(250u);" in thread, \
        "frame_thread.c: a refused Present is not made again with the lock let go"
    assert thread.index("gfx_present_pending()") < thread.index("if (!g_go) {"), \
        "frame_thread.c: a refused Present waits while the thread is held"
    assert "gfx_present_nowait(gfx_present_nowait_wanted());" in thread, "frame_thread.c: the thread does not turn it on"
    assert "would have waited for the display" in pres, "present.c: the log does not count the Presents that would wait"


def main():
    pres = (ROOT / "port" / "base" / "present.c").read_text(encoding="utf-8")
    thread = (ROOT / "port" / "runtime" / "frame_thread.c").read_text(encoding="utf-8")
    check(pres, thread)
    print("present: a Present that would wait comes back and is made again with the lock let go: ok")
    missed = []
    for name, (old, new) in PLANTS.items():
        assert (pres + "\n" + thread).count(old) == 1, f"plant {name!r}: its text is gone"
        try:
            check(pres.replace(old, new), thread.replace(old, new))
            missed.append(name)
            print(f"plant {name}: MISSED")
        except AssertionError as e:
            print(f"plant {name}: caught ({str(e)[:90]})")
    assert not missed, f"plants missed: {missed}"
    contract = (ROOT / "port" / "CONTRACT.md").read_text(encoding="utf-8")
    assert "`--present-nowait on\\|off`" in contract, "CONTRACT.md: --present-nowait not listed"
    print("OK")


if __name__ == "__main__":
    main()
