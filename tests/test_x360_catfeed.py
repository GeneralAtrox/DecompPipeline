"""catfeed.py: the port kit's extra function starts (holes, mid entries, leaf entries) fed into a judge workspace's
catalogue, the drift check, and ghidra_setup.place, which keeps Ghidra from being given two functions over one word.

A synthetic image (base 82000000) holds, in order:
  F1 82000000..20 (pdata) with a mid entry at 82000010 that F1 falls into;
  hole 82000020 after F1's blr; hole 82000028 that tail-branches back to F1; hole 82000030 after that tail branch,
  `mtctr r4; bctr` with no table, so xb_leaf runs on to the blr of hole 82000038 (after the bctr);
  padding, F2 82000048..50 (pdata), leaf 82000050 (state.json leaf_entries), hole 82000058 that falls into F3 82000060.
The drift test on a real workspace runs when DP_X360_CATFEED_WS names one (its port_functions.json and catalogue).

usage: python tests/test_x360_catfeed.py
"""
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
X360_TOOLS = TOOL / "targets" / "xbox360_ppc" / "tools"
sys.path[:0] = [str(X360_TOOLS), str(TOOL / "targets" / "xbox360_ppc" / "port" / "tools")]

import catfeed  # noqa: E402
import ghidra_setup  # noqa: E402
import ppcscan  # noqa: E402

BASE = 0x82000000
BLR, BCTR, NOP = 0x4E800020, 0x4E800420, 0x60000000


def li(n):
    return 0x38600000 | n


def b(at, to):
    return 0x48000000 | ((to - at) & 0x03FFFFFC)


WORDS = {
    0x00: li(0), 0x04: li(0), 0x08: li(0), 0x0C: li(0), 0x10: li(1), 0x14: li(1), 0x18: li(1), 0x1C: BLR,   # F1, mid 10
    0x20: 0x38630001, 0x24: BLR,                       # hole 20
    0x28: li(2), 0x2C: b(BASE + 0x2C, BASE),           # hole 28: tail branch back to F1
    0x30: 0x7C8903A6, 0x34: BCTR,                      # hole 30: mtctr r4; bctr (no table)
    0x38: li(3), 0x3C: BLR,                            # hole 38, after the bctr
    0x40: 0, 0x44: 0,                                  # padding
    0x48: li(5), 0x4C: BLR,                            # F2
    0x50: li(7), 0x54: BLR,                            # leaf 50
    0x58: li(9), 0x5C: NOP,                            # hole 58 falls into F3
    0x60: BLR,                                         # F3
}
SIZE = 0x80
PDATA = [(BASE, 0x20), (BASE + 0x48, 8), (BASE + 0x60, 4)]
GEN = ["82000000", "82000010", "82000020", "82000028", "82000030", "82000038", "82000048", "82000050", "82000058",
       "82000060"]
PORT = {"gen": GEN, "mid_entries": {"82000010": "82000020"}, "leaf_entries": {"82000050": "82000058"}}


def image():
    img = bytearray(SIZE)
    for off, w in WORDS.items():
        img[off:off + 4] = struct.pack(">I", w)
    return bytes(img)


def make_ws(root, port=PORT):
    ws = pathlib.Path(root)
    img = image()
    desc = {"load_address": BASE, "imports": [], "pdata": [[a, s, 0] for a, s in PDATA],
            "sections": [{"name": ".text", "start": BASE, "end": BASE + SIZE, "raw_end": BASE + SIZE, "code": True,
                          "execute": True, "read": True, "write": False, "loaded": True}]}
    im = ppcscan.Image(img, desc)
    funcs = [ppcscan.features(im, a, s, "pdata", {}, {}) for a, s in PDATA]
    cat = {"helpers": {}, "helper_ranges": [], "imports": [], "functions": funcs, "mid_entries": [],
           "stats": ppcscan.stats(funcs, [])}
    (ws / "image.bin").write_bytes(img)
    (ws / "image.json").write_text(json.dumps(desc))
    (ws / "catalog_inputs.json").write_text(json.dumps(cat))
    (ws / "port_functions.json").write_text(json.dumps(port))
    return ws


def catalog(ws):
    return {f["addr"]: f for f in json.loads((ws / "catalog_inputs.json").read_text())["functions"]}


