"""Fixtures for the pipeline's own tooling (no Ghidra, no compiler, no agent; a synthetic project in a temp folder).

Covers: readable effort resolution and the agent command line, the output cap, the regress ORACLE mapping and the
oracle-trouble retry test, the --jobs default's bounds, the oracle stamp (deterministic, sensitive to its inputs,
blind to a copied project's paths), usage_report readable's transcript parsing, and lean header selection; and, from
tests/test_retire.py, retire, the workspace consistency check and honest readable redo status; from
tests/test_locks_systems.py, cross-process state writes and system assignment; from tests/test_headers.py, the header
lint and the readable runs' validated header snapshots; from tests/test_sync.py, sync-names and the raw-name audit;
from tests/test_batch18.py, callee records (and the re-prepare cascade), a variadic function's own thunk and
the declared extents of named globals; from tests/test_batch19.py, refine.py library, today's oracle stamp and
tools/final_audit.py on a tiny project; from tests/test_batch20.py, raw C's address constants and pointer coverage,
context.md's pointer and variadic notes, RR5, callee_reads, callee out-pointers, the varargs audit and requests;
from tests/test_batch21.py and test_batch21_runs.py, target dedupe, per-function claims (two concurrent runs over
overlapping lists), stale claims in the doctor, infrastructure errors retried and never recorded as verdicts;
from tests/test_batch22.py, raw.c's guest stack addresses (notes, context.md, RR6), stored stack addresses paired
and callee buffer sizes; from tests/test_batch23.py, look-alikes across projects, machine drafts, re-adopting
earlier attempts and the header parser's multi-line /* comment after a #define; from tests/test_batch24.py, the
drafts' adoption gate; from tests/test_batch24_lean.py, complete lean sandbox headers (what the readable file
uses, values only one constant has, the closure, a declaration's multi-line /* comment).
From tests/test_batch25_lean.py, the smaller lean sandbox (struct projections and their static-assert proof).
From tests/test_batch25a.py, the SDK Function ID tooling (library formats, skip-if-done stamps, the InstallShield 5
reader, FID merge, SDK/engine). From tests/test_scope*.py, the scope list (project.json extra_functions).
From tests/test_batch26.py, machine-named read-only strings, large functions' effort and TASK note, and the
constants a projection hid; from tests/test_batch26b.py, no machine paths in tracked files (config.local.json).
From tests/test_batch27.py, raw return-type overrides and their candidates, functions referenced only as data, and
stack words read before any write. From tests/test_batch29.py, the words a `mova` points at in the sandbox asm and
the wiring of the rescue pass's store-fault repair. From tests/test_batch30.py, the tail-call rewrite with a trailing
comment, frame addresses in a callee's own argument registers, designer renames ("was"), `(void)` dropped for a
void callee and lint RR7 (a void callee's result used). From tests/test_reach.py, call-site coverage (the figure,
the final audit's calls check, the verdict's reasons) and the reach pass's wiring. From tests/test_port_kit.py, the
Dreamcast port kit (game.json, the generated per-game files, no game material in port/, the runtime at /W4 /WX
where Visual Studio's 32-bit tools are found, skipped elsewhere).

usage: python tests/test_tooling.py   (or python -m unittest discover -s tests -p "test_*.py")
"""
import atexit
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="tooling_", dir=TOOL / "tests" / ".work")
PROJ = pathlib.Path(_TMP.name)
(PROJ / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name": "fixture",
                                               "binary": "game.bin"}), encoding="utf-8")
os.environ["DECOMP_PROJECT"] = str(PROJ)
sys.path[:0] = [str(TOOL), str(TOOL / "tools")]

