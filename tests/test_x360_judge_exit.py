"""The Xbox 360 judge's exit state (target.json "exit_state"; core/strict_judge EXIT_CLASSES): CR, XER, CTR, LR and
FPSCR at exit, the callee-saved r14..r31, f14..f31 and CR2..CR4, the volatile registers, the vector registers and the
stack bytes written, which the normal judge does not compare.
  Comparer: a planted difference in every class is found in that class only, enforced or reported by its mode, with
the NaN rule for floats; an older harness (no exit lines) gets no comparison.
  Translator: the exit-state form of six plants (a save and restore of r31 and f31, addc/adde, cmpwi cr3, a CTR loop,
vector ops, a stack store) records each register it writes; the normal and port forms are byte for byte what they
were before the exit state existed (hashes from 9ff73cb4), and the port form refuses the flag.
  Runtime (needs cl): the plants' exit-state C run by runner.exe "exit" on seeded vectors matches the exit state worked
out by hand from the PowerPC ISA, and a mutant per class (r31 clobbered, CA flipped, a CR3 bit flipped, CTR changed,
a vector lane changed, a stack store changed) is rejected.
  The oracle's side (TcExit, TcFacts "exit_state", the stamp scope): tests/test_x360_judge_exit_oracle.py.
usage: python tests/test_x360_judge_exit.py
"""
import copy
import hashlib
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import unittest
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import TOOL, X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

sys.path.insert(0, str(X360))
sys.path.insert(0, str(X360 / "translator"))
sys.path.insert(0, str(TOOL))
import x360_check  # noqa: E402
import x360_compare as cmp  # noqa: E402
from core import strict_judge  # noqa: E402
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, TranslateError, translate_function  # noqa: E402

RT = X360 / "runtime"
BASE = 0x82010000
SP, SENT = 0x700FE000, 0x700FF000


def bits(d):
    return struct.unpack(">Q", struct.pack(">d", d))[0]


# ------------------------------------------------------------------------------------------------- the exit state
NV = [(14 + i) * 0x0101010101010101 for i in range(18)]        # the seeds of r14..r31
GV = [bits(14 + i + 0.5) for i in range(18)]                    # f14..f31
K = 0x00936000                                                  # CR2 = 9, CR3 = 3, CR4 = 6


def state(p, q=0, z=0):
    """The exit state of a function that writes nothing: every register as it was at entry (TcOracle.setEntry)."""
    xs = [0] * 32
    xs[1], xs[3], xs[4], xs[10] = SP, p, q, z
    xs[14:32] = NV
    xf = [0] * 14 + GV
    return {"done": True, "faults": [], "r3": p, "f1": 0, "writes": {}, "calls": [], "cov": set(),
            "xs": xs, "xf": xf, "xc": [K, 0, 0, 0, 0, SENT, 0], "xv": {}, "xw": {}}


def be(a, v, n=8):
    return {a + i: b for i, b in enumerate(v.to_bytes(n, "big"))}


META = {"ret": "u64", "callees": [], "params": []}
VEC = {"patches": []}
IMAGE = bytes(0x100)


def found(exp, act, meta=META):
    return {c for c, d in cmp.compare_exit(meta, IMAGE, VEC, exp, act).items() if d}


