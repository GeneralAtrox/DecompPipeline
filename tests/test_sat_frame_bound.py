"""gen_saturn's frame bound for the translator's compact form (robopit-port, 2026-11-01). A big function comes with its
`u32 r0=0,...;` on the function's first line; the bound took `r15=0,...` for a computed move and gave it the 8 KB
dynamic frame. Robo Pit's fn_06055404 then had its callees' frames down in memory SGL clears, its saved r14 was zeroed
and it popped its registers from address 0: r8 came back 0 where the title code keeps a flag in it, so every start
printed "Now saving" and wrote an empty robot table over the player's save.
Controls:
1. the compact form's bound is its pushes plus the margin, the same as the spread form's;
2. a real computed move is still marked computed;
3. plain restores (`r15=r14`) add nothing, in either form;
4. a function that calls itself (the translator's `body(sp, ...)`, entered with `r15 = sp`) is bound by its pushes, not
   the 8 KB dynamic frame, and its calls pass r15 so each level's callees start below that level's pushes. Saturn
   Bomberman's sound task (stack top 060bfe8c) runs the recursive 06035e94: with 8 KB its callees' frames sat on the CD
   library's work area (060bd9f8..060bdd68), their pushes overwrote its directory-change state, the CD server waited
   on it until its timer ran out, and Battle mode's music file never opened (GFS error, jump through BIOS vector 0).
5. a computed move (an SH-2 `sub rN,r15`, locals sized at run time) has no catch-all 8 KB frame either: the generated
   function reports its real sp after every write to r15 (sat_sp_at) and its table frame is the margin. With 8 KB, a
   callee placed without an sp and any interrupt taken inside it went 8 KB below its entry sp, the same overwrite as in
   4 (Saturn Bomberman had 64 such functions). The text stays valid C: a write inside `if (...) x; else` stays one
   statement, and the declaration line and comparisons are untouched.
Run: python -W error tests/test_sat_frame_bound.py.
Nearest existing: tests/test_sat_exit_regs.py - gen_saturn's other text changes, not the frame bound.
"""
import json
import pathlib
import re
import sys
import tempfile

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
BODY = """static u32 body(u32 sp, u32 ret_addr, u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = 0, r4 = 0, r8 = 0, r15 = 0, t = 0, pr = 0;
    TcArgs a = {{0, 0, 0, 0}};
    r15 = sp;
    r15 -= 4u;
    wr32(r15, r8);
    r15 -= 20u;
    if (r4) r0 = (u32)body(r15, 0x06035fe6u, r4, a1, a2, a3);
    a.r[0] = r4; r0 = tc_call(0x06036f40u, &a);
    r15 += 20u;
    r8 = rd32(r15); r15 += 4u;
    return r0;
}
u32 fn_06035e94(u32 a0, u32 a1, u32 a2, u32 a3)
{
    return body(sat_entry_sp, 0x060ff000u, a0, a1, a2, a3);
}
"""
COMPUTED = "u32 fn_06000200(u32 a0,u32 a1,u32 a2,u32 a3){u32 r0=0,r1=0,r15=0;\nr15-=r1;return r0;}\n"
# the judge form as the translator writes it: a fixed entry sp, a constant push, a computed move, a conditional restore
ALLOCA = """#include "tc_rt.h"
u32 fn_06000300(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = 0, r1 = 0, r2 = 0, r14 = 0, r15 = 0, t = 0, pr = 0;
    TcArgs a = {{0, 0, 0, 0}};
    r15 = 0x060fe000u;
    r15 -= 4u;
    wr32(r15, r14);
    r14 = r15;
    r1 = a0 << 2;
    r15 -= r1; /* r15 = locals */
    a.r[0] = r15; r0 = tc_call(0x06000400u, &a);
    t = r15 == r14;
    if (t) r15 = r14; else r15 += r1;
    r14 = rd32(r15); r15 += 4u;
    return r0;
}
"""
BOUND = ALLOCA.replace("fn_06000300", "fn_06000100").replace("    r1 = a0 << 2;\n    r15 -= r1; /* r15 = locals */\n", "").replace(
    "else r15 += r1;", "else r15 = r14;")


