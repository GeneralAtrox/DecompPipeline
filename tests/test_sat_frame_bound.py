"""gen_saturn's frame bound for the translator's compact form (robopit-port, 2026-11-01). A big function comes with its
`u32 r0=0,...;` on the function's first line; the bound took `r15=0,...` for a computed move and gave it the 8 KB
dynamic frame. Robo Pit's fn_06055404 then had its callees' frames down in memory SGL clears, its saved r14 was zeroed
and it popped its registers from address 0: r8 came back 0 where the title code keeps a flag in it, so every start
printed "Now saving" and wrote an empty robot table over the player's save.
Controls:
1. the compact form's bound is its pushes plus the margin, the same as the spread form's;
2. a real computed move is still dynamic;
3. plain restores (`r15=r14`) add nothing, in either form.
Run: python -W error tests/test_sat_frame_bound.py.
Nearest existing: tests/test_sat_exit_regs.py - gen_saturn's other text changes, not the frame bound.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import gen_saturn  # noqa: E402

COMPACT = "u32 fn_06055404(u32 a0,u32 a1,u32 a2,u32 a3){u32 r0=0,r8=0,r14=0,r15=0,t=0,pr=0;\nr15-=4u;wr32(r15,r8);r15-=4u;wr32(r15,r14);r14=r15;r15=r14;return r0;}\n"
SPREAD = """u32 fn_06000100(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = 0, r8 = 0, r14 = 0, r15 = 0, t = 0, pr = 0;
    r15 -= 4u;
    wr32(r15, r8);
    r15 -= 4u;
    wr32(r15, r14);
    r14 = r15;
    r15 = r14;
    return r0;
}
"""
COMPUTED = "u32 fn_06000200(u32 a0,u32 a1,u32 a2,u32 a3){u32 r0=0,r1=0,r15=0;\nr15-=r1;return r0;}\n"


def main():
    test_vblank_waits()
    compact, spread = gen_saturn.frame_bound(COMPACT), gen_saturn.frame_bound(SPREAD)
    assert compact == spread == (8 + gen_saturn.MARGIN, False), (compact, spread)
    assert gen_saturn.frame_bound(COMPUTED) == (gen_saturn.DYNAMIC, True)
    print("ok: compact and spread forms bound the same, a computed move stays dynamic")


def test_vblank_waits():
    loop = "    do {\n        r0 = (u32)(s32)(s8)rd8(sh4_stc_gbr() + 19u);\n        t = (s32)r0 >= 0;\n    } while (t);\n"
    got = gen_saturn.vblank_waits(loop, [19])
    assert "t = (s32)r0 >= 0; if (t) sat_vblank_wait();" in got, got
    assert gen_saturn.vblank_waits(loop, [18]) == loop  # another byte is not a wait on this flag
    plain = "    r0 = (u32)(s32)(s8)rd8(sh4_stc_gbr() + 19u);\n    t = (s32)r0 >= 0;\n    return r0;\n"
    assert gen_saturn.vblank_waits(plain, [19]) == plain  # a read outside a loop is no wait


if __name__ == "__main__":
    main()
