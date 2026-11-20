"""The per-op "confirmed" gate for vector ops (the coordinator's rule, 2026-11-14; VMX_JUDGE_PLAN.md P0).

A function holding a vector op that is not on the one list (targets/xbox360_ppc/ghidra_scripts/TcVmxConfirmed.java) is
refused, naming the op: the oracle's TcFixups.refuseVector ("VMX unconfirmed <op>"), and the catalog scan
(tools/catalog_refuse.py, "unsupported: VMX unconfirmed <op>") read that same list. Checked:
1. catalog_refuse on a tiny image: an unconfirmed op refuses and is named, the first one in address order; adding the op to
   the list admits the function; a function with no vector word, and a class refusal, are unchanged;
2. the list's rows are read from the Java source (comments aside), and TcVmxConfirmed.refusal (javac only, no Ghidra)
   agrees: it names the op, an op in the set is admitted, another op is still refused, and its list equals the parsed one;
3. the wiring: refuseVector asks the gate first, target.json lists the file.
usage: python tests/test_x360_vmx_gate.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
X360 = TOOL / "targets" / "xbox360_ppc"
sys.path[:0] = [str(X360 / "tools"), str(TOOL / "tests")]

import catalog_refuse  # noqa: E402
from test_tail import jdk  # noqa: E402

BASE = 0x82000000
BLR = 0x4E800020
IMAGE = b"".join(w.to_bytes(4, "big") for w in (
    0x10000080, BLR,                           # A at +0:  vadduwm
    0x38600001, BLR,                           # B at +8:  addi (no vector word)
    0x38600001, 0x7C0318CE, 0x14000010, BLR,   # C at +16: addi, lvx, vaddfp128 ... (see ops_of)
    0x10000080, BLR))                          # D at +32: vadduwm, class "sys" refused


def func(off, nwords, vmx, refuse=None, sys_=0):
    counts = {"vmx": vmx}
    if sys_:
        counts["sys"] = sys_
    return {"kind": "function", "addr": "%08x" % (BASE + off), "size": nwords * 4, "counts": counts, "refuse": refuse}


def catalog():
    return {"functions": [func(0, 2, 1), func(8, 2, 0), func(16, 4, 2), func(32, 2, 1, "unsupported: system", 1)]}


def ops_of(cat):
    return {f["addr"]: catalog_refuse.function_ops(f, IMAGE, BASE) for f in cat["functions"] if f["counts"]["vmx"]}


class Catalog(unittest.TestCase):
    def test_unconfirmed_op_is_refused_and_named(self):
        cat = catalog()
        ops = ops_of(cat)
        self.assertEqual(ops["82000000"], ["vadduwm"])
        self.assertEqual(ops["82000010"][0], "lvx")
        catalog_refuse.rerefuse(cat, ops=ops, confirmed={})
        why = {f["addr"]: f["refuse"] for f in cat["functions"]}
        self.assertEqual(why["82000000"], "unsupported: VMX unconfirmed vadduwm")
        self.assertEqual(why["82000010"], "unsupported: VMX unconfirmed lvx")       # the first in address order
        self.assertIsNone(why["82000008"])                                         # no vector word: unchanged
        self.assertEqual(why["82000020"], "unsupported: system")                   # a class refusal comes first

    def test_adding_the_op_admits_it(self):
        cat = catalog()
        ops = ops_of(cat)
        rest = ops["82000010"][1:]
        catalog_refuse.rerefuse(cat, ops=ops, confirmed={"vadduwm": "test", "lvx": "test"})
        why = {f["addr"]: f["refuse"] for f in cat["functions"]}
        self.assertIsNone(why["82000000"])
        if rest:
            self.assertEqual(why["82000010"], "unsupported: VMX unconfirmed " + rest[0])  # another op still refuses
        catalog_refuse.rerefuse(cat, ops=ops, confirmed={o: "t" for o in ["vadduwm", "lvx"] + rest})
        self.assertIsNone(cat["functions"][2]["refuse"])

    def test_without_ops_nothing_changes(self):
        cat = catalog()
        self.assertEqual(catalog_refuse.rerefuse(cat), {})

    def test_stale_vmx_refusal_is_replaced(self):
        cat = {"functions": [dict(func(0, 2, 1), refuse="unsupported: VMX")]}
        ch = catalog_refuse.rerefuse(cat, ops=ops_of(cat), confirmed={})
        self.assertEqual(ch, {("unsupported: VMX", "unsupported: VMX unconfirmed vadduwm"): 1})


class Rows(unittest.TestCase):
    def test_rows_are_read_comments_aside(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "TcVmxConfirmed.java"
            p.write_text('// row("vxor", "no")\n/* row("vor", "no") */\nrow("vaddfp", "AltiVec PEM p.1");\n'
                         '  row("lvlx", "CBEA 3.2");\n', encoding="utf-8")
            self.assertEqual(catalog_refuse.confirmed_ops(p), {"vaddfp": "AltiVec PEM p.1", "lvlx": "CBEA 3.2"})

    def test_real_list_is_parseable_and_sourced(self):
        ops = catalog_refuse.confirmed_ops()
        self.assertTrue(all(v.strip() for v in ops.values()))


@unittest.skipUnless(jdk("javac") and jdk("java"), "no JDK on this machine")
class Java(unittest.TestCase):
    def test_gate(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        out = tempfile.mkdtemp(prefix="vmxgate_", dir=work)
        try:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-sourcepath",
                                str(X360 / "ghidra_scripts"), str(TOOL / "tests" / "java" / "TcVmxGateStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out, "TcVmxGateStub"], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        lines = r.stdout.splitlines()
        self.assertNotIn("WRONG", r.stdout)
        for op in ("vaddfp", "vrefp", "lvx"):
            for k in ("named", "admitted", "other_op_still_refused"):
                self.assertIn(f"{k} {op}", lines)
        listed = [x for x in lines[0].split(" ", 1)[1].split(",") if x] if " " in lines[0] else []
        self.assertEqual(sorted(listed), sorted(catalog_refuse.confirmed_ops()))   # Java and Python read one list


class Wiring(unittest.TestCase):
    def test_text(self):
        fx = (X360 / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        body = fx[fx.index("static String refuseVector"):]
        self.assertLess(body.index("TcVmxConfirmed.refusal(op.mn)"), body.index("TcVmx.supported"))
        self.assertIn('"TcVmxConfirmed.java"', (X360 / "target.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
