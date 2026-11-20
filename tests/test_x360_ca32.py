"""32-bit mode (MSR[SF] = 0, as the Xbox 360 runs its code): XER[CA] is the carry out of the low 32 bits and a record
form's CR0 is the signed test of the result's low 32 bits (Power ISA Book I, "Fixed-Point Processor": "in 32-bit mode
CA reflects the carry out of bit 32"; CR0 "the low-order 32 bits"). Through the translator and runtime (leg A) and the
oracle (leg B: Ghidra's 64-bit language takes both from 64 bits; TcFixups' mode32Before / mode32After rewrite them).

Each carry case reads CA back into a register with a following addze rX, r7 (rX = CA) or subfe rX, r7, r7
(rX = CA - 1, so 0 for CA 1 and all ones for CA 0); r7 is 0. The inputs are chosen so that the 32-bit and 64-bit carries
differ (r4 = 0x0000000100000000 with addic -1: no carry out of 32 bits, a carry out of 64; r4 = 0xffffffff + 1: the
reverse), with controls where they agree. CA is set to 1 for the chained cases by addic r3, r10, 1 with
r10 = all ones (a carry at both widths). Every expected value is worked out by hand from the rule.
usage: python tests/test_x360_ca32.py (leg A needs cl, leg B ghidra_home and java_home: see test_x360_fp_nan.py)
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_x360_fp_nan import HAVE_CL, HAVE_GHIDRA, case, leg_a, leg_b  # noqa: E402

HI = 0x0000000100000000
LO = 0x00000000FFFFFFFF
ALL = 0xFFFFFFFFFFFFFFFF
SET_CA = "addic r3, r10, 1"          # CA = 1 at both widths (r10 = ALL), r3 = 0
READ = "addze r11, r7"               # r11 = CA
READ_E = "subfe r11, r7, r7"         # r11 = 0 if CA else ALL


def ca(label, insns, ins, outs):
    return case(label, insns, dict({"r7": 0, "r10": ALL}, **ins), outs)


CARRY_CASES = [
    # first-step carries: 32-bit and 64-bit answers differ
    ca("addic_hi", ["addic r3, r4, -1", READ], {"r4": HI}, {"r3": LO, "r11": 0}),
    ca("addic_lo", ["addic r3, r4, 1", READ], {"r4": LO}, {"r3": HI, "r11": 1}),
    ca("addc_lo", ["addc r3, r4, r5", READ], {"r4": LO, "r5": 1}, {"r3": HI, "r11": 1}),
    ca("subfic_hi", ["subfic r3, r4, 0", READ], {"r4": HI}, {"r3": 0xFFFFFFFF00000000, "r11": 1}),
    ca("subfc_hi", ["subfc r3, r4, r5", READ_E], {"r4": HI, "r5": 0}, {"r3": 0xFFFFFFFF00000000, "r11": 0}),
    # carries in: CA = 1 at both widths first, then the op's own carry differs by width
    ca("adde_lo", [SET_CA, "adde r6, r8, r9", READ], {"r8": LO, "r9": 0}, {"r3": 0, "r6": HI, "r11": 1}),
    ca("addze_lo", [SET_CA, "addze r6, r8", READ_E], {"r8": LO}, {"r6": HI, "r11": 0}),
    ca("subfe_hi", [SET_CA, "subfe r6, r8, r9", READ], {"r8": HI, "r9": 0}, {"r6": 0xFFFFFFFF00000000, "r11": 1}),
    ca("subfze_hi", [SET_CA, "subfze r6, r8", READ], {"r8": HI}, {"r6": 0xFFFFFFFF00000000, "r11": 1}),
    ca("addme_hi", ["addme r6, r8", READ], {"r8": HI}, {"r6": LO, "r11": 0}),                  # CA in 0
    ca("subfme_lo", ["subfme r6, r8", READ], {"r8": LO}, {"r6": 0xFFFFFFFEFFFFFFFF, "r11": 0}),
    # CA read by an adde of the next step: 0x1_00000000 - 1 leaves CA 0 in 32-bit mode
    ca("addic_adde", ["addic r3, r4, -1", "adde r6, r8, r9"], {"r4": HI, "r8": 5, "r9": 6}, {"r3": LO, "r6": 11}),
]
CR0_CASES = [
    ca("addic_dot_hi", ["addic. r3, r4, -1", READ], {"r4": HI}, {"r3": LO, "r11": 0, "cr": 0x80000000}),
    ca("rlwinm_dot_sign", "rlwinm. r3, r4, 0, 0, 31", {"r4": 0x80000000}, {"r3": 0x80000000, "cr": 0x80000000}),
    ca("andis_dot_sign", "andis. r3, r4, 0x8000", {"r4": LO}, {"r3": 0x80000000, "cr": 0x80000000}),
    ca("add_dot_hi_zero", "add. r3, r4, r5", {"r4": HI, "r5": 0}, {"r3": HI, "cr": 0x20000000}),
]
CONTROL_CASES = [
    ca("addic_small_neg", ["addic r3, r4, -1", READ], {"r4": 5}, {"r3": 4, "r11": 1}),
    ca("addic_small_pos", ["addic r3, r4, 1", READ], {"r4": 5}, {"r3": 6, "r11": 0}),
    ca("addc_all", ["addc r3, r4, r5", READ], {"r4": ALL, "r5": 1}, {"r3": 0, "r11": 1}),
    ca("subfic_one", ["subfic r3, r4, 0", READ], {"r4": 1}, {"r3": ALL, "r11": 0}),
    ca("subfc_eq", ["subfc r3, r4, r5", READ], {"r4": 7, "r5": 7}, {"r3": 0, "r11": 1}),
    ca("adde_small", ["adde r6, r8, r9", READ], {"r8": 2, "r9": 3}, {"r6": 5, "r11": 0}),
    ca("rlwinm_dot_pos", "rlwinm. r3, r4, 0, 0, 31", {"r4": 1}, {"r3": 1, "cr": 0x40000000}),
]
ALL_CASES = CARRY_CASES + CR0_CASES + CONTROL_CASES


@unittest.skipUnless(HAVE_CL, "needs cl")
class Translator(unittest.TestCase):
    def test_carry_and_cr0(self):
        self.assertEqual(leg_a(ALL_CASES), [])


@unittest.skipUnless(HAVE_GHIDRA, "needs ghidra_home and java_home")
class Oracle(unittest.TestCase):
    def test_carry_and_cr0(self):
        self.assertEqual(leg_b(ALL_CASES), [])


if __name__ == "__main__":
    if not HAVE_CL:
        print("SKIPPED: needs cl (the translator cases)")
    if not HAVE_GHIDRA:
        print("SKIPPED: needs ghidra_home and java_home (the oracle cases)")
    unittest.main()
