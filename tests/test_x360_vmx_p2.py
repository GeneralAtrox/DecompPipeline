"""VMX_JUDGE_PLAN.md P2: vpermwi128 and vrlimi128 confirmed from the game's own code, in TcVmxConfirmed.java.

The sites (TcVmxConfirmed.java's comment gives the reasoning): vpermwi128 at 82e90d48 (a plane from a cross product, its
lane 3 added to a dot of lanes 0-2) and 82e74950 (a triangle's cross product read back lane by lane with lfs);
vrlimi128 at 827be458 (a script call adding args 2, 3, 4 to x, y, z) and 827c5530 (a matrix's rows scaled by three
floats), both read back by lfs after stvx. Checked:
1. decode.py gives the game's words the immediates the reasoning uses (perm 135 and 99; IMM/z 4/3 and 3/2);
2. the list holds both ops, each sourced from game addresses;
3. TcVmxP2Stub (javac and java only) replays the sites through TcVmx: the args in order, the scales in order, the
   cross products with w = 0. TCVMX_SCRIPTS points it at another ghidra_scripts;
4. plants: TcVmx with vpermwi128's immediate read low pair first, or vrlimi128 rotating right, or its mask read bit 0 =
   word 0, fails the stub (so the stub can tell the readings apart).
The translator's helpers take the same cases in translator/tests/test_vmx.py (vpermwi_135, vpermwi_99,
vrlimi_827be654; MSVC).
usage: python tests/test_x360_vmx_p2.py
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
X360 = TOOL / "targets" / "xbox360_ppc"
sys.path[:0] = [str(X360 / "tools"), str(X360 / "translator"), str(TOOL / "tests")]

import catalog_refuse  # noqa: E402
from test_tail import jdk  # noqa: E402
from xenontranslate.decode import decode  # noqa: E402

SCRIPTS = pathlib.Path(os.environ.get("TCVMX_SCRIPTS") or X360 / "ghidra_scripts")
WORDS = {   # game address: (word, op, fields)
    0x827be644: (0x19a407d0, "vrlimi128", {"vd": 13, "vb": 0, "imm": 4, "z": 3}),
    0x827be650: (0x180467d0, "vrlimi128", {"vd": 0, "vb": 12, "imm": 4, "z": 3}),
    0x827be654: (0x19a30790, "vrlimi128", {"vd": 13, "vb": 0, "imm": 3, "z": 2}),
    0x827c5570: (0x18045fd0, "vrlimi128", {"vd": 0, "vb": 11, "imm": 4, "z": 3}),
    0x82e90d6c: (0x19870310, "vpermwi128", {"vd": 12, "vb": 0, "perm": 135}),
    0x82e90d70: (0x19636ad0, "vpermwi128", {"vd": 11, "vb": 13, "perm": 99}),
    0x82e749d4: (0x19676b10, "vpermwi128", {"vd": 11, "vb": 13, "perm": 135}),
    0x82e749e0: (0x18e36ad0, "vpermwi128", {"vd": 7, "vb": 13, "perm": 99}),
}
PLANTS = {   # TcVmx.java line -> a wrong reading
    "vpermwi low pair first": ("r[i] = B[(op.perm >>> (6 - 2 * i)) & 3];", "r[i] = B[(op.perm >>> (2 * i)) & 3];"),
    "vrlimi rotates right": ("B[(i + op.z) & 3] : D[i];", "B[(i - op.z) & 3] : D[i];"),
    "vrlimi mask bit 0 = word 0": ("((op.imm >>> (3 - i)) & 1)", "((op.imm >>> i) & 1)"),
}


def stub(scripts):
    work = TOOL / "tests" / ".work"
    work.mkdir(parents=True, exist_ok=True)
    out = tempfile.mkdtemp(prefix="vmxp2_", dir=work)
    try:
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-sourcepath", str(scripts),
                            str(TOOL / "tests" / "java" / "TcVmxP2Stub.java")], capture_output=True, text=True,
                           timeout=300)
        if r.returncode:
            return r.returncode, r.stdout + r.stderr
        r = subprocess.run([jdk("java"), "-cp", out, "TcVmxP2Stub"], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr
    finally:
        shutil.rmtree(out, ignore_errors=True)


class Decode(unittest.TestCase):
    def test_game_words(self):
        for addr, (w, op, fields) in WORDS.items():
            i = decode(w, addr)
            self.assertEqual(i.op, op, hex(addr))
            self.assertEqual({k: i.f[k] for k in fields}, fields, hex(addr))


class List(unittest.TestCase):
    def test_confirmed_from_game_code(self):
        ops = catalog_refuse.confirmed_ops()
        for op, sites in (("vpermwi128", ("82e90d48", "82e74950")), ("vrlimi128", ("827be458", "827c5530"))):
            self.assertIn(op, ops)
            self.assertIn("game code", ops[op])
            for s in sites:
                self.assertIn(s, ops[op])


@unittest.skipUnless(jdk("javac") and jdk("java"), "no JDK on this machine")
class Java(unittest.TestCase):
    def test_stub(self):
        rc, out = stub(SCRIPTS)
        self.assertEqual(rc, 0, out)
        self.assertEqual([x for x in out.splitlines() if x.startswith("WRONG")], [], out)
        self.assertIn("ALL OK", out)

    def test_plants_fail(self):
        src = (SCRIPTS / "TcVmx.java").read_text(encoding="utf-8")
        for name, (old, new) in PLANTS.items():
            self.assertEqual(src.count(old), 1, name)
            with tempfile.TemporaryDirectory(dir=TOOL / "tests" / ".work") as d:
                shutil.copytree(SCRIPTS, pathlib.Path(d) / "s", ignore=shutil.ignore_patterns("*.class"))
                (pathlib.Path(d) / "s" / "TcVmx.java").write_text(src.replace(old, new), encoding="utf-8")
                rc, out = stub(pathlib.Path(d) / "s")
            self.assertIn("WRONG", out, name)


if __name__ == "__main__":
    unittest.main()
