"""core/tflag_scan: the candidate finder of selective_restamp --change tflag is as cautious as TcTflag.exportedFn.
The three functions rs32's samples found it missing (PS2 8c085d90 and 8c178500, Spawn main 8c055e10) and the oracle's
other conservative cases, on synthetic packet asm:
1. `stc SR,r0` (printed upper case by the packet asm) after a call reads T, `ldc r0,SR` writes it (8c055e10, 8c178500);
2. a cycle in the hand-on callers' chain counts as exported, whether or not any caller reads T (8c085d90's callers);
3. a chain of hand-on callers longer than MAX_DEPTH counts; one just under it does not;
4. a callee called from a function without a packet (state/graph.tsv) counts only with outside=True;
5. controls: a caller that writes T first, and a hand-on chain nobody reads, stay out.
Run: python -W error tests/test_tflag_scan.py.
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import tflag_scan as ts  # noqa: E402

CALL = "jsr @r3                              ; call %s"
TAIL = "bra 0x8c000000                       ; call %s"


def asm(rows):
    return "; part 1 of 1 of asm.txt\n" + "".join(f"  {a:08x}  {t:<32} \n" for a, t in rows)


def packets(fns, graph=None):
    """A workspace folder with packets/<addr>/asm.txt for each {addr: [(address, text)]} and an optional graph.tsv."""
    t = tempfile.TemporaryDirectory(prefix="tflag_scan_", dir=ROOT / "tests" / ".work")
    ws = pathlib.Path(t.name)
    for a, rows in fns.items():
        (ws / "packets" / a).mkdir(parents=True)
        (ws / "packets" / a / "asm.txt").write_text(asm(rows))
    if graph:
        (ws / "state").mkdir()
        (ws / "state" / "graph.tsv").write_text(graph)
    return t, str(ws / "packets")


def caller(base, callee, after):
    """A function at base: call callee, then `after` rows (text), then rts."""
    base = int(base, 16)
    rows = [(base, CALL % callee), (base + 2, "_nop")]
    for i, text in enumerate(after + ["rts", "_nop"]):
        rows.append((base + 4 + 2 * i, text))
    return rows


class Cases(unittest.TestCase):
    def setUp(self):
        (ROOT / "tests" / ".work").mkdir(parents=True, exist_ok=True)

    def run_scan(self, fns, **kw):
        t, pk = packets(fns, kw.pop("graph", None))
        with t:
            return ts.tflag_candidates(pk, **kw)

    def test_stc_sr_reads_t(self):  # upper case SR as the packet asm prints it
        c = self.run_scan({"8c010000": caller("8c010000", "8c020000", ["stc SR,r0", "and r2,r0", "ldc r0,SR"]),
                           "8c020000": [(0x8c020000, "rts"), (0x8c020002, "_nop")]})
        self.assertEqual(c, {"8c010000", "8c020000"})

    def test_ldc_sr_writes_t(self):  # ldc to SR first: T is written before any read
        c = self.run_scan({"8c010000": caller("8c010000", "8c020000", ["ldc r0,SR", "stc SR,r0"]),
                           "8c020000": [(0x8c020000, "rts"), (0x8c020002, "_nop")]})
        self.assertEqual(c, set())

    def test_stc_gbr_is_not_a_read(self):
        c = self.run_scan({"8c010000": caller("8c010000", "8c020000", ["stc GBR,r0", "cmp/eq r1,r0"]),
                           "8c020000": [(0x8c020000, "rts"), (0x8c020002, "_nop")]})
        self.assertEqual(c, set())

    def test_cycle(self):  # A tail-calls B, B tail-calls A: each hands T on, no reader anywhere: the oracle exports
        a = [(0x8c030000, "nop"), (0x8c030002, TAIL % "8c040000"), (0x8c030004, "_nop")]
        b = [(0x8c040000, "nop"), (0x8c040002, TAIL % "8c030000"), (0x8c040004, "_nop")]
        self.assertEqual(self.run_scan({"8c030000": a, "8c040000": b}), {"8c030000", "8c040000"})

    def test_cycle_above(self):  # f <- c1 <- x <- y <- x: f's callers' chain reaches the cycle x, y
        f = [(0x8c050000, "rts"), (0x8c050002, "_nop")]
        c1 = [(0x8c060000, CALL % "8c050000"), (0x8c060002, "_nop"), (0x8c060004, "rts"), (0x8c060006, "_nop")]
        x = [(0x8c070000, CALL % "8c080000"), (0x8c070002, "_nop"), (0x8c070004, TAIL % "8c060000"),
             (0x8c070006, "_nop")]
        y = [(0x8c080000, "nop"), (0x8c080002, TAIL % "8c070000"), (0x8c080004, "_nop")]
        c = self.run_scan({"8c050000": f, "8c060000": c1, "8c070000": x, "8c080000": y})
        self.assertIn("8c050000", c)
        self.assertIn("8c060000", c)

    def chain(self, n):
        """f0 <- f1 <- ... <- fn: each tail-calls the one below it; nobody reads T."""
        fns = {}
        for i in range(n + 1):
            a = 0x8c100000 + 0x100 * i
            rows = [(a, "nop")]
            if i:
                rows += [(a + 2, TAIL % f"{a - 0x100:08x}"), (a + 4, "_nop")]
            else:
                rows += [(a + 2, "rts"), (a + 4, "_nop")]
            fns[f"{a:08x}"] = rows
        return fns

    def test_deep_chain(self):  # past MAX_DEPTH the oracle counts; a short chain with no reader does not
        deep = self.run_scan(self.chain(ts.MAX_DEPTH + 4))
        self.assertIn("8c100000", deep)
        self.assertIn("8c100100", deep)
        self.assertEqual(self.run_scan(self.chain(5)), set())

    def test_hand_on_chain_nobody_reads(self):
        c = self.run_scan({"8c0a0000": [(0x8c0a0000, "bsr 0x8c0a1000                       ; call 8c0a1000"),
                                        (0x8c0a0002, "_nop"), (0x8c0a0004, "rts"), (0x8c0a0006, "_nop")],
                           "8c0a1000": [(0x8c0a1000, "rts"), (0x8c0a1002, "_nop")]})
        self.assertEqual(c, set())

    def test_outside_caller(self):  # 8c020000 is called from 8c999999, which has no packet: only with outside
        fns = {"8c020000": [(0x8c020000, "rts"), (0x8c020002, "_nop")]}
        graph = "8c999999\t8c020000,8c030000\t\t\t\n8c020000\t\t\t\t\n"
        self.assertEqual(self.run_scan(fns, graph=graph), set())
        self.assertEqual(self.run_scan(fns, graph=graph, outside=True), {"8c020000"})
        self.assertEqual(self.run_scan(fns, outside=True), set())  # no graph.tsv: nothing


if __name__ == "__main__":
    unittest.main()
