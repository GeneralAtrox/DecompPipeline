"""GameCube template TcInputs: two hint forms, for pointers a function gets from its callees and for streams of
pointers. Skies of Arcadia 80011160 / 80014ba8 loop 12 times over call()->0x24->0x4c and 800e96c0 stores through
call results 5 and 7: a hint could name only rN or a global, and the 12-repair limit ran out, so they stayed under 16
valid vectors. 80226dc4 / 80227068 / 802280d4 scan a table of pointers that genstream could fill only with numbers.
Now LOC may be retN (mocked call N's r3 result, N < 16), set like a register (a bare retN takes the value; retN+0xOFF
gets a fresh chunk first), and a genstream value may be "ptr" (a fresh chunk each). Neither form appears in any hint
before this change, and a hints file without them draws exactly as before.
Controls (tests/java/TcGcHintRetStub): a value, one step, two steps, two lines sharing one chunk, an index past 15
refused, rN unchanged, a stream of 6 ptr words all ours; javalint accepts the template.
usage: python tests/test_gc_hintret.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_tail import CP, jdk  # noqa: E402
from core import javalint  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"


class Template(unittest.TestCase):
    def test_lint(self):
        self.assertEqual(javalint.lint_inputs((GC / "template" / "TcInputs.java").read_text(encoding="utf-8")), [])


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Hints(unittest.TestCase):
    def test_cases(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as out:
            src = os.pathsep.join([str(GC / "ghidra_scripts"), str(GC / "template"), str(SHARED)])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcGcHintRetStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcGcHintRetStub"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
        self.assertEqual(got["ret_value"], "ret5=7")
        self.assertEqual(got["ret_ptr"], "ret3=own poke own=own")
        self.assertEqual(got["ret_chain"], "ret0=own poke own=own poke own=own")
        self.assertEqual(got["ret_twice"], "ret2=own poke own=own poke own=own")
        self.assertTrue(got["ret_bad"].startswith("ERROR"), got["ret_bad"])
        self.assertNotIn("ret", got["reg_unchanged"])
        self.assertEqual(got["stream_ptr"], "6 of 6")


if __name__ == "__main__":
    unittest.main()
