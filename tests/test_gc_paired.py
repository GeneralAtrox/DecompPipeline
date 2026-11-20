"""GameCube paired singles: the oracle's rules (TcPaired, called by TcFixups) and the translator's matching C.

Ghidra's Gekko SLEIGH (GameCubeLoader) was wrong for the whole family, so the oracle refused it, and 51 Skies of
Arcadia functions went untested. The SLEIGH's faults:
  - ps_* slots are computed in double, never rounded to single;
  - its NaNs are Ghidra's canonical ones;
  - a quantized psq_l multiplies an int2float's bits by an ldexpf CALLOTHER's result as integers;
  - it converts the unsigned GQR types 4 and 5 as signed;
  - a quantized psq_st neither rounds to single first nor saturates.
TcFixups now recomputes both slots of every ps_* result, and every quantized load or store, from the hardware's rules
(Dolphin's interpreter): per-slot single rounding (IEEE, so 1.5 * 2^-150 rounds to 2^-149 where Ghidra flushes), the
750CL's NaNs, fres / frsqrte's tables, the dequantize u8 u16 s8 s16 / 2^scale in single, and the quantize that rounds
to single, scales, saturates and truncates (NaN gives 0). The translator follows it:
  - ps_res and ps_rsqrte use the same tables (ppc_fres, ppc_frsqrte), not an exact reciprocal;
  - px_quant rounds to single before scaling. 0.99999999999 stored as u8 is 1 on the hardware; truncating the
    double gives 0.

It also checks the store-single narrowing (TcFixups.toSingle) that psq_st and stfs write, into memory and into the
hardware-register log. Skies 8019dba8's fix has no judge plant: its NaNs reach only GX FIFO words, which the judge
counts as equal whatever their NaN. The old oracle wrote 0x7fc00000 for every NaN; the hardware keeps the sign and the
top 23 fraction bits, unquieted (0x7ff8123456789abc -> 0x7fc091a2).

The Java cases need config.local.json's ghidra_home and a JDK (javac and java against the Ghidra jars). The
translator text checks always run.
usage: python tests/test_gc_paired.py
"""
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_tail import CP, jdk  # noqa: E402
from test_gc_estimate import ref_fres, ref_frsqrte  # noqa: E402
from test_gc_exit_hw import emit, translate  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"
BLR = 0x4E800020


def single(bits):
    """bits of a double rounded to single (IEEE nearest even) and widened back; a NaN keeps its top 23 bits."""
    if (bits >> 52) & 0x7FF == 0x7FF and bits & ((1 << 52) - 1):
        return bits & 0xFFFFFFFFE0000000
    x = struct.unpack(">d", struct.pack(">Q", bits))[0]
    return struct.unpack(">Q", struct.pack(">d", struct.unpack(">f", struct.pack(">f", x))[0]))[0]


def d(x):
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def narrow_nan(bits):
    """The single a store of the NaN double with bits makes: sign, all-ones exponent, top 23 fraction bits."""
    return (bits >> 63) << 31 | 0x7F800000 | (bits >> 29) & 0x7FFFFF


CASES = {
    "narrow_qnan": narrow_nan(0x7FF8123456789ABC), "narrow_snan": narrow_nan(0x7FF0000020000000),
    "narrow_neg_qnan": narrow_nan(0xFFFC000000000000), "narrow_inf": 0x7F800000,
    "dq_u8_ff": d(255.0), "dq_s8_ff": d(-1.0), "dq_u16_8000_sc8": d(128.0), "dq_s16_3_scm1": d(6.0),
    "dq_s16_8000": d(-32768.0),
    "q_u8_300": 0xFF, "q_u8_neg": 0, "q_u8_nan": 0, "q_u8_single_first": 1, "q_s8_m200": 0x80,
    "q_s16_sc8": 384, "q_u16_scm1": 3, "q_s16_trunc": 0xFFFE,
    "add_rounds_0": d(1.0), "add_rounds_1": d(1.0 + 2.0 ** -23),
    "add_subnormal_0": d(2.0 ** -149), "add_subnormal_1": 0,  # Ghidra's float2float flushes it to 0
    "mul_nan_0": 0x7FF8123440000000, "mul_nan_1": 0x7FF8000000000000,  # the first NaN, quieted; 0 * inf: default
    "nmadd_0": 0x7FF8000000000000, "nmadd_1": d(-3.0),  # a NaN result is not negated
    "neg_snan_0": 0xFFF0000000000001, "neg_snan_1": d(-1.0),  # only the sign changes, no quieting
    "merge10_0": d(2.0), "merge10_1": d(3.0),
    "sum1_0": d(7.0), "sum1_1": d(5.0),
    "madds1_0": d(2.0), "madds1_1": d(2.5),
    "sel_0": d(1.0), "sel_1": d(2.0),  # -0.0 >= 0
    "div_0": 0x7FF8000000000000, "div_1": d(0.25),
    "res_0": ref_fres(d(3.0)), "res_1": ref_fres(0),
    "rsqrte_0": single(ref_frsqrte(d(3.0))), "rsqrte_1": single(ref_frsqrte(0x7FF0000000000000)),
}


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Rules(unittest.TestCase):
    def test_cases(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcps_", dir=work) as out:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath",
                                os.pathsep.join([str(GC / "ghidra_scripts"), str(SHARED)]),
                                str(TOOL / "tests" / "java" / "TcPairedStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcPairedStub"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {ln.split()[1]: int(ln.split()[2], 16) for ln in r.stdout.splitlines() if ln.startswith("case")}
        self.assertEqual({k: f"{v:016x}" for k, v in got.items()}, {k: f"{v:016x}" for k, v in CASES.items()})


class Translator(unittest.TestCase):
    def test_estimates(self):  # ps_res f1,f2 ; ps_rsqrte f1,f2
        src = translate([0x10201030, BLR])  # f1 is the result; ps1 is unused, so only ps0's call stays
        self.assertIn("ppc_fres(arg_f2)", src)
        src = translate([0x10201034, BLR])
        self.assertIn("ppc_frsqrte(arg_f2)", src)
        self.assertNotIn("px_rsqrt", src)

    def test_quant_rounds_to_single_first(self):
        self.assertIn("f64 x = (f64)(f32)v * m;", emit.HELPERS["px_quant"])

    def test_oracle_wiring(self):
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        paired = (GC / "ghidra_scripts" / "TcPaired.java").read_text(encoding="utf-8")
        self.assertIn("} else if (TcPaired.fix(this, emu, ram, bank, reads)) {", fix)
        self.assertIn("long[] r = result(f.op, f.pairs);", paired)
        self.assertIn('h.add("ldexpf");', (GC / "ghidra_scripts" / "TcCallOther.java").read_text(encoding="utf-8"))
        self.assertNotIn('if (m.startsWith("ps_")) return', fix)
        self.assertIn("f.store(emu, ram, bank, st, size, quant(f.qtype, f.qscale, f.srcBits[k++]));", paired)


if __name__ == "__main__":
    unittest.main()
