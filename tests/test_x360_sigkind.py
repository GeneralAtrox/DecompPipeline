"""A float in an integer register never reaches the prototype as f32 (82c14050 ended ERROR "parameter param_1 in r3 has
kind f32"). The code stores r3 and reloads it with lfs, so Ghidra's decompiler typed the parameter float; TcBase
.prototypeOf kept that type for a GPR (it only forced f64 for an FPR), and x360_check.gen_thunk refuses f32 in r3..r10.
Now TcSigKind.registerKind gives u32 for it. Checked: the Java rule itself (javac and java, no Ghidra), that
prototypeOf calls it for every register parameter, and that the resulting meta gets a thunk where f32 in r3 does not.
usage: python tests/test_x360_sigkind.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360  # noqa: E402

import x360_check  # noqa: E402

SCRIPTS = X360 / "ghidra_scripts"
STUB = """public class SigKindStub { public static void main(String[] a) {
  for (int i = 0; i + 1 < a.length; i += 2) System.out.println(a[i] + " " + a[i + 1] + " -> " + TcSigKind.registerKind(a[i], a[i + 1]));
} }
"""
CASES = [("r3", "f32", "u32"), ("r10", "f32", "u32"), ("r3", "u32", "u32"), ("r4", "s64", "s64"), ("r6", "f64", "f64"),
         ("f1", "f32", "f64"), ("f13", "f64", "f64"), ("f2", "u64", "f64")]


def javac():
    for home in (shutil.which("javac"),):
        if home:
            return home, str(pathlib.Path(home).with_name("java"))  # the JDK's own java, not an older one on PATH
    return None, None


class Rule(unittest.TestCase):
    @unittest.skipUnless(javac()[0], "needs a JDK")
    def test_register_kind(self):
        jc, j = javac()
        with tempfile.TemporaryDirectory(dir=str(pathlib.Path(__file__).resolve().parent.parent / ".work")
                                         if (pathlib.Path(__file__).resolve().parent.parent / ".work").is_dir() else None) as d:
            d = pathlib.Path(d)
            (d / "SigKindStub.java").write_text(STUB)
            subprocess.run([jc, "-d", str(d), str(SCRIPTS / "TcSigKind.java"), str(d / "SigKindStub.java")],
                           check=True, timeout=120)
            args = [x for c in CASES for x in c[:2]]
            out = subprocess.run([j, "-cp", str(d), "SigKindStub"] + args, check=True, capture_output=True,
                                 text=True, timeout=60).stdout.splitlines()
        self.assertEqual(out, [f"{s} {k} -> {w}" for s, k, w in CASES])


class Source(unittest.TestCase):
    def test_prototype_uses_the_rule_and_script_is_listed(self):
        src = (SCRIPTS / "TcBase.java").read_text()
        self.assertIn("q.kind = TcSigKind.registerKind(q.storage, q.kind);", src)
        self.assertNotIn('if (q.storage.matches("f([1-9]|1[0-3])")) q.kind = "f64";', src)
        self.assertIn('"TcSigKind.java"', (X360 / "target.json").read_text())


class Thunk(unittest.TestCase):
    def meta(self, kind):
        return {"name": "fn_82c14050", "signature": "u32 fn_82c14050(u32 a)", "ret": "u32",
                "params": [{"name": "param_1", "storage": "r3", "kind": kind}]}

    def test_u32_in_r3_has_a_thunk_f32_does_not(self):
        self.assertIn("fn_82c14050((u32)r[0])", x360_check.gen_thunk(self.meta("u32")))
        with self.assertRaises(ValueError):
            x360_check.gen_thunk(self.meta("f32"))


if __name__ == "__main__":
    unittest.main()
