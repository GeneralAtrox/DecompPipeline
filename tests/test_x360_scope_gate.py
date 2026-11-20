"""The "gate" stamp scope (target.json "stamp_scopes"): the GATE extra vectors (TcSeedx GATE, x360-judge-plantclass)
reach only the functions TcGateScan.Seeds puts in GATE, so every other function keeps the stamp of its harness. The
scope reads the list Ghidra's TcGateScan writes with that same decision (state/gate_scope.txt, tools/gate_scan.py).
Checked: across the GATE change (3927c7ca's TcSeedx.java and TcVectors.java to today's, TcGateScan.java added) a
function the list leaves out keeps its stamp and a listed one has it moved; a stale or missing list puts every
function in scope; and there is one decision: TcVectors and TcGateScan both take it from TcGateScan.Seeds.
usage: python tests/test_x360_scope_gate.py
"""
import hashlib
import json
import pathlib
import shutil
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360, tmpdir  # noqa: E402

from core import oracle_stamp, paths, target_hooks  # noqa: E402
import x360_scope  # noqa: E402

SCRIPTS = X360 / "ghidra_scripts"
# the two scripts as they stood at 3927c7ca, the base without GATE (TcGateScan.java did not exist)
BASE = {"TcSeedx.java": "c43763e4161778bda184b5a12a2d9013491d4e6151c376187a337b8dd1fbee0a",
        "TcVectors.java": "7a3569b5049ce387bbc464ef71213abeddf0bbef591c9bf752c343e59b6b9de2"}
IN, OUT = "82a1d008", "82000004"   # a GATE function (82a1d008: its gate and fields, tests/java/TcSeedxStub) and another


def script_files():
    out = {p.name: p for p in SCRIPTS.glob("*.java")}
    for d in json.loads((X360 / "target.json").read_text(encoding="utf-8")).get("shared_scripts") or []:
        out.update({p.name: p for p in (paths.SHARED_SCRIPTS / d).glob("*.java")})
    return out


class Stamps(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir("x360gate_")
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / "catalog.json").write_text("{}", encoding="utf-8")
        (self.d / "TcInputs.java").write_text("// inputs", encoding="utf-8")
        # binary_path and an empty bodies file: master's unlisted_call_args scope (TcTrack) asks core/unchecked_args,
        # which puts a function with no recorded body in scope, the same on both sides of each comparison here
        (self.d / "image.bin").write_bytes(bytes(8))
        (self.d / "bodies.txt").write_text("", encoding="utf-8")
        self.cfg = dict(json.loads((X360 / "target.json").read_text(encoding="utf-8")), binary_sha256="ab",
                        binary_path=self.d / "image.bin")
        self.now = script_files()
        self.base = {n: p for n, p in self.now.items() if n != "TcGateScan.java"}
        for p in (mock.patch.object(paths, "STATE", self.d), mock.patch.object(paths, "CATALOG", self.d / "catalog.json"),
                  mock.patch.object(paths, "INPUTS", self.d / "TcInputs.java"),
                  mock.patch.object(paths, "FUNCTION_BODIES", self.d / "bodies.txt"),
                  mock.patch.object(paths, "PROJECT", {"target": "xbox360_ppc"}),
                  mock.patch.object(paths, "target_scripts", side_effect=lambda: dict(self.now)),
                  mock.patch.object(target_hooks, "get", return_value=x360_scope.in_scope)):
            p.start()
            self.addCleanup(p.stop)

    def write_list(self, addrs, header=None):
        # (getattr: the test runs, and fails on its assertions, on a tree without the scope too)
        lines = [header or getattr(x360_scope, "gate_header", lambda: "#")(), f"# functions 2 gate {len(addrs)}", *addrs]
        (self.d / getattr(x360_scope, "GATE_LIST", "gate_scope.txt")).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def stamp(self, addr, base):
        if not base:
            return oracle_stamp.stamp_of(self.cfg, addr, self.now, "-", None, 48, 48)
        real = oracle_stamp.file_hash
        with mock.patch.object(oracle_stamp, "file_hash", side_effect=lambda p: BASE.get(pathlib.Path(p).name) or real(p)):
            return oracle_stamp.stamp_of(self.cfg, addr, self.base, "-", None, 48, 48)

    def test_outside_keeps_its_stamp(self):
        self.write_list([IN])
        self.assertEqual(self.stamp(OUT, False), self.stamp(OUT, True))

    def test_inside_has_it_moved(self):
        self.write_list([IN])
        self.assertNotEqual(self.stamp(IN, False), self.stamp(IN, True))
        self.assertTrue(x360_scope.in_scope(self.cfg, IN.upper(), "gate"))
        self.write_list([])   # the list decides: unlisted, the same function is out of scope
        self.assertFalse(x360_scope.in_scope(self.cfg, IN, "gate"))

    def test_stale_or_missing_list_moves_every_stamp(self):
        self.assertIsNone(x360_scope.gate_list())   # no list
        self.assertNotEqual(self.stamp(OUT, False), self.stamp(OUT, True))
        self.write_list([IN], header="# TcGateScan scripts 0000000000000000 catalog -")  # other scripts
        self.assertIsNone(x360_scope.gate_list())
        self.assertNotEqual(self.stamp(OUT, False), self.stamp(OUT, True))
        self.write_list([IN])
        (self.d / "catalog.json").write_text('{"a": 1}', encoding="utf-8")   # another catalog
        self.assertIsNone(x360_scope.gate_list())


class OneDecision(unittest.TestCase):
    """The scope's membership and the vectors' GATE class are one decision: TcGateScan.Seeds."""

    def test_one_decision(self):
        v = (SCRIPTS / "TcVectors.java").read_text(encoding="utf-8")
        g = (SCRIPTS / "TcGateScan.java").read_text(encoding="utf-8")
        seeds = g[g.index("static final class Seeds"):g.index("public void run()")]
        self.assertIn("cw[0] = TcSeedx.withGate(cw[0], gateFields);", seeds)
        self.assertIn("return (cw[0] & TcSeedx.GATE) != 0;", seeds)
        mk = v[v.index("void makeVectors"):v.index("int nextId")]
        self.assertIn("TcGateScan.Seeds sd = new TcGateScan.Seeds(prog(), body);", mk)
        self.assertIn("seedExtras(sd.cw, ", mk)
        self.assertIn("gateFields = sd.gateFields;", mk)
        self.assertIn("new Seeds(currentProgram, f.getBody()).gate()", g)
        # nothing else decides GATE
        for p in SCRIPTS.glob("*.java"):
            n = p.read_text(encoding="utf-8").count("withGate(")
            self.assertEqual(n, {"TcGateScan.java": 1, "TcSeedx.java": 1}.get(p.name, 0), p.name)

    def test_scan_tool_compiles(self):
        import py_compile
        py_compile.compile(str(X360 / "tools" / "gate_scan.py"), doraise=True, cfile=str(tmpdir("x360gate_pyc_") / "g.pyc"))

    def test_entry(self):
        e = {s["scope"]: s for s in json.loads((X360 / "target.json").read_text(encoding="utf-8"))["stamp_scopes"]}["gate"]
        self.assertTrue(e["why"])
        self.assertEqual({n: b for n, (b, _) in e["scripts"].items()}, dict(BASE, **{"TcGateScan.java": None}))


if __name__ == "__main__":
    unittest.main()
