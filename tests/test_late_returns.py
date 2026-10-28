"""Results for the mocked calls after the 16th (TcLate, r15). Before, every call after a vector's 16 scripted results
returned 0, so a branch on a later call's result never ran (TC 8c0117f4: Entity_HasCamView is the 17th call, and a
wrong readable C passed 96/96 with 38 of 55 blocks untested). One pass and one fail control each:
1. the wiring: TcLate is locked and at 400 lines or fewer like every script, TcOracle.RETS equals tc_rt's TCRT_RETS,
   TcOracle returns entryLate after Vec.rets, TcVectors draws it and writes it on line C, TcReach keeps it with a
   vector, and both runtimes return the scripted result for every call below TCRT_RETS;
2. both runners' own line-C parse (compiled from their source): an old line of 16 words and a new one of 20 both read
   in full, a shorter line after a longer one leaves no word of it behind, and a CRLF line stops at its end;
3. selective_restamp --change late takes a harness with a vector that made more than 16 calls, and no other;
4. the repair (decision 5): TcVectors repairs a fault through a later result (TcLate.repair) after TcProvenance,
   TcOracle returns the pool's chunk for a marked callee (TcLate.pooled), TcReach's vector copy keeps the marks and
   the pool (TcLate.Marks: Vec is each workspace's own TcInputs.java, so it gains no field), and selective_restamp
   --change lrep adds the functions with an invalid fault and a call.
The oracle itself needs Ghidra: the re-prepare of 8c0117f4 judges it.
Synthetic data. Run: python -W error tests/test_late_returns.py.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
import selective_restamp as sr  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
RUNNERS = (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c")


def text(p):
    return p.read_text(encoding="utf-8")


class Wiring(unittest.TestCase):
    def test_scripts(self):
        locked = json.loads(text(SH4 / "target.json"))["locked_scripts"]
        self.assertIn("TcLate.java", locked)
        for p in SCRIPTS.glob("*.java"):
            self.assertLessEqual(len(text(p).splitlines()), 400, p.name)
        rets = re.search(r"static final int RETS = (\d+);", text(SCRIPTS / "TcOracle.java"))
        tcrt = re.search(r"#define TCRT_RETS (\d+)", text(SH4 / "runtime" / "tc_rt_internal.h"))
        self.assertEqual(rets.group(1), tcrt.group(1))
        oracle = text(SCRIPTS / "TcOracle.java")
        self.assertIn("entryLate != null && k < entryLate.length ? (entryLate[k] = TcLate.ranged(v, pc, calls, TcFret.at(",
                      oracle)  # genretfn wraps it (tests/test_genretfn.py)
        vectors = text(SCRIPTS / "TcVectors.java")
        self.assertIn("entryLate = TcLate.draw(facts, seed, id, idTry, v.rets.length);", vectors)
        self.assertIn("vout.append(TcLate.tail(entryLate, r.calls.size() - v.rets.length))", vectors)
        reach = text(SCRIPTS / "TcReach.java")
        self.assertEqual(reach.count("g.entryLate = e.late;"), 2)
        self.assertIn("e.late = g.entryLate == null ? null : g.entryLate.clone();", reach)

    def test_repair(self):
        late, oracle = text(SCRIPTS / "TcLate.java"), text(SCRIPTS / "TcOracle.java")
        vectors, reach = text(SCRIPTS / "TcVectors.java"), text(SCRIPTS / "TcReach.java")
        self.assertIn("static boolean repair(TcVectors g, TcInputs.Vec v, TcOracle.Result r)", late)
        self.assertIn("m.callees.add(Long.parseLong(r.calls.get(c).split(", late)
        self.assertIn("TcFret.at(floatRet, pc, TcLate.pooled(v, pc, k, entryLate[k]))", oracle)
        prov, rep = vectors.find("TcProvenance.repair(this, v, r)"), vectors.find("TcLate.repair(this, v, r)")
        self.assertTrue(0 < prov < rep, "TcLate.repair runs after TcProvenance")
        self.assertIn("TcLate.copy(v, c);", reach)
        # the marks live in TcLate: each workspace compiles its own gated TcInputs.java, so Vec gains no field
        self.assertNotIn("late", text(SH4 / "template" / "TcInputs.java").split("class Vec", 1)[1].split("}", 1)[0])

    def test_runtimes(self):
        for p in (SH4 / "runtime" / "tc_rt.c", SH4 / "runtime_rd" / "rd_veh.c"):
            src = text(p)
            self.assertIn("static u32 g_rets[TCRT_RETS];", src, p.name)
            self.assertIn("g_ncalls < TCRT_RETS ? g_rets[g_ncalls] : 0", src, p.name)
        for p in RUNNERS:
            self.assertIn("rets[TCRT_RETS] = {0};", text(p), p.name)


def parse_snippet(runner):
    """The runner's own hexval and its line-C branch body, as C text."""
    src = text(runner).replace("\r\n", "\n")
    hexval = re.search(r"static int hexval\(int c\)\n\{.*?\n\}\n", src, re.S).group(0)
    body = re.search(r"\} else if \(line\[0\] == 'C'\) \{\n(.*?)\n\s*\} else if", src, re.S).group(1)
    return hexval, body