class Feed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = make_ws(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_drift_check_fails_before_the_feed_and_passes_after(self):
        miss = catfeed.missing(self.ws)
        self.assertEqual(sorted(miss), ["82000010", "82000020", "82000028", "82000030", "82000038", "82000050",
                                        "82000058"])
        self.assertEqual((miss["82000010"], miss["82000050"], miss["82000020"]), ("mid entry", "leaf entry", "gen/"))
        self.assertEqual(catfeed.main(["catfeed", "check", str(self.ws)]), 1)
        catfeed.feed(self.ws)
        self.assertEqual(catfeed.missing(self.ws), {})
        self.assertEqual(catfeed.main(["catfeed", "check", str(self.ws)]), 0)

    def test_a_new_port_function_is_drift_again(self):
        catfeed.feed(self.ws)
        port = dict(PORT, gen=GEN + ["82000068"])
        (self.ws / "port_functions.json").write_text(json.dumps(port))
        self.assertEqual(catfeed.missing(self.ws), {"82000068": "gen/"})

    def test_bodies_kinds_and_origins_are_the_kits(self):
        r = catfeed.feed(self.ws)
        self.assertEqual(r["added"], {"kit_hole": 5, "kit_mid": 1, "kit_leaf": 1})
        c = catalog(self.ws)
        want = {  # addr: (source, size, after, tail calls)
            "82000010": ("kit_mid", 0x10, "fallthrough", []),
            "82000020": ("kit_hole", 8, "blr", []),
            "82000028": ("kit_hole", 8, "blr", ["82000000"]),
            "82000030": ("kit_hole", 0x10, "tail_branch", []),
            "82000038": ("kit_hole", 8, "bctr", []),
            "82000050": ("kit_leaf", 8, "blr", []),
            "82000058": ("kit_hole", 0xC, "blr", []),
        }
        for a, (src, size, after, tails) in want.items():
            f = c[a]
            self.assertEqual((f["source"], f["size"], f["origin"]["after"], f["tail_calls"], f["kind"]),
                             (src, size, after, tails, "function"), a)
            self.assertEqual(f["origin"]["end"], f"{int(a, 16) + size:08x}")
        self.assertEqual(c["82000010"]["origin"]["parent"], "82000000")
        self.assertEqual([a for a, f in sorted(c.items()) if f.get("origin", {}).get("padded")], [])
        self.assertEqual(c["82000030"]["indirect_calls"] + c["82000030"]["jump_tables"], 1)   # the bctr is in its body
        self.assertEqual(c["82000000"]["source"], "pdata")
        self.assertEqual(r["recorded_end_differs"], [])
        ranges = (self.ws / "code_ranges.txt").read_text().split()
        self.assertEqual(ranges, ["82000000", "82000040", "82000048", "82000064"])

    def test_feed_is_idempotent_and_records_a_changed_end(self):
        port = dict(PORT, leaf_entries={"82000050": "82000060"})   # the kit took it when xb_leaf ended it elsewhere
        (self.ws / "port_functions.json").write_text(json.dumps(port))
        catfeed.feed(self.ws)
        first = (self.ws / "catalog_inputs.json").read_text()
        r = catfeed.feed(self.ws)
        self.assertEqual((self.ws / "catalog_inputs.json").read_text(), first)
        self.assertEqual(r["recorded_end_differs"], ["82000050"])
        self.assertEqual(sum(1 for f in catalog(self.ws).values() if f["source"].startswith("kit_")), 7)

    def test_a_list_elsewhere_is_copied_into_the_workspace(self):
        other = pathlib.Path(self.tmp.name) / "port_elsewhere.json"
        other.write_text(json.dumps(dict(PORT, gen=GEN[:3])))
        r = catfeed.feed(self.ws, other)
        self.assertEqual(r["added"], {"kit_hole": 1, "kit_mid": 1, "kit_leaf": 0})
        self.assertEqual(json.loads((self.ws / "port_functions.json").read_text())["gen"], GEN[:3])

    def test_a_start_in_a_helper_family_is_not_fed_and_not_missing(self):
        cat = json.loads((self.ws / "catalog_inputs.json").read_text())
        cat["helper_ranges"] = [["savegprlr", BASE + 0x20, BASE + 0x28]]
        (self.ws / "catalog_inputs.json").write_text(json.dumps(cat))
        r = catfeed.feed(self.ws)
        self.assertIn("82000020", r["unfed"])
        self.assertEqual(catfeed.missing(self.ws), {})

    def test_snapshot_reads_names_and_state_only(self):
        out = pathlib.Path(self.tmp.name) / "out"
        (out / "gen").mkdir(parents=True)
        for a in ("82000000", "82000020"):
            (out / "gen" / f"fn_{a}.c").write_text("/* not read */")
        (out / "gen" / "tables.c").write_text("")
        (out / "state.json").write_text(json.dumps({"rounds": [], "failed": {}, "mid_entries": {"82000010": "82000020"}}))
        port = catfeed.snapshot(out, pathlib.Path(self.tmp.name) / "p.json")
        self.assertEqual((port["gen"], port["mid_entries"], port["leaf_entries"]),
                         (["82000000", "82000020"], {"82000010": "82000020"}, {}))


class Place(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = make_ws(self.tmp.name)
        catfeed.feed(self.ws)
        self.cat = json.loads((self.ws / "catalog_inputs.json").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def F(self, out):
        return [t.split()[1] for t in out if t.startswith("F ")]

    def test_whole_catalogue_drops_kit_entries_that_share_words(self):
        dropped = []
        got = self.F(ghidra_setup.lines(self.cat, None, (), dropped))
        self.assertEqual(got, ["82000000", "82000020", "82000028", "82000030", "82000048", "82000050", "82000060"])
        self.assertEqual(sorted(f["addr"] for f in dropped), ["82000010", "82000038", "82000058"])

    def test_a_root_wins_over_the_function_it_overlaps(self):
        dropped = []
        got = self.F(ghidra_setup.lines(self.cat, None, ["82000010", "82000058"], dropped))
        self.assertIn("82000010", got)
        self.assertIn("82000058", got)
        self.assertNotIn("82000000", got)
        self.assertNotIn("82000060", got)
        self.assertEqual(sorted(f["addr"] for f in dropped), ["82000000", "82000038", "82000060"])

    def test_two_roots_that_share_words_are_refused(self):
        with self.assertRaises(ghidra_setup.Overlap):
            ghidra_setup.lines(self.cat, None, ["82000030", "82000038"])

    def test_a_catalogue_without_kit_entries_is_listed_as_before(self):
        plain = dict(self.cat, functions=[f for f in self.cat["functions"] if f["source"] == "pdata"])
        self.assertEqual(ghidra_setup.lines(plain), ["F 82000000 20", "F 82000048 8", "F 82000060 4"])

    def test_the_closure_follows_a_kit_entrys_tail_branch(self):
        funcs = {f["addr"]: f for f in self.cat["functions"]}
        self.assertEqual(ghidra_setup.closure(funcs, ["82000028"], 4), {"82000028", "82000000"})


class Setup(unittest.TestCase):
    def test_x360setup_feeds_before_it_checks_the_roots(self):
        import x360setup
        with tempfile.TemporaryDirectory() as tmp:
            src = make_ws(pathlib.Path(tmp))
            ws = pathlib.Path(tmp) / "ws"
            roots = pathlib.Path(tmp) / "roots.txt"
            roots.write_text("82000010\n82000038\n")

            def build(_src, out):
                out = pathlib.Path(out)
                for n in ("image.bin", "image.json", "catalog_inputs.json"):
                    (out / n).write_bytes((src / n).read_bytes())
                return json.loads((out / "catalog_inputs.json").read_text())

            with mock.patch.object(x360setup.catalog_inputs, "build", build), \
                    mock.patch.object(x360setup, "write_oracle", lambda ws: []):
                x360setup.setup("default.xex", ws, name="th_catfeed", roots_file=roots,
                                feed=src / "port_functions.json")
            text = (ws / "ghidra_setup.txt").read_text()
            self.assertIn("F 82000010 10", text)
            self.assertIn("F 82000038 8", text)
            self.assertNotIn("F 82000000 ", text)
            self.assertEqual(json.loads((ws / "setup_roots.json").read_text())["functions"], ["82000010", "82000038"])
            self.assertTrue((ws / "catfeed.json").exists())


@unittest.skipUnless(os.environ.get("DP_X360_CATFEED_WS"), "DP_X360_CATFEED_WS names no fed workspace")
class Workspace(unittest.TestCase):
    def test_the_workspace_catalogue_has_every_port_function(self):
        miss = catfeed.missing(os.environ["DP_X360_CATFEED_WS"])
        self.assertEqual(miss, {}, f"{len(miss)} port functions missing, first {list(miss.items())[:10]}")


if __name__ == "__main__":
    unittest.main()
