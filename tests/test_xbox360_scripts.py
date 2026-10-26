"""The Xbox 360 target's Ghidra scripts (targets/xbox360_ppc/ghidra_scripts), without Ghidra:
- every script and the template TcInputs compile (javac against the Ghidra jars);
- TcClass, the Java twin of tools/ppcscan.classify, gives the same classes on every primary and extended opcode;
- TcFixups' Xenon results (fctiw/fctid with saturation, the stfs store bits, the once-rounded multiply-add, traps)
  agree with the independent Python model of tests/test_xbox360_helpers.py, which the C runtime is tested against;
- the formats agree with x360_compare.py (13 float registers per call, stack doublewords at r1+0x50+8N), the fma
  mode with target.json, and TcFacts accepts the facts core/ghidra writes;
- the template passes core/javalint, the locked scripts exist, and tools/ghidra_setup.py writes the setup list.

usage: python tests/test_xbox360_scripts.py
"""
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import TEXT, THUNK, TOOL, X360, synthetic_image, synthetic_xex, tmpdir  # noqa: E402
from test_xbox360_helpers import (M64, dbl, float_cases, fma_triples, model_conv, model_fma,  # noqa: E402
                                  model_single_bits)
from test_tail import CP, jdk  # noqa: E402
from core import ghidra, javalint  # noqa: E402

import decode_audit  # noqa: E402
import ghidra_setup  # noqa: E402
import ppcscan  # noqa: E402
import x360_compare  # noqa: E402
import xex  # noqa: E402

TARGET = json.loads((X360 / "target.json").read_text(encoding="utf-8"))
SCRIPTS = X360 / "ghidra_scripts"


def words():
    """Every primary opcode with every 10-bit extended opcode, every SPR of mfspr/mtspr, and random words."""
    rng = random.Random(360)
    out = [(op << 26) | (xo << 1) | (rng.getrandbits(15) << 11) & 0x03FFF800 for op in range(64) for xo in range(1024)]
    out += [(31 << 26) | ((spr & 31) << 16) | ((spr >> 5) << 11) | (xo << 1) for spr in range(1024) for xo in (339, 467)]
    out += [rng.getrandbits(32) for _ in range(20000)]
    return [w & 0xFFFFFFFF for w in out]


