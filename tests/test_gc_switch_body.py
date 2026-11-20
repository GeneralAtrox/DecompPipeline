"""GameCube: jump-table cases outside the body Ghidra gave the function (Skies of Arcadia round r11, 2026-11-14).

Before: Skies 8002356c, 8012ce60, 80130d30, 80199678 and 801b737c were ERROR "executes code outside its body": Ghidra
left the switch unresolved (the table load is the plain `lis; addi; lwzx; mtctr; bctr`), its body stopped at the bctr,
and the cases lay in a gap of it no function owns. The oracle stopped there, and the translator's switch had no cases
there either (ppctranslate jump_table stops at the first target outside the body). 8010bbd8 had the same gap and
passed only because no vector reached its cases.

The rule (TcSwitchBody, read by TcPrepare): such cases, walked until they rejoin the body, are added to the body the
oracle runs and written to meta.json "switch_body"; the translator adds them to the body it reads
(ppctranslate.workspace.body_of, Workspace.body). A case another function owns, outside the body's hull, or a table
with no case count adds nothing. Without "switch_body" in meta.json every translation is as before.

Before (deadcffb) TcSwitchBody.java does not exist (the stub does not compile) and translate.py ignores meta.json
"switch_body" (the third case's call is missing from the C).
usage: python tests/test_gc_switch_body.py
"""
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
GC = TOOL / "targets" / "gamecube_ppc"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(GC / "translator")]
from test_tail import jdk  # noqa: E402

BASE = 0x80001000
CODE = [0x281E0002, 0x4181005C, 0x3C608000, 0x38632000, 0x7C03E82E, 0x7C0903A6, 0x4E800420,  # cmplwi .. bctr
        0x38600001, 0x48000040, 0x38600002, 0x48000038, 0x48003FD5, 0x48000030] + [0x60000000] * 11 + [0x4E800020]
TABLE = [0x8000101C, 0x80001024, 0x8000102C]
BODY = [(0x80001000, 0x8000101C), (0x80001060, 0x80001064)]


def dol():
    """A DOL with the code as text section 0 at BASE and the table as data section 7 at 0x80002000."""
    code = b"".join(struct.pack(">I", w) for w in CODE)
    table = b"".join(struct.pack(">I", w) for w in TABLE)
    h = bytearray(0x100)
    struct.pack_into(">I", h, 0x00, 0x100)
    struct.pack_into(">I", h, 0x48, BASE)
    struct.pack_into(">I", h, 0x90, len(code))
    struct.pack_into(">I", h, 0x1C, 0x100 + len(code))
    struct.pack_into(">I", h, 0x64, 0x80002000)
    struct.pack_into(">I", h, 0xAC, len(table))
    return bytes(h) + code + table


class Translator(unittest.TestCase):
    def translate(self, meta):
        """translate.py (the pipeline's translator command) on a one-function workspace: the C text."""
        import json
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as d:
            ws = pathlib.Path(d)
            (ws / "state").mkdir()
            (ws / "game.dol").write_bytes(dol())
            (ws / "project.json").write_text(json.dumps({"name": "t", "target": "gamecube_ppc", "binary": "game.dol"}))
            (ws / "state" / "function_bodies.txt").write_text("".join(f"{BASE:08x} {lo:08x} {hi:08x}\n" for lo, hi in BODY))
            m = {"addr": f"{BASE:08x}", "name": f"fn_{BASE:08x}", "signature": f"u32 fn_{BASE:08x}(void)", "params": [],
                 "ret": "u32", **meta}
            (ws / "meta.json").write_text(json.dumps(m))
            r = subprocess.run([sys.executable, "-B", str(GC / "translate.py"), "--repo", str(GC / "translator"),
                                "--workspace", str(ws), "--addr", f"{BASE:08x}", "--meta", str(ws / "meta.json"),
                                "--target-json", str(GC / "target.json"), "--out", str(ws / "out.c")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return (ws / "out.c").read_text(encoding="utf-8")

    def test_cases(self):
        before = self.translate({})
        after = self.translate({"switch_body": [["8000101c", "80001034"]]})
        self.assertNotIn("0x80005000", before)  # no "switch_body": as before, the cases are missing
        self.assertIn("0x80005000", after)  # deadcffb: the third case's call is missing here too
        self.assertIn("switch", after)


@unittest.skipUnless(jdk("javac") and jdk("java"), "no JDK on this machine")
class Stub(unittest.TestCase):
    def test_rule(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as out:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-sourcepath",
                                str(GC / "ghidra_scripts"), str(TOOL / "tests" / "java" / "TcGcSwitchBodyStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # deadcffb: no TcSwitchBody
            r = subprocess.run([jdk("java"), "-cp", out, "TcGcSwitchBodyStub"], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
        print(r.stdout)
        self.assertEqual(got["ext"], '[["8000101c","80001034"]]')  # the three cases, up to the last case's b
        self.assertEqual(got["taken"], "[]")  # a case another function owns: nothing added
        self.assertEqual(got["whole"], "[]")  # every case already in the body
        self.assertEqual(got["no_bound"], "[]")
        self.assertEqual(got["unknown"], "[]")
        self.assertEqual(got["hull"], "[]")
        self.assertEqual(got["dest"], "8 1 -1")


if __name__ == "__main__":
    unittest.main()
