"""The stricter judge (pre-wave item 2; target.json "mock_junk", "mock_writes", "call_snapshots", "exit_frame").

1. Facts: with every flag off (absent or false, an unused "mock_write_facts" list, report modes) the facts file is
   byte-identical, so the oracle's stamp and every harness are; each flag adds its one line; the write facts are
   checked, normalized and sorted; every target.json has the four flags, true only on ON_TARGETS (the user's).
2. Stream: TcJunk.at and at8 (stub, tests/java/TcStrictStub.java) against a Python splitmix64 model on random keys;
   TcStrict.header and meta (vectors.txt J and U, meta.json "mock_writes") on DC and GameCube facts.
3. Runtimes and Readable: tests/test_strict_runtime.py (the runners' side, compiled).
4. Judge: compare.py and ppc_compare.py on CS and XR (masked bytes, uncompared fields, the mirror rule, NaNs), report
   mode (into "strict_report", never failing), the caps.
5. Wiring: one TcJunk, TcStrict and tc_strict.h each, locked; the hooks once each; the line caps.
The oracle's own runs need Ghidra (its proof: tools/oracle_probe.py, flags off byte-identical, then on). Synthetic
data. Run: python -W error tests/test_strict_judge.py.
"""
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "port" / "tools"), str(ROOT / "targets" / "gamecube_ppc")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_fsca_rm import CP, jdk  # noqa: E402

from core import compare, ghidra, paths, strict_judge  # noqa: E402
import ppc_compare  # noqa: E402

T = ROOT / "targets"
TARGETS, ON_TARGETS = ("dreamcast_sh4", "saturn_sh2", "gamecube_ppc"), {"dreamcast_sh4", "saturn_sh2"}
FLAGS = list(strict_judge.FLAGS)
M = (1 << 64) - 1
WORK = ROOT / "tests" / ".work"


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def mix(z):
    z = (z + 0x9E3779B97F4A7C15) & M
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M
    return z ^ (z >> 31)


def at(seed, vid, call, slot):
    return mix(mix(mix(mix(seed & M) ^ (vid & M)) ^ (call & M)) ^ (slot & M))


def at8(seed, vid, call, slot, k):
    return (at(seed, vid, call, slot + (k >> 3)) >> (8 * (k & 7))) & 0xFF


def stream(field, n, seed=0x1234, vid=7, call=0):
    """The bytes a mock write puts through call record field `field` (tc_strict.h, TcStrict)."""
    return bytes(at8(seed, vid, call, 0x10000 * (1 + field), k) for k in range(n))


def keys(n=40):
    rnd = random.Random(2)
    out = [(1, 2, 3, 4, 5), (-1, 0, 0, 0, 0), ((1 << 63) - 1, 7, 4095, 65552, 255), (-(1 << 63), -1, 0, 17, 7)]
    for _ in range(n):
        out.append((rnd.getrandbits(64) - (1 << 63), rnd.getrandbits(32), rnd.randrange(64),
                    rnd.choice([rnd.randrange(64), 0x10000 * rnd.randrange(1, 26)]), rnd.randrange(256)))
    return out


WRITES = {"0x8C000010": {"s1": 4, "why": "mov.l r0,@(4,r15) store"}, "8c012345": {"r4": 16, "r5": 2, "why": "x"}}