from core import agent_cli, oracle_stamp, paths, readable_headers, readable_lean  # noqa: E402
import regress  # noqa: E402
import regress_project  # noqa: E402
import usage_readable  # noqa: E402
from test_retire import Doctor, RedoStatus, Retire  # noqa: E402,F401  (retire, consistency check, redo status)
from test_locks_systems import Assign, Locks  # noqa: E402,F401  (file locks, system assignment)
from test_headers import Compile, Lint, MidRunEdit, Snapshots  # noqa: E402,F401  (header lint, header snapshots)
from test_sync import SyncAudit, SyncCompile, SyncNames, SyncText  # noqa: E402,F401  (sync-names, raw-name audit)
from test_batch18 import Cascade, CalleeDrift, GlobalExtents, VariadicOwn  # noqa: E402,F401  (callee records)
from test_batch19 import Decls, FinalAudit, FreshStamp, Library  # noqa: E402,F401  (library, final audit)
from test_batch20 import CalleeReads, Context, OutPtr, RawConsts, Requests, Varargs  # noqa: E402,F401  (batch 20)
from test_batch21 import Claims, Cycle, Infra, Targets, VerifyBusy  # noqa: E402,F401  (one worker per function)
from test_batch21_runs import ConcurrentRuns  # noqa: E402,F401  (two refine.py run processes, overlapping lists)
from test_batch22 import OutSizes, PairStores, StackNotes  # noqa: E402,F401  (guest stack, buffer sizes)
from test_batch23 import Draft, Headers23, Hook, Lookalike, Readopt  # noqa: E402,F401  (look-alikes, drafts, re-adoption)
from test_batch24 import Gate  # noqa: E402,F401  (the drafts' adoption gate)
from test_batch24_lean import LeanUses, Reaped  # noqa: E402,F401  (complete lean sandbox headers)
from test_batch25_lean import LeanViews  # noqa: E402,F401  (struct projections, their proof)
from test_batch25a import Cabinet, Classify, Formats, Merge, Stamps  # noqa: E402,F401  (SDK Function ID tooling)
from test_batch26 import Hidden, Large, Strings  # noqa: E402,F401  (strings, large functions, hidden constants)
from test_batch26b import Config as LocalConfig, NoMachinePaths, Patterns  # noqa: E402,F401  (machine paths)
from test_batch27 import Candidates, DataFuncs, Override, Rewrite, StackUninit  # noqa: E402,F401  (raw ret, data, stack)
from test_batch28 import SelfAddress  # noqa: E402,F401  (the function under test's own address)
from test_batch29 import Mova, Readings, Wiring  # noqa: E402,F401  (mova words, store-fault repair)
from test_batch30 import CalleeStorage, PassThrough, SyncWasAndVoid, TailComment, VoidResult, Was  # noqa: E402,F401  (design leftovers)
from test_port_c import FrameArgs, FrameSpans  # noqa: E402,F401  (port phase C)
from test_port_kit import GameConfig, KitIsGameFree, RuntimeCompiles  # noqa: E402,F401  (port kit)
from test_reach import AuditCheck, Figure, Seed, Verdict, Wiring as ReachWiring  # noqa: E402,F401  (call-site coverage)
from test_scope import Bytes, CrossProject, Doctor as ScopeDoctor, ExtraSystems, InScope  # noqa: E402,F401  (scope)
from test_scope_runs import Audit as ScopeAudit, FinalAuditExtras, Orchestrator  # noqa: E402,F401  (scope list, runs)


atexit.register(_TMP.cleanup)  # not tearDownModule: unittest also calls that when it switches to the test_retire
# classes and back, which would remove the folder mid-run


