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
void callee and lint RR7 (a void callee's result used). From tests/test_batch31.py, `serve` refusing a
port that is already taken. From tests/test_batch32.py, the proof gates (the coverage gate's states and audit check,
mutant generation, the kill and survive logic, the runner output cap, the backlog tool, the unreached blocks); from
tests/test_float_gaps.py, the float gaps (fr0 results, the original's pool floats, TcFret's wiring); from
tests/test_mirror.py, the mirror rule (one RAM word through its 0x0c and 0x8c views, the raw pointer compares);
from tests/test_ptr_views.py, matched C that keeps a pool pointer in the other view; from tests/test_helper_abi.py,
matched C that calls a runtime helper without the r0..r3 it takes. From tests/test_stack_window.py, the
original's own frame: bounded below (a global in the stack window), the members of a struct local passed by address.
From tests/test_judge_window.py, the judge's stack window (only the own frame is scratch: 0x8cff.... globals judged);
from tests/test_oracle_window.py, the oracle's and both runners' (stores there logged, loads there inputs).
From tests/test_p4_window.py, QACR0/1 stores logged and judged, a store-queue pointer built in any register.
From tests/test_reach.py, call-site coverage (the figure, the final audit's calls check, the verdict's reasons) and the
reach pass's wiring. From tests/test_port_kit.py, the Dreamcast port kit (game.json, the generated per-game files, no
game material in port/, the runtime at /W4 /WX where Visual Studio's 32-bit tools are found, skipped elsewhere). From
tests/test_pch_c4505.py, the port's C4505 (a precompiled header) in the readable check. From tests/test_lean_cap.py, the
cap on a lean context's types. From tests/test_tail.py, TcTail's run-on rule on a stub program (javac and java against
the Ghidra jars, skipped without them).
From tests/test_catalog_hw.py, the catalog's hardware address-use scan on a stub listing (the same jars), and a function
matched before the catalog flags it hardware keeps its tier, row and PASS.
From tests/test_mova_pool.py, pool words read through a `mova` base are the function's text (TcText).
From tests/test_finish_no_attempts.py, a function ended before any attempt ends cleanly (its worker lives on).
From tests/test_requeue_rejudge.py, a requeued function's own C judged again before any agent run.
From tests/test_translate_stage.py, the target's translator writes and the judge decides C before any agent run.
From tests/test_cache_ttl.py, the agents' prompt cache lifetime (config cache_ttl).
From tests/test_shared_prefix.py, the shared prompt prefix of raw attempts (config shared_prefix).
From tests/test_ledger_rows.py, the ledger's save: one line per function, the same data read back.
From tests/test_prefetch.py, the next function's Ghidra prepare while the current agent works (config prefetch).
From tests/test_kill_cost.py, a killed session's spend estimated from its requests (config agent_rates).
From tests/test_joined.py, a matched function whose body a catalog run grew over a function its C still calls is
requeued (decomp.py setup/catalog/seed; core/joined.py).
From tests/test_stale_skips.py, finished rows whose blocker kind a landing fixed after they were judged are
requeued (target.json blocker_kinds "fixed_at"; tools/requeue.py --stale-skips and decomp.py serve).
From tests/test_restage.py, xproject.py restage: staged imports today's relocation maps better are rewritten.
From tests/test_xmatch_prefer.py, xproject.py match tries OLD's matched twins first, never a function itself.
From tests/test_fsca_rm.py, the FSCA tables (pinned SHA-256s, the state file's and the port kit's layouts).
From tests/test_fmac.py, fmac rounded once: the C (32- and 64-bit) and the oracle against exact arithmetic.
From tests/test_xmatch_ties.py, xmatch near matches are the same whatever the hash seed, never worse than most_common(5).
From tests/test_checkout_state.py, two checkouts on one project can't change each other's facts, scripts or fsca
table.
From tests/test_regress_targets.py, the targeted regress gate (tools/regress.py --targeted, the harness cache).
From tests/test_land_smoke.py, the live-prepare smoke for landings (only when needed; no slot fails, never skips).
From tests/test_prebuilt_runtime.py, the judge's runtime objects compiled once per content, and run_bounded without a
sleep.
From tests/test_scorecard.py and tests/test_records.py, the definition-of-done scorecard and the records it reads
(workers' busy/idle time, manual operator steps, readable attempts' duration_s): records only, verdicts unchanged.
From tests/test_readable_tx.py, the readable pass's machine files (adopted at any length when they pass, else the
agent's start) and the lift's rules that keep the raw C's meaning, per target.
From tests/test_kit_gdfs.py, the kit's gdFs streaming calls over a fake disc (skipped without the 32-bit Visual
Studio tools).

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
import regress, regress_project, usage_readable  # noqa: E401,E402
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
from test_batch31 import PortTaken  # noqa: E402,F401  (serve refuses a taken port)
from test_batch32 import (Allowance, AuditProof, Backlog, Candidates, Check, Classes, CoverageVerdict,  # noqa: E402,F401
                          DoctorFigure, Generate, LedgerRow, OutputCap, Scan, Settings, Sizes as ProofSizes, UnhitBlocks,
                          Tokens)  # (proof gates)