class Comparer(unittest.TestCase):
    PLANTS = {
        "callee_saved_gpr": lambda a: a["xs"].__setitem__(20, a["xs"][20] ^ 1),
        "callee_saved_fpr": lambda a: a["xf"].__setitem__(31, bits(2.0)),
        "cr_nonvolatile": lambda a: a["xc"].__setitem__(0, a["xc"][0] ^ (1 << (31 - 9))),    # CR2 GT
        "cr_volatile": lambda a: a["xc"].__setitem__(0, a["xc"][0] ^ (1 << 31)),             # CR0 LT
        "xer": lambda a: a["xc"].__setitem__(1, 1),
        "ctr": lambda a: a["xc"].__setitem__(4, 5),
        "lr": lambda a: a["xc"].__setitem__(5, 0x82010010),
        "fpscr": lambda a: a["xc"].__setitem__(6, 0x82000000),
        "volatile_gpr": lambda a: a["xs"].__setitem__(5, 7),
        "volatile_fpr": lambda a: a["xf"].__setitem__(2, bits(1.0)),
        "stack_pointer": lambda a: a["xs"].__setitem__(1, SP - 16),
        "vector": lambda a: a["xv"].__setitem__(3, (10, 10, 11, 10)),
        "stack_writes": lambda a: a["xw"].update(be(0x700FDFF8, 1)),
    }

    def test_every_class_is_planted(self):
        self.assertEqual(set(self.PLANTS), set(strict_judge.EXIT_CLASSES))

    def test_plants(self):
        modes = strict_judge.exit_modes({})
        self.assertEqual({c for c, m in modes.items() if m == "enforce"}, set(strict_judge.EXIT_ENFORCED))
        exp = state(5, 6)
        exp["xv"] = {3: (10, 10, 10, 10)}
        exp["xw"] = be(0x700FDFF8, 0)
        self.assertEqual(found(exp, copy.deepcopy(exp)), set())
        for cls, plant in self.PLANTS.items():
            with self.subTest(cls):
                act = copy.deepcopy(exp)
                plant(act)
                self.assertEqual(found(exp, act), {cls})
                report, n = [], Counter()
                diffs = cmp.compare_vector(META, IMAGE, VEC, exp, exp, exit_act=act, modes=modes, report=report, classes=n)
                self.assertEqual(n, Counter({cls: 1}))
                if cls in strict_judge.EXIT_ENFORCED:
                    self.assertTrue(diffs and all(d.startswith(f"exit state, {cls}: ") for d in diffs), diffs)
                    self.assertEqual(report, [])
                else:
                    self.assertEqual(diffs, [])
                    self.assertTrue(report and report[0].startswith(f"exit_state {cls} (report only): "), report)
                    result = {}
                    strict_judge.note(result, "dev", 0, report)
                    self.assertEqual(result["strict_report"]["vectors"], 1)
                every = dict.fromkeys(strict_judge.EXIT_CLASSES, "enforce")
                self.assertTrue(cmp.compare_vector(META, IMAGE, VEC, exp, exp, exit_act=act, modes=every, report=[], classes=Counter()))

    def test_rules(self):
        exp = state(5)
        act = copy.deepcopy(exp)
        exp["xf"][20], act["xf"][20] = 0x7FF8000000000001, 0x7FF8000000000000   # two NaNs: the payload is the FPU's
        self.assertEqual(found(exp, act), set())
        act["xf"][20] = bits(1.0)
        self.assertEqual(found(exp, act), {"callee_saved_fpr"})
        act = copy.deepcopy(exp)
        act["xs"][3] = 99           # r3 is the integer return: compared by RET, not here
        self.assertEqual(found(exp, act), set())
        self.assertEqual(found(exp, act, dict(META, ret="void")), {"volatile_gpr"})
        act = copy.deepcopy(exp)
        act["xf"][1] = bits(3.0)    # f1 likewise for a float return
        self.assertEqual(found(exp, act, dict(META, ret="f64")), set())
        self.assertEqual(found(exp, act), {"volatile_fpr"})
        self.assertEqual(found(exp, None), {"callee_saved_gpr"})      # the exit-state form did not run: enforced
        modes = strict_judge.exit_modes({})
        self.assertTrue(cmp.compare_vector(META, IMAGE, VEC, exp, exp, exit_act=None, modes=modes, report=[], classes=Counter()))

    def test_old_harness(self):
        old = {k: v for k, v in state(5).items() if not k.startswith("x")}
        act = state(5)
        act["xs"][20] = 1
        self.assertEqual(cmp.compare_exit(META, IMAGE, VEC, old, act), {})
        n = Counter()
        self.assertEqual(cmp.compare_vector(META, IMAGE, VEC, old, old, exit_act=act, modes=strict_judge.exit_modes({}), report=[], classes=n), [])
        self.assertEqual(n, Counter())
        self.assertEqual(cmp.compare_vector(META, IMAGE, VEC, old, old), [])

    def test_modes(self):
        m = strict_judge.exit_modes({"strict_judge": {"exit_state": {"xer": "enforce", "callee_saved_fpr": "report"}}})
        self.assertEqual((m["xer"], m["callee_saved_fpr"], m["callee_saved_gpr"]), ("enforce", "report", "enforce"))
        for bad in ({"exit_state": {"xr": "report"}}, {"exit_state": {"xer": "warn"}}, {"exit_state": "report"}):
            with self.assertRaises(ValueError):
                strict_judge.exit_modes({"strict_judge": bad})
        strict_judge.modes({"strict_judge": {"exit_state": {"xer": "enforce"}}})     # accepted beside the others
        self.assertEqual(strict_judge.facts_lines({"target": "xbox360_ppc", "exit_state": True})[-1:], ["exit_state 1"])
        self.assertNotIn("exit_state 1", strict_judge.facts_lines({"target": "xbox360_ppc"}))
        with self.assertRaises(ValueError):
            strict_judge.facts_lines({"target": "gamecube_ppc", "exit_state": True})

    def test_parse(self):
        d = tmpdir("x360jx_")
        try:
            p = d / "out.txt"
            p.write_text("V 3 dev\nRET 1 0\nW 40000000 01020304\nXW 700fdffc 00001234\n"
                         "XS " + " ".join(f"{i:016x}" for i in range(32)) + "\n"
                         "XF " + " ".join(f"{bits(i):016x}" for i in range(32)) + "\n"
                         "XC 00936000 1 0 1 0000000000000008 00000000700ff000 82000000\n"
                         "XV 3 0000000a 0000000a 0000000a 0000000b\n"
                         "E\n", encoding="utf-8")
            r = cmp.parse_results(p)[3]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual((r["xs"][31], r["xf"][2], r["xc"], r["xv"]),
                         (31, bits(2), [0x936000, 1, 0, 1, 8, SENT, 0x82000000], {3: (10, 10, 10, 11)}))
        self.assertEqual((r["xw"], r["writes"]), (be(0x700FDFFC, 0x1234, 4), be(0x40000000, 0x01020304, 4)))