class Effort(unittest.TestCase):
    def test_resolution(self):
        e = agent_cli.readable_effort
        self.assertEqual(e({"effort": "high", "readable_effort": None}), "high")  # falls back to raw's effort
        self.assertEqual(e({"effort": "high", "readable_effort": "medium"}), "medium")
        self.assertEqual(e({"effort": "high", "readable_effort": "medium"}, "low"), "low")  # refine.py --effort
        self.assertIsNone(e({"effort": "high"}, "default"))
        self.assertIsNone(e({"effort": None, "readable_effort": None}))
        with self.assertRaises(ValueError):
            e({"readable_effort": "huge"})

    def test_command(self):
        cfg = {"claude": sys.executable, "model": "m", "attempt_budget_usd": 1, "effort": None}
        cmd = agent_cli.claude_command(cfg)
        self.assertNotIn("--effort", cmd)
        self.assertIn("--system-prompt", cmd)  # lean by default
        self.assertIn("Bash(python check.py)", cmd[cmd.index("--allowedTools") + 1])
        cmd = agent_cli.claude_command(dict(cfg, effort="medium", agent_system_prompt="cli"))
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        self.assertNotIn("--system-prompt", cmd)
        env = agent_cli.agent_env()
        self.assertEqual((env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"]), ("1", "1"))


def assistant(mid, out):
    return {"type": "assistant", "message": {"id": mid, "usage": {"output_tokens": out}}}


class Cap(unittest.TestCase):
    def test_counts_each_message_once(self):
        cap = agent_cli.OutputCap(100)
        for ev in (assistant("a", 30), assistant("a", 60), {"type": "user"}, assistant("b", 40)):
            cap.feed(ev)
        self.assertFalse(cap.is_set())  # a's usage is cumulative (60, not 90): 100 in all, not past the cap
        cap.feed(assistant("b", 41))
        self.assertTrue(cap.is_set())

    def test_off_and_stop(self):
        stop = threading.Event()
        cap = agent_cli.OutputCap(0, stop)
        cap.feed(assistant("a", 10 ** 9))
        self.assertFalse(cap.is_set())
        stop.set()
        self.assertTrue(cap.is_set())


class Oracle(unittest.TestCase):
    def test_zero_vectors_is_oracle(self):
        meta = PROJ / "meta.json"
        meta.write_text(json.dumps({"unsupported": "the oracle did not finish in time"}), encoding="utf-8")
        note = regress_project.oracle_note({"verdict": "FAIL", "stage": "compare", "total": 0}, meta)
        self.assertIn("not a verdict: the oracle did not finish in time", note)
        self.assertIn("no valid vectors", regress_project.oracle_note(
            {"verdict": "FAIL", "stage": "compare", "total": 0}, PROJ / "none.json"))
        for res in ({"verdict": "FAIL", "stage": "compare", "total": 12}, {"verdict": "PASS", "stage": "compare"},
                    {"verdict": "FAIL", "stage": "build"}):
            self.assertIsNone(regress_project.oracle_note(res, meta))

    def test_trouble(self):
        t = regress_project.oracle_trouble
        self.assertTrue(t({"got": "ORACLE"}))
        self.assertTrue(t({"got": "REFUSED", "unsupported": "out of time: did not finish in time (750 s)"}))
        self.assertTrue(t({"got": "FAIL", "detail": "harness: did not finish in time"}))
        self.assertFalse(t({"got": "FAIL", "detail": "r0 differs"}))

    def test_auto_jobs_bounds(self):
        saved = regress.free_ram, os.cpu_count
        try:
            for cores, ram, want in ((64, 64 << 30, 8), (8, 64 << 30, 2), (2, 64 << 30, 1), (32, 7 << 30, 2),
                                     (32, 1 << 30, 1), (32, None, 3), (None, 64 << 30, 1)):
                regress.free_ram, os.cpu_count = (lambda r=ram: r), (lambda c=cores: c)
                self.assertEqual(regress.auto_jobs(), want, (cores, ram))
        finally:
            regress.free_ram, os.cpu_count = saved


class Stamp(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=PROJ))
        (self.d / "scripts").mkdir()
        (self.d / "scripts" / "TcA.java").write_text("class TcA {}", encoding="utf-8")
        (self.d / "ranges.txt").write_text("8c010000 8c020000\n", encoding="utf-8")
        self.facts = self.d / "facts.txt"
        self.facts.write_text(f"deadline 750\ncode_ranges {self.d / 'ranges.txt'}\n", encoding="utf-8")
        self.cfg = {"binary_sha256": "ab", "ghidra_home": "g"}
        paths.FSCA_TABLE, paths.CATALOG = self.d / "fsca.bin", self.d / "catalog.json"

    def s(self, **kw):
        a = dict(cfg=self.cfg, addr="8c010000", scripts_dir=self.d / "scripts", facts_path=self.facts, hints=None,
                 ndev=48, nhidden=40)
        a.update(kw)
        return oracle_stamp.stamp(**a)

    def test_deterministic_and_sensitive(self):
        base = self.s()
        self.assertEqual(base, self.s())
        self.assertEqual(len(base), 16)
        self.assertNotEqual(base, self.s(ndev=47))
        self.assertNotEqual(base, self.s(addr="8c010002"))
        self.assertNotEqual(base, self.s(cfg={"binary_sha256": "cd", "ghidra_home": "g"}))
        (self.d / "scripts" / "TcA.java").write_text("class TcA { int x; }", encoding="utf-8")
        changed = self.s()
        self.assertNotEqual(base, changed)
        (self.d / "ranges.txt").write_text("8c010000 8c030000\n", encoding="utf-8")
        self.assertNotEqual(changed, self.s())

    def test_copy_paths_do_not_matter(self):
        base = self.s()
        other = self.d / "copy"
        other.mkdir()
        (other / "ranges.txt").write_bytes((self.d / "ranges.txt").read_bytes())
        facts = other / "facts.txt"
        facts.write_text(f"deadline 750\ncode_ranges {other / 'ranges.txt'}\n", encoding="utf-8")
        self.assertEqual(base, self.s(facts_path=facts))

    def test_record(self):
        meta = self.d / "meta.json"
        meta.write_text(json.dumps({"addr": "8c010000"}), encoding="utf-8")
        oracle_stamp.record(meta, "0123456789abcdef")
        self.assertEqual(json.loads(meta.read_text(encoding="utf-8")),
                         {"addr": "8c010000", "oracle_stamp": "0123456789abcdef"})


def tool_use(i, name, **inp):
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


class Usage(unittest.TestCase):
    def test_transcript(self):
        u1 = {"cache_creation_input_tokens": 3000, "cache_read_input_tokens": 4000, "input_tokens": 5,
              "output_tokens": 50}
        events = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"id": "m1", "usage": u1, "content": [
                tool_use("t1", "Read", file_path="..\\box\\TASK.md"), tool_use("t2", "Bash", command="ls")]}},
            {"type": "assistant", "message": {"id": "m1", "usage": u1, "content": [  # the same message, streamed again
                tool_use("t1", "Read", file_path="..\\box\\TASK.md")]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True,
                                                      "content": "Permission to use Bash has been denied."}]}},
            {"type": "assistant", "message": {"id": "m2", "usage": dict(u1, cache_read_input_tokens=9000),
                                              "content": [tool_use("t3", "Read", file_path="/box/raw.c"),
                                                          tool_use("t4", "Bash", command="python check.py")]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t4", "is_error": True,
                                                      "content": "FAIL: 3/88 vectors differ"}]}},
            "not an object",
            {"type": "result", "num_turns": 4, "usage": {"cache_creation_input_tokens": 3500,
                                                         "cache_read_input_tokens": 20000, "output_tokens": 900,
                                                         "output_tokens_details": {"thinking_tokens": 400}}},
        ]
        t = PROJ / "transcript.jsonl"
        t.write_text("\n".join(json.dumps(e) for e in events) + "\n{broken\n", encoding="utf-8")
        r = usage_readable.transcript(t)
        self.assertEqual(r["first"], 7005)
        self.assertEqual(r["reads"], ["TASK.md", "raw.c"])
        self.assertEqual(r["refused"], ["ls"])  # a failing check.py is not a refusal
        self.assertEqual((r["cache_w"], r["cache_r"], r["output"], r["thinking"], r["turns"]),
                         (3500, 20000, 900, 400, 4))
        self.assertTrue(r["result"])

    def test_since(self):
        import time
        self.assertAlmostEqual(usage_readable.parse_since("2h"), time.time() - 7200, delta=5)
        self.assertIsNone(usage_readable.parse_since(None))
        self.assertIsNotNone(usage_readable.parse_since("2026-10-13T20:00"))
        with self.assertRaises(SystemExit):
            usage_readable.parse_since("yesterday")