from test_float_gaps import FloatLits, RetReg, Wiring as FloatWiring  # noqa: E402,F401  (float gaps)
from test_mirror import Follow, Judge as MirrorJudge, SameWord  # noqa: E402,F401  (mirror rule)
from test_stack_window import FrameLocals, Window as StackWindow  # noqa: E402,F401  (own frame)
from test_judge_window import (Compare as WindowCompare, OutPointers as WindowOutPointers,  # noqa: E402,F401
                               Pairing as WindowPairing, Varargs as WindowVarargs)  # (judge's stack window)
from test_oracle_budget import Facts as BudgetFacts, Rule as BudgetRule  # noqa: E402,F401  (work, not clock)
from test_oracle_window import (Facts as WindowFacts, Oracle as WindowOracle,  # noqa: E402,F401
                                Runners as WindowRunners)  # (the producers' stack window)
from test_p4_window import (Judge as P4Judge, Oracle as P4Oracle,  # noqa: E402,F401
                            Runners as P4Runners)  # (QACR stores, built store-queue pointers)
from test_mid_entries import MidEntries  # noqa: E402,F401  (second entry points folded into a body)
from test_build_prune import Prune as BuildPrune, Wiring as BuildPruneWiring  # noqa: E402,F401  (build/)
from test_worktree_prune import Prune as WorktreePrune  # noqa: E402,F401  (landed worktrees)
from test_ptr_views import Check as ViewCheck, Literals as ViewLiterals  # noqa: E402,F401  (pointer views)
from test_helper_abi import Asm as AbiAsm, C as AbiC, Helpers as AbiHelpers  # noqa: E402,F401  (helper ABI)
from test_gamecube_match import (DtkSymbols, ElfSymbols, Flow as MatchFlow, Grade as MatchGrade,  # noqa: E402,F401
                                 MatchPercent)  # (gamecube_ppc byte-match check)
from test_ghidra_broker import Broker, EveryStart, PassNotCaptured, Wiring as BrokerWiring  # noqa: E402,F401
from test_wave import (  # noqa: E402,F401
    Catalog as WaveCatalog, Estimate as WaveEstimate, Fmac as WaveFmac, Guard as WaveGuard, Parse as WaveParse)
from test_idle_prepare import (Guard as IdleGuard, LongTail, Plan as IdlePlan,  # noqa: E402,F401
                               Run as IdleRun, Watch as IdleWatch)  # (tools/idle_prepare.py, core/longtail)
from test_harness_reuse import (Investigate as ReuseInvestigate, PrefetchReused, PrepareList,  # noqa: E402,F401
                                RejudgeReused, Reusable, StatusPlate)  # (current harness: no Ghidra; prepare_list)
