"""Readable parameters never read while the original reads their storage (core/param_unread.py, docs/PROOF_GATES.md
section 7). One fail and one pass control each:
1. Stack arguments (TC 8c0117f4 Camera_UpdateKind10): words loaded from the caller's argument area and copied into
   an object, or into the local whose address goes to a call (even a pointer call), are read; a word the original
   never loads, or loads and drops, is not. A dead spill above a passed local (TC Screen_SaveCapture) is not.
2. Register arguments: an argument passed through to a callee whose record reads it is read (TC Vec3_Magnitude);
   one whose callee record does not read it, or a call through a pointer, is not.
3. A compare of an argument whose T nobody reads is no use; one that feeds a branch is.
4. frchg swaps the banks: fr4 after it is another register.
5. A counter stepped and stored back to its frame slot, never read otherwise (TC fn_8c070bbe's r5), is dead; the
   same value added into a pointer that is then read is a use. A value through FPUL into a return is a use.
6. Correlated tests (TC Anim_SeekTime): r6 replaced when a word is set and read only when the same word is set
   again is not read; a store between the two tests, or a test of another word, keeps the read.
7. Readable side: `(void)name;` alone is unread; any other mention is a read; comments do not count.
Synthetic asm, no harness. Run: python -W error tests/test_param_unread.py.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import param_unread as pu, sh4_asm  # noqa: E402


def listing(lines):
    """asm.txt text from (mnemonic and operands) lines, two bytes apart from 8c010000."""
    return "\n".join(f"  {0x8c010000 + 2 * i:08x}  {t}" for i, t in enumerate(lines)) + "\n"


def reads(lines, callees=None):
    return pu.original_reads(sh4_asm.parse(listing(lines)), callees)


class StackArgs(unittest.TestCase):
    # sts.l PR + 0x10 bytes of locals: entry stack+0 is r15+0x14 after the prologue.
    BODY = ["sts.l PR,@-r15", "add #-0x10,r15",
            "mov.l @(0x14,r15),r1", "mov.l r1,@r15",
            "mov.l @(0x18,r15),r1", "mov.l r1,@(0x4,r15)",
            "mov r15,r4", "mov.l @r6,r3", "jsr @r3", "_nop",
            "add #0x10,r15", "lds.l @r15+,PR", "rts", "_nop"]
    # The same words copied into an object (Kind10: r8 + 0x184), not a local.
    OUT = ["mov.l @r15,r1", "mov.l r1,@r4", "mov.l @(0x8,r15),r2", "rts", "_nop"]

    def test_copy_at_the_passed_address(self):
        o = reads(self.BODY)
        self.assertEqual(o["status"], "ok")
        self.assertTrue(pu.reads_storage(o, "stack+0"))
        self.assertFalse(pu.reads_storage(o, "stack+4"))  # above the passed address: not followed (a known miss)

    def test_dead_spill_above_a_passed_local(self):
        # Screen_SaveCapture: r5/r6 spilled in the prologue and never reloaded; &local+0 goes to a call.
        o = reads(["sts.l PR,@-r15", "add #-0xc,r15", "mov.l r5,@(0x4,r15)", "mov.l r6,@(0x8,r15)",
                   "mov.l r7,@r15", "mov.l @r4,r3", "jsr @r3", "_mov r15,r4",
                   "add #0xc,r15", "lds.l @r15+,PR", "rts", "_nop"])
        self.assertEqual(o["status"], "ok")
        self.assertFalse(pu.reads_storage(o, "r5"))
        self.assertFalse(pu.reads_storage(o, "r6"))
        self.assertTrue(pu.reads_storage(o, "r7"))  # stored at the passed address

    def test_word_never_loaded_is_not(self):
        o = reads(self.BODY)
        self.assertFalse(pu.reads_storage(o, "stack+8"))

    def test_copied_out_and_dead_load(self):
        o = reads(self.OUT)
        self.assertTrue(pu.reads_storage(o, "stack+0"))
        self.assertIn("mov.l r1,@r4", o["where"]["stack+0"])
        self.assertFalse(pu.reads_storage(o, "stack+8"))  # loaded, then nothing reads it


class RegisterArgs(unittest.TestCase):
    PASS_THROUGH = ["sts.l PR,@-r15", "bsr 0x8c010020 ; call 8c010020", "_nop", "lds.l @r15+,PR", "rts", "_nop"]

    def test_pass_through_to_a_reading_callee(self):
        o = reads(self.PASS_THROUGH, [{"addr": "8c010020", "regs": ["r4"]}])
        self.assertTrue(pu.reads_storage(o, "r4"))
        self.assertIn("8c010020", o["where"]["r4"])

    def test_callee_that_does_not_read_it(self):
        o = reads(self.PASS_THROUGH, [{"addr": "8c010020", "regs": ["r5"]}])
        self.assertFalse(pu.reads_storage(o, "r4"))
        self.assertTrue(pu.reads_storage(o, "r5"))

    def test_pointer_call_reads_none(self):
        o = reads(["sts.l PR,@-r15", "mov.l @r6,r3", "jsr @r3", "_nop", "lds.l @r15+,PR", "rts", "_nop"])
        self.assertFalse(pu.reads_storage(o, "r4"))
        self.assertTrue(pu.reads_storage(o, "r6"))


class Compares(unittest.TestCase):
    def test_dead_t(self):
        o = reads(["tst r5,r5", "mov #0x1,r0", "rts", "_nop"])
        self.assertFalse(pu.reads_storage(o, "r5"))

    def test_t_feeds_a_branch(self):
        o = reads(["tst r5,r5", "bt 0x8c010008", "mov #0x1,r0", "rts", "_mov #0x0,r0"])
        self.assertTrue(pu.reads_storage(o, "r5"))


class Banks(unittest.TestCase):
    def test_frchg(self):
        o = reads(["frchg", "fmov fr4,fr0", "rts", "_frchg"])
        self.assertFalse(pu.reads_storage(o, "fr4"))

    def test_no_frchg(self):
        o = reads(["fmov fr4,fr0", "rts", "_nop"])
        self.assertTrue(pu.reads_storage(o, "fr4"))


class Arithmetic(unittest.TestCase):
    def test_dead_counter(self):
        o = reads(["add #-0x8,r15", "mov.l r5,@(0x4,r15)", "mov.l @(0x4,r15),r2", "add #0x8,r2",
                   "mov.l r2,@(0x4,r15)", "add #0x8,r15", "rts", "_nop"])
        self.assertEqual(o["status"], "ok")
        self.assertFalse(pu.reads_storage(o, "r5"))

    def test_added_into_a_read_pointer(self):
        o = reads(["add r5,r4", "mov.l @r4,r0", "rts", "_nop"])
        self.assertTrue(pu.reads_storage(o, "r5"))

    def test_through_fpul_to_the_return(self):
        o = reads(["lds r5,FPUL", "float FPUL,fr0", "rts", "_nop"])
        self.assertTrue(pu.reads_storage(o, "r5"))
        self.assertFalse(pu.reads_storage(o, "r6"))


class CorrelatedTests(unittest.TestCase):
    @staticmethod
    def body(store, off="0x8"):
        # r6 = 0 when @(8,r4) is set; r6 read only when @(8,r4) (or @(off,r4)) is set.
        return ["mov.l @(0x8,r4),r2", "tst r2,r2", "bt 0x8c010008", "mov #0x0,r6",
                store, f"mov.l @({off},r4),r3", "tst r3,r3", "bt 0x8c010012", "mov.l @r6,r0", "rts", "_nop"]

    def test_same_word_frame_store_between(self):
        self.assertFalse(pu.reads_storage(reads(self.body("mov.l r1,@r15")), "r6"))

    def test_store_outside_the_frame_between(self):
        self.assertTrue(pu.reads_storage(reads(self.body("mov.l r1,@r5")), "r6"))

    def test_another_word(self):
        self.assertTrue(pu.reads_storage(reads(self.body("mov.l r1,@r15", off="0xc")), "r6"))


class Readable(unittest.TestCase):
    SRC = """
/* Camera_UpdateKind10(cam_px) is mentioned in this comment. */
static f32 helper(f32 a) { return a; }
void Camera_UpdateKind10(s32 kind, f32 cam_px, f32 cam_py)
{
    (void)cam_px;
    helper(cam_py);
}
"""

    def test_definition_and_unread(self):
        proto, body = pu.definition(self.SRC, ["Camera_UpdateKind10"])
        self.assertTrue(proto.startswith("void Camera_UpdateKind10("))
        self.assertTrue(pu.unread(body, "cam_px"))
        self.assertFalse(pu.unread(body, "cam_py"))
        self.assertTrue(pu.unread(body, "kind"))


if __name__ == "__main__":
    unittest.main()
