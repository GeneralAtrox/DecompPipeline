"""GameCube system instructions the oracle used to refuse: mtmsr / mfmsr, sc and dcbi (TcFixups's header has the
rules). 21 Skies of Arcadia functions went untested for them (13 mfmsr, 3 mtmsr, 3 sc, 2 dcbi).

  - mtmsr: Ghidra's Gekko SLEIGH keeps only MSR's EE and ME bits; the 750CL writes the whole register. MSR enters as
    0xB032 (EE ME FP IR DR RI) in the oracle (TcSystem.MSR_ENTRY), the C runtime (TC_MSR_ENTRY) and the translator
    (translate.MSR_ENTRY), and a run that leaves it otherwise reports "MSR <value>" on both sides (TcVectors, tc_xmsr),
    which ppc_compare compares.
  - sc: the OS's handler at 0x80000c00 only leaves r9 = HID0 and r10 = HID0 | 8. The translator called 0x80000c00 as
    a mocked callee, which clobbered r3, r4 and the other volatile registers the handler keeps.
  - dcbi: a no-op, unless the run wrote its block first (then the vector is dropped as invalid).
usage: python tests/test_gc_system.py
"""
import json
import pathlib
import re
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_gc_exit_hw import translate  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
sys.path.insert(0, str(GC))
import ppc_compare  # noqa: E402
from translator.ppctranslate import translate as tr  # noqa: E402

BLR = 0x4E800020
SC, MR_R3_R10 = 0x44000002, 0x7D435378
MFMSR_R3, CLEAR_EE_R4, MTMSR_R4, EE_R3 = 0x7C6000A6, 0x5464045E, 0x7C800124, 0x54638FFE


class Translator(unittest.TestCase):
    def test_sc_is_the_os_handler(self):  # sc; mr r3,r10; blr: r10 = HID0 | 8 (HID0 enters as 0)
        src = translate([SC, MR_R3_R10, BLR])
        self.assertIn("return 8u;", src)
        self.assertNotIn("tc_call", src)

    def test_disable_interrupts(self):  # OSDisableInterrupts: mfmsr; clear EE; mtmsr; return the old EE
        src = translate([MFMSR_R3, CLEAR_EE_R4, MTMSR_R4, EE_R3, BLR])
        self.assertIn("tc_xmsr(0x3032u);", src)
        self.assertIn("return 1u;", src)

    def test_entry_values_agree(self):
        java = (GC / "ghidra_scripts" / "TcSystem.java").read_text(encoding="utf-8")
        rt = (GC / "runtime" / "tc_rt.h").read_text(encoding="utf-8")
        j = int(re.search(r"MSR_ENTRY = 0x([0-9A-Fa-f]+)L;", java).group(1), 16)
        c = int(re.search(r"#define TC_MSR_ENTRY 0x([0-9A-Fa-f]+)u", rt).group(1), 16)
        self.assertEqual((j, c), (tr.MSR_ENTRY, tr.MSR_ENTRY))
        self.assertIn("void tc_xmsr(u32 v) { (void)v; }", (GC / "runtime_rd" / "rd_mem.c").read_text(encoding="utf-8"))

    def test_oracle_wiring(self):
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        refuse = re.search(r'if \(m\.matches\("(dcbz_l[^"]*)"\)\)', fix).group(1).split("|")
        for m in ("mtmsr", "mfmsr", "sc", "dcbi"):
            self.assertNotIn(m, refuse)
        other = (GC / "ghidra_scripts" / "TcCallOther.java").read_text(encoding="utf-8")
        for name in ("syscall", "dataCacheBlockInvalidate"):
            self.assertIn(f'h.add("{name}");', other)
        self.assertIn("if (!TcCallOther.HANDLED.contains(name)) return", fix)
        self.assertIn("TcSystem.sc(emu);", other)
        self.assertIn("set(emu, TcSystem.msr(emu), operand);", fix)
        self.assertIn("if (invalid == null) invalid = TcSystem.dcbi(emu, ram, bank, ea);", fix)
        locked = json.loads((GC / "target.json").read_text(encoding="utf-8"))["locked_scripts"]
        for name in ("TcCallOther.java", "TcSystem.java", "TcPaired.java"):  # compiled with TcFixups in the staging bundle
            self.assertIn(name, locked)
        self.assertIn('if (r.msr != TcSystem.MSR_ENTRY) eout.append("MSR ")',
                      (GC / "ghidra_scripts" / "TcVectors.java").read_text(encoding="utf-8"))
        self.assertIn(r'if (g_msr != TC_MSR_ENTRY) fprintf(out, "MSR %08x\n", g_msr);',
                      (GC / "runtime" / "tc_rt.c").read_text(encoding="utf-8"))


class Compare(unittest.TestCase):
    def test_msr_line(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcsys_", dir=work) as d:
            p = pathlib.Path(d) / "r.txt"
            p.write_text("V 0 dev\nRET 00000001 0000000000000000 00000000\nMSR 00003032\nCOV 0\nE\n"
                         "V 1 dev\nRET 00000001 0000000000000000 00000000\nCOV 0\nE\n", encoding="utf-8")
            res = ppc_compare.parse_results(str(p))
        self.assertEqual(res[0]["msr"], 0x3032)
        self.assertNotIn("msr", res[1])
        src = (GC / "ppc_compare.py").read_text(encoding="utf-8")
        self.assertIn('if exp.get("msr") != act.get("msr"):', src)


if __name__ == "__main__":
    unittest.main()