from test_port_c import FrameArgs, FrameSpans  # noqa: E402,F401  (port phase C)
from test_port_kit import GameConfig, KitIsGameFree, LockstepCounter, LockstepTimer, RuntimeCompiles  # noqa: E402,F401  (port kit)
from test_overlays import OverlayBuilt, OverlayGroups, OverlayWindows  # noqa: E402,F401  (overlays)
from test_reach import AuditCheck, DoctorAnyWorkspace, Figure, Seed, Verdict, Wiring as ReachWiring  # noqa: E402,F401  (call-site coverage)
from test_scope import Bytes, CrossProject, Doctor as ScopeDoctor, ExtraSystems, InScope  # noqa: E402,F401  (scope)
from test_scope_runs import Audit as ScopeAudit, FinalAuditExtras, Orchestrator  # noqa: E402,F401  (scope list, runs)
from test_lean_cap import Cap as TypesCap  # noqa: E402,F401  (context cap)
from test_pch_c4505 import PchCompile, PchWiring  # noqa: E402,F401  (C4505)
from test_tail import RunOn  # noqa: E402,F401  (TcTail's run-on rule)
from test_kit_gdfs import Streaming as GdfsStreaming  # noqa: E402,F401  (the kit's gdFs streaming calls)
from test_translate_stage import (Contract as TxContract, Crash as TxCrash, NoTranslation as TxNone,  # noqa: E402,F401
                                  SaturnAdapter as TxSaturn, Slow as TxSlow, Stopped as TxStopped,
                                  Tamper as TxTamper, Translated)  # (translator stage)
from test_fsca_rm import Tables as Fsca  # noqa: E402,F401  (FSCA's two tables)
from test_fmac import (C as FmacC, Oracle as FmacOracle, Reference as FmacReference,  # noqa: E402,F401
                       Wiring as FmacWiring)  # (fmac rounds once: every side against exact arithmetic)
from test_catalog_hw import HwScan, MatchedKeepsRow  # noqa: E402,F401  (catalog hw scan; matched keeps its row)
from test_mova_pool import MovaPool  # noqa: E402,F401  (mova pool words are text)
from test_prepare_timing import ClockTest as PrepareClock, Prepare as PrepareTiming  # noqa: E402,F401  (prepare timing)
from test_finish_no_attempts import NoAttempts  # noqa: E402,F401  (a function ended before any attempt)
from test_requeue_rejudge import AuditAttempt0, Rejudge  # noqa: E402,F401  (a requeued function's own C judged first)
from test_cache_ttl import CacheTtl  # noqa: E402,F401  (the agents' prompt cache lifetime)
from test_shared_prefix import SharedPrefix  # noqa: E402,F401  (the shared prompt prefix)
from test_ledger_rows import Rows as LedgerRows  # noqa: E402,F401  (the ledger's save: one line per function)
from test_prefetch import Prefetch  # noqa: E402,F401  (prefetch)
from test_kill_cost import RunAttempt as KillCostRun, SpendMeter as KillCostMeter  # noqa: E402,F401  (killed sessions' spend)
from test_joined import RequeueJoined, Rule as JoinedRule  # noqa: E402,F401  (stale jump-in parents)
from test_stale_skips import Requeue as StaleSkipsRequeue, Rule as StaleSkipsRule  # noqa: E402,F401  (stale skips)
from test_restage import Restage  # noqa: E402,F401  (xproject restage)
from test_xmatch_prefer import Prefer as XmatchPrefer  # noqa: E402,F401  (matched twins first)
from test_ghidra_server import FirstSlot, PrepareServed, Serves as GhidraServe  # noqa: E402,F401  (one Ghidra per slot)
from test_xmatch_ties import Ties as XmatchTies  # noqa: E402,F401  (near-match ties)
from test_checkout_state import CheckoutState, FscaState, SharedScripts  # noqa: E402,F401  (per-checkout state)
from test_sr_sq import Catalog as SqCat, Judge as SqJudge, SrWord, Tier as SqTier, Wiring as SqWire  # noqa: E402,F401
from test_spin import Candidates as SpCand, Judge as SpJudge, Runtimes as SpRt, Wiring as SpWire  # noqa: E402,F401
from test_hw_ops import Classifier as HwCls, Judge as HwJudge, Listing as HwList, Runtimes as HwRt  # noqa: E402,F401
from test_hw_ops import Stamp as HwStamp, Wiring as HwWire  # noqa: E402,F401  (listed hardware ops)
from test_hw_ops import WriteRuntime as HwWrt, Writes as HwWrites  # noqa: E402,F401  (listed writes, cache ops)
from test_regress_targets import RegressTargets  # noqa: E402,F401  (the targeted regress gate)
from test_land_smoke import LandSmoke  # noqa: E402,F401  (the live-prepare smoke for landings)
from test_prebuilt_runtime import RealBuild as RtBuild, RunBounded, RuntimeObjs  # noqa: E402,F401  (prebuilt runtime)
from test_mutation_audit import Gate as MaGate, Needed as MaNeeded, Outcomes as MaOut  # noqa: E402,F401
from test_branch_cov import Grade as BcGrade, Rule as BcRule, Wiring as BcWire  # noqa: E402,F401  (item 3)
from test_targeted_vectors import Draws as TvDraws, Harvest as TvHarvest, Wiring as TvWire  # noqa: E402,F401
from test_saturn_sh2 import (OverlayImage as SatOverlay, Runtime as SatRuntime, SatDisc,  # noqa: E402,F401
                             Scripts as SatScripts, Target as SatTarget)  # (the Saturn SH-2 target)
