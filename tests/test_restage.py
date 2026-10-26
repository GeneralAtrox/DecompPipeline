"""xproject.py restage: rewrite staged imports that today's relocation maps better (after a relocation fix).
Spawn's TC division helpers were staged before f6254d7 (a match's own address pairs) and kept TC's error global
0x8c1129f4 though their match pairs it with the overlay's 0x0c57a054; each failed its attempt 0.
1. an unmatched function's import whose old C is the same and loses its unmapped address is rewritten (the header
   keeps its first line and drops the unmapped note); a second run rewrites nothing;
2. left alone: a matched function's import, an import whose old C has changed since (more than addresses differ),
   one today's relocation does not improve, and everything under --dry;
3. the CLI refuses an OLD that is not the one import.json was matched against.
Run: python -W error tests/test_restage.py (also from test_tooling.py).
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import xproject  # noqa: E402

OLD_C = "s32 fn_8c067c64(u32 a)\n{\n    if (a == 0) {\n        wr32(0x8c1129f4u, 0x44eu);\n    }\n    return a;\n}\n"
HEAD = "/* imported from ToyCommander {} (exact, 1.0) */\n"
NOTE = "/* unmapped addresses of the old project: 8c1129f4 */\n"


def staged(old, new):
    return OLD_C.replace("fn_8c067c64", f"fn_{new}").replace("8c067c64", old)


class Restage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="restage_", dir=TOOL / "tests" / ".work")
        root = pathlib.Path(self.tmp.name)
        self.old, self.new = root / "old", root / "new"
        (self.old / "matched").mkdir(parents=True)
        (self.new / "import").mkdir(parents=True)
        (self.new / "state").mkdir()
        pair = {"8c1129f4": "0c57a054"}
        # 8c563e40: stale; 8c5f6000: matched; 8c5f7000: its old C changed since; 8c5f8000: no better today
        self.matches = {a: {"from": f, "kind": "exact", "ratio": 1.0, "pairs": p} for a, f, p in (
            ("8c563e40", "8c067c64", pair), ("8c5f6000", "8c067c64", pair), ("8c5f7000", "8c067d00", pair),
            ("8c5f8000", "8c067e00", {}))}
        for f in ("8c067c64", "8c067d00", "8c067e00"):
            (self.old / "matched" / f"{f}.c").write_text(staged(f, f), encoding="utf-8")
        (self.old / "matched" / "8c067d00.c").write_text(staged("8c067d00", "8c067d00").replace("a == 0", "!a"),
                                                         encoding="utf-8")
        led = {a: {"addr": a, "status": "queued"} for a in self.matches}
        led["8c5f6000"]["status"] = "matched"
        (self.new / "state" / "ledger.json").write_text(json.dumps({"functions": led}), encoding="utf-8")
        (self.new / "state" / "import.json").write_text(json.dumps(
            {"from": str(self.old), "matches": self.matches, "address_map": {}}), encoding="utf-8")
        self.before = {}
        for a, m in self.matches.items():
            text = HEAD.format(m["from"]) + NOTE + staged(m["from"], a)
            (self.new / "import" / f"{a}.c").write_text(text, encoding="utf-8")
            self.before[a] = text

    def tearDown(self):
        self.tmp.cleanup()

    def files(self):
        return {a: (self.new / "import" / f"{a}.c").read_text(encoding="utf-8") for a in self.matches}

    def test_rewrites_only_the_stale_import(self):
        self.assertEqual(xproject.restage(self.new, self.old, self.matches, {}, dry=True),
                         [("8c563e40", ["8c1129f4"], [])])
        self.assertEqual(self.files(), self.before)  # --dry
        self.assertEqual(xproject.restage(self.new, self.old, self.matches, {}), [("8c563e40", ["8c1129f4"], [])])
        after = self.files()
        self.assertEqual(after["8c563e40"], HEAD.format("8c067c64") + staged("8c067c64", "8c563e40").replace(
            "0x8c1129f4u", "0x0c57a054u"))
        for a in ("8c5f6000", "8c5f7000", "8c5f8000"):
            self.assertEqual(after[a], self.before[a], a)
        self.assertEqual(xproject.restage(self.new, self.old, self.matches, {}), [])  # nothing twice

    def test_cli_checks_the_old_project(self):
        cmd = [sys.executable, str(TOOL / "xproject.py"), "--project", str(self.new), "--from"]
        p = subprocess.run(cmd + [str(self.old), "restage", "--dry"], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("8c563e40: unmapped 8c1129f4 -> none", p.stdout)
        self.assertIn("would rewrite 1 staged import(s)", p.stdout)
        p = subprocess.run(cmd + [str(self.new), "restage"], capture_output=True, text=True, timeout=120)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("was matched against", p.stderr)
        self.assertEqual(self.files(), self.before)


if __name__ == "__main__":
    unittest.main()