def trap_model(to, a, b):
    sa, sb = a - (1 << 64) if a >> 63 else a, b - (1 << 64) if b >> 63 else b
    return bool((to & 16 and sa < sb) or (to & 8 and sa > sb) or (to & 4 and a == b) or (to & 2 and a < b)
                or (to & 1 and a > b))


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Scripts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tmpdir("x360java_")
        files = [str(p) for p in sorted(SCRIPTS.glob("*.java"))]
        files += [str(X360 / "template" / "TcInputs.java"), str(TOOL / "tests" / "java" / "X360Stub.java")]
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-J-Xmx512m", "-d", str(cls.out), "-cp", CP, *files],
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            shutil.rmtree(cls.out, ignore_errors=True)
            raise AssertionError(r.stdout + r.stderr)
        rng = random.Random(7)
        cls.words = words()
        cls.floats = float_cases(rng)
        cls.triples = fma_triples(rng)
        cls.traps = [(to, rng.choice([0, 1, M64, 1 << 63, rng.getrandbits(64)]), rng.choice([0, 1, M64, rng.getrandbits(64)]))
                     for to in range(32) for _ in range(8)]
        cases = [f"w {w:08x}" for w in cls.words] + [f"c {x:016x}" for x in cls.floats]
        cases += [f"m {a:016x} {c:016x} {b:016x}" for a, c, b in cls.triples]
        cases += [f"t {to} {a:016x} {b:016x}" for to, a, b in cls.traps]
        (cls.out / "cases.txt").write_text("\n".join(cases) + "\n", encoding="utf-8")
        (cls.out / "facts.txt").write_text(ghidra.facts_body(dict(
            TARGET, fixed_regs={"r13": "0x7fff0000"}, global_ranges=[["0x82400000", "0x82500000"]])), encoding="utf-8")
        r = subprocess.run([jdk("java"), "-Xmx256m", "-cp", os.pathsep.join([str(cls.out), CP]), "X360Stub",
                            str(cls.out / "cases.txt"), str(cls.out / "facts.txt")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            shutil.rmtree(cls.out, ignore_errors=True)
            raise AssertionError(r.stderr[-2000:])
        cls.lines = r.stdout.splitlines()
        cls.k = dict(ln.split()[1:3] for ln in cls.lines if ln.startswith("k "))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def rows(self, tag):
        return [ln.split()[1:] for ln in self.lines if ln.startswith(tag + " ")]

    def test_classes(self):
        bad = []
        for w, got in self.rows("w"):
            want = ",".join(ppcscan.classify(int(w, 16))) or "-"
            if got != want:
                bad.append(f"{w}: java {got} python {want}")
        self.assertEqual(len(self.rows("w")), len(self.words))
        self.assertEqual(bad[:10], [])

    def test_conversions(self):
        for row in self.rows("c"):
            x = int(row[0], 16)
            got = [int(v, 16) for v in row[1:]]
            want = [model_conv(x, 32, True) & M64, model_conv(x, 32, False) & M64, model_conv(x, 64, True) & M64,
                    model_conv(x, 64, False) & M64, model_single_bits(dbl(x))]
            self.assertEqual(got, want, f"{x:016x}")

    def test_fused(self):
        checked = 0
        for row in self.rows("m"):
            a, c, b = (int(v, 16) for v in row[:3])
            for k, got in enumerate(row[3:]):
                want = model_fma(a, c, b, True, bool(k & 4), bool(k & 2), bool(k & 1))
                if want is not None:  # NaN and infinite operands: the model has no opinion
                    self.assertEqual(int(got, 16), want, f"{a:016x} {c:016x} {b:016x} mode {k}")
                    checked += 1
        self.assertGreater(checked, 2000)

    def test_traps(self):
        for to, a, b, got in self.rows("t"):
            self.assertEqual(got == "1", trap_model(int(to), int(a, 16), int(b, 16)), (to, a, b))

    def test_formats(self):
        k = self.k
        self.assertEqual(int(k["STACK_ARGS"]), x360_compare.STACK_ARGS)
        self.assertEqual(int(k["GEN_STACK_ARGS"]), x360_compare.STACK_ARGS)
        self.assertEqual(int(k["STACK_ARGS"]), TARGET["abi"]["stack_args"]["offset"])
        self.assertEqual(int(k["CALL_FLOATS"]), x360_compare.CALL_FLOATS)
        self.assertEqual(int(k["CALL_FLOATS"]), len(TARGET["abi"]["float_args"]))
        self.assertEqual(int(k["FMA_FUSED"]), {"two_roundings": 0, "fused": 1}[TARGET["fma"]])
        rt = (X360 / "runtime" / "tc_rt.c").read_text(encoding="utf-8")
        self.assertIn(f"MAX_CALLS {k['CALL_LIMIT']}", rt.replace("#define ", ""))
        self.assertEqual([int(k[f"slot_{o}"]) for o in ("50", "54", "58", "8c", "90", "4c", "52")],
                         [0x50, 0x50, 0x58, 0x88, -1, -1, -1])  # a word at +4 is the low half of its doubleword
        self.assertEqual([k[f"mmio_{a}"] for a in ("7fc80000", "7fcfffff", "7fd00000", "e0000000", "ffffffff",
                                                   "82000000")], ["1", "1", "0", "1", "1", "0"])

    def test_facts(self):
        k = self.k
        self.assertEqual((k["facts_ram"], k["facts_sp"], k["facts_r13"]), ("40000000", "700fe000", "7fff0000"))
        self.assertEqual((int(k["facts_mmio"]), k["facts_global"]), (len(TARGET["memory"]["mmio"]), "1"))


class Target(unittest.TestCase):
    def test_inputs_lint(self):
        self.assertEqual(javalint.lint_inputs((X360 / "template" / "TcInputs.java").read_text(encoding="utf-8")), [])

    def test_locked(self):
        names = {p.name for p in SCRIPTS.glob("*.java")}
        locked = set(TARGET["locked_scripts"])
        self.assertFalse(locked - names)
        # every script TcPrepare builds on is locked; the others are whole-program tools (setup, catalog, export, audit)
        self.assertEqual(names - locked, {"TcSetup.java", "TcCatalog.java", "TcExport.java",
                                          "TcDecodeAudit.java"})

    def test_memory_map(self):
        base = (SCRIPTS / "TcBase.java").read_text(encoding="utf-8")
        mem, abi = TARGET["memory"], TARGET["abi"]
        for name, want in (("RAM_LO", mem["ram"][0]), ("RAM_HI", mem["ram"][1]), ("IMAGE_LO", mem["image"][0]),
                           ("HEAP_LO", mem["heap"][0]), ("HEAP_HI", mem["heap"][1]), ("STACK_LO", mem["stack"][0]),
                           ("STACK_HI", mem["stack"][1]), ("SP", abi["entry_sp"]), ("SENTINEL", abi["return_sentinel"]),
                           ("KERNEL_LO", mem["kernel"][0]), ("KERNEL_HI", mem["kernel"][1])):
            self.assertRegex(base, rf"\b{name} = {want}L", name)
        self.assertIn(f"SCR_SIZE = 0x{int(mem['scratch'][1], 16) - int(mem['scratch'][0], 16):x}L", base)

    def test_setup_list(self):
        img = synthetic_image()
        d = xex.describe(xex.Xex(synthetic_xex(img)), img)
        cat = json.loads(json.dumps(ppcscan.scan(xex.resolve_imports(img, d), d)))
        work = tmpdir("x360setup_")
        try:
            (work / "cat.json").write_text(json.dumps(cat), encoding="utf-8")
            (work / "proof.json").write_text(json.dumps({"functions": [{"addr": f"{TEXT:08x}"}]}), encoding="utf-8")
            with mock.patch("sys.stdout"):
                self.assertEqual(ghidra_setup.main(["x", str(work / "cat.json"), str(work / "all.txt")]), 0)
                self.assertEqual(ghidra_setup.main(["x", str(work / "cat.json"), str(work / "p.txt"), "--proof",
                                                    str(work / "proof.json"), "--depth", "1"]), 0)
            full = (work / "all.txt").read_text(encoding="utf-8").splitlines()[1:]
            proof = (work / "p.txt").read_text(encoding="utf-8").splitlines()[1:]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        f = [ln.split()[1] for ln in full if ln[0] == "F"]
        self.assertEqual(f, [f"{a:08x}" for a in (TEXT, TEXT + 0x200, TEXT + 0x300, TEXT + 0x400)])  # no helper
        self.assertIn(f"H savegprlr {TEXT + 0x100:08x} {TEXT + 0x150:08x}", full)
        self.assertIn(f"L __savegprlr_14 {TEXT + 0x100:08x}", full)
        self.assertEqual(sum(ln[0] == "I" for ln in full), 1)
        self.assertTrue(any(ln.startswith(f"I {THUNK:08x} ") for ln in full))
        # the proof keeps its root, the callee one call away, every helper and the thunk it calls
        self.assertEqual([ln.split()[1] for ln in proof if ln[0] == "F"], [f"{TEXT:08x}", f"{TEXT + 0x200:08x}"])
        self.assertEqual([ln for ln in proof if ln[0] in "HLI"], [ln for ln in full if ln[0] in "HLI"])

    def test_decode_audit(self):
        image = bytes.fromhex("38600001" "fc22182a" "fc2208fa" "0cc30000" "7c601828" "10000000" "00000000")
        rows = [(0x82000000, 0x38600001, "li"), (0x82000004, 0xFC22182A, "fadd"), (0x82000008, 0xFC2208FA, "fmadd"),
                (0x8200000C, 0x0CC30000, "twi"), (0x82000010, 0x7C601828, "lwarx")]
        self.assertEqual(decode_audit.audit(rows, image, 0x82000000), [])
        bad = decode_audit.audit([(0x82000014, 0x10000000, "vaddubm"), (0x82000018, 0, "??"),
                                  (0x82000000, 0x38600002, "li"), (0x82000004, 0xFC22182A, "add")], image, 0x82000000)
        self.assertEqual([b.split(": ")[1] for b in bad], ["a vector instruction (scanner ['vmx'])",
                                                           "Ghidra decoded no instruction", "not the image's word",
                                                           "Ghidra [], scanner ['fpu']"])


if __name__ == "__main__":
    unittest.main()