from test_xbox360_ppc import (Aes as XAes, TooHuman as XTooHuman, Xex as XXex,  # noqa: E402,F401
                              Xiso as XXiso)  # (the Xbox 360 Xenon target, offline)
from test_xbox360_glue import Glue as XGlue, Runtime as XRuntime, Scan as XScan  # noqa: E402,F401
from test_xbox360_helpers import Helpers as XHelpers  # noqa: E402,F401
from test_xbox360_scripts import Scripts as XScripts, Target as XTarget  # noqa: E402,F401  (its Ghidra scripts)
from test_scorecard import Gates as ScGates, Readers as ScReaders, Records as ScRecords  # noqa: E402,F401  (scorecard)
from test_records import (BusyLog, Hooks as RecHooks, OpLog, RawJudge as RecRawJudge,  # noqa: E402,F401
                          Readable as RecReadable)  # (the scorecard's records: busy/idle, operator steps, duration_s)
from test_own_translators import (Dreamcast as OwnTxDc, Regress as OwnTxRegress, Small as OwnTxSmall,  # noqa: E402,F401
                                  Stage as OwnTxStage, Xbox360 as OwnTxX360)
from test_game_setup import Check as GsCheck, Names as GsNames, Setup as GsSetup  # noqa: E402,F401  (game_setup.py)
from test_warm_check import Warm as WarmCheck  # noqa: E402,F401  (the warm agent check, core/warm_check)
from test_ghidra_identity import Identity as GhidraIdentity  # noqa: E402,F401  (the oracle stamp's Ghidra line)
from test_compiler_identity import (Pin as VcPin, PortBuild as VcPortBuild,  # noqa: E402,F401
                                    Stamp as VcStamp, Cache as VcCache,
                                    Fails as VcFails, SdkFiles as VcSdkFiles)  # (vcvars_args, the stamp's compiler)
from test_requirements import Requirements  # noqa: E402,F401  (requirements.txt)
from test_readable_tx import Adoption as MachineAdoption, Constants as MachineConstants  # noqa: E402,F401
from test_readable_tx import Lift as MachineLift, Specs as MachineSpecs  # noqa: E402,F401
from test_strict_judge import (Facts as SjFacts, Judge as SjJudge, Stream as SjStream,  # noqa: E402,F401
                               Wiring as SjWire)  # (item 2: the stricter judge)
from test_strict_runtime import Readable as SjReadable, Runtimes as SjRuntimes  # noqa: E402,F401  (item 2)

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

    def test_defaults(self):  # raw attempts medium (r15 A/B), readable sessions still high
        cfg = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(agent_cli.effort_value(cfg["effort"]), "medium")
        self.assertEqual(agent_cli.readable_effort(cfg), "high")

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