HEADER = """#ifndef TYPES_H
#define TYPES_H

typedef unsigned char u8;
typedef unsigned int u32;

typedef struct Bar { u32 x; } Bar;

/* An actor. */
typedef struct Foo {
    Bar bar;
    u32 flags;
    u32 mode; /* MODE_* */
    u32 slot; /* 0x0c: Slot */
} Foo;

typedef struct Slot { u32 id; } Slot;

typedef struct Baz { u32 y; } Baz;

typedef struct Pool { u32 n; } Pool;

/* Bits of a Foo's flags. */
#define FOO_FLAG_A 0x10
#define FOO_FLAG_B 0x20

#define MODE_IDLE 0
#define MODE_RUN 1

/* The pool of a world. */
#define WORLD_POOL(w) ((Pool *)((u8 *)(w) + 0x1b0))

#endif
"""


class Lean(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = pathlib.Path(tempfile.mkdtemp(dir=PROJ))
        (d / "types.h").write_text(HEADER, encoding="utf-8")
        cls.hs = readable_headers.load(d)

    def names(self, evidence):
        chosen = readable_lean.select(self.hs, "void Tick(Foo *f);", {"Tick"}, evidence)
        return {readable_lean._plain({n}).pop() for it in chosen for n in it.names}

    def test_closure_and_pointers(self):
        n = self.names(set())
        self.assertTrue({"Foo", "Bar", "Slot", "MODE_IDLE", "MODE_RUN"} <= n, n)  # code, wildcard, element comment
        self.assertFalse({"Baz", "Pool", "WORLD_POOL"} & n, n)
        self.assertNotIn("FOO_FLAG_A", n)  # only a comment naming an included type points at it: needs evidence

    def test_evidence(self):
        self.assertIn("FOO_FLAG_A", self.names({0x20}))  # raw.c writes one of its values
        self.assertIn("FOO_FLAG_A", self.names(None))  # no evidence filter
        n = self.names({0x1b4})
        self.assertNotIn("WORLD_POOL", n)  # 0x1b4 is past Pool's unknown size: not the accessor's offset
        n = self.names({0x1b0})
        self.assertTrue({"WORLD_POOL", "Pool"} <= n, n)  # the accessor raw.c's offset reaches, and its type


if __name__ == "__main__":
    unittest.main(verbosity=1)
