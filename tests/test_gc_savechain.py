"""The GameCube oracle runs a register save/restore helper inline to its blr, not only the called entry's Ghidra
body: Ghidra splits CodeWarrior's _savegpr / _restgpr chains into one-instruction functions (Skies of Arcadia
8025a2b0..8025a2c8), so the fall-through into the next entry was mocked as a call (802790e8, 80270a98, found by
ppc-translate). The walk is TcFlow.isSaveHelper's: from the entry, at most 40 instructions, to the blr.

usage: python tests/test_gc_savechain.py
"""
import pathlib
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
GC = TOOL / "targets" / "gamecube_ppc" / "ghidra_scripts"


class Wiring(unittest.TestCase):
    def test_walk(self):
        orc = (GC / "TcOracle.java").read_text(encoding="utf-8")
        flow = (GC / "TcFlow.java").read_text(encoding="utf-8")
        self.assertIn("if (!TcFlow.isSaveHelper(listing, c.getEntryPoint())) continue;\n"
                      "            inline.add(c.getBody());", orc)  # the old set, kept: the walk only adds
        self.assertIn("for (int n = 0; in != null && n < 40; n++, in = in.getNext()) {\n"
                      "                inline.add(in.getMinAddress(), in.getMaxAddress());\n"
                      '                if (in.getMnemonicString().equalsIgnoreCase("blr")) break;', orc)
        self.assertIn("for (int n = 0; in != null && n < 40; n++, in = in.getNext()) {", flow)  # the same bound
        self.assertIn('if (m.equals("blr")) return n > 0;', flow)
        self.assertIn("if (!body.contains(pca) && !inline.contains(pca)) {", orc)  # where the set decides a call


if __name__ == "__main__":
    unittest.main()
