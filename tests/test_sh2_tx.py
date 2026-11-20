"""sh2-translate fixes from the Saturn failures (saturn-tx round, 2026-10-30), without Ghidra: each test translates a
few hand-assembled SH-2 instructions and checks the C the judge needs.

Nearest existing: tests/test_sh2_fixes.py - the same helper; kept in its own file so two rounds' tests do not collide.

usage: python tests/test_sh2_tx.py
"""
import json
import pathlib
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
SH2 = TOOL / "targets" / "saturn_sh2"
sys.path.insert(0, str(SH2 / "translator"))
from sh2translate.bodyfix import drop_dead_tail  # noqa: E402
from sh2translate.translate import Image, Signature, TargetFacts, translate_function  # noqa: E402

FACTS = TargetFacts.from_target_json(json.loads((SH2 / "target.json").read_text(encoding="utf-8")))


def c_of(code, hi, ret="void", callees=None):
    m = {"name": "fn_06000000", "signature": f"{ret} fn_06000000(void)", "params": [], "ret": ret}
    if callees is not None:
        m["callees"] = callees
    return translate_function(Image(bytes.fromhex(code), FACTS.image_base), 0x06000000, Signature.from_meta(m), FACTS,
                              [(0x06000000, 0x06000000 + hi)], entries={0x06000000})


class DeadEpilogue(unittest.TestCase):
    # L: mov.l @r4,r0; bra L; nop; rts; nop  (the rts is never reached)
    CODE = "6042" "AFFD" "0009" "000B" "0009"

    def test_no_return_after_endless_loop(self):
        src = c_of(self.CODE, 0xA, "u32")
        self.assertIn("for (;;)", src)
        self.assertNotIn("return r0;", src)

    def test_loop_with_exit_keeps_return(self):
        # L: mov.l @r4,r0; tst r0,r0; bf L; nop  -> falls out into rts
        src = c_of("6042" "2008" "8BFC" "0009" "000B" "0009", 0xC, "u32")
        self.assertIn("return r0;", src)

    def test_lines(self):
        body = ["    for (;;) {", "        x();", "    }", "    return r0;"]
        self.assertEqual(drop_dead_tail(body), body[:3])
        broken = ["    for (;;) {", "        if (c) break;", "    }", "    return r0;"]
        self.assertEqual(drop_dead_tail(broken), broken)
        inner = ["    for (;;) {", "        for (;;) {", "            break;", "        }", "    }", "    return r0;"]
        self.assertEqual(drop_dead_tail(inner), inner[:5])
        labelled = ["    for (;;) {", "    }", "L_06000010:;", "    return r0;"]
        self.assertEqual(drop_dead_tail(labelled), labelled)


class FrameAddressArg(unittest.TestCase):
    # mov.l @(pool,pc),r1; jsr @r1; mov r15,r4 (delay slot); rts; nop; nop; pool: .long 0x06001000
    CODE = "D102" "410B" "64F3" "000B" "0009" "0009" "06001000"

    def test_stack_address_goes_on_when_callee_lists_only_r0(self):
        """The callee's meta lists r0 only, so r4 went as 0; the original passes its own stack address there and the
        lint (R12, core/frame_args) refuses the 0 (Robo Pit 06005498)."""
        src = c_of(self.CODE, 0x10, callees=[{"addr": "06001000", "regs": ["r0"]}])
        self.assertIn("a.r[0] = r4;", src)

    def test_other_values_stay_zero(self):
        # mov #5,r4 in the delay slot: not a frame address
        src = c_of(self.CODE.replace("64F3", "E405"), 0x10, callees=[{"addr": "06001000", "regs": ["r0"]}])
        self.assertNotIn("a.r[0] = r4;", src)

    def test_pass_lines(self):
        from sh2translate.bodyfix import pass_frame_args
        body = ["    r4 = r15 + 8u;", "    sh4_set_r(8, r8);", "    a.rlo[0] = r0;", "    r0 = tc_call(0x06001000u, &a);"]
        self.assertEqual(pass_frame_args(body)[3], "    a.r[0] = r4;")
        self.assertEqual(len(pass_frame_args(body)), 5)
        body[0] = "    r4 += 4u;"
        self.assertEqual(pass_frame_args(body), body)
        body[0] = "    r4 = r15;"
        body.insert(1, "    if (t) {")
        self.assertEqual(pass_frame_args(body), body)
        body = ["    r4 = r15;", "    a.r[0] = r4;", "    r0 = tc_call(0x06001000u, &a);"]
        self.assertEqual(pass_frame_args(body), body)


