"""tools/gc_truth.py: the decomp config readers, boundary classes, translator and naming scores, the disc check and
the clean-room refusals. Synthetic decomp configs and workspaces in a temp folder (no game data, no decomp data).
Run: python -W error tests/test_gc_truth.py
"""
import contextlib
import hashlib
import io
import json
import pathlib
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
import gc_truth  # noqa: E402
import gc_truth_decomp  # noqa: E402
import gc_truth_dp  # noqa: E402

CONFIG = """\
object: sys/main.dol
hash: ABCDEF0123456789ABCDEF0123456789ABCDEF01
symbols: config/V1/symbols.txt # where
splits: config/V1/splits.txt

modules:
- object: files/a.rel
  hash: 1111111111111111111111111111111111111111
  symbols: config/V1/rels/a/symbols.txt
# map: a comment at column 0 between modules
- object: files/b.rel
  hash: 2222222222222222222222222222222222222222
  symbols: config/V1/rels/b/symbols.txt
extract:
- object: files/x.bin
"""

# truth: f0 100..140, f1 140..180, f2 180..1c0, f3 1c0..200, f4 200..240, f5 240..280, f6 280..2c0
SYMBOLS = "".join(f"TruthName{i} = .text:0x{0x80000100 + 0x40 * i:08X}; // type:function size:0x40 scope:global\n"
                  for i in range(7)) + "lbl_data = .data:0x80100000; // type:object size:0x10\n"
SPLITS = """\
Sections:
\t.text       type:code align:4

game/a.c:
\t.text       start:0x80000100 end:0x80000180

game/b.cpp:
\t.text       start:0x80000180 end:0x80000280
"""


def run(argv):
    """gc_truth.main(argv) with its report printout kept off the test output."""
    with contextlib.redirect_stdout(io.StringIO()):
        return gc_truth.main(argv)


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.decomp = self.tmp / "decomp"
        write(self.decomp / "config" / "V1" / "config.yml", CONFIG)
        write(self.decomp / "config" / "V1" / "symbols.txt", SYMBOLS)
        write(self.decomp / "config" / "V1" / "splits.txt", SPLITS)
        write(self.decomp / "config" / "V1" / "rels" / "a" / "symbols.txt",
              "r = .text:0x00000000; // type:function size:0x8\ns = .text:0x00000008; // type:function size:0x8\n")
        write(self.decomp / "config" / "V1" / "rels" / "b" / "symbols.txt",
              "t = .text:0x00000000; // type:function size:0x8\n")
        self.ws = self.tmp / "ws"

    def tearDown(self):
        self._tmp.cleanup()


class Config(Base):
    def test_config_and_modules(self):
        v = gc_truth.Version(self.decomp, "V1")
        self.assertEqual(v.dol_sha1(), "abcdef0123456789abcdef0123456789abcdef01")
        self.assertEqual([m["object"] for m in v.modules], ["files/a.rel", "files/b.rel"])  # not extract:'s
        self.assertEqual(v.module_functions(), [("files/a.rel", 2), ("files/b.rel", 1)])
        self.assertEqual(len(v.functions), 7)  # the object is not a function

    def test_languages(self):
        self.assertEqual(gc_truth.Version(self.decomp, "V1").languages(), {"c": 2, "cpp": 4, "unsplit": 1})


class Classify(unittest.TestCase):
    TRUTH = [(0x100 + 0x40 * i, 0x40) for i in range(7)]

    def test_kinds(self):
        bodies = {
            0x100: [(0x100, 0x140)],                  # f0 exact
            0x140: [(0x140, 0x1c0)],                  # f1 merged with f2 (one body covers both entries)
            0x1c0: [(0x1c0, 0x1e0)], 0x1e0: [(0x1e0, 0x200)],  # f3 split in two
            0x200: [(0x200, 0x23c)],                  # f4 other end
            # f5 missed
            0x290: [(0x290, 0x2c0)],                  # f6 other start
            0x400: [(0x400, 0x404)],                  # outside every truth function
        }
        kinds, extra = gc_truth.classify(self.TRUTH, bodies)
        self.assertEqual(kinds, {0x100: "exact", 0x140: "merged", 0x180: "merged", 0x1c0: "split",
                                 0x200: "boundary_differs", 0x240: "missed", 0x280: "boundary_differs"})
        self.assertEqual(extra, 1)

    def test_body_ranges_not_extent(self):
        # f0's second range lies inside f1 but holds no truth entry: an end difference, not a merge
        bodies = {0x100: [(0x100, 0x120), (0x160, 0x180)], 0x140: [(0x140, 0x160)]}
        kinds, _ = gc_truth.classify([(0x100, 0x40), (0x140, 0x40)], bodies)
        self.assertEqual(kinds, {0x100: "boundary_differs", 0x140: "boundary_differs"})