MAIN = """#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int u32;
#define TCRT_RETS 64
%s
int main(int argc, char **argv)
{
    static char line[1 << 16];
    u32 rets[TCRT_RETS] = {0};
    FILE *in = argc > 1 ? fopen(argv[1], "rb") : NULL;
    if (!in) return 2;
    while (fgets(line, sizeof line, in)) {
        if (line[0] == 'C') {
%s
            int n = 0;
            for (int i = 0; i < TCRT_RETS; i++) if (rets[i]) n = i + 1;
            printf("%%d", n);
            for (int i = 0; i < n; i++) printf(" %%x", rets[i]);
            printf("\\n");
        }
    }
    fclose(in);
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class RunnerParse(unittest.TestCase):
    def test_line_c(self):
        lines = [
            "C " + " ".join("%08x" % (i + 1) for i in range(16)),         # an older harness: 16 words
            "C " + " ".join("%08x" % (i + 0x101) for i in range(20)),     # 20: calls 17..20 scripted too
            "C " + " ".join("%08x" % (i + 1) for i in range(16)),         # shorter again: no word 17..20 left
            "C 00000007 00000008\r",                                       # CRLF: stops at the line's end
        ]
        want = ["16 " + " ".join("%x" % (i + 1) for i in range(16)),
                "20 " + " ".join("%x" % (i + 0x101) for i in range(20)),
                "16 " + " ".join("%x" % (i + 1) for i in range(16)),
                "2 7 8"]
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "v.txt").write_bytes(("\n".join(lines) + "\n").encode("ascii"))
            vc = str(game_config.vcvars32()).replace("/", "\\")
            for runner in RUNNERS:
                hexval, body = parse_snippet(runner)
                (d / "p.c").write_text(MAIN % (hexval, body), encoding="utf-8")
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX p.c"', cwd=d,
                                   capture_output=True, text=True, errors="replace", timeout=300)
                self.assertEqual(r.returncode, 0, runner.name + r.stdout[-2000:])
                out = subprocess.run([str(d / "p.exe"), str(d / "v.txt")], capture_output=True, text=True,
                                     timeout=60)
                self.assertEqual(out.stdout.split("\n")[:4], want, runner.name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


def harness(root, addr, calls_per_vector):
    d = root / addr
    d.mkdir(parents=True)
    out = []
    for i, n in enumerate(calls_per_vector):
        out += ["V %d dev" % i, "RET 00000000 00000000"] + ["CALL 8c010000"] * n + ["E"]
    (d / "expected.txt").write_text("\n".join(out) + "\n", encoding="utf-8")


class Candidates(unittest.TestCase):
    def test_late(self):
        with tempfile.TemporaryDirectory() as t:
            h = pathlib.Path(t)
            harness(h, "8c000100", [17])        # a 17th call: its result is TcLate's now
            harness(h, "8c000200", [16, 16])    # 16 calls in each vector: written byte for byte as before
            harness(h, "8c000300", [3, 40, 0])  # one vector past 16
            (h / "8c000400").mkdir()            # no expected.txt yet
            self.assertEqual(sr.late_candidates(h), {"8c000100", "8c000300"})

    def test_lrep(self):
        with tempfile.TemporaryDirectory() as t:
            h, k = pathlib.Path(t) / "harness", pathlib.Path(t) / "packets"
            harness(h, "8c000100", [17])  # a late candidate already
            harness(h, "8c000200", [2])
            harness(h, "8c000300", [2])
            harness(h, "8c000500", [2])
            harness(h, "8c000600", [2])
            inv = {"8c000200": {"write outside RAM": 700}, "8c000300": {"fault: read X": 3},
                   "8c000500": {"step limit (possible infinite loop)": 9}, "8c000600": {"fault: read X": 3}}
            for a, i in inv.items():
                (h / a / "oracle_summary.json").write_text(json.dumps({"invalid": i}), encoding="utf-8")
                (k / a).mkdir(parents=True)
                asm = ("  8c010000  jsr @r3\n  8c010002  _nop\n  8c010004  bf 0x8c010000\n" if a != "8c000600"
                       else "  8c010000  rts\n")
                (k / a / "asm.txt").write_text(asm, encoding="utf-8")
            # 8c000500: no fault; 8c000600: no call
            self.assertEqual(sr.lrep_candidates(h, k), {"8c000100", "8c000200", "8c000300"})
        loop = "  8c010000  mov #1,r1\n  8c010002  jsr @r3\n  8c010004  _nop\n  8c010006  bt 0x8c010000\n"
        self.assertTrue(sr.calls_17(loop))  # the call runs once per pass
        self.assertFalse(sr.calls_17(loop.replace("bt 0x8c010000", "bt 0x8c010008")))  # forward: once
        self.assertFalse(sr.calls_17("  8c010000  bt 0x8c010000\n  8c010002  jsr @r3\n"))  # the loop holds no call
        self.assertTrue(sr.calls_17("".join("  %08x  bsr 0x8c020000\n" % (0x8c010000 + 4 * i) for i in range(17))))


if __name__ == "__main__":
    unittest.main()
