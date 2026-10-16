"""Port phase C: tooling fixes for what running the readable C natively found.
1. core/readable_stack: context.md says how many bytes each own-frame address spans up to the next stack address
   raw.c names or the prologue's saved registers. 8c06fc14 passed an 8-byte AnimFrame where Anim_ReadFrame fills
   0x2f0 bytes; the judge cannot see a host stack overflow, the port crashed on the smashed return address.
2. core/frame_args (raw lint R12): where the asm passes r15 + k at every call of a callee, raw C passing a literal
   0 or a variable only ever set to 0 fails; the frame address passes, and so does a callee called once without it.
Synthetic data. Run: python -W error tests/test_port_c.py (also from test_tooling.py).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_batch22 import reg_with  # noqa: E402

from core import frame_args, readable_stack  # noqa: E402

ADDR = "8c06fc14"
PROTO = "void Lib_PoseMixerInit(LibPoseMixer *mixer, void *motion)"
META = {"addr": ADDR, "signature": "void fn_8c06fc14(u32 param_1, u32 param_2)",
        "params": [{"name": "param_1", "storage": "r4", "stack_offset": -1},
                   {"name": "param_2", "storage": "r5", "stack_offset": -1}], "callees": []}
ASM = ("  8c06fc14  mov.l r13,@-r15\n  8c06fc16  mov.l r12,@-r15\n  8c06fc18  mov.l r11,@-r15\n"
       "  8c06fc1a  mov.l r10,@-r15\n  8c06fc1c  mov.l r9,@-r15\n  8c06fc1e  sts.l PR,@-r15\n"
       "  8c06fc20  mov.w 0x8c06fd10,r0                  ; = 0xfffffd10\n  8c06fc22  add r0,r15\n"
       "  8c06fc24  mov r5,r11\n  8c06fc2a  jsr @r3                              ; call 8c04eed8\n"
       "  8c06fc2c  _mov.l r8,@-r15\n")
RAW = "void fn_8c06fc14(void)\n{\n    u32 r15 = 0x8cffdcf8u;\n    a.r[2] = 0x8cffdcf8u;\n}\n"


class FrameSpans(unittest.TestCase):
    def test_pushes(self):
        self.assertEqual(readable_stack.prologue_pushes(ASM), 24)  # stops at the add to r15, not the slot push
        self.assertEqual(readable_stack.prologue_pushes("  8c000000  rts\n  8c000002  _nop\n"), 0)
        self.assertIsNone(readable_stack.prologue_pushes(None))

    def test_spans(self):
        self.assertEqual(readable_stack.frame_spans([0x8CFFDCF8], 24), {0x8CFFDCF8: 0x2F0})
        self.assertEqual(readable_stack.frame_spans([0x8CFFDCF8, 0x8CFFDD00, 0x8CFFE000], 24),
                         {0x8CFFDCF8: 8, 0x8CFFDD00: 0x2E8})  # a stack argument spans nothing
        self.assertEqual(readable_stack.frame_spans([0x8CFFDFF8], None), {0x8CFFDFF8: 8})  # no listing: to entry r15

    def test_context_note(self):
        reg = reg_with({ADDR: {"proto": PROTO}})
        text = "\n".join(readable_stack.context_lines(reg, ADDR, META, RAW, asm_text=ASM))
        self.assertIn("The original's frame has 0x2f0 bytes from here", text)
        small = RAW.replace("0x8cffdcf8u", "0x8cffdfe4u")  # 4 bytes below the saved registers: no note
        self.assertNotIn("frame has", "\n".join(readable_stack.context_lines(reg, ADDR, META, small, asm_text=ASM)))


CALL_ASM = ("  8c043d8e  mov.l 0x8c043dbc,r3                  ; = 0x8c05fc28  (function 8c05fc28)\n"
            "  8c043d90  mov r15,r5\n  8c043d92  mov.l @(0x10,r15),r6\n  8c043d94  mov r12,r4\n"
            "  8c043d96  add #0x28,r5\n  8c043d98  jsr @r3                              ; call 8c05fc28\n"
            "  8c043d9a  _add #0x5c,r4\n  8c043d9c  tst r0,r0\n")
CALL_RAW = ("u32 fn_8c043cb4(void)\n{\n    TcArgs args;\n    u32 base = 0u;\n    args.r[0] = q + 0x5cu;\n"
            "    args.r[1] = 0u;\n    rv = tc_call(0x8c05fc28u, &args);\n}\n")


class FrameArgs(unittest.TestCase):
    def test_scan(self):
        found, calls = frame_args.frame_args(frame_args.sh4_asm.parse(CALL_ASM))
        self.assertEqual(found, {"8c05fc28": {1: [(0x8C043D98, 0x28)]}})
        self.assertEqual(calls, {"8c05fc28": 1})

    def test_lint(self):
        got = frame_args.lint_frame_args(CALL_RAW, CALL_ASM)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("R12: at the call of 8c05fc28, the original passes r5 = r15 + 0x28"))
        self.assertEqual(len(frame_args.lint_frame_args(CALL_RAW.replace("= 0u;\n    rv", "= base;\n    rv"),
                                                        CALL_ASM)), 1)  # a variable only ever set to 0
        good = CALL_RAW.replace("args.r[1] = 0u;", "args.r[1] = 0x8cffdff4u;")
        self.assertEqual(frame_args.lint_frame_args(good, CALL_ASM), [])
        other = CALL_ASM.replace("  8c043d90  mov r15,r5\n", "  8c043d90  mov r9,r5\n")
        self.assertEqual(frame_args.lint_frame_args(CALL_RAW, other), [])  # the asm passes no frame address
        self.assertEqual(frame_args.lint_frame_args(CALL_RAW, None), [])


if __name__ == "__main__":
    unittest.main()
