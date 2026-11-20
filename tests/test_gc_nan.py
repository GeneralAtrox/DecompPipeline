"""The GameCube oracle's NaN results (TcFixups with TcNaN, core/ghidra_shared/ppc), on Ghidra's own FloatFormat
(tests/java/TcNaNStub.java): javac and java against the Ghidra jars, no Ghidra application and no project.
Skipped without config.local.json's ghidra_home and a JDK.

Ghidra makes every NaN it computes or converts the canonical one (an invalid operation's negative), where the
PowerPC keeps the operand's payload (Xbox 360 Too Human 82635508: lfs of 0xffffffff, fmuls, fadds, stfs, then the
word in an integer register: the hardware's 0xffffffff, the oracle's 0x7fc00000). The test proves, over 1.9 million
operand sets of every arithmetic op the fixup covers (NaNs of any payload, quiet and signalling, infinities, zeros,
singles):
  - Ghidra's result is a NaN exactly when IEEE's is, so the fixup (which fires only on a NaN result) never touches
    any other value;
  - TcNaN.arith always gives a quiet NaN;
and checks the PowerPC rules on fixed cases: lfs widens a single NaN's payload without quieting; the first NaN of
frA, frB, frC (fields, not assembly order) is the result, quieted; a single result keeps the top 23 fraction bits;
an invalid operation gives 0x7ff8000000000000; fnm* do not negate a NaN; fneg and fabs change only the sign.

RuntimeNaN holds the C side to the same rule (below).

usage: python tests/test_gc_nan.py
"""
import math
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