# ---------------------------------------------------------------------------------------------------- the plants
PLANTS = {   # name: (words, p, q, the exit state worked out from the ISA on top of state(p, q, z))
    "a": ([0xFBE1FFF8,      # std r31,-8(r1)
           0xDBE1FFF0,      # stfd f31,-16(r1)
           0x7C7F1B78,      # mr r31,r3
           0x387F0001,      # addi r3,r31,1
           0xCBE1FFF0,      # lfd f31,-16(r1)
           0xEBE1FFF8,      # ld r31,-8(r1)
           0x4E800020],     # blr
          0x1111, 0, lambda s: (s["xs"].__setitem__(3, 0x1112),
                                s["xw"].update({**be(0x700FDFF0, GV[17]), **be(0x700FDFF8, NV[17])}))),
    "b": ([0x7CA32014,      # addc r5,r3,r4: 1 + 2^64-1 = 0 carry 1
           0x7C632114,      # adde r3,r3,r4: 1 + 2^64-1 + 1 = 1 carry 1
           0x4E800020],
          1, 0xFFFFFFFFFFFFFFFF,
          lambda s: (s["xs"].__setitem__(3, 1), s["xs"].__setitem__(5, 0), s["xc"].__setitem__(1, 1))),
    "c": ([0x2D830005,      # cmpwi cr3,r3,5: 3 < 5, so CR3 = LT (SO is XER[SO], 0)
           0x4E800020],
          3, 0, lambda s: s["xc"].__setitem__(0, (K & ~0x000F0000) | 0x00080000)),
    "d": ([0x38A00000,      # li r5,0
           0x7C6903A6,      # mtctr r3
           0x38A50002,      # addi r5,r5,2
           0x4200FFFC,      # bdnz -4: 4 times
           0x7CA903A6,      # mtctr r5
           0x7CA32B78,      # mr r3,r5
           0x4E800020],
          4, 0, lambda s: (s["xs"].__setitem__(3, 8), s["xs"].__setitem__(5, 8), s["xc"].__setitem__(4, 8))),
    "e": ([0x1045038C,      # vspltisw v2,5
           0x10621080,      # vadduwm v3,v2,v2
           0x4E800020],
          7, 0, lambda s: s["xv"].update({2: (5, 5, 5, 5), 3: (10, 10, 10, 10)})),
    "f": ([0x38A01234,      # li r5,0x1234
           0x90A1FFFC,      # stw r5,-4(r1): a stack store nothing reads (the normal form drops it)
           0x4E800020],
          9, 0, lambda s: (s["xs"].__setitem__(5, 0x1234), s["xw"].update(be(0x700FDFFC, 0x1234, 4)))),
}
# a frame with a call, for the port form's hash only
CALLER = [0x7D8802A6, 0x9181FFF8, 0x9421FFA0, 0x48000101, 0x38210060, 0x8181FFF8, 0x7D8803A6, 0x4E800020]
# sha256 of the normal ("port" False) and port forms' C at 9ff73cb4, before the exit state
# ("b" re-recorded for 32-bit mode's XER[CA]: its one changed line is the carry test, (u32)(p + q) < (u32)p)
GOLDEN = {
    ("a", False): "91e19f8578ae74857ca26d5510e8208a9fa5bf8594132b1654eaf24f0e965a75",
    ("a", True): "00ebfa4e50494999ed13bff2595049c28edaca062de8f20031bae6c0a6e2fcaf",
    ("b", False): "2303155a309aead3c186fdb23e347db9dd653f61d9ae5fd76d80e0f8eb0f2466",
    ("b", True): "2303155a309aead3c186fdb23e347db9dd653f61d9ae5fd76d80e0f8eb0f2466",
    ("c", False): "82fe6809bfd65c250f4c3efab78432a49112c1a9335e4810dc67af0372a05452",
    ("c", True): "82fe6809bfd65c250f4c3efab78432a49112c1a9335e4810dc67af0372a05452",
    ("d", False): "9b22bae7da1f20e428c7c19ccbb32b6f64f08d27f326da06cccc98c0dbe8ef0e",
    ("d", True): "9b22bae7da1f20e428c7c19ccbb32b6f64f08d27f326da06cccc98c0dbe8ef0e",
    ("e", False): "14588d1b77fce37033f5f75e6c21929abca3f016de87503ffc1c336a42d760ec",
    ("e", True): "a692b84df5bca4c27e4a313dc48c4bd9317c22554002c59a4316ed0cc32b7ee3",
    ("f", False): "004b0397fdc76db08c924f353128ead6960a6d15048b071a305d08d729e4cada",
    ("f", True): "8cd3f2d557b03ef78b99398ae3dbd40f3a7d9d26121a3aa571b8d79ddc9e233e",
    ("g", True): "d13823151413242af03210349b24e0ebac08b4b9faf641b45c8d12dcef86c40e",
}
# per mutant: the plant, its exit-state C changed (pattern, replacement), and the class that must reject it
MUTANTS = {
    "clobber r31": ("a", r"tcx_r\(31u, (.*)\);", r"tcx_r(31u, (\1) ^ 0x10ull);", "callee_saved_gpr"),
    "change a stack store": ("a", r"wr64\(0x700fdff8u, (.*)\);", r"wr64(0x700fdff8u, (\1) ^ 0x100ull);",
                             "stack_writes"),
    "flip CA": ("b", r"tcx_xer\(0u, (.*)\);", r"tcx_xer(0u, !(\1));", "xer"),
    "flip a CR3 bit": ("c", r"tcx_cr\(14u, (.*)\);", r"tcx_cr(14u, !(\1));", "cr_nonvolatile"),
    "change CTR": ("d", r"tcx_ctr\((.*)\);", r"tcx_ctr((\1) + 1ull);", "ctr"),
    "change a vector lane": ("e", r"tcx_v\(3u, (\w+)\.w\);", r"\1.w[2] ^= 1u; tcx_v(3u, \1.w);", "vector"),
    "change the stack store's value": ("f", r"wr32\(0x700fdffcu, (.*)\);", r"wr32(0x700fdffcu, (\1) ^ 1u);",
                                       "stack_writes"),
}