class TableLimitKeptInARegister(unittest.TestCase):
    """GCC keeps the range check's limit (`mov #9,r11`) in a callee-saved register from before a call: the check
    `cmp/hi r11,r1` still bounds the jump table (Robo Pit 060112d4, the CD init's status dispatch)."""
    # mov #2,r11; mov.l @(pool),r0; jsr @r0; nop; mov r4,r1; add #-1,r1; cmp/hi r11,r1; bt/s default; add r1,r1;
    # mova @(table),r0; mov.w @(r0,r1),r1; add r1,r0; jmp @r0; nop; table: .word 8, 0xc, 0x10;
    # 3 cases `rts; mov #k,r0` at 0x24, 0x28, 0x2c; default `rts; mov #-1,r0` at 0x30; pool at 0x38
    CODE = ("EB02" "D00D" "400B" "0009" "6143" "71FF" "31B6" "8D0F" "311C" "C702" "011D" "301C" "402B" "0009"
            "0008" "000C" "0010" "0009" "000B" "E000" "000B" "E001" "000B" "E002" "000B" "E0FF" "0009" "0009"
            "06001000")

    def test_switch_over_the_table(self):
        src = c_of(self.CODE, 0x3C, ret="s32")
        self.assertIn("switch (", src)
        self.assertNotIn("u32 g_", src)

    def test_no_switch_without_the_limit(self):
        src = c_of(self.CODE.replace("EB02", "0009"), 0x3C, ret="s32")
        self.assertNotIn("switch (", src)


class SignedIndexCheck(unittest.TestCase):
    """GCC checks a signed table index with `cmp/pz r1; bf out` and then `cmp/ge r2,r1; bt out` (r2 = 0x20): together
    they bound it like cmp/hs; the byte table is then read (Saturn Bomberman 0609460c)."""
    # mov r4,r1; cmp/pz r1; mov #0x20,r2; bf out; cmp/ge r2,r1; bt out2; mov.l @(table),r2; add r1,r2; mov.b @r2,r2;
    # mov.l @(base),r1; add r2,r1; jmp @r1; nop; pad; cases at 0x20 (mov #1,r0) and 0x24 (mov #2,r0), out at 0x28,
    # out2 at 0x2c (each `rts; mov #k,r0`); 32 table bytes at 0x30 (0 or 4); pool: 0x06000030, 0x06000020
    CODE = ("6143" "4111" "E220" "8B0F" "3123" "890F" "D210" "321C" "6220" "D110" "312C" "412B" "0009" "0009" "0009"
            "0009" "000B" "E001" "000B" "E002" "000B" "E0FF" "000B" "E0FE" + "0004" * 16 + "06000030" "06000020")

    def test_switch_over_the_table(self):
        src = c_of(self.CODE, 0x58, ret="s32")
        self.assertIn("switch (", src)
        self.assertNotIn("u32 g_", src)

    def test_no_switch_without_the_sign_check(self):
        src = c_of(self.CODE.replace("4111", "0009"), 0x58, ret="s32")
        self.assertNotIn("switch (", src)


