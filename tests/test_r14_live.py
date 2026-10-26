"""Callees changing r8..r14 and the store queues (TcLive, TcSq) and their selective re-stamp
(tools/selective_restamp.py). One pass and one fail control each:
1. the candidate scan: a caller of a writer (own, or through a writer callee) is a candidate, a caller of a function
   that pushes and pops the register is not; a function with its own pref @r14, or of a copy of r14 made in the
   same block, is one, pref @r4 alone is not; a harness a rescue pass made is one, another is not;
2. the identity check: a harness differing only in its stamp keys is identical, one vector byte is not; one
   differing only in callee records taken from callees re-prepared since (callee drift) is clean, but a vector,
   expected or measured meta difference still refuses, and the refresh leaves a drifted caller stale;
3. the wiring: the new scripts are locked, every script stays at 400 lines or fewer, TcPrepare writes header lines K
   and S before the vectors, both runners parse them and fold the store-queue window only after line S.
4. baselines: an --old-scripts folder is a scripts folder or a baseline (ghidra_scripts/ with that time's catalog and
   code ranges beside it), and --list reads the first word of each line.
The oracle side itself needs Ghidra: the regress gate judges it (Toy Commander's tc-r14-* fixtures).
Synthetic data. Run: python -W error tests/test_r14_live.py.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import selective_restamp as sr  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"


def packet(root, addr, asm, callees=()):
    d = root / addr
    d.mkdir(parents=True)
    (d / "asm_1.txt").write_text("".join(f"  {addr}  {ln}\n" for ln in asm), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"addr": addr, "callees": [{"addr": c} for c in callees]}),
                                 encoding="utf-8")


class Candidates(unittest.TestCase):
    def test_scan(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t)
            packet(p, "8c000100", ["add #0x20,r14", "rts", "_nop"])                       # writer
            packet(p, "8c000200", ["mov.l r14,@-r15", "add #0x20,r14", "mov.l @r15+,r14", "rts"])  # saves it
            packet(p, "8c000300", ["jsr @r1", "_nop"], ["8c000100"])                        # calls the writer
            packet(p, "8c000400", ["jsr @r1", "_nop"], ["8c000200"])                        # calls the saver
            packet(p, "8c000500", ["jsr @r1", "_nop"], ["8c000300"])                        # writer through 300
            packet(p, "8c000600", ["pref @r14", "add #0x20,r14"])                          # own pref @r14
            packet(p, "8c000700", ["pref @r4", "rts"])                                     # pref of r4 only
            self.assertEqual(sr.candidates(p), {"8c000300", "8c000500", "8c000600"})

    def test_queue_through_a_copy(self):  # Toy Commander 8c075898: r3 = r14 + 0x100 going down, `pref @r3`
        def has(*asm):
            return sr.queue_pref([f"  8c00{i:04x}  {ln}\n" for i, ln in enumerate(asm)])
        tc = ["add r1,r14", "mov r14,r3", "fmov.s fr6,@-r3", "fmov.s fr0,@-r3", "pref @r3", "add #-0x8,r3",
              "fmov.s fr10,@-r3", "pref @r3", "rts", "_nop"]
        self.assertTrue(has(*tc))
        self.assertTrue(has("mov r14,r3", "mov r3,r5", "pref @r5"))                  # a copy of the copy
        self.assertFalse(has("pref @r4", "rts"))                                     # no state register at all
        self.assertFalse(has("mov r14,r3", "mov.l @r5,r3", "pref @r3"))              # written since: not a copy
        self.assertFalse(has("mov r14,r3", "jsr @r1", "_nop", "pref @r3"))           # a call between
        self.assertFalse(has("jsr @r1", "_mov r14,r3", "pref @r3"))                  # copied in its delay slot
        self.assertFalse(has("mov r14,r3", "bra 0x8c000002", "_nop", "pref @r3"))     # its target starts a block
        self.assertTrue(has("mov r14,r3", "bra 0x8c000009", "_nop", "pref @r3"))      # a target elsewhere
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t)
            packet(p, "8c000800", tc)
            packet(p, "8c000900", ["mov r4,r3", "pref @r3", "rts"])                      # a copy of r4: not one
            self.assertEqual(sr.candidates(p), {"8c000800"})

    def test_rescued(self):
        with tempfile.TemporaryDirectory() as t:
            h = pathlib.Path(t)
            for a, summary in (("8c000100", {"dev_valid": 48, "rescued": 1}), ("8c000200", {"dev_valid": 48})):
                (h / a).mkdir()
                (h / a / "oracle_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            self.assertEqual(sr.rescued(h), {"8c000100"})


class Identity(unittest.TestCase):
    def test_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = pathlib.Path(t) / "a", pathlib.Path(t) / "b"
            for d, stamp in ((a, "1111"), (b, "2222")):
                d.mkdir()
                (d / "vectors.txt").write_bytes(b"V 0 dev\nE\n")
                (d / "expected.txt").write_bytes(b"V 0 dev\nE\n")
                meta = {"addr": "8c000100", "oracle_stamp": stamp}
                if d == b:
                    meta["oracle_refresh"] = "selective refresh, sample 1, 1 identical"
                (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            self.assertEqual(sr.harness_bytes(a), sr.harness_bytes(b))
            (b / "vectors.txt").write_bytes(b"V 0 dev\nS 14\nE\n")
            self.assertNotEqual(sr.harness_bytes(a), sr.harness_bytes(b))

    def harnesses(self, t):
        """(harness folder, before copy, after prepare) of a caller 8c000100 whose callee 8c000200 was re-prepared
        with another signature since: the prepare took the callee's new signature (callee_drift.adopt)."""
        h, before = pathlib.Path(t) / "harness", pathlib.Path(t) / "before" / "8c000100"
        for d, meta in ((h / "8c000200", {"addr": "8c000200", "signature": "void fn_8c000200(u32 param_r4)"}),
                        (before, {"addr": "8c000100", "oracle_stamp": "1111", "callees": [
                            {"addr": "8c000200", "regs": ["r4"], "signature": "void fn_8c000200(void)"}]}),
                        (h / "8c000100", {"addr": "8c000100", "oracle_stamp": "2222", "callees": [
                            {"addr": "8c000200", "regs": ["r4"], "signature": "void fn_8c000200(u32 param_r4)",
                             "ghidra_signature": "void fn_8c000200(void)"}]})):
            d.mkdir(parents=True)
            (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            for n in ("vectors.txt", "expected.txt"):
                (d / n).write_bytes(b"V 0 dev\nE\n")
        return h, before, h / "8c000100"

    def test_callee_drift_only_is_clean(self):  # Spawn main: 4 of 100 differed only in meta.json's callee records
        with tempfile.TemporaryDirectory() as t:
            h, before, after = self.harnesses(t)
            sig_of = sr.callee_drift.Signatures(h)
            self.assertNotEqual(sr.harness_bytes(before), sr.harness_bytes(after))
            self.assertEqual(sr.compare(before, after, sig_of), "drift")
            self.assertEqual(sr.compare(after, after, sig_of), "same")
            rec = {"sample": ["8c000100", "8c000300"], "identical": 1, "drift_only": ["8c000100"], "differ": {}}
            self.assertTrue(sr.sample_clean(rec))
            self.assertFalse(sr.sample_clean(dict(rec, drift_only=[])))  # one not done
            self.assertFalse(sr.sample_clean({"sample": []}))

    def test_callee_back_in_line_with_ghidra(self):  # Spawn main 8c01f1a0: callee 8c056700 re-matched to Ghidra's view
        with tempfile.TemporaryDirectory() as t:
            h, before, after = self.harnesses(t)
            sig = "void fn_8c000200(s32 param_1, f32 param_fr10)"
            (h / "8c000200" / "meta.json").write_text(json.dumps({"addr": "8c000200", "signature": sig}),
                                                      encoding="utf-8")
            (before / "meta.json").write_text(json.dumps({"addr": "8c000100", "callees": [  # adopted earlier
                {"addr": "8c000200", "signature": "void fn_8c000200(s32 param_1)", "ghidra_signature": sig}]}),
                encoding="utf-8")
            (after / "meta.json").write_text(json.dumps({"addr": "8c000100", "callees": [  # adopt changed nothing
                {"addr": "8c000200", "signature": sig}]}), encoding="utf-8")
            self.assertEqual(sr.compare(before, after, sr.callee_drift.Signatures(h)), "drift")

    def test_script_difference_still_refuses(self):
        with tempfile.TemporaryDirectory() as t:
            h, before, after = self.harnesses(t)
            sig_of = sr.callee_drift.Signatures(h)
            (after / "expected.txt").write_bytes(b"V 0 dev\nR r0 1\nE\n")
            self.assertEqual(sr.compare(before, after, sig_of), ["expected.txt"])
            (after / "expected.txt").write_bytes(b"V 0 dev\nE\n")
            (after / "vectors.txt").write_bytes(b"V 0 dev\nK r8 4\nE\n")
            self.assertEqual(sr.compare(before, after, sig_of), ["vectors.txt"])
            (after / "vectors.txt").write_bytes(b"V 0 dev\nE\n")
            meta = json.loads((after / "meta.json").read_text(encoding="utf-8"))
            meta["callees"][0]["regs"] = ["r4", "r5"]  # a measured difference, not drift
            (after / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            self.assertEqual(sr.compare(before, after, sig_of), ["meta.json"])
            rec = {"sample": ["8c000100"], "identical": 0, "drift_only": [], "differ": {"8c000100": ["meta.json"]}}
            self.assertFalse(sr.sample_clean(rec))

    def test_refresh_leaves_drifted_callers_stale(self):
        with tempfile.TemporaryDirectory() as t:
            h, before, _ = self.harnesses(t)
            (h / "8c000300").mkdir()
            (h / "8c000300" / "meta.json").write_text(json.dumps({"addr": "8c000300", "callees": [  # still old
                {"addr": "8c000200", "signature": "void fn_8c000200(void)"}]}), encoding="utf-8")
            fresh, stale = sr.refresh_split(["8c000100", "8c000300"], h)
            self.assertEqual(fresh, ["8c000100"])
            self.assertEqual(list(stale), ["8c000300"])


class Wiring(unittest.TestCase):
    def test_scripts(self):
        locked = json.loads((SH4 / "target.json").read_text(encoding="utf-8"))["locked_scripts"]
        self.assertIn("TcLive.java", locked)
        self.assertIn("TcSq.java", locked)
        for p in (SH4 / "ghidra_scripts").glob("*.java"):
            self.assertLessEqual(len(p.read_text(encoding="utf-8").splitlines()), 400, p.name)
        prep = (SH4 / "ghidra_scripts" / "TcPrepare.java").read_text(encoding="utf-8")
        self.assertIn('(live != null ? live.header() : "")\n            + (sq != null ? sq.header() : "");', prep)
        self.assertIn("(head + TcStrict.header(facts) + vout)", prep)  # the headers, then the strict ones, then V

    def test_runners(self):
        for r in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            src = r.read_text(encoding="utf-8")
            self.assertIn("line[0] == 'K'", src, r.name)
            self.assertIn("line[0] == 'S'", src, r.name)
            self.assertIn("tcrt_set_sq_window();", src, r.name)
        fold = (SH4 / "runtime" / "tc_rt_internal.h").read_text(encoding="utf-8")
        self.assertIn("if (tcrt_sq_window && a - 0xe0000000u < 0x04000000u)", fold)


class Baselines(unittest.TestCase):
    def test_baseline(self):  # TC: the 10-16 scripts with that day's catalog and code ranges; a plain scripts folder
        with tempfile.TemporaryDirectory() as t:
            b = pathlib.Path(t) / "b16"
            (b / "ghidra_scripts").mkdir(parents=True)
            (b / "ghidra_scripts" / "TcPrepare.java").write_text("class A {}", encoding="utf-8")
            (b / "catalog.json").write_text("{}", encoding="utf-8")
            scripts, cat, rng = sr.baseline(b)
            self.assertEqual((sorted(scripts), cat, rng), (["TcPrepare.java"], b / "catalog.json", None))
            scripts, cat, rng = sr.baseline(b / "ghidra_scripts")
            self.assertEqual((sorted(scripts), cat, rng), (["TcPrepare.java"], None, None))

    def test_listed(self):
        with tempfile.TemporaryDirectory() as t:
            f = pathlib.Path(t) / "restamp.txt"
            f.write_text("8c078f94  # xemu ftrc\n# 8c000100 a comment\n8C01C178\nnot an address\n", encoding="utf-8")
            self.assertEqual(sr.listed(f), {"8c078f94", "8c01c178"})


if __name__ == "__main__":
    unittest.main()
