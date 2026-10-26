"""Saturn oracle (TcOracle.inputJump): a jump to an odd address is an address error on the SH-2, so no run of the
original reaches it. Such a target from the inputs is an invalid vector (repaired or dropped like a fault), also
inside the code ranges, not a refusal of the function (Dragon Force 0602cc92 ...: 06030607, 06040203 from the
inputs). An even target in code still refuses the function, as before.

usage: python tests/test_sat_odd_jump.py
"""
import pathlib
import unittest

SAT = pathlib.Path(__file__).resolve().parents[1] / "targets" / "saturn_sh2"


class Wiring(unittest.TestCase):
    def test_saturn_odd_target(self):
        orc = (SAT / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn("    boolean inputJump(long prev, long pc) {\n"
                      "        if ((pc & 1) != 0) return true;\n"
                      "        if (!facts.haveCode || facts.isCode(pc)", orc)


if __name__ == "__main__":
    unittest.main()