class JumpIntoOwnBody(unittest.TestCase):
    """A copy helper with no bound check jumps through a table of addresses into its own unrolled body (Saturn
    Bomberman 0609445c, a computed goto): the jump is a switch over those labels, not a tail call to an address
    that is no function (STUB 060944a2)."""
    # mov.l r3,@-r15; mov.l @(pool),r3; mov.l @(r0,r3),r3; add #-4,r0; jmp @r3; mov.l @(r0,r2),r3; nop;
    # sled 0x0e..0x19: mov.l r0,@(8,r1); mov.l @(4,r2),r0; mov.l r3,@(4,r1); mov.l @r2,r3; mov.l r0,@(4,r1);
    # mov.l r3,@r1; rts at 0x1a; mov.l @r15+,r3; pad; pool 0x06000024 at 0x20; table at 0x24: 1a 16 12 0e, then
    # a word that is no address in the body
    CODE = ("2F36" "D307" "033E" "70FC" "432B" "032E" "0009" "1102" "5021" "1131" "6322" "1101" "2132" "000B" "63F6"
            "0009" "06000024" "0600001A" "06000016" "06000012" "0600000E" "FFFFFFFF")

    def test_switch_into_the_sled(self):
        src = c_of(self.CODE, 0x38)
        self.assertIn("switch (", src)
        for k in ("0x0600000eu", "0x06000012u", "0x06000016u", "0x0600001au"):
            self.assertIn(f"case {k}:", src)

    def test_table_past_the_body_stays_a_tail_call(self):
        # entries pointing at or after the table are not the function's own code: no switch, as before
        src = c_of(self.CODE.replace("0600001A" "06000016", "06000030" "06000034"), 0x38)
        self.assertNotIn("switch (", src)
        self.assertIn("tc_call(g_", src)

    # Saturn Bomberman 060944f4 (STUB 06094516): a byte count checked first, `mov #8,r3; cmp/hs r0,r3; bf big`, then
    # mov.l @(pool),r3; mov.l @(r0,r3),r3; jmp @r3; nop; pool 0x06000020; sled 0x14..0x1f: mov.l @(4,r2),r0;
    # mov.l r0,@(4,r1); mov.l @r2,r0; mov.l r0,@r1; rts; mov.l @r15+,r3; table at 0x20: 1c 18 14 (offsets 0, 4, 8);
    # big at 0x2c, after the table: mov #0,r0; rts; mov.l @r15+,r3
    CHECKED = ("2F36" "E308" "3302" "8B11" "D301" "033E" "432B" "0009" "06000020" "5021" "1101" "6022" "2102" "000B"
               "63F6" "0600001C" "06000018" "06000014" "E000" "000B" "63F6")

    def test_bounded_byte_offset_is_a_switch(self):
        # the bound is on the byte offset r0 <= 8, not on an index: the aligned offsets 0, 4, 8 are the 3 entries
        src = c_of(self.CHECKED, 0x32)
        self.assertIn("switch (", src)
        for k in ("0x06000014u", "0x06000018u", "0x0600001cu"):
            self.assertIn(f"case {k}:", src)
        self.assertNotIn("case 0x06000020u:", src)
        self.assertNotIn("tc_call(0x06000014u", src)

    # Saturn Bomberman 060946cc (GCC's __ashrsi3, STUB 06094744): the range check is two branches back, with a rotate
    # that sets T between it and the jump, and a second way in from the positive path. mov.l r2,@-r15; cmp/pz r1;
    # mov #4,r2; bf out; cmp/ge r2,r1; bt big; mov r0,r2; rotl r2; bf pos; tab: mov.l @(pool),r2; add r2,r1;
    # mov.b @r1,r2; mov.l @(pool),r1; add r2,r1; jmp @r1; nop; pos 0x20: mov #2,r2; cmp/gt r2,r1; bf tab; rts;
    # mov.l @r15+,r2; big 0x2a: mov #-1,r0; rts; mov.l @r15+,r2; pools 0x06000038 (table) and 0x06000042 (base);
    # table of signed byte offsets 00 fe fc fa; sled 0x3c: shar r0 x3; out 0x42: rts; mov.l @r15+,r2
    SHIFT = ("2F26" "4111" "E204" "8B1C" "3123" "890E" "6203" "4204" "8B06" "D207" "312C" "6210" "D106" "312C" "412B"
             "0009" "E202" "3127" "8BF5" "000B" "62F6" "E0FF" "000B" "62F6" "06000038" "06000042" "00FEFCFA"
             "4021" "4021" "4021" "000B" "62F6")

    def test_check_branches_back_bounds_a_byte_table(self):
        src = c_of(self.SHIFT, 0x46, "u32")
        self.assertIn("switch (", src)
        for k in ("0x0600003cu", "0x0600003eu", "0x06000040u", "0x06000042u"):
            self.assertIn(f"case {k}:", src)

    def test_no_range_check_is_no_bound(self):
        # without `cmp/ge r2,r1; bt big` only cmp/pz guards r1: no bound on any way in, so no switch
        src = c_of(self.SHIFT.replace("3123" "890E", "0009" "0009", 1), 0x46, "u32")
        self.assertNotIn("switch (", src)