GC = TOOL / "targets" / "gamecube_ppc"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"
CASES = {
    "lfs_ffffffff": "ffffffffe0000000",
    "lfs_snan_7f800001": "7ff0000020000000",
    "fmuls_toohuman_0": "ffffffffe0000000",
    "stfs_toohuman": "00000000ffffffff",
    "fadd_snan_1": "7ff8000000000001",
    "fadd_qa_qb": "7ff8123456789abc",
    "fadd_1_qb": "fff8000000000042",
    "fadds_qa_1": "7ff8123440000000",
    "fmadd_c_before_b": "fff8000000000042",
    "fnmadd_qb": "fff8000000000042",
    "fsub_inf_inf": "7ff8000000000000",
    "fdiv_0_0": "7ff8000000000000",
    "ghidra_fdiv_0_0": "fff8000000000000",  # the bug: Ghidra's invalid-operation NaN is negative
    "ghidra_lfs_ffffffff": "7ff8000000000000",  # the bug: the payload is gone at the load
    "fneg_qb": "7ff8000000000042",
    "fabs_qb": "7ff8000000000042",
    "fneg_snan": "fff0000000000001",
}


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class NaNResults(unittest.TestCase):
    def test_rules(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcnan_", dir=work) as out:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath",
                                os.pathsep.join([str(GC / "ghidra_scripts"), str(SHARED)]),
                                str(TOOL / "tests" / "java" / "TcNaNStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcNaNStub", "100000", "1"],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        counts = {k: int(v) for k, v in (ln.split() for ln in lines if not ln.startswith(("example", "case")))}
        examples = [ln for ln in lines if ln.startswith("example")]
        cases = {ln.split()[1]: ln.split()[2] for ln in lines if ln.startswith("case")}
        self.assertGreater(counts.get("tried", 0), 1_800_000)
        for key in ("when_ghidra", "loud"):
            self.assertEqual(counts.get(key, 0), 0, f"{key}: " + "; ".join(e for e in examples if key in e))
        self.assertEqual(cases, CASES)


class Wiring(unittest.TestCase):
    def test_text(self):
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        self.assertIn("if (TcNaN.nan(bits(emu, dest))) put(emu, TcNaN.arith(op, srcBits));", fix)
        self.assertIn("if (TcNaN.nan(operand)) put(emu, TcNaN.sign(op, operand));", fix)
        self.assertIn("if (rd[2] == 4 && TcNaN.nan32(rd[3])) put(emu, TcNaN.load(rd[3]));", fix)
        self.assertIn("ps1 = null;\n        if (in == null) return;", fix)  # no step inherits the last one's ps1


# The C side: the runtime's single fused helpers (tc_ppc.c) and the translator's double helpers (emit.HELPERS), built
# as DP builds them (MSVC /fp:strict) and run on every combination of numbers, infinities, zeros and quiet and
# signalling NaNs of both signs, against TcNaN.arith's rule: the first NaN of frA, frB, frC (the fields), quieted, a
# single keeping the top 23 fraction bits; with no NaN operand the IEEE result, or +0x7ff8000000000000 for an invalid
# operation. Found by Skies 80285fd8 (2026-11-14): x86 takes the product's NaN (frC) before the addend's (frB), so a
# fused op with both NaN gave frC's payload where the 750CL and the oracle give frB's. Planted wrong helpers (that
# order, no quieting, no sign) must each be caught.
sys.path.insert(0, str(GC / "translator"))
from test_fmac import vcvars  # noqa: E402
from ppctranslate import emit  # noqa: E402

QUIET, DEFAULT, SIGN = 0x0008000000000000, 0x7FF8000000000000, 1 << 63
OPERANDS = [0x3FF8000000000000, 0xC000000000000000, 0x7FF0000000000000, 0xFFF0000000000000, 0x0000000000000000,
            0x7FF8A00000000000, 0xFFFC500000000000, 0x7FF4300000000000, 0xFFF1E00000000000]  # NaNs: quiet, signalling
# name, C call (a, b, c: frA, frB, frC), the fields in TcNaN's order, the IEEE value, single, negated (fnm*)
OPS = [("fmadds", "ppc_fmadds(a, c, b)", "abc", lambda a, b, c: a * c + b, True, False),
       ("fmsubs", "ppc_fmsubs(a, c, b)", "abc", lambda a, b, c: a * c - b, True, False),
       ("fnmadds", "ppc_fnmadds(a, c, b)", "abc", lambda a, b, c: a * c + b, True, True),
       ("fnmsubs", "ppc_fnmsubs(a, c, b)", "abc", lambda a, b, c: a * c - b, True, True),
       ("fmadd", "px_fmadd(a, c, b)", "abc", lambda a, b, c: a * c + b, False, False),
       ("fmsub", "px_fmsub(a, c, b)", "abc", lambda a, b, c: a * c - b, False, False),
       ("fnmadd", "px_fnm(px_fmadd(a, c, b))", "abc", lambda a, b, c: a * c + b, False, True),
       ("fnmsub", "px_fnm(px_fmsub(a, c, b))", "abc", lambda a, b, c: a * c - b, False, True),
       ("fadd", "px_fadd(a, b)", "ab", lambda a, b, c: a + b, False, False),
       ("fsub", "px_fsub(a, b)", "ab", lambda a, b, c: a - b, False, False),
       ("fmul", "px_fmul(a, c)", "ac", lambda a, b, c: a * c, False, False),
       ("fdiv", "px_fdiv(a, b)", "ab", lambda a, b, c: _div(a, b), False, False)]
# Wrong fused helpers, each judged as fmadd: the old x86 order, the explicit a, c, b order, no quieting, no sign.
_NAN3 = "f64 r = a * c + b; if (r == r) return r; if (a == a && b == b && c == c) return F(0x7ff8000000000000ull); "
PLANTS = {
    "x86_order": "f64 r = a * c + b; return r != r && a == a && b == b && c == c ? F(0x7ff8000000000000ull) : r;",
    "c_before_b": _NAN3 + "return F(B(a != a ? a : c != c ? c : b) | 0x0008000000000000ull);",
    "not_quieted": _NAN3 + "return a != a ? a : b != b ? b : c;",
    "sign_lost": _NAN3 + "return F((B(a != a ? a : b != b ? b : c) | 0x0008000000000000ull) & 0x7fffffffffffffffull);",
}


def _f(b):
    return struct.unpack(">d", struct.pack(">Q", b))[0]


def _bits(x):
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def _isnan(b):
    return (b & 0x7FF0000000000000) == 0x7FF0000000000000 and (b & 0x000FFFFFFFFFFFFF) != 0


def _div(a, b):
    if b == 0.0:
        return math.nan if a == 0.0 or a != a else math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def expected(fields, value, sgl, neg, ops):
    """TcNaN.arith on operand bits ops = {a, b, c}, else the IEEE result (a single rounded to nearest)."""
    nans = [ops[k] for k in fields if _isnan(ops[k])]
    if nans:
        r = nans[0] | QUIET
    else:
        v = value(*(_f(ops[k]) for k in "abc"))
        r = DEFAULT if v != v else _bits(v)
    if _isnan(r):
        return r & 0xFFFFFFFFE0000000 if sgl else r  # fnm* keep a NaN's sign
    if sgl:
        r = _bits(struct.unpack(">f", struct.pack(">f", _f(r)))[0])
    return r ^ SIGN if neg else r


def run_c(work):
    """Build the driver with tc_ppc.c as DP builds the runner; {(i, j, k): [bits per OPS, then per PLANTS]}."""
    names = ["px_f2b", "px_b2f", "px_fadd", "px_fsub", "px_fmul", "px_fdiv", "px_fmadd", "px_fmsub", "px_fnm"]
    plants = [f"static f64 plant_{n}(f64 a, f64 c, f64 b)\n{{\n    {body}\n}}" for n, body in PLANTS.items()]
    cols = [c for _, c, *_ in OPS] + [f"plant_{n}(a, c, b)" for n in PLANTS]
    vals = ", ".join(f"0x{v:016x}ull" for v in OPERANDS)
    prints = "\n".join(f'                printf(" %016llx", (unsigned long long)B({c}));' for c in cols)
    n = len(OPERANDS)
    src = "\n".join(['#include <stdio.h>', '#include <string.h>', '#include "tc_rt.h"',
                     *(emit.HELPERS[h] for h in names),
                     "static u64 B(f64 x)\n{\n    u64 u;\n    memcpy(&u, &x, 8);\n    return u;\n}",
                     "static f64 F(u64 u)\n{\n    f64 x;\n    memcpy(&x, &u, 8);\n    return x;\n}", *plants,
                     f"static const u64 V[{n}] = {{{vals}}};",
                     "int main(void)\n{",
                     f"    for (int i = 0; i < {n}; i++)\n        for (int j = 0; j < {n}; j++)\n"
                     f"            for (int k = 0; k < {n}; k++) {{\n"
                     "                f64 a = F(V[i]), b = F(V[j]), c = F(V[k]);\n"
                     '                printf("%d %d %d", i, j, k);', prints,
                     '                printf("\\n");\n            }', "    return 0;\n}\n"])
    pathlib.Path(work, "nan.c").write_text(src, encoding="utf-8")
    rt = GC / "runtime"
    vc = str(vcvars(64)).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /O1 /fp:strict /W4 /WX /TC '
                       f'/I"{rt}" /Fenan.exe nan.c "{rt / "tc_ppc.c"}""', cwd=work, capture_output=True, text=True,
                       errors="replace", timeout=600)
    assert r.returncode == 0, "\n".join(ln for ln in r.stdout.splitlines() if "error" in ln or "warning" in ln)[:2000]
    run = subprocess.run([str(pathlib.Path(work, "nan.exe"))], cwd=work, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-800:]
    out = {}
    for ln in run.stdout.splitlines():
        t = ln.split()
        out[tuple(int(x) for x in t[:3])] = [int(x, 16) for x in t[3:]]
    return out


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class RuntimeNaN(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcnanc_", dir=work) as d:
            cls.got = run_c(d)

    def wrong(self, col, op):
        _, _, fields, value, sgl, neg = op
        bad = []
        for (i, j, k), row in sorted(self.got.items()):
            ops = {"a": OPERANDS[i], "b": OPERANDS[j], "c": OPERANDS[k]}
            want = expected(fields, value, sgl, neg, ops)
            if row[col] != want:
                bad.append(f"a={ops['a']:016x} b={ops['b']:016x} c={ops['c']:016x}: {row[col]:016x}, want {want:016x}")
        return bad

    def test_helpers_follow_the_750cl(self):
        self.assertEqual(len(self.got), len(OPERANDS) ** 3)
        for n, op in enumerate(OPS):
            bad = self.wrong(n, op)
            self.assertEqual(bad, [], f"{op[0]} ({op[1]}): {len(bad)} wrong, e.g. " + "; ".join(bad[:3]))

    def test_plants_caught(self):
        fmadd = next(op for op in OPS if op[0] == "fmadd")
        for n, name in enumerate(PLANTS):
            self.assertTrue(self.wrong(len(OPS) + n, fmadd), f"planted {name} passed")


if __name__ == "__main__":
    unittest.main()
