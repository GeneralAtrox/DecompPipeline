"""tools/tail_refuse.py: functions every run of which leaves the body by a branch the oracle cannot follow are
listed (the oracle refuses them, "executes code outside its body"; skipping their prepare saves Ghidra time), and
catalog_refuse.py --tails turns the list into the catalog's refusals. The classes, on a made-up image:
  certain  every path ends in a branch into the middle of another function;
  neither  a path that returns (blr), a branch to a function entry, an import thunk or code in no function (the oracle
           mocks it: TcRun.tailJump), a computed tail call (bcctr without link: mocked too), a call that returns.
usage: python tests/test_x360_tails.py
"""
import json
import pathlib
import shutil
import sys
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360, tmpdir  # noqa: E402

sys.path[:0] = [str(X360 / "tools"), str(X360 / "translator")]
import catalog_refuse  # noqa: E402
import tail_refuse  # noqa: E402

BASE = 0x82000000
BLR, BCTR, MTCTR, LWZ, LI, CMPWI, BEQ8 = 0x4E800020, 0x4E800420, 0x7D6903A6, 0x81630000, 0x38600001, 0x2C030000, 0x41820008


def b(src, dst, link=False):
    return (18 << 26) | ((dst - src) & 0x03FFFFFC) | (1 if link else 0)


# {name: (address, words)}
FUNCS = {
    "out": (BASE + 0x00, lambda a: [LI, b(a + 4, BASE + 0x300)]),                       # certain
    "call_out": (BASE + 0x10, lambda a: [b(a, BASE + 0x200, True), b(a + 4, BASE + 0x300)]),   # certain (bl returns)
    "computed": (BASE + 0x20, lambda a: [LWZ, MTCTR, BCTR]),                              # a tail call through a pointer
    "plain": (BASE + 0x30, lambda a: [BLR]),
    "branch_returns": (BASE + 0x40, lambda a: [CMPWI, BEQ8, BLR, b(a + 12, BASE + 0x300)]),   # a blr path
    "to_entry": (BASE + 0x50, lambda a: [b(a, BASE + 0x200)]),                            # tail call of a function
    "to_import": (BASE + 0x60, lambda a: [b(a, BASE + 0x280)]),                           # through an import thunk
    "loop": (BASE + 0x70, lambda a: [b(a, a)]),                                          # no exit at all
    "gap": (BASE + 0x80, lambda a: [b(a, BASE + 0x380)]),                                 # to code in no function
    "entry": (BASE + 0x200, lambda a: [BLR]),
    "other": (BASE + 0x2F0, lambda a: [LI] * 8 + [BLR]),                                  # BASE + 0x300 is mid-body
}


def workspace(d):
    img = bytearray(0x400)
    funcs = []
    for name, (a, mk) in FUNCS.items():
        words = mk(a)
        for k, w in enumerate(words):
            img[a - BASE + 4 * k:a - BASE + 4 * k + 4] = w.to_bytes(4, "big")
        funcs.append({"addr": f"{a:08x}", "size": 4 * len(words), "kind": "function", "refuse": None, "counts": {}})
    (d / "image.bin").write_bytes(bytes(img))
    (d / "image.json").write_text(json.dumps({"load_address": BASE}), encoding="utf-8")
    (d / "catalog_inputs.json").write_text(json.dumps({
        "functions": funcs, "helper_ranges": [], "imports": [{"thunk": BASE + 0x280}]}), encoding="utf-8")


class Tails(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir("x360tails_")
        workspace(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_classes(self):
        certain = tail_refuse.Scan(self.d).scan()
        a = lambda n: f"{FUNCS[n][0]:08x}"  # noqa: E731
        self.assertEqual(certain, {a("out"): [f"{BASE + 0x300:08x}"], a("call_out"): [f"{BASE + 0x300:08x}"]})

    def test_catalog_refusals(self):
        certain = tail_refuse.Scan(self.d).scan()
        cat = json.loads((self.d / "catalog_inputs.json").read_text())
        changed = catalog_refuse.rerefuse(cat, tails={"certain": certain})
        self.assertEqual(changed, {(None, catalog_refuse.TAILS[0]): 2})
        why = {f["addr"]: f["refuse"] for f in cat["functions"] if f["refuse"]}
        self.assertEqual(len(why), 2)
        self.assertEqual(catalog_refuse.rerefuse(cat, tails={"certain": certain}), {})

    def test_check_against_harnesses(self):
        certain = tail_refuse.Scan(self.d).scan()
        for name, unsupported, vectors in (("out", "executes code outside its body at 82000300", False),
                                           ("call_out", None, True), ("plain", None, True)):
            h = self.d / "harness" / f"{FUNCS[name][0]:08x}"
            h.mkdir(parents=True)
            (h / "meta.json").write_text(json.dumps({"unsupported": unsupported}), encoding="utf-8")
            if vectors:
                (h / "vectors.txt").write_text("E\n", encoding="utf-8")
        fp, outside, missed, other = tail_refuse.check(self.d, certain)
        self.assertEqual((fp, outside, missed, other), ([f"{FUNCS['call_out'][0]:08x}"], 1, [], []))


if __name__ == "__main__":
    unittest.main()
