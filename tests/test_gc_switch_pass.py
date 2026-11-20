"""GameCube oracle: the directed switch pass (TcSwitchPass; Skies of Arcadia, 2026-11-15).

Round r11's census found 1,438 of 2,749 jump-table cases of PASS functions reached by no vector: the selector is a
parameter, a word behind a pointer, a global or a call's result drawn at random. TcSwitchPass slices each table bctr's
selector back to that root and inverts the chain (addi, rlwinm, andi., extsb/extsh, loads, a stack local) to the root
value of each case index; copies of valid vectors with that value set are kept when they reach a case still open.

Wiring: TcOracle records the first load per pc (Result.loadAt), TcPrepare runs the pass after TcBoundPass and writes
"switch_pass", TcBoundPass's kept vectors add their switch hits, the file is locked and in the switch_body stamp scope.
Stub (no Ghidra): the slicer on hand-built functions: a parameter less 2, a byte behind r3, a call's result kept in
r31, a parameter stored to the stack and reloaded, two registers or'ed (not directed), a clrlwi'd word less 1 (index
15 is out of its mask), a cmplwi further back than TcSwitchBody's window, a signed halfword, a srwi, a constant, and
the table base in another register (addi r3,r5,lo after lis r5,hi: Skies 800217d0, which TcSwitchBody's table misses).
Round 2 (TcSwitchSel): a prologue's stmw walked past (round 1 stopped there: 65 Skies cases), lbzu, xoris (8010bbd8),
two halfwords joined by `or` (8003617c: each root set to its half), and guards: the register a branch's compare tests
(cmpwi, cmplwi, andi.) and the values sending it either way; a ctr loop and a two-register compare are named.

Before (e097aa11) there is no TcSwitchPass: the wiring checks fail and the stub does not compile. Before round 2
(1469f100e) there is no TcSwitchSel: the stub does not compile. Round 3: a switch in a loop, where both directions of
a branch lead back to the bctr, gets the nearer direction as its way (frontier; before it there is none). Round 4: an
argument switch tries the bases that ran it before the rest (ranFirst; before it the stub does not compile, and round
3's plain order took 8003f5b8 from 10 new cases to 1).
usage: python tests/test_gc_switch_pass.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_tail import CP, jdk  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"


def addi(rd, ra, i): return 14 << 26 | rd << 21 | ra << 16 | (i & 0xFFFF)
def lis(rd, i): return 15 << 26 | rd << 21 | (i & 0xFFFF)
def cmplwi(ra, i): return 10 << 26 | ra << 16 | i
def rlwinm(ra, rs, sh, mb, me): return 21 << 26 | rs << 21 | ra << 16 | sh << 11 | mb << 6 | me << 1
def x31(rs, ra, rb, xo): return 31 << 26 | rs << 21 | ra << 16 | rb << 11 | xo << 1
def dform(op, rd, d, ra): return op << 26 | rd << 21 | ra << 16 | (d & 0xFFFF)


NOP, BCTR = 0x60000000, 0x4E800420


def tail(sel, n, table, far=0):
    hi, lo = table >> 16, table & 0xFFFF
    return ([cmplwi(sel, n - 1)] + [NOP] * far + [16 << 26 | 12 << 21 | 1 << 16 | 0x40, lis(4, hi), rlwinm(0, sel, 2, 0, 29),
            addi(4, 4, lo), x31(0, 4, 0, 23), 0x7C0903A6, BCTR])


def image():
    img, cases = {}, []
    fns = [
        ("arg", [addi(5, 3, -2)], 5, 4, 0),
        ("byte", [dform(34, 6, 5, 3)], 6, 4, 0),
        ("call", ["bl", x31(3, 31, 3, 444)], 31, 5, 0),
        ("stack", [dform(36, 4, 8, 1), "bl", dform(32, 0, 8, 1), x31(0, 7, 0, 444)], 7, 3, 0),
        ("or", [x31(3, 8, 4, 444)], 8, 3, 0),
        ("clrlwi", [dform(32, 9, 0, 3), rlwinm(9, 9, 0, 28, 31), addi(9, 9, -1)], 9, 8, 0),
        ("far", [x31(3, 10, 3, 444)], 10, 3, 20),
        ("lha", [dform(42, 11, 2, 3)], 11, 4, 0),
        ("srwi", [rlwinm(12, 3, 28, 4, 31)], 12, 4, 0),
        ("const", [addi(5, 0, 3)], 5, 4, 0),
        ("rt", [], 3, 6, -1),  # 800217d0's shape: lis r5,hi; slwi r0,r3,2; addi r3,r5,lo; lwzx r0,r3,r0
        ("stmw", [47 << 26 | 27 << 21 | 1 << 16 | 8, x31(3, 9, 3, 444)], 9, 4, 0),
        ("lbzu", [dform(35, 6, 5, 3)], 6, 4, 0),
        ("xoris", [27 << 26 | 3 << 21 | 7 << 16 | 0x8000], 7, 16, 0),
        ("join", [dform(42, 4, 2, 3), dform(42, 0, 0, 3), rlwinm(4, 4, 16, 0, 15), x31(4, 5, 0, 444), dform(15, 5, -3, 5)],
         5, 16, 0),
    ]
    for k, (name, pre, sel, n, far) in enumerate(fns):
        fn, table = 0x80001000 + 0x100 * k, 0x80100000 + 0x40 * k
        words = []
        for w in pre:
            words.append(18 << 26 | ((0x80003000 - (fn + 4 * len(words))) & 0x3FFFFFC) | 1 if w == "bl" else w)
        if far < 0:
            words += [cmplwi(3, n - 1), addi(6, 0, -1), 16 << 26 | 12 << 21 | 1 << 16 | 0x40, lis(5, table >> 16),
                      rlwinm(0, 3, 2, 0, 29), addi(3, 5, table & 0xFFFF), x31(0, 3, 0, 23), 0x7C0903A6, BCTR]
        else:
            words += tail(sel, n, table, far)
        for i, w in enumerate(words):
            img[fn + 4 * i] = w
        for i in range(n):
            img[table + 4 * i] = fn + 0x80 + 4 * i
        cases.append((name, fn, fn + 4 * (len(words) - 1)))
    guards = []
    for k, (name, words, taken) in enumerate(GUARDS):
        fn = 0x80004000 + 0x40 * k
        for i, w in enumerate(words):
            img[fn + 4 * i] = w
        guards.append((name, fn, fn + 4 * (len(words) - 1), taken))
    for i, w in enumerate(LOOP):
        img[0x80005000 + 4 * i] = w
    return img, cases, guards


# A switch in a loop: beq tail; cmplwi; bgt tail; bctr; tail: addi; b head. Both directions of both branches reach the
# bctr again; the nearer is the fallthrough. MusyX 80289390's shape (bgt past the range check to the loop's tail).
LOOP = [11 << 26 | 3 << 16 | 5, 16 << 26 | 12 << 21 | 2 << 16 | 0x10, cmplwi(3, 3), 16 << 26 | 12 << 21 | 1 << 16 | 8, BCTR,
        addi(3, 3, 1), 18 << 26 | (-0x18 & 0x3FFFFFC)]


def bc(bo, bi): return 16 << 26 | bo << 21 | bi << 16 | 0x10


GUARDS = [  # (name, words ending in the branch, 1: the wanted direction is taken)
    ("eq", [11 << 26 | 3 << 16 | 5, bc(12, 2)], 1),
    ("ne", [11 << 26 | 3 << 16 | 5, bc(12, 2)], 0),
    ("bit", [28 << 26 | 4 << 21 | 0x10, NOP, bc(4, 2)], 1),
    ("null", [dform(32, 5, 8, 3), 10 << 26 | 5 << 16, bc(12, 2)], 0),
    ("ctr", [bc(16, 0)], 1),
    ("two", [x31(0, 3, 4, 0), bc(12, 0)], 1),
]


class Wiring(unittest.TestCase):
    def test_wiring(self):
        s = GC / "ghidra_scripts"
        orc = (s / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn("res.loadAt.putIfAbsent(ld[0], ld);", orc)
        prep = (s / "TcPrepare.java").read_text(encoding="utf-8")
        self.assertIn("swpass = why == null ? TcSwitchPass.run(this, seed) : null;", prep)
        self.assertIn('(swpass != null ? swpass + "," : "")', prep)
        self.assertIn("g.switchHits.add(r.bctrs);", (s / "TcBoundPass.java").read_text(encoding="utf-8"))
        tj = json.loads((GC / "target.json").read_text(encoding="utf-8"))
        self.assertIn("TcSwitchPass.java", tj["locked_scripts"])
        sc = next(x for x in tj["stamp_scopes"] if x["scope"] == "switch_body")
        self.assertIsNone(sc["scripts"]["TcSwitchPass.java"][0])
        self.assertIn("TcSwitchSel.java", tj["locked_scripts"])
        self.assertIsNone(sc["scripts"]["TcSwitchSel.java"][0])
        sp = (s / "TcSwitchPass.java").read_text(encoding="utf-8")
        self.assertIn("static List<long[]> frontier(", sp)  # round 3
        self.assertIn("pool = ranFirst(all(), b -> ran(b, s.bctr));", sp)  # round 4


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Stub(unittest.TestCase):
    def test_slicer(self):
        img, cases, guards = image()
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as out:
            o = pathlib.Path(out)
            (o / "img.txt").write_text("".join(f"{a:08x} {w:08x}\n" for a, w in img.items()), encoding="utf-8")
            (o / "q.txt").write_text("".join(f"{fn:08x} {b:08x} 0 3 7 15\n" for _, fn, b in cases)
                                     + "".join(f"G {fn:08x} {b:08x} {t}\n" for _, fn, b, t in guards)
                                     + "W 80005000 80005018 80005010\n" + "O 0 1 0 0 1 1\n", encoding="utf-8")
            src = os.pathsep.join([str(GC / "ghidra_scripts"), str(GC / "template"), str(SHARED)])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcGcSwitchPassStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # e097aa11: no TcSwitchPass; 1469f100e: no TcSwitchSel
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcGcSwitchPassStub", str(o / "img.txt"),
                                str(o / "q.txt")], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        print(r.stdout)
        got = dict(zip([c[0] for c in cases] + ["G" + g[0] for g in guards] + ["W", "O"], r.stdout.splitlines()))
        f = {c[0]: c[1] for c in cases}
        want = {  # kind reg at callee size | value(0) value(3) value(7) value(15) | table length
            "arg": "arg 0 ffffffffffffffff 0 0 | 2 5 9 11 | 4",
            "byte": f"load -1 {f['byte']:x} 0 1 | 0 3 7 f | 4",
            "call": f"call -1 {f['call']:x} 80003000 0 | 0 3 7 f | 5",
            "stack": "arg 1 ffffffffffffffff 0 0 | 0 3 7 f | 3",
            "or": f"join -1 {f['or']:x} 0 0 | null null null null | 3",  # two parameters: no bits to split by
            "clrlwi": f"load -1 {f['clrlwi']:x} 0 4 | 1 4 8 null | 8",
            "far": "arg 0 ffffffffffffffff 0 0 | 0 3 7 f | 3",
            "lha": f"load -1 {f['lha']:x} 0 2 | 0 3 7 f | 4",
            "srwi": "arg 0 ffffffffffffffff 0 0 | 0 30 70 f0 | 4",
            "const": "constant -1 ffffffffffffffff 0 0 | null null null null | 4",
            "rt": "arg 0 ffffffffffffffff 0 0 | 0 3 7 f | 6",
            "stmw": "arg 0 ffffffffffffffff 0 0 | 0 3 7 f | 4",
            "lbzu": f"load -1 {f['lbzu']:x} 0 1 | 0 3 7 f | 4",
            "xoris": "arg 0 ffffffffffffffff 0 0 | 80000000 80000003 80000007 8000000f | 16",
            "join": f"join -1 {f['join'] + 12:x} 0 0 | 3+0 3+3 3+7 3+f | 16",
            "Geq": "-| arg 0 | 5",
            "Gne": "-| arg 0 | 6 4 7 3 0 1 ffffffff 7fffffff 80000000",
            "Gbit": "-| arg 1 | 10 ffffffff",
            "Gnull": "-| load -1 | 1 ffffffff 2 fffffffe 7fffffff 80000000",
            "Gctr": "a loop count (ctr)| - -1 |",
            "Gtwo": "a compare of two registers| - -1 |",
            "W": "8000500c:f 80005004:f",  # 1469f100e..round 2: no TcSwitchPass.frontier, the stub does not compile
            "O": "1 4 5 0 2 3",  # round 3 (3bf483b5e): no TcSwitchPass.ranFirst, the stub does not compile
        }
        for k, v in want.items():
            self.assertEqual(got[k].split(": ", 1)[1] if k not in ("W", "O") else got[k][3:], v, k)


if __name__ == "__main__":
    unittest.main()
