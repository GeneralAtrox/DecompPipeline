"""sh4-translate: r4..r7 handed back to callers (TcOut) and computed transfers decided across blocks (sh4translate/gconst.py).
Pass and fail controls on hand-assembled code:
1. an exported r4 (meta r_exports) that the function never writes reaches its exit through its calls: the runtime's
   r4..r7 after a mocked call are the TcArgs ones (tc_rt.c tc_call), so every call hands r4 over and the entry value
   is read (sh4_r(4)). Without r_out/r_exports the C hands nothing over (as before: Spawn fe 8c4b9eca got 0);
2. `jsr @r8` with r8 loaded once, before a block and a call, from a pool word holding the function's own address is
   the recursion DP's oracle runs (Vigilante 8 8c0666c0), not a computed tc_call;
3. a byte jump table read through extu.b takes its offsets unsigned (Vigilante 8 8c07ac60: 0x90 is +144): a switch
   reaching the far case, where the signed reading left the braf a computed tail call;
4. a jump table whose range check compares with a limit loaded before a call (Vigilante 8 8c08b000: `mov #3,r8` ...
   jsr ... `cmp/hi r8,r0`) is a switch, not a computed tail call; a register the paths load differently is no
   constant (the call stays computed).
Synthetic data. Run: python -W error tests/test_tx_fix.py.
"""
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from sh4translate import translate as tx  # noqa: E402

E = 0x8c000000
NOP, RTS = 0x0009, 0x000B


def image(words, at=None):
    """words from E, then {offset: u32 or [u16..]} placed at their offsets (pool words, far code)."""
    data = bytearray(b"".join(struct.pack("<H", w) for w in words))
    for off, v in sorted((at or {}).items()):
        data += b"\x09\x00" * ((off - len(data)) // 2)
        data += struct.pack("<I", v) if isinstance(v, int) else b"".join(struct.pack("<H", w) for w in v)
    return tx.Image(bytes(data), E)


def translate(img, sig):
    src = tx.translate_function(img, E, sig, tx.TargetFacts())
    return src[0] if isinstance(src, tuple) else src


def sig(params, ret, callees, r_out=frozenset()):
    text = f"{ret} fn_8c000000({', '.join(f'u32 p_{r}' for r in params) or 'void'})"
    return tx.Signature("fn_8c000000", text, [(f"p_{r}", "u32", r) for r in params], ret, None, callees, r_out=r_out)


# 1. push pr; r3 = 8c000100 (pool at +0x10); jsr @r3; pop pr; rts. r4 is never written.
EXPORT = [0x4F22, 0xD303, 0x430B, NOP, 0x4F26, RTS, NOP]
# 2. push r8, pr; r8 = E (pool at +0x18); tst r4,r4; bt out; add #-1,r4; jsr @r8; nop; out: pop pr, r8; rts.
SELF = [0x2F86, 0x4F22, 0xD804, 0x2448, 0x8903, 0x74FF, 0x480B, NOP, NOP, 0x4F26, RTS, 0x68F6]
# 3. r1 = 1; cmp/hi r1,r4; bt dflt; mova tbl,r0; mov.b @(r0,r4),r1; extu.b r1,r1; braf r1; nop;
#    tbl (+0x10): 02 90; +0x12: case 0 (r0 = 10); +0x18: dflt (r0 = 0); +0xa0: case 1 (r0 = 20).
TABLE = [0xE101, 0x3416, 0x8908, 0xC702, 0x014C, 0x611C, 0x0123, NOP, 0x9002, 0xE00A, RTS, NOP, 0xE000, RTS, NOP]
# 4. push r8, pr; r8 = 1; r3 = 8c000100 (pool at +0x30); jsr @r3; nop; mov r0,r4; cmp/hi r8,r4; bt dflt;
#    mova tbl,r0; mov.b @(r0,r4),r1; braf r1; nop; +0x1a: case 0 (r0 = 10), bra out; +0x20: case 1 (r0 = 20), bra out;
#    +0x26: dflt (r0 = 0); out: pop pr, r8; rts. tbl (+0x34): 00 06.
BOUND = [0x2F86, 0x4F22, 0xE801, 0xD30A, 0x430B, NOP, 0x6403, 0x3486, 0x8909, 0xC708, 0x014C, 0x0123, NOP, 0xE00A,
         0xA004, NOP, 0xE014, 0xA001, NOP, 0xE000, 0x4F26, RTS, 0x68F6]


class ExportedR4(unittest.TestCase):
    def test_exported_r4_goes_through_the_call(self):
        c = translate(image(EXPORT, {0x10: 0x8C000100}), sig([], "void", {0x8C000100: []}, frozenset({"r4"})))
        self.assertIn("r4 = sh4_r(4);", c)
        self.assertIn("a.r[0] = r4;", c)

    def test_without_r_out_nothing_is_handed_over(self):
        c = translate(image(EXPORT, {0x10: 0x8C000100}), sig([], "void", {0x8C000100: []}))
        self.assertNotIn("sh4_r(4)", c)
        self.assertNotIn("a.r[0]", c)


class HeldTargets(unittest.TestCase):
    def test_recursion_through_a_held_register(self):
        c = translate(image(SELF, {0x18: E}), sig(["r4"], "void", {E: ["r4"]}))
        self.assertNotIn("tc_call(r8", c)
        self.assertNotIn("tc_call(0x8c000000u", c)
        self.assertIn("calls itself", c)

    def test_unsigned_byte_table(self):
        c = translate(image(TABLE, {0xA0: [0xE014, RTS, NOP]}), sig(["r4"], "u32", {}))
        self.assertIn("case 0x8c0000a0u:", c)     # case 1 (r0 = 20), offset 0x90 read unsigned
        self.assertIn("case 0x8c000012u:", c)

    def test_bound_loaded_before_a_call(self):
        img = image(BOUND, {0x30: 0x8C000100, 0x34: [0x0600]})
        c = translate(img, sig([], "u32", {0x8C000100: []}))
        self.assertIn("case 0x8c00001au:", c)     # a switch over both cases, not a computed tail call
        self.assertIn("case 0x8c000020u:", c)
        self.assertIn("tc_call(0x8c000100u", c)

    def test_a_register_loaded_two_ways_is_no_constant(self):
        # bt over a reload: r8 is 8c000100 on one path and 8c000200 on the other at the jsr
        words = [0x2F86, 0x4F22, 0xD804, 0x2448, 0x8900, 0xD804, 0x480B, NOP, 0x4F26, RTS, 0x68F6]
        c = translate(image(words, {0x18: 0x8C000100, 0x1C: 0x8C000200}), sig(["r4"], "void", {}))
        self.assertIn("tc_call(r8", c)


if __name__ == "__main__":
    unittest.main()
