"""Address errors in the oracles: an access or a jump no run of the original can make.

- Dreamcast (TcFold, tests/java/TcAlignStub.java, javac and java against the Ghidra jars, no Ghidra application):
  a 2-byte access at an odd address or a longer one off a multiple of 4, while TcOracle steps an instruction, is
  recorded (TcFold.misaligned, the first one, and whether it wrote); the run goes on as before. Aligned accesses,
  byte accesses and the oracle's own reads between steps are not, nor the instruction fetch (Ghidra's decoder reads
  its cache through getChunk with stopOnUninitialized, from a 2-aligned pc); a P4 cache read is still recorded too.
- Dreamcast (TcAlign, tests/java/TcAlignDropStub.java): a valid vector whose run made one is left out of the harness
  (writeVector), and the summary's counts and coverage are those of the written vectors only; "address_errors"
  appears only when a vector was left out. Generation counts it as before, so every other vector is unchanged
  (Power Stone 2 8c052cbe, 8c159102; Spawn 8c05fec2, 8c07cfe0, 8c07d140: a NaN stored through a pointer from the
  inputs at 8cc00507, 8cc00f9b ...); TcAlign.replace then draws replacements after the reach and bound passes.
Skipped (the stubs) without config.local.json's ghidra_home and a JDK.

usage: python tests/test_sh4_align.py
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

SH4 = TOOL / "targets" / "dreamcast_sh4"
CASES = """
aligned      sw4@8cc00504 sr2@8cc00506 sw8@8cc00508 sr4@8c010000
bytes        sw1@8cc00507 sr1@8cc00001
long_odd     sw4@8cc00507
long_half    sr4@8cc00f9a
word_odd     sr2@8cc01ab1
quad_half    sw8@8cc00002
not_stepping w4@8cc00507 r2@8cc00001
first_kept   sr2@8cc00001 sw4@8cc00507
after_step   sr4@8c010000 w4@8cc00507
p4_too       sw4@8cc00507 sr4@f4000000
fetch        sf4@8c010002 sf16@8c010006 sf8@8c01000a
odd_sizes    sr3@8cc00001 sr16@8cc00002
fetch_then   sf16@8c010002 sr4@8cc00f9a
"""
WANT = {
    "aligned": "- -",
    "bytes": "- -",
    "long_odd": "8cc00507 write -",
    "long_half": "8cc00f9a read -",
    "word_odd": "8cc01ab1 read -",
    "quad_half": "8cc00002 write -",
    "not_stepping": "- -",
    "first_kept": "8cc00001 read -",
    "after_step": "- -",
    "p4_too": "8cc00507 write f4000000",
    "fetch": "- -",
    "odd_sizes": "- -",
    "fetch_then": "8cc00f9a read -",
}
DROPS = """
none     dev:-:0,1:8c010010 hidden:-:1:-
one_dev  dev:-:0,1:8c010010 dev:8cc00507:2:8c010020 hidden:-:1,2:8c010020
hidden   dev:-:0:- hidden:8cc00f9a:0,3:8c010010 hidden:-:3:-
all      dev:8cc00001:0:8c010010 hidden:8cc00002:1:-
"""
DROP_WANT = {
    "none": "ww | valid 1 1 | covDev [0, 1] | hits {0=1, 1=2} | sites 8c010010=1 | -",
    "one_dev": "w-w | valid 1 1 | covDev [0, 1] | hits {0=1, 1=2, 2=1} | sites 8c010010=1 8c010020=1"
               ' | "address_errors":{"left_out":[1,0],"replaced":[0,0]},',
    "hidden": "w-w | valid 1 1 | covDev [0] | hits {0=1, 3=1} | sites | "
              '"address_errors":{"left_out":[0,1],"replaced":[0,0]},',
    "all": '-- | valid 0 0 | covDev [] | hits {} | sites | "address_errors":{"left_out":[1,1],"replaced":[0,0]},',
}


def stub(name, cases):
    """Compile tests/java/NAME.java against the Ghidra jars and run it on CASES: {case: output}."""
    work = TOOL / "tests" / ".work"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="align_", dir=work) as out:
        src = os.pathsep.join(str(p) for p in (SH4 / "ghidra_scripts", SH4 / "template",
                                               TOOL / "core" / "ghidra_shared" / "sh"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                            str(TOOL / "tests" / "java" / f"{name}.java")],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        path = pathlib.Path(out) / "cases.txt"
        path.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, name, str(path)],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
    return dict(ln.split(": ", 1) for ln in r.stdout.splitlines())


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Sh4Bank(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(stub("TcAlignStub", CASES), WANT)

    def test_drop(self):
        self.assertEqual(stub("TcAlignDropStub", DROPS), DROP_WANT)


class Wiring(unittest.TestCase):
    def test_sh4_step(self):
        def text(name):
            return (SH4 / "ghidra_scripts" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        orc = text("TcOracle.java")
        self.assertIn("bank.stepping = true; boolean stepped = emu.step(monitor); bank.stepping = false;", orc)
        self.assertIn("if (bank.p4Read >= 0 && fault[0] == null) {", orc)  # the P4 fault as before; no other
        self.assertIn("TcExit.collect(this, emu, res); TcStrict.exit(res, emu); res.misaligned = bank.misaligned;"
                      " res.misWrite = bank.misWrite;", orc)  # the normal exit only
        fold = text("TcFold.java")
        self.assertIn("    public void setChunk(long offset, int size, byte[] val) {\n"
                      "        align(offset, size, true);", fold)
        self.assertIn("    public int getChunk(long offset, int size, byte[] res, boolean stopOnUninitialized) {\n"
                      "        if (!stopOnUninitialized) align(offset, size, false);", fold)
        vec = text("TcVectors.java")
        self.assertIn("    void writeVector(int id, String set, Vec v, Result r) {", vec)
        self.assertIn("        if ((r = TcStrict.replay(this, v, r, set)) == null || align.drop(set, r)) return;", vec)
        self.assertNotIn("misaligned", vec)  # generation itself is unchanged
        prep = text("TcPrepare.java")
        self.assertIn("        String reach = why == null ? TcReach.run(this, proto, seed, seeds) : null;", prep)
        self.assertIn("        String bound = why == null ? TcBoundPass.run(this, proto, seed, new ArrayList<>(globals)) : null;"
                      "  // TcBound\n        if (why == null) align.replace(this, proto, new ArrayList<>(globals), hints,"
                      " seed);", prep)
        self.assertLess(prep.index("align.replace("), prep.index("vectors.txt"))
        # after the strict replays' drops are taken off valid, before the summary's coverage
        self.assertLess(prep.index("valid[s] -= TcStrict.dropped[s];"), prep.index("if (why == null) align.fix(this);"))
        self.assertLess(prep.index("align.fix(this);"), prep.index("StringBuilder sites"))
        self.assertIn('dirs.summary(dirsDev, dirsAll) : "") + (why == null ? align.note() : ""),', prep)
        res = text("TcRescue.java")
        self.assertIn("        s.align = t.align;", res)
        self.assertIn("        t.align = new TcAlign();", res)
        self.assertIn("        t.align = align;", res)


if __name__ == "__main__":
    unittest.main()