class Names(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(gc_truth.truth_tokens("update__Q24Game4PikiFv"), ["piki", "update"])
        self.assertEqual(gc_truth.truth_tokens("__ct__10JKRArchiveFv"), ["jkr", "archive", "ctor"])
        self.assertEqual(gc_truth.truth_tokens("Player_Update"), ["player", "update"])
        self.assertEqual(gc_truth.words("lbGetHPRatio"), ["lb", "get", "hp", "ratio"])

    def test_scores(self):
        truth = {1: "update__Q24Game4PikiFv", 2: "Player_Update", 3: "DrawShadow"}
        dp = {1: "Piki_update", 2: "Player_Step", 3: "fn_80000300", 4: "Unrelated"}
        s = gc_truth.name_scores(truth, dp)
        self.assertEqual((s["named"], s["exact"], s["any_word"]), (3, 1, 2))
        self.assertAlmostEqual(s["mean_overlap"], round((1 + 1 / 3 + 0) / 3, 4))


class Score(Base):
    def workspace(self, attempts):
        bodies = "".join(f"{a:08x} {a:08x} {a + 0x40:08x}\n" for a in range(0x80000100, 0x80000280, 0x40))
        write(self.ws / "state" / "function_bodies.txt", bodies)
        write(self.ws / "state" / "ledger.json", json.dumps({"functions": attempts}))
        write(self.ws / "readable" / "names" / "a.json",
              json.dumps({"functions": {"80000100": {"proto": "void TruthName0(void)"}}}))

    def test_score_report(self):
        self.workspace({
            "80000100": {"attempts": [{"model": "translated", "verdict": "PASS"}]},
            "80000140": {"attempts": [{"model": "translated", "verdict": "FAIL"}, {"model": "haiku",
                                                                                  "verdict": "PASS"}]},
            "80000180": {"translator": {"ok": False, "why": "no translation"}, "attempts": []},
            "80000400": {"attempts": [{"model": "translated", "verdict": "PASS"}]},
            "800001c0": {"attempts": [{"model": "haiku", "verdict": "PASS"}]},
        })
        out = self.tmp / "reports"
        rc = run(["score", "--decomp", str(self.decomp), "--version", "V1", "--project", str(self.ws),
                            "--out", str(out)])
        self.assertEqual(rc, 0)
        r = json.loads((out / "score.json").read_text(encoding="utf-8"))
        self.assertEqual(r["catalog"]["exact"], 6)
        self.assertEqual(r["catalog"]["missed"], 1)
        self.assertEqual(r["addresses"]["missed"], ["80000280"])
        t = r["translator"]
        self.assertEqual((t["judged"], t["pass"], t["fail"], t["no_c"], t["outside_truth"]), (2, 1, 1, 1, 1))
        self.assertEqual(r["naming"]["exact"], 1)
        self.assertEqual(r["rel_functions_not_scored"], 3)
        text = (out / "score.json").read_text(encoding="utf-8") + (out / "score.md").read_text(encoding="utf-8")
        self.assertNotIn("TruthName", text)  # no name reaches a report

    def test_verdicts_file(self):
        self.workspace({})
        vf = self.tmp / "v.txt"
        vf.write_text("80000100 PASS\n80000140 FAIL # why\n", encoding="utf-8")
        out = self.tmp / "r2"
        run(["score", "--decomp", str(self.decomp), "--version", "V1", "--project", str(self.ws),
                       "--out", str(out), "--verdicts", str(vf)])
        t = json.loads((out / "score.json").read_text(encoding="utf-8"))["translator"]
        self.assertEqual((t["pass"], t["fail"], t["pass_rate"]), (1, 1, 0.5))

    def test_refuses_report_inside_workspace_or_repo(self):
        self.workspace({})
        for out in (self.ws / "reports", TOOL / "tests" / ".work" / "gc_truth_refused"):
            with self.assertRaises(SystemExit) as cm:
                run(["score", "--decomp", str(self.decomp), "--version", "V1", "--project", str(self.ws),
                               "--out", str(out)])
            self.assertIn("refused", str(cm.exception))
            self.assertFalse(out.exists())


class Rel(Base):
    """REL truth functions placed through a gcrel link.json and scored with main.dol's."""
    LINK = {"modules": [
        {"module_id": 5, "name": "files/a.rel", "sections": [
            {"index": 1, "exec": True, "bss": False, "size": 0x10, "addr": "80400000"},
            {"index": 4, "exec": False, "bss": False, "size": 0x20, "addr": "80400020"}]}]}

    def workspace(self, link_beside=True):
        bodies = "".join(f"{a:08x} {a:08x} {a + 0x40:08x}\n" for a in range(0x80000100, 0x800002c0, 0x40))
        bodies += "80400000 80400000 80400008\n80400008 80400008 8040000c\n"  # r exact, s other end
        write(self.ws / "state" / "function_bodies.txt", bodies)
        write(self.ws / "state" / "ledger.json", json.dumps({"functions": {
            "80400000": {"attempts": [{"model": "translated", "verdict": "PASS"}]}}}))
        write(self.ws / "project.json", json.dumps({"binary": "../disc/linked.dol"}))
        if link_beside:
            write(self.tmp / "disc" / "link.json", json.dumps(self.LINK))

    def score(self, *extra):
        out = self.tmp / "rel_reports"
        run(["score", "--decomp", str(self.decomp), "--version", "V1", "--project", str(self.ws),
             "--out", str(out)] + list(extra))
        return json.loads((out / "score.json").read_text(encoding="utf-8"))

    def test_linked_module_scored(self):
        self.workspace()
        r = self.score()
        rel = r["rel"]
        self.assertEqual((rel["modules"], rel["scored_modules"], rel["truth_functions"]), (2, 1, 2))
        self.assertEqual((rel["catalog"]["exact"], rel["catalog"]["boundary_differs"]), (1, 1))
        self.assertEqual(rel["by_module"]["files/a.rel"]["exact"], 1)
        self.assertEqual(rel["not_scored"], {"files/b.rel": {"functions": 1, "why": "not linked"}})
        self.assertEqual(r["rel_functions_not_scored"], 1)
        self.assertEqual((r["catalog"]["exact"], r["catalog"]["extra_dp"]), (7, 0))  # main.dol apart
        self.assertEqual((r["translator"]["pass"], r["translator"]["outside_truth"]), (1, 0))
        self.assertIn("80400008", r["addresses"]["boundary_differs"])

    def test_link_given_and_sections_mismatch(self):
        self.workspace(link_beside=False)
        self.assertEqual(self.score()["rel"]["scored_modules"], 0)  # no link.json: counted only
        two = json.loads(json.dumps(self.LINK))
        two["modules"][0]["sections"][1]["exec"] = True  # two code sections, the decomp lists one
        write(self.tmp / "two.json", json.dumps(two))
        rel = self.score("--link", str(self.tmp / "two.json"))["rel"]
        self.assertEqual(rel["not_scored"]["files/a.rel"]["why"], "no splits.txt section list, 2 code sections in the module")

    def test_module_match_and_sections(self):
        rec = {"modules": [{"module_id": 1, "name": "files/RELS/Stage.rel"}, {"module_id": 2, "name": "files/x.rel"}]}
        self.assertEqual(gc_truth_dp.match_module("orig/GXXE01/files/rels/stage.rel", rec)["module_id"], 1)
        self.assertEqual(gc_truth_dp.match_module("files/other/x.rel", rec)["module_id"], 2)
        self.assertIsNone(gc_truth_dp.match_module("files/y.rel", rec))
        sp = self.tmp / "splits.txt"
        sp.write_text("Sections:\n\t.init type:code align:4\n\t.data type:data\n\t.text type:code\n\nu.cpp:\n",
                      encoding="utf-8")
        self.assertEqual(gc_truth_decomp.code_section_names(sp), [".init", ".text"])


class Disc(Base):
    def dol(self):
        head = bytearray(0x100)
        struct.pack_into(">I", head, 0x00, 0x100)       # text0 offset
        struct.pack_into(">I", head, 0x90, 0x20)        # text0 size
        struct.pack_into(">I", head, 0x1c, 0x200)       # data0 offset
        struct.pack_into(">I", head, 0xac, 0x10)        # data0 size
        return bytes(head) + b"\x11" * 0x100 + b"\x22" * 0x10

    def test_dol_from_image_and_file(self):
        dol = self.dol()
        iso = bytearray(0x2000)
        iso[0x1c:0x20] = b"\xc2\x33\x9f\x3d"
        struct.pack_into(">I", iso, 0x420, 0x1000)
        iso[0x1000:0x1000 + len(dol)] = dol
        (self.tmp / "game.iso").write_bytes(bytes(iso))
        (self.tmp / "main.dol").write_bytes(dol)
        self.assertEqual(gc_truth.dol_bytes(self.tmp / "game.iso"), dol)
        self.assertEqual(gc_truth.dol_bytes(self.tmp / "main.dol"), dol)

    def test_check_disc_matches_version(self):
        dol = self.dol()
        (self.tmp / "main.dol").write_bytes(dol)
        cfg = self.decomp / "config" / "V1" / "config.yml"
        cfg.write_text(CONFIG.replace("ABCDEF0123456789ABCDEF0123456789ABCDEF01", hashlib.sha1(dol).hexdigest()),
                       encoding="utf-8")
        self.assertEqual(run(["check-disc", "--decomp", str(self.decomp), str(self.tmp / "main.dol")]), 0)
        (self.tmp / "other.dol").write_bytes(dol + b"\0")
        self.assertEqual(run(["check-disc", "--decomp", str(self.decomp), str(self.tmp / "other.dol")]), 1)


if __name__ == "__main__":
    unittest.main()
