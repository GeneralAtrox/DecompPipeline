"""gen_saturn exit_regs for r1..r3 (robopit-port, 2026-10-31: fn_06084b5c ends `r2 = macl; r3 = macl` and its callers read
r2/r3 after the call; only r0 and r4..r14 reached them, so the title logo's sprite had size 0).
Controls:
1. a function that declares r2/r3 hands them on at every return (sh4_set_r) and its caller reloads them after each call;
2. a function that never declares r2 hands nothing on (the kit seeds the shared r1..r3 with the caller's at every frame
   start, so the caller's reload gets its own value back) and its callers still reload;
3. the default (4,) is unchanged: no r2/r3 lines without the config;
4. the kit seeds r1..r3 (sat_call.c run_frame) beside r4..r7.
Also gen_saturn's recursive wrapper (robopit-port, 2026-10-31: Robo Pit fn_0604b2a8 passed the constant 0x060fe000 to its body,
so stack-passed arguments read zeros and every list screen took the wrong branch):
5. `body(0x060fe000u, ...)` in a function's wrapper becomes `body(sat_entry_sp, ...)` through main(); a function without a
   wrapper and the body's own `r15 = sp` are untouched.
And the translator's compact form (robopit-port, 2026-11-01: the patterns read only the spaced form, so a function over the
size cap kept 0 for r0..r3 on entry, handed no register on and missed the popped-pr test):
6. a compact function gets the same rewrites as its spaced form, and the code after its declaration stays.
Run: python -W error tests/test_sat_exit_regs.py.
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import gen_saturn  # noqa: E402

CALLEE = """u32 fn_06084b5c(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = 0, r2 = 0, r3 = 0;
    r2 = 5; r3 = 6;
    return r0;
}
"""
CALLER = """u32 fn_06084bba(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = 0, r2 = 0, r3 = 0;
    r0 = tc_call(0x06084b5c, &a); r0 = r0 + 1;
    return r0;
}
"""
PLAIN = """u32 fn_06000100(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = 0, r1 = 0;
    return r0;
}
"""


RECURSIVE = """static u32 body(u32 sp, u32 ret_addr, u32 a0)
{
    u32 r0 = 0, r15 = 0, pr = 0;
    r15 = sp; pr = ret_addr;
    r0 = body(r15, 0x06000102u, a0);
    return r0;
}
u32 fn_06000100(u32 a0, u32 a1, u32 a2, u32 a3)
{
    return body(0x060fe000u, 0x060ff000u, a0);
}
"""

COMPACT = """u32 fn_06000200(u32 a0,u32 a1,u32 a2,u32 a3){u32 r0=0,r1=0,r2=0,r4=0,r15=0,pr=0;r1=r1+1;r15=0x060fe000u;
pr=rd32(r15);r15+=4u;r0=tc_call(0x06000300u,&a);if(pr==0x06000210u)goto L_1;r2=7;L_1:;return r0;}
"""
SPACED = """{
    u32 r0 = 0, r1 = 0, r5 = 0;
    r1 = 0;
}
"""
SPACED_LIVE = """{
    u32 r0 = sat_in_r(0), r1 = sat_in_r(1), r5 = 0;
    r1 = 0;
}
"""


def generated(text):
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        tx = d / "tx"
        tx.mkdir()
        (tx / "06000100.c").write_text(text, encoding="utf-8")
        game = d / "game.json"
        game.write_text(json.dumps({"saturn": {"image": "x", "image_base": "0x06000000", "entry": "0x06000100",
                                               "entry_sp": "0x060fe000"}}), encoding="utf-8")
        gen_saturn.main(["--game", str(game), "--tx", str(tx), "--out", str(d / "out")])
        return (d / "out" / "txchunk_00.c").read_text(encoding="utf-8")


def main():
    out = generated(RECURSIVE)
    assert "return body(sat_entry_sp, 0x060ff000u, a0);" in out and "0x060fe000u" not in out, out
    assert "r15 = sp;" in out and "body(r15, 0x06000102u, a0)" in out, out
    out = generated(PLAIN.replace("fn_06000100", "fn_06000100"))
    assert "body(" not in out, out
    out = gen_saturn.exit_regs(CALLEE, (4, 2, 3))
    assert "sh4_set_r(2, r2)" in out and "sh4_set_r(3, r3)" in out, out
    out = gen_saturn.exit_regs(CALLER, (4, 2, 3))
    assert out.count("r2 = sh4_r(2);") == 1 and out.count("r3 = sh4_r(3);") == 1, out
    out = gen_saturn.exit_regs(PLAIN, (4, 2, 3))
    assert "sh4_set_r(2" not in out and "sh4_set_r(3" not in out, out
    assert "sh4_set_r(4, a0)" in out
    out = gen_saturn.exit_regs(CALLEE, (4,))
    assert "sh4_set_r(2" not in out and "sh4_set_r(3" not in out, out
    src = (ROOT / "port" / "saturn" / "sat_call.c").read_text(encoding="utf-8")
    assert "c->r[1] = a->rlo[1], c->r[2] = a->rlo[2], c->r[3] = a->rlo[3];" in src
    out = gen_saturn.live_in(COMPACT)
    assert "u32 r0 = sat_in_r(0),r1 = sat_in_r(1),r2 = sat_in_r(2),r4=0,r15=0,pr=0;r1=r1+1;" in out, out
    out = gen_saturn.live_in(SPACED)
    assert out == SPACED_LIVE, out
    out = gen_saturn.exit_regs(COMPACT, (4, 2))
    assert "sh4_set_r(4, r4)" in out and "sh4_set_r(2, r2)" in out and "r4 = sh4_r(4);" in out and "r2 = sh4_r(2);" in out, out
    out = gen_saturn.pr_returns(COMPACT)
    assert "r15+=4u; sat_pr_popped();" in out, out
    assert "sat_window_back(r15); if (pr == 0x06000210u && !sat_pr_took()) goto L_1;" in out, out
    assert gen_saturn.frame_bound(COMPACT.replace("r15=0x060fe000u;", ""))[1] is False, COMPACT
    print("ok")


if __name__ == "__main__":
    main()
