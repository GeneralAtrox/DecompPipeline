"""A double the prototype passes in an integer register (Ghidra's guess for a parameter past f13: 82aa9648 has
param_7 f64 in r6 and param_8 f64 in r7). Before: x360_check.gen_thunk raised ValueError and killed the whole
translate_backlog run. Now the register holds the double's 64 bits: the thunk passes them as a double (tc_bits_f64)
and the translator's entry rebuilds the register from the parameter's bits (px_f2b), not by a numeric conversion.
Every other thunk is unchanged. Skipped parts: without vcvars (the runtime build).
usage: python tests/test_x360_f64_gpr.py
"""
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

import x360_check  # noqa: E402

RT = X360 / "runtime"
BITS = 0x4008000000000000   # 3.0


def meta(ret="u64"):
    return {"name": "fn_82010000", "signature": f"{ret} fn_82010000(u32 a, f64 x, f64 y)", "ret": ret, "params": [
        {"name": "a", "storage": "r3", "kind": "u32"}, {"name": "x", "storage": "r6", "kind": "f64"},
        {"name": "y", "storage": "f2", "kind": "f64"}]}


class Thunk(unittest.TestCase):
    def test_f64_in_a_register(self):
        t = x360_check.gen_thunk(meta())
        self.assertIn("fn_82010000((u32)r[0], tc_bits_f64(r[3]), f[1])", t)
        self.assertIn("static f64 tc_bits_f64(u64 x)", t)

    def test_other_thunks_are_unchanged(self):
        m = meta()
        m["params"] = [dict(p) for p in m["params"]]
        m["params"][1] = {"name": "x", "storage": "r6", "kind": "u64"}
        t = x360_check.gen_thunk(m)
        self.assertNotIn("tc_bits_f64", t)
        self.assertTrue(t.startswith('#include "tc_rt.h"\nu64 fn_82010000('), t[:80])
        with self.assertRaises(ValueError):   # a float32 in a register stays refused
            m["params"][1] = {"name": "x", "storage": "r6", "kind": "f32"}
            x360_check.gen_thunk(m)


class Entry(unittest.TestCase):
    def test_translator_rebuilds_the_register_from_the_bits(self):
        sys.path.insert(0, str(X360 / "translator"))
        try:
            from xenontranslate.image import XexImage
            from xenontranslate.translate import Facts, Signature, translate_function
        finally:
            sys.path.remove(str(X360 / "translator"))
        img = XexImage(bytes.fromhex("4e800020"), 0x82010000)     # blr: returns r3
        for kind, want in (("f64", "return px_f2b(x);"), ("u64", "return x;")):
            sig = Signature("fn_82010000", f"u64 fn_82010000({kind} x)", [("x", kind, "r3", -1)], "u64")
            src, _ = translate_function(img, 0x82010000, sig, Facts(), [(0x82010000, 0x82010004)])
            self.assertIn(want, src)


@unittest.skipUnless(vcvars64(), "no vcvars (MSVC) on this machine")
class Runtime(unittest.TestCase):
    def test_bits_arrive_as_the_double(self):
        m = {"name": "fn_82010000", "signature": "u64 fn_82010000(f64 x)", "ret": "u64",
             "params": [{"name": "x", "storage": "r3", "kind": "f64"}]}
        work = ('#include "tc_rt.h"\nu64 fn_82010000(f64 x)\n{\n    union { f64 d; u64 b; } v;\n    v.d = x;\n'
                '    return v.b;\n}\n')
        zeros = " ".join(["0"] * 13)
        vectors = f"V 0 dev\nR {BITS:x} 0 0 0 0 0 0 0\nF {zeros}\nE\n"
        d = tmpdir("x360f64_")
        try:
            (d / "work.c").write_text(work, encoding="utf-8")
            (d / "thunk.c").write_text(x360_check.gen_thunk(m), encoding="utf-8")
            srcs = " ".join(f'"{RT / s}"' for s in x360_check.SOURCES)
            rc, out = cl(d, f'/DTC_FMA_FUSED=0 /I"{RT}" /Ferunner.exe work.c thunk.c {srcs}')
            self.assertEqual(rc, 0, out[-3000:])
            (d / "image.bin").write_bytes(bytes(0x10400))
            (d / "code.txt").write_text("82010000 82010010\n", encoding="utf-8")
            (d / "vectors.txt").write_text(vectors, encoding="utf-8")
            run = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "vectors.txt", "all", "out.txt"],
                                 cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn(f"RET {BITS:016x}", (d / "out.txt").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