class NarrowHardwareAccess(unittest.TestCase):
    """A byte or word at a constant hardware address (SMPC SF 0x20100063, CD block HIRQ 0x25890008) goes through
    tc_hw_read8/16 and tc_hw_write8/16, as the 4-byte one does: rd8/wr8 fault on hardware in the test runtime."""

    def src(self, access):
        # mov.l @(pool),r1; <access>; rts; nop; pool: the register. The pool stays outside the body.
        return c_of("d101" + access + "000b0009" + "20100063", 8, ret="u32")

    def test_byte_read_is_sign_extended(self):
        self.assertIn("(u32)(s32)(s8)tc_hw_read8(0x20100063u)", self.src("6010"))

    def test_word_read_is_sign_extended(self):
        self.assertIn("(u32)(s32)(s16)tc_hw_read16(0x20100063u)", self.src("6011"))

    def test_long_read_unchanged(self):
        self.assertIn("tc_hw_read32(0x20100063u)", self.src("6012"))

    def test_byte_store(self):
        src = self.src("2120")
        self.assertIn("tc_hw_write8(0x20100063u, (u8)", src)
        self.assertNotIn("wr8(", src)

    def test_ram_stays_plain(self):
        src = c_of("d101" + "6010" + "000b0009" + "06001000", 8, ret="u32")
        self.assertNotIn("tc_hw_", src)
        self.assertIn("rd8(", src)


class HardwareAddressAcrossABranch(unittest.TestCase):
    """A hardware address loaded before a branch stays known in the blocks after it, when every way in leaves it (Robo
    Pit 06014268, a poll of SMPC SF after `mov.l SF,r1`: the loop head was a new block, so rd8 faulted in the judge)."""

    def test_poll_loop(self):
        # mov.l @(pool),r1; L: mov.b @r1,r0; tst #1,r0; bf L; rts; nop; pool
        src = c_of("d102" + "6010" + "c801" + "8bfc" + "000b0009" + "20100063", 12, ret="u32")
        self.assertIn("tc_hw_read8(0x20100063u)", src)
        self.assertNotIn("rd8(", src.replace("tc_hw_read8(", ""))

    def test_access_in_a_return_slot(self):
        # mov.l @(pool),r1; tst #1,r0; bf L; nop; L: rts; mov.l r0,@r1 (Bomberman 060a51ac's last store); pool
        src = c_of("d103" + "c801" + "8b00" + "0009" + "000b" + "2102" + "0009" + "0009" + "ffffffb0", 16, ret="u32")
        self.assertIn("tc_hw_write32(0xffffffb0u,", src)

    def test_ram_stays_plain(self):
        src = c_of("d102" + "6010" + "c801" + "8bfc" + "000b0009" + "06001000", 12, ret="u32")
        self.assertNotIn("tc_hw_", src)

    def test_two_addresses_stay_plain(self):
        # r1 is one register on the taken edge and another on the fall: not a constant, so the access stays rd8
        src = c_of("d103" + "8901" + "d103" + "0009" + "6010" + "000b0009" + "0009" + "20100063" + "20100061", 14, ret="u32")
        self.assertNotIn("tc_hw_read8", src)
        self.assertIn("rd8(r1)", src)


class FlowPastTheBody(unittest.TestCase):
    """Running past the end of the body the oracle gave is a call of that address (TcOracle.entersCallee, CALL lines in
    expected.txt: Dragon Force 0603f2ae, a shift ladder Ghidra cut at 0603f2b1), entry or not."""

    def test_tail_call_past_the_body(self):
        self.assertIn("tc_call(0x06000002u", c_of("00090009", 1))

    def test_inside_the_body_stays_a_fall_through(self):
        self.assertNotIn("tc_call(", c_of("00090009000b0009", 7))


class SelfCallWithAStackParameter(unittest.TestCase):
    """A recursive function with a parameter on the stack (Robo Pit 0604b2a8, Dragon Force 2 06078a88): the body reads
    it through r15 from what the call pushed, so the helper's argument goes unused."""

    def test_call_passes_a_placeholder(self):
        m = {"name": "fn_06000000", "signature": "void fn_06000000(u32 p)", "ret": "void",
             "params": [{"name": "p", "kind": "u32", "storage": "stack", "stack_offset": 0}]}
        src = translate_function(Image(bytes.fromhex("bffe0009000b0009"), FACTS.image_base), 0x06000000,
                                 Signature.from_meta(m), FACTS, [(0x06000000, 0x06000007)], entries={0x06000000})
        self.assertIn("body(r15, 0x06000004u, 0u)", src)


if __name__ == "__main__":
    unittest.main()