def translate(words, name, **facts):
    code = b"".join(struct.pack(">I", w) for w in words) + bytes(0x400)
    sig = Signature(name, f"u64 {name}(u64 p, u64 q)", [("p", "u64", "r3", -1), ("q", "u64", "r4", -1)], "u64")
    return translate_function(XexImage(code, BASE), BASE, sig, Facts(**facts), [(BASE, BASE + 4 * len(words))])[0]


class Translator(unittest.TestCase):
    def test_normal_and_port_forms_unchanged(self):
        for (k, port), h in GOLDEN.items():
            with self.subTest(plant=k, port=port):
                words = CALLER if k == "g" else PLANTS[k][0]
                src = translate(words, f"plant_{k}", port=port)
                self.assertEqual(hashlib.sha256(src.encode()).hexdigest(), h)
                self.assertEqual(translate(words, f"plant_{k}", port=port, exit_state=False), src)
                self.assertNotIn("tcx_", src)

    def test_records(self):
        want = {"a": ["tcx_r(31u, rd64(0x700fdff8u));", "tcx_f(31u, rdf64(0x700fdff0u));",
                      "wr64(0x700fdff8u, tcx_in_r(0x1fu));",
                      "wrf64(0x700fdff0u, tcx_in_f(0x1fu));", "tcx_r(1u, 0x700fe000ull);", "tcx_lr(0x700ff000ull);"],
                "b": ["tcx_xer(0u, ", "tcx_r(5u, "], "c": [f"tcx_cr({b}u, " for b in (12, 13, 14, 15)],
                "d": ["tcx_ctr(r5);"], "e": ["tcx_v(2u, v2.w);", "tcx_v(3u, v3.w);"],
                "f": ["wr32(0x700fdffcu, 0x1234u);"]}
        for k, (words, *_) in PLANTS.items():
            with self.subTest(plant=k):
                src = translate(words, f"plant_{k}", exit_state=True)
                for w in want[k]:
                    self.assertIn(w, src)
                self.assertEqual(src.count("tcx_r(3u, "), 1)
        self.assertNotIn("0x700fdffc", translate(PLANTS["f"][0], "plant_f"))   # the normal form drops it (stack_dce)
        with self.assertRaises(TranslateError):
            translate(PLANTS["a"][0], "plant_a", port=True, exit_state=True)