def generated(text, addr):
    """(the table's frame for addr, the function's generated text): gen_saturn run on the one function"""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "tx").mkdir()
        (d / "tx" / f"{addr:08x}.c").write_text(text, encoding="utf-8")
        game = {"saturn": {"image_base": "0x06004000", "image": "0.BIN", "entry": "0x06004000", "entry_sp": "0x06002000"}}
        (d / "game.json").write_text(json.dumps(game), encoding="utf-8")
        gen_saturn.main(["--game", str(d / "game.json"), "--tx", str(d / "tx"), "--out", str(d / "out")])
        table = (d / "out" / "sat_table.c").read_text(encoding="utf-8")
        row = re.search(r"\{0x%08xu, fn_%08x, (\d+)u" % (addr, addr), table)
        return int(row.group(1)), (d / "out" / "txchunk_00.c").read_text(encoding="utf-8")


def test_computed_tracks_sp():
    frame, text = generated(ALLOCA, 0x06000300)
    assert frame == gen_saturn.MARGIN, f"a computed move's table frame is {frame}: the kit is told its real sp instead"
    for line in ("r15 = sat_entry_sp, sat_sp_at(r15);", "r15 -= 4u, sat_sp_at(r15);",
                 "r15 -= r1, sat_sp_at(r15); /* r15 = locals */",
                 "if (t) r15 = r14, sat_sp_at(r15); else r15 += r1, sat_sp_at(r15);",
                 "r14 = rd32(r15); r15 += 4u, sat_sp_at(r15);",
                 "r14 = 0, r15 = 0, t = 0, pr = 0;", "t = r15 == r14;",
                 "tc_call_sp(0x06000400u, &a, r15)"):
        assert line in text, f"missing {line!r} in\n{text}"
    assert text.count("sat_sp_at") == 6, text
    frame, text = generated(BOUND, 0x06000100)
    assert frame == 4 + gen_saturn.MARGIN and "sat_sp_at" not in text, (frame, text)  # a bounded function is unchanged


def main():
    test_vblank_waits()
    test_computed_tracks_sp()
    compact, spread = gen_saturn.frame_bound(COMPACT), gen_saturn.frame_bound(SPREAD)
    assert compact == spread == (8 + gen_saturn.MARGIN, False), (compact, spread)
    assert gen_saturn.frame_bound(COMPUTED) == (gen_saturn.MARGIN, True)
    body = gen_saturn.frame_bound(BODY)
    assert body == (24 + gen_saturn.MARGIN, False), f"a recursive body's frame {body}: its pushes are 24 bytes"
    called = gen_saturn.pass_call_sp(BODY)
    assert "tc_call_sp(0x06036f40u, &a, r15)" in called and "tc_call(" not in called, called
    assert gen_saturn.pass_call_sp(COMPUTED) == COMPUTED  # no entry sp: nothing to pass
    print("ok: compact and spread forms bound the same, a computed move reports its real sp (no 8 KB frame), "
          "a recursive body is bound by its pushes")


def test_vblank_waits():
    loop = "    do {\n        r0 = (u32)(s32)(s8)rd8(sh4_stc_gbr() + 19u);\n        t = (s32)r0 >= 0;\n    } while (t);\n"
    got = gen_saturn.vblank_waits(loop, [19])
    assert "t = (s32)r0 >= 0; if (t) sat_vblank_wait();" in got, got
    assert gen_saturn.vblank_waits(loop, [18]) == loop  # another byte is not a wait on this flag
    plain = "    r0 = (u32)(s32)(s8)rd8(sh4_stc_gbr() + 19u);\n    t = (s32)r0 >= 0;\n    return r0;\n"
    assert gen_saturn.vblank_waits(plain, [19]) == plain  # a read outside a loop is no wait


if __name__ == "__main__":
    main()
