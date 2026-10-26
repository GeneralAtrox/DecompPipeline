"""Saturn: SR's interrupt mask at exit. TcSr.exitMask read the field register IMASK, which the stock SH-2 SLEIGH does
not define (its mask is sr[4..7]); TcEmu reads a register the language lacks as 0, so every expected mask was 0 and
an SH-2 function that sets the mask (Dragon Force 2 060182d4: stc sr, and #0xff0f, r4 << 4, or, ldc sr) failed with
correct C. Now the mask is read from SR where the language has no IMASK field. Controls:
1. the premise, from the Ghidra this machine runs: superh.sinc defines sr, I0..I3 as sr[4..7], and no IMASK;
2. the wiring: exitMask takes SR >> 4 without the field, the field where a language has one; the call in TcExit.
The oracle itself needs Ghidra: the Dragon Force 2 re-prepares in docs/proofs/judge-gaps.md judge it.
Run: python -W error tests/test_sat_sr.py.
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_tail import CFG  # noqa: E402

SCRIPTS = ROOT / "targets" / "saturn_sh2" / "ghidra_scripts"
SINC = pathlib.Path(CFG.get("ghidra_home") or ".", "Ghidra", "Processors", "SuperH", "data", "languages",
                    "superh.sinc")


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(SINC.is_file(), "no ghidra_home with the SuperH processor on this machine")
class Premise(unittest.TestCase):
    def test_sh2_mask_is_in_sr(self):
        s = text(SINC)
        self.assertRegex(s, r"define register offset=0x100 size=4 \[sr ")
        for i in range(4):
            self.assertIn(f'@define I{i}_FLAG  "sr[{4 + i},1]"', s)
        self.assertIsNone(re.search(r"\bIMASK\b", s))


class Wiring(unittest.TestCase):
    def test_exit_mask(self):
        sr = text(SCRIPTS / "TcSr.java")
        self.assertIn('boolean field = emu.getProgram().getRegister("IMASK") != null;', sr)
        self.assertIn('return (int) ((field ? reg(emu, "IMASK") : reg(emu, "SR") >> 4) & 15);', sr)
        self.assertIn("if (o.sr != null) res.imask = TcSr.exitMask(emu);", text(SCRIPTS / "TcExit.java"))


if __name__ == "__main__":
    unittest.main()