DISPATCH = ("#include \"tc_rt.h\"\n{decls}\nu64 fn_82010000(u64 p, u64 q, u64 z)\n{{\n"
            "    switch (z) {{\n{cases}    }}\n    return 0;\n}}\n")
THUNK_META = {"name": "fn_82010000", "ret": "u64", "signature": "u64 fn_82010000(u64 p, u64 q, u64 z)",
              "params": [{"name": "p", "kind": "u64", "storage": "r3"}, {"name": "q", "kind": "u64", "storage": "r4"},
                         {"name": "z", "kind": "u64", "storage": "r10"}]}


class Runtime(unittest.TestCase):
    def test_plants_and_mutants(self):
        if not vcvars64():
            print("SKIPPED: needs cl")
            self.skipTest("needs cl (config.local.json vcvars)")
        runs = [(k, k, None) for k in PLANTS] + [(f"m{i}", k, m) for i, (m, (k, *_)) in enumerate(MUTANTS.items())]
        d = tmpdir("x360jx_")
        try:
            files = []
            for z, (fn, k, m) in enumerate(runs):
                src = translate(PLANTS[k][0], f"plant_{fn}", exit_state=True)
                if m:
                    pat, rep, _ = MUTANTS[m][1:]
                    src, n = re.subn(pat, rep, src)
                    self.assertEqual(n, 1, f"{m}: {pat} in\n{src}")
                (d / f"plant_{fn}.c").write_text(src, encoding="utf-8")
                files.append(f"plant_{fn}.c")
            (d / "work.c").write_text(DISPATCH.format(
                decls="".join(f"u64 plant_{fn}(u64 p, u64 q);\n" for fn, _, _ in runs),
                cases="".join(f"    case {z}: return plant_{fn}(p, q);\n" for z, (fn, _, _) in enumerate(runs))),
                encoding="utf-8")
            (d / "thunk.c").write_text(x360_check.gen_thunk(THUNK_META), encoding="utf-8")
            srcs = " ".join(f'"{RT / s}"' for s in x360_check.SOURCES)
            rc, out = cl(d, f'/DTC_FMA_FUSED=0 /I"{RT}" /Ferunner.exe work.c thunk.c {" ".join(files)} {srcs}')
            self.assertEqual(rc, 0, out[-3000:])
            (d / "image.bin").write_bytes(bytes(0x10400))
            (d / "code.txt").write_text("82010000 82010010\n", encoding="utf-8")
            vecs, exps = [], {}
            for z, (fn, k, _) in enumerate(runs):
                words, p, q, edit = PLANTS[k]
                vecs.append(f"V {z} dev\nR {p:x} {q:x} 0 0 0 0 0 {z:x}\nF {' '.join(['0'] * 13)}\n"
                            f"Q {' '.join(f'{v:x}' for v in NV)}\nG {' '.join(f'{v:x}' for v in GV)}\nK {K:x}\nE\n")
                exps[z] = state(p, q, z)
                edit(exps[z])
                exps[z]["r3"] = exps[z]["xs"][3]
            (d / "vectors.txt").write_text("".join(vecs), encoding="utf-8")
            run = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "vectors.txt", "all", "out.txt",
                                  "exit"], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            got = cmp.parse_results(d / "out.txt")
            plain = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "vectors.txt", "all", "plain.txt"],
                                   cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(plain.returncode, 0, plain.stderr)
            # no exit lines without "exit"
            self.assertFalse(re.search(r"^X", (d / "plain.txt").read_text(encoding="utf-8"), re.M))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        every = dict.fromkeys(strict_judge.EXIT_CLASSES, "enforce")
        for z, (fn, k, m) in enumerate(runs):
            with self.subTest(fn=fn, mutant=m):
                self.assertEqual(got[z]["r3"], exps[z]["r3"])
                classes = found(exps[z], got[z])
                diffs = cmp.compare_vector(META, IMAGE, VEC, exps[z], got[z], exit_act=got[z], modes=every, report=[], classes=Counter())
                if m is None:
                    self.assertEqual(classes, set(), cmp.compare_exit(META, IMAGE, VEC, exps[z], got[z]))
                    self.assertEqual(diffs, [])
                else:
                    self.assertIn(MUTANTS[m][3], classes)
                    self.assertTrue(diffs)


if __name__ == "__main__":
    unittest.main()