class Facts(unittest.TestCase):
    def cfg(self, **kw):
        cfg = {k: v for k, v in paths.config().items() if k not in FLAGS + ["mock_write_facts", "strict_judge"]}
        return dict(cfg, **kw)

    def test_off_is_byte_identical(self):
        base = ghidra.facts_body(self.cfg())
        for kw in ({f: False for f in FLAGS}, {"mock_write_facts": WRITES}, {"mock_writes": 0},
                   {"strict_judge": {"call_snapshots": "report", "exit_frame": "report"}},
                   dict({f: False for f in FLAGS}, mock_write_facts=WRITES, mock_junk=None)):
            self.assertEqual(ghidra.facts_body(self.cfg(**kw)), base, kw)
        self.assertNotIn("mock_", base)

    def test_each_flag(self):
        base = ghidra.facts_body(self.cfg()).splitlines()
        for f in FLAGS:
            on = ghidra.facts_body(self.cfg(**{f: True})).splitlines()
            self.assertEqual(sorted(on), sorted(base + [f"{f} 1"]), f)
            self.assertEqual(ghidra.facts_body(self.cfg(**{f: 1})).splitlines(), on, f)
        on = ghidra.facts_body(self.cfg(mock_writes=True, mock_write_facts=WRITES)).splitlines()
        self.assertEqual([x for x in on if x.startswith("mock_write ")],
                         ["mock_write 8c000010 s1 4", "mock_write 8c012345 r4 16", "mock_write 8c012345 r5 2"])

    def test_bad_facts(self):
        cfg = self.cfg(mock_writes=True)
        for bad in (["8c000010"], {"zz": {"r4": 4, "why": "x"}}, {"8c000010": {"r4": 4}},
                    {"8c000010": {"r4": 4, "why": " "}}, {"8c000010": {"r0": 4, "why": "x"}},
                    {"8c000010": {"r4": 0, "why": "x"}}, {"8c000010": {"r4": 257, "why": "x"}},
                    {"8c000010": {"r4": True, "why": "x"}}, {"8c000010": {"r4": "4", "why": "x"}},
                    {"123456789": {"r4": 4, "why": "x"}}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                strict_judge.facts_lines(dict(cfg, mock_write_facts=bad))
        self.assertEqual(strict_judge.modes({}), {"call_snapshots": "enforce", "exit_frame": "enforce"})
        self.assertEqual(strict_judge.modes({"strict_judge": {"exit_frame": "report"}})["exit_frame"], "report")
        for bad in ({"mock_junk": "report"}, {"exit_frame": "off"}, "report"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                strict_judge.modes({"strict_judge": bad})

    def test_targets_flags(self):
        for t in TARGETS:
            cfg, on = json.loads(text(T / t / "target.json")), t in ON_TARGETS
            self.assertEqual([cfg[f] for f in FLAGS], [on] * 4, t)
            self.assertEqual(strict_judge.facts_lines(cfg)[:4], [f"{f} 1" for f in FLAGS] if on else [], t)


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Stream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="strict_", dir=WORK)
        cls.out = {}
        for t in ("dreamcast_sh4", "gamecube_ppc"):
            out = pathlib.Path(cls.tmp.name, t)
            sh = [paths.SHARED_SCRIPTS / d for d in json.loads(text(T / t / "target.json")).get("shared_scripts") or []]
            src = os.pathsep.join(str(p) for p in [T / t / "ghidra_scripts", T / t / "template"] + sh)
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(out), "-cp", CP, "-sourcepath", src,
                                str(ROOT / "tests" / "java" / "TcStrictStub.java")],
                               capture_output=True, text=True, timeout=300)
            if r.returncode:
                raise AssertionError(r.stdout + r.stderr)
            cls.out[t] = str(out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def stub(self, t, ks, *facts):
        d = pathlib.Path(self.tmp.name)
        (d / "keys.txt").write_text("".join(" ".join(map(str, k)) + "\n" for k in ks))
        names = []
        for i, lines in enumerate(facts):
            (d / f"f{i}.txt").write_text("".join(x + "\n" for x in lines))
            names.append(str(d / f"f{i}.txt"))
        r = subprocess.run([jdk("java"), "-cp", self.out[t] + os.pathsep + CP, "TcStrictStub", str(d / "keys.txt"),
                            *names], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return r.stdout.split("\n")

    def test_stream(self):
        ks = keys()
        got = self.stub("dreamcast_sh4", ks)[:len(ks)]
        fr = [z & 0x807FFFFF | 0x3F800000 for z in (at(*k[:4]) for k in ks)]  # TcJunk.fr
        self.assertEqual(got, [f"{at(*k[:4]):x} {at8(*k)} {f:x}" for k, f in zip(ks, fr)])

    def test_header_and_meta(self):
        for t, args, f4, fs in (("dreamcast_sh4", "r4 r5 r6 r7", 1, 14),
                                ("gamecube_ppc", "r3 r4 r5 r6 r7 r8 r9 r10", 1, 18)):
            a = args.split()[0]
            cfg = {"abi": {"int_args": args.split()}, "mock_write_facts": {"0x8C012345": {a: 16, "why": "x"},
                                                                           "8c000010": {"s1": 4, "why": "y"}}}
            on = strict_judge.facts_lines(dict(cfg, **{f: True for f in FLAGS}))
            writes = strict_judge.facts_lines(dict(cfg, mock_writes=True, mock_write_facts={}))
            out = self.stub(t, [], [f"int_args {args}"], [f"int_args {args}"] + on, [f"int_args {args}"] + writes)
            self.assertEqual(out[:3], ["0", "", ""], t)
            self.assertEqual(out[3:5], ["15", f"J 15|U 8c000010 {fs} 4|U 8c012345 {f4} 16|"], t)
            meta = json.loads('{"x": 1' + out[5].replace("|", "\n") + "}")["mock_writes"]
            self.assertEqual(meta, [{"callee": "8c000010", "field": "s1", "size": 4},
                                    {"callee": "8c012345", "field": a, "size": 16}], t)
            self.assertEqual(out[6:9], ["2", "J 2|", ""], t)
            if t == "gamecube_ppc":
                self.assertEqual(out[9], "", t)  # no callee masks: GameCube's mocks keep zeroing, now junk, as before
                continue
            facts = [f"int_args {args}", "mock_junk 1"]
            out = self.stub(t, [], facts)
            # 8c020000 changes every scratch register: no line; 8c030000 keeps fr8 (r6 is no fr: the C cannot read it)
            self.assertEqual(out[3], f"J 1|U 8c030000 0 {0xFFE & ~0x100}|", t)
            scratch = 0xFFF & ~1 | 0xF0000 | 0xE00000  # fr1..fr11, r4..r7, r1..r3 (TcFlow masks)
            self.assertEqual(out[4], f"{scratch & ~(1 << 18) & ~(1 << 8):x} {scratch:x}", t)  # unlisted: the ABI's
            fields = dict(x.split("=") for x in out[5].split()[:-2])  # the CJ line's field: the judge's REG_INDEX
            self.assertEqual({r: int(n) for r, n in fields.items()}, {r: compare.REG_INDEX[r] for r in fields}, t)
            self.assertEqual(len(fields), 18, t)
            self.assertEqual(out[5].split()[-2:], ["-1", "-1"], t)


XR_SH = "XR 8cffe000 00000001 0c001234 00000003 00000004 00000005 00000006 00000007 3f800000 7fc00001 0 0"


def result_file(lines, mod=compare):
    with tempfile.TemporaryDirectory(dir=WORK) as tmp:
        f = pathlib.Path(tmp, "r.txt")
        f.write_text("\n".join(["V 0 dev"] + lines + ["E"]) + "\n")
        return mod.parse_results(str(f))[0]


class Judge(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)

    def sh(self, cs, xr=XR_SH, call="8c020000", strict=None, report=None, ecs=("CS 0 1 44332211xxxxxxxx",)):
        meta = {"ret": "u32", "callees": [{"addr": "8c020000", "regs": ["r4"]}], "globals": []}
        base = ["RET 00000005 00000000"]
        exp = result_file(base + ["CALL 8c020000 8c040000 8c040100", *ecs, "CS 0 2 00006655", XR_SH])
        act = result_file(base + [f"CALL {call} 8c040000 8c040100", *cs] + ([xr] if xr else []))
        return compare.compare_vector(meta, b"", {"patches": []}, exp, act, compare.FRAME_LO, strict, report)

    def test_snapshots(self):
        self.assertEqual(self.sh(["CS 0 1 4433221100000000", "CS 0 2 00006655"]), [])
        self.assertEqual(self.sh(["CS 0 1 44332211ffffffff", "CS 0 2 ffffffff"]), [])  # xx; r5 not compared
        bad = self.sh(["CS 0 1 4433221200000000"])
        self.assertEqual(len(bad), 1)
        self.assertIn("call #1 to 0x8c020000: the bytes argument r4 points to", bad[0])
        self.assertIn("expected 44332211xxxxxxxx, got 4433221200000000", bad[0])
        self.assertIn("got nothing (not readable there)", self.sh(["CS 0 1 -"])[0])
        self.assertIn("got nothing (not readable there)", self.sh([])[0])
        self.assertEqual(len(self.sh(["CS 0 1 443322"])), 1)  # a shorter run
        other = self.sh([], call="8c099999")  # another callee: the calls' comparison fails it, not the snapshot
        self.assertFalse([d for d in other if "the bytes argument" in d])
        many = strict_judge.compare({"callees": [{"addr": "8c020000", "regs": ["r4"]}]},
                                    {"calls": [[0x8C020000]] * 6, "cs": {(k, 1): "00" for k in range(6)}},
                                    {"calls": [[0x8C020000]] * 6}, compare.REG_INDEX, strict_judge.XR_SH,
                                    compare.same_word)
        self.assertEqual((len(many), many[-1]), (5, "... and 2 more call snapshots differ"))

    def test_exit_frame(self):
        ok = ["CS 0 1 4433221100000000"]
        self.assertEqual(self.sh(ok, XR_SH.replace("7fc00001", "7f800002")), [])  # NaN against NaN
        mirror = self.sh(ok, XR_SH.replace("0c001234", "8c001234"))  # r8: one RAM address through its two views
        self.assertEqual(len(mirror), 0 if compare.MIRROR_RULE else 1)
        rep = []
        bad = self.sh(ok, XR_SH.replace("8cffe000", "8cffdffc").replace("3f800000", "3f800001"), report=rep)
        self.assertEqual([d.split(":")[0] for d in bad], ["fr12 at exit"])  # r15: no C keeps one, reported only
        self.assertEqual(rep, ["exit_frame (report only): r15 at exit: the original's is 0x8cffe000, the C's "
                               "0x8cffdffc (the C keeps no stack pointer: a frame the original leaves to a callee or "
                               "pops for its caller)"])
        self.assertEqual(self.sh(ok, XR_SH.replace("8cffe000", "8cffdffc")), [])  # no report list: dropped
        self.assertEqual(self.sh(ok, None), ["the exit frame (the stack pointer and callee-saved registers): "
                                             "expected it, got nothing"])
        cut = self.sh(ok, XR_SH[:40])  # r15, r8..r10 and r11 cut to "0"
        self.assertEqual(len(cut), 8)  # r11, then r12..r14 and fr12..fr15 missing
        self.assertTrue(cut[0].startswith("r11 at exit: expected 0x00000004, got 0x00000000"))
        self.assertTrue(cut[1].startswith("r12 at exit: expected 0x00000005, got nothing"))

    def test_report_mode(self):
        rep = []
        mode = {"call_snapshots": "report", "exit_frame": "enforce"}
        d = self.sh(["CS 0 1 4433221200000000"], XR_SH.replace("00000003", "00000013"), strict=mode, report=rep)
        self.assertEqual([x.split(":")[0] for x in d], ["r10 at exit"])
        self.assertEqual(len(rep), 1)
        self.assertTrue(rep[0].startswith("call_snapshots (report only): call #1 to 0x8c020000"))
        rep2 = []
        both = {"call_snapshots": "report", "exit_frame": "report"}
        self.assertEqual(self.sh([], None, strict=both, report=rep2), [])
        self.assertEqual([x.split(" (")[0] for x in rep2], ["call_snapshots", "exit_frame"])
        res = {}
        strict_judge.note(res, "dev", 3, [])
        self.assertEqual(res, {})
        for i in range(7):
            strict_judge.note(res, "dev", i, rep2 * 4)
        self.assertEqual(res["strict_report"]["vectors"], 7)
        self.assertEqual(len(res["strict_report"]["first"]), 5)
        self.assertEqual(res["strict_report"]["first"][0], {"set": "dev", "vector": 0, "diffs": (rep2 * 4)[:6]})

    def test_snapshot_nan(self):
        """A stored word NaN on both sides matches, as in the memory comparison (Spawn 8c056460: the oracle's
        0x7fc00000, the C's 0xffffffff); any other word, or a word with an unstored byte, does not."""
        for want, got, n in (("0000c07fxxxxxxxx", "ffffffff00000000", 0), ("0000c07fxxxxxxxx", "0000803f00000000", 1),
                             ("0000c07f0000803f", "0100c07f0000803f", 0), ("xx00c07fxxxxxxxx", "ffffc07f00000000", 1)):
            self.assertEqual(len(self.sh([f"CS 0 1 {got}", "CS 0 2 00006655"], ecs=(f"CS 0 1 {want}",))), n, got)
        self.assertTrue(strict_judge._same_bytes("7fc00000", "ffffffff", ">I"))
        self.assertFalse(strict_judge._same_bytes("7fc00000", "ffffffff", "<I"))  # 0x0000c07f: no NaN

    def test_leftover_junk(self):
        """CJ: an argument still holding a callee's junk may be the junk or the value it had without it."""
        meta = {"ret": "u32", "callees": [{"addr": "8c020000", "regs": ["r6", "fr8"]}], "globals": []}
        exp = result_file(["RET 00000005 00000000", "CALL 8c020000 0 0 9eef57fc 0 0 0 0 0 43db7491",
                           "CJ 0 3 8c001234", "CJ 0 9 3f000000"])
        def run(r6, fr8):
            act = result_file(["RET 00000005 00000000", f"CALL 8c020000 0 0 {r6} 0 0 0 0 0 {fr8}"])
            return compare.compare_vector(meta, b"", {"patches": []}, exp, act)
        self.assertEqual(run("9eef57fc", "43db7491"), [])  # left as it was
        self.assertEqual(run("8c001234", "3f000000"), [])  # the value before the junk
        bad = run("00000001", "3f800000")
        self.assertEqual(len(bad), 2)
        self.assertEqual(bad[0], "call #1 to 0x8c020000: argument r6 expected 0x9eef57fc (junk a callee left there: "
                                 "the original never set it) or 0x8c001234 (its value before), got 0x00000001")
        plain = result_file(["RET 00000005 00000000", "CALL 8c020000 0 0 9eef57fc 0 0 0 0 0 43db7491"])
        act = result_file(["RET 00000005 00000000", "CALL 8c020000 0 0 8c001234 0 0 0 0 0 43db7491"])
        self.assertEqual(compare.compare_vector(meta, b"", {"patches": []}, plain, act),
                         ["call #1 to 0x8c020000: argument r6 expected 0x9eef57fc, got 0x8c001234"])  # no CJ: exact
        gmeta = {"ret": "u32", "callees": [{"addr": "80020000", "regs": ["r7", "f2"]}]}
        f2 = "4000000000000000"
        ret, call = "RET 00000005 0000000000000000 00000000", f"CALL 80020000 0 0 0 0 1234abcd 0 0 0 0 {f2}"
        gexp = result_file([ret, call, call, "CJ 1 5 00000000", "CJ 1 10 0000000000000000"], ppc_compare)
        for r7, f, n in (("1234abcd", f2, 0), ("00000000", "0000000000000000", 0), ("00000001", f2, 1)):
            act = result_file([ret, call, f"CALL 80020000 0 0 0 0 {r7} 0 0 0 0 {f}"], ppc_compare)
            self.assertEqual(len(ppc_compare.compare_vector(gmeta, [], {"patches": []}, gexp, act)), n, r7)

    def test_gamecube(self):
        meta = {"ret": "u32", "callees": [{"addr": "80020000", "regs": ["r3"]}]}
        xr = "XR 817fe000 " + " ".join(f"{i:08x}" for i in range(18)) + " " + " ".join(["7ff8000000000001"] * 18)
        exp = result_file(["RET 00000005 0000000000000000 00000000", "CALL 80020000 80040000", "CS 0 1 11223344xx",
                           xr], ppc_compare)
        for cs, x, n in (("CS 0 1 1122334400", xr, 0), ("CS 0 1 11223345ff", xr, 1),
                         ("CS 0 1 1122334400", xr.replace("7ff8000000000001", "7ff0000000000002"), 0),
                         ("CS 0 1 1122334400", xr.replace("00000001", "8c000001", 1), 1)):
            act = result_file(["RET 00000005 0000000000000000 00000000", "CALL 80020000 80040000", cs, x], ppc_compare)
            self.assertEqual(len(ppc_compare.compare_vector(meta, [], {"patches": []}, exp, act)), n, (cs, x[:30]))
        rep = []
        act = result_file(["RET 00000005 0000000000000000 00000000", "CALL 80020000 80040000", "CS 0 1 -", xr],
                          ppc_compare)
        self.assertEqual(ppc_compare.compare_vector(meta, [], {"patches": []}, exp, act,
                                                    {"call_snapshots": "report"}, rep), [])
        self.assertEqual(len(rep), 1)


class Wiring(unittest.TestCase):
    def test_xemu_skips(self):
        """tools/xemu_check: a harness with mock_junk or exit_frame is skipped (xemu keeps its own r1..r7, r15)."""
        sys.path.insert(0, str(ROOT / "tools"))
        import xemu_check
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            h = pathlib.Path(tmp)
            (h / "meta.json").write_text('{"blocks": [], "ret": "u32"}')
            for head, n in (("J 9\n", 9), ("J 6\n", 6), ("", 0), ("S 8c000000\nJ 1\n", 1)):
                (h / "vectors.txt").write_text(head + "V 0 dev\nJ 8\nE\n")  # a J after V is no header
                self.assertEqual(xemu_check.strict_flags(h), n, head)
            for head, skip in (("J 8\n", True), ("J 1\n", True), ("J 6\n", False)):
                (h / "vectors.txt").write_text(head + "V 0 dev\nE\n")
                if skip:
                    rec = xemu_check.check_one("8c010000", h, None, {}, b"", None, h, 1)
                    self.assertEqual((rec["vectors"], rec["disagree"]), (0, 0), head)
                    self.assertIn("strict judge", rec["skipped"], head)
                else:
                    self.assertFalse(xemu_check.strict_flags(h) & 9, head)

    def test_one_copy(self):
        for name, ts in (("ghidra_scripts/TcJunk.java", TARGETS), ("runtime/tc_strict.h", TARGETS),
                         ("ghidra_scripts/TcStrict.java", TARGETS[:2])):
            self.assertEqual({(T / t / name).read_bytes() for t in ts}, {(T / ts[0] / name).read_bytes()}, name)
        for t in TARGETS:
            locked = json.loads(text(T / t / "target.json"))["locked_scripts"]
            self.assertTrue({"TcJunk.java", "TcStrict.java"} <= set(locked), t)

    def test_lines(self):
        caps = {("dreamcast_sh4", "TcOracle"): 400, ("dreamcast_sh4", "TcVectors"): 400,
                ("saturn_sh2", "TcOracle"): 413, ("gamecube_ppc", "TcOracle"): 294}
        for (t, s), n in caps.items():
            self.assertEqual(len(text(T / t / "ghidra_scripts" / f"{s}.java").splitlines()), n, (t, s))
        for f in (T / "dreamcast_sh4" / "ghidra_scripts").glob("*.java"):
            self.assertLessEqual(len(text(f).splitlines()), 400, f.name)
        new = [ROOT / "core" / "strict_judge.py", ROOT / "tests" / "test_strict_judge.py",
               ROOT / "tests" / "test_strict_runtime.py", ROOT / "tests" / "java" / "TcStrictStub.java"]
        new += [T / t / n for t in TARGETS for n in ("ghidra_scripts/TcJunk.java", "ghidra_scripts/TcStrict.java",
                                                    "runtime/tc_strict.h")]
        for f in new:
            lines = text(f).splitlines()
            self.assertLessEqual(len(lines), 400, f.name)
            self.assertEqual([x for x in lines if len(x) > 120], [], f.name)
            self.assertNotIn(b"\r\n", f.read_bytes(), f.name)

    def test_hooks(self):
        for t in TARGETS:
            g = T / t / "ghidra_scripts"
            oracle = ("TcStrict.of(", "TcStrict.call(res, emu, calls)", "TcStrict.exit(res, emu);")
            for f, hooks in (("TcOracle", oracle),
                             ("TcVectors", ("TcStrict.vec(r)", "TcStrict.exp(r)", "TcStrict.replay(this, v, r, set)")),
                             ("TcPrepare", ("TcStrict.header(facts)", "TcStrict.meta(facts)", "TcStrict.dropped[s];")),
                             ("TcFacts", ('case "mock_write":', 'case "exit_frame":'))):
                for h in hooks:
                    self.assertEqual(text(g / f"{f}.java").count(h), 1, (t, f, h))
            rt = text(T / t / "runtime" / "tc_rt.c")
            for h in ('#include "tc_strict.h"', "tcrt_strict_reset();", "tcrt_strict_report(out);"):
                self.assertEqual(rt.count(h), 1, (t, h))
            self.assertEqual(rt.count("tcs_call("), 1, t)
        rd = T / "dreamcast_sh4" / "runtime_rd"
        self.assertEqual(text(rd / "rd_veh.c").count("tcs_call("), 2)
        self.assertEqual(text(rd / "rd_veh.c").count("tcrt_strict_reset();"), 1)
        for r in [T / t / "runtime" / "runner.c" for t in TARGETS] + [rd / "rd_runner.c"]:
            src = text(r)
            self.assertEqual(src.count("tcrt_strict_line(line);"), 2, r)
            nw = src.index("|| line[0] == 'W'")  # the N/W branch (SH-4's N also checks strict_key(): decision 1)
            self.assertLess(src.index("} else if (!active) {"), nw, r)
            self.assertLess(src.index("line[0] == 'J' || line[0] == 'U'"), src.index("} else if (line[0] == 'V') {"))
        self.assertIn("    lines += strict_judge.facts_lines(cfg)", text(ROOT / "core" / "ghidra.py"))
        for f in (ROOT / "core" / "check.py", ROOT / "core" / "readable_check.py", T / "gamecube_ppc" / "ppc_check.py"):
            src = text(f)
            self.assertIn("strict = strict_judge.modes(cfg)", src, f.name)
            self.assertEqual(len(re.findall(r"compare_vector\(.*strict, report := \[\]\)\n\s+strict_judge\.note\("
                                            r"result, s, i, report\)", src)), 1, f.name)


if __name__ == "__main__":
    unittest.main()
