# Proof records for the waste audit fixes, 2026-10-22

These are the proofs `docs/WASTE_AUDIT_2026-10-22.md` listed as owed, run with Ghidra time the user approved.
- Every run used private copies in a scratch folder: its own `project.json`, and copies of the Ghidra project and
  state. No live workspace file was written.
- Each run was bounded by `ramguard.py`: a hard time limit that kills the process tree, and a pause at 95%
  machine RAM until 5 readings fall under 85%.
- The broker's HOLD pass went only into the run's own environment. It was never printed or stored.
- "Old" is the code before the fix (`git archive <fix>~1`). "New" is this branch.

## 1. `ghidra.export` keeps its copy of slot 1 (4d22a2d)

**Run.** Toy Commander (TC): 20 functions, evenly spaced over the catalog, exported in 4 calls of 5, as
`port/tools/sdk_ref.py` does (`export_proof.py`). Old and new each used their own private workspace, holding a
copy of TC's slot 1 with its `copied_from.txt` and mtime.

**Byte-identical: yes.** Old and new wrote the same 40 files (`asm.txt` and `ghidra.c` for each of the 20
functions), equal byte for byte (`diff -r`). Their hashes are in `export_out.sha256`. The new code copied
slot 1 once (the `export_of.txt` mark) and reused that copy for calls 2–4.

**Saving: negligible.**

| Call | Old (s) | New (s) |
|------|---------|---------|
| 1 | 572.2 (waiting in the broker queue behind another session) | 22.8 |
| 2 | 10.2 | 7.6 |
| 3 | 7.8 | 6.7 |
| 4 | 8.6 | 9.5 |

- The copy the new code skips takes 0.04 s (TC slot 1, 70 MB, 5 timed copies of 0.037–0.056 s). PS2's slot 1 is
  122 MB.
- The gap between calls (7–10 s) is Ghidra start-up and noise, not the copy.
- **Correction to the audit:** it reasoned "about 1 s per call". The measured saving is about 0.04 s per call.
  The fix is kept because its output is proven identical, but it saves almost nothing.

## 2. Served slots in a live-style run (7735605)

**Run.** TC: 50 matched functions, evenly spaced over `matched/` (`serve_proof.py`). Each was prepared 2 at a
time over 3 slots (the master and 2 read-only copies), in its own private workspace. That workspace held copies of
the master Ghidra project, the state files, `oracle/`, `readable/names` and every `harness/*/meta.json` (the callee
signatures).
- **Off:** `ghidra_serve` disabled. The order is the old one: the master first, a fresh Ghidra per prepare.
- **On:** `ghidra_serve` enabled. The new `_order` puts a served copy first and the master last.

**Byte-identical: yes.** Both runs wrote the same 353 harness and packet files (meta.json with its oracle stamp,
vectors, packet parts), equal byte for byte. Their hashes are in `serve_out.sha256`.

**Saving.**

| | Off (old order) | On (served first) |
|---|---|---|
| Slots used | master 32, slot 1 17, slot 2 1 | slot 1 37, slot 2 13, master 0 |
| Median prepare | 10.7 s | 1.4 s |
| Prepare time, all 50 | 847 s | 445 s |
| Prepare time without the one 200 s function | 643 s | 225 s |
| Wall clock | 496 s | 508 s |

- The served run spends its time in TcPrepare itself. `prepare_timing.jsonl` shows JVM 0.03 s, against the cold
  JVM start and project open.
- The wall clock did not drop in this run, for two reasons:
  - One function (8c065fe4) takes about 200 s of script time either way, and both runs end on it.
  - The served run waited 373 s for broker tokens (153 s and 109 s on two prepares), against 44 s for the cold
    run. The broker is machine-wide, and another session was preparing at the same time.
- So the measured saving is about 8.4 s of Ghidra time per prepare (64%), and it shows up as wall clock only when
  slots, not the broker queue, are the bottleneck.
- Turning `ghidra_serve` on for live runs is still the user's call: one idle JVM per served slot, for up to
  120 s.

## 3. Regress keeps a private copy per mode (7bdd400)

**Run.** Spawn sst00, from exported trees in a scratch folder. Ghidra refuses this worktree's path, because
`.claude` is a path element starting with a dot. Each run was
`tools/regress.py --projects sst00 --sample 5 --seed 1 --jobs 2`, with or without `--catalog`, the catalog step
that `--targeted` always turns on. The copies are each tree's own `tests/ghidra_work` (`withpass.py` supplies the
broker pass).

| Run | Code | Mode | Copy | Catalog step | Wall |
|-----|------|------|------|--------------|------|
| A | new | catalog, fresh | copied (0 s) | ran (47 s) | 140 s |
| B | new | plain, fresh | copied (0 s) | — | 108 s |
| C | new | catalog after the switch | **kept** | **kept** (catalog_out) | 128 s |
| D | new | plain after the switch | **kept** | — | 109 s |
| E | new | catalog, copy removed first | copied (0 s) | ran (51 s) | 162 s |
| F | new | plain, copy removed first | copied (0 s) | — | 131 s |
| A | old | catalog, fresh | copied (1 s) | ran (78 s) | 224 s |
| B | old | plain after the switch | copied again (0 s) | — | 135 s |
| C | old | catalog after the switch | copied again (0 s) | ran again (56 s) | 160 s |

**Same verdicts: yes.** All 9 runs give the same 9 items: 5 sampled functions and 4 fixtures. Each has the same
verdict, stage, failing count and vector count (`valid`), and each is the expected one. The results are in
`regress/*.json`, and the steps with their times in `regress/steps.txt`. The work-state catalog and code ranges
after old C (catalog step run) and after new F (live catalog) are byte-equal (sha256 8b3afdc3…, 61c354ff…).

**Saving, measured.**
- The repeated cost is the catalog step: 47–78 s for sst00 (median about 52 s). The project copy is 0–1 s; the
  audit's "about 80 s per project" counted the copy as well.
- The new code skips the step on a catalog-mode run that follows a plain one: a `--targeted` run after a gate. A
  plain run after a catalog run saves only the 0–1 s copy.
- Across the 24 SST projects, that is about 21 min per `--targeted` run after a gate, against the audit's
  "about 32 min per gate".
- Also, the reused master's catalog is now its own catalog step's, not the live one. In this run the two are
  byte-equal, because sst00's scripts did not change.

## 4. Wave re-stamp RAM brake (1a8bb09)

Three runs. The first (below, "Run 1") was cut short by RAM. The second (2026-10-23, `--slots 3`, 300 functions)
deadlocked the shared broker for about 40 minutes and was stopped with its whole process tree: its waiting worker
held 3 idle slot servers that counted against every waiter's limit (audit finding 11, fixed in ddcdbd9). The third
ran on the tree with that fix.

### Run 3 (2026-10-23, the final tree with ddcdbd9): concluded for output and slots; the brake's band not reached

**Run.** PS2, two identical private copies (`brake_proof.py build`, the 100 lowest matched addresses lose their
oracle stamp), from the final tree. `tools/reprepare_stale.py --stamps --serve --slots 2 --max 100 --rounds 0`, once
with `--brake 85` and once without, each under the RAM guard (pause at 95%, 2 h limit). `slotwatch.py` sampled every
2 s: RAM, the run's own Ghidra JVMs, and its broker holds and tickets. Other sessions used the broker throughout
(3 holds and 5–8 tickets of theirs most of the time).

| | brake on | brake off |
|---|---|---|
| Re-prepared | 100/100, 0 errors | 100/100, 0 errors |
| Wall time | 3,203 s | 2,355 s |
| Sum of prepare times / median | 1,023 s / 8.75 s | 1,052 s / 9.61 s |
| Own JVMs at most / own holds at most | 2 / 2 | 2 / 2 |
| RAM peak; seconds at 85–88% | 99.9% (another session's load); 6 s | 70.4%; 0 s |
| RAM guard | 1 pause at 98.3%, resumed at 67.9%; 0 kills | no pause |
| Brake held prepares back | under 1 s (not printed) | — |

**Byte-identical: yes.** 13,712 files in `harness/`, `packets/`, `matched/` and `oracle/` of each copy: 712 were
rewritten by both runs, and all 712 are equal byte for byte; 0 files differ; none is in only one copy. Timings:
`brake_r3_*_prepare_timing.jsonl`.

**Within its slots: yes.** The run was granted 2 slots (`--slots 2`) and never had more than 2 JVMs or 2 holds
of its own, and the queue kept granting other sessions' tickets throughout (no stall).

**Seen.** Every one of the 200 prepares started a cold JVM (median 8.75–9.61 s, against about 1 s served in
proof 2): with other sessions' tickets ahead, the worker gave its server back each time it waited (ddcdbd9), as it
must. Served slots save JVM starts only while the queue is free. Most of each run's wall time was the shared
queue: 1,023 s of prepares in 3,203 s.

**Not concluded:** whether the brake turns guard kills into pauses. RAM was in the brake's band (85–88%) for 6 s of
the 3,203 s, and the brake held nothing back. Reaching that band on purpose means loading the user's machine to
85% and keeping it there; that was not done. Its decisions are covered offline (`tests/test_wave.py::Brake`, 4
tests); it only changes when a prepare starts, and the output above is the same with it and without it.

### Run 1 (2026-10-22): partial

**Run.** PS2, with the real tool on two identical private copies (`brake_proof.py`). In each copy the 300 lowest
matched functions had their oracle stamp removed. The command was
`tools/reprepare_stale.py --stamps --serve --slots 3 --max 300 --rounds 0`, once with `--brake 85` and once
without. A RAM sampler logged machine RAM every second, and each run had a limit of 1 hour.

**Result.**
- Neither run finished. The brake run completed 3 prepares in the hour, the run without the brake 8.
- Machine RAM was at 88% or more for 1,274 s (brake) and 1,434 s (no brake) of the hour, and peaked at 98–99.9%.
  At 88% the broker grants no new Ghidra, so both runs were held by the broker, not by the brake.
- The RAM guard paused the runs at 95% (78 s and 478 s of samples).
- The load was outside these runs: a game the user was playing (6.4 GB) and other sessions' compile jobs. After
  the runs ended, RAM still read 95.9%, with no Java process left.

**Byte-identical: yes, for what both runs made.** 8c010080, 8c010220 and 8c0102c4 were prepared by both. Their
harness files (4 each) and packet files (3 each) are equal byte for byte. Timings are in
`brake_*_prepare_timing.jsonl`.

**Not concluded:** whether the brake turns guard kills into pauses. That needs a chunk where RAM sits between 85%
and 88% with room to run: the brake's band, under the broker's gate. Under this machine's load that did not
happen. The brake only gates when a prepare starts, so its output is the same; the offline tests
(`tests/test_wave.py::Brake`) cover its decisions.

**Seen on the way.** The scan found 6,516 of PS2's matched harnesses "not current by oracle stamp" in the private
copy. Whether the live PS2 shows the same count, or the stamp depends on something workspace-specific, was not
checked here.

## 5. The judge branch, verdict by verdict (6680db3, ed6b23f, a4efcdb: F6, F1, F5)

**Run.** `refine.py verify --no-sync` as refine.py runs it (the current headers' snapshot, then
`core/readable_verify.verify`, 8 workers), over a private copy of each workspace's readable files, harnesses,
packets and state (`verify_proof.py`; `build_prune` off in the copy, so what one run keeps is there for the next).
- **Sample.** 300 readable files per game, the same for every run: `random.Random(1).sample` of TC's and PS2's
  readable files (`PROOF_SAMPLE=300 PROOF_SEED=1`). A full pass was started first and stopped: at about 6 min per 300
  it did not fit the time bound with all five runs.
- **Base:** the code before the judge branch (2da1869). **Judge:** the branch tip. Cold: `build/` removed first.
  Warm: run again on what the cold run kept (the agent's check leaving the official judge its runner, as in a
  refine cycle).
- Each function's final judgement (verdict, stage, failing, total, first_diffs, note, out_pointers) went to a JSON
  file per run.

**Same verdicts: yes, all 600.** In every run the per-function results are equal, field by field
(`verify/runs.txt`: one sha256 per game for all its runs; `verify/tc.json` and `verify/ps2.json` hold them).
- TC: 297 PASS, 3 FAIL at compare, each HARNESS_STALE (the matched raw C fails its harness too: 8c013850,
  8c062c48, 8c06419c).
- PS2: 287 PASS, 13 FAIL: 12 at build, STALE (written against older headers), and 8c17bf60 at compare,
  HARNESS_STALE.
- The judge run used its caches: one runtime set (`build/runtime_rd_obj`), a kept runner per function
  (`rd_runners`), `raw_runs.json` for the functions with out-pointer calls (29 TC, 21 PS2).

| Run | TC (s) | PS2 (s) |
|-----|--------|---------|
| Base | 375.3 | 453.0 |
| Judge, cold | 327.5 | 347.1 |
| Judge, warm | 258.6 | 252.3 |

## 6. The final tree: the raw judge's kept runner and the kept size probe (c2f885e, 5596ba5)

**Run.** As proof 5, same samples and workspaces, with the waste-final tree before its rebase onto 3736d25 (the
same code; 3736d25 changes only which environment variables the compiler caches keep).

**Same verdicts: yes, all 600**, equal to base field by field (`verify/runs.txt`).
- The kept size probes were used: `type_sizes.json` in 29 TC and 20 PS2 function folders.
- Verify runs the raw judge (harness_stale) only for a file that fails, so it kept 3 TC and 13 PS2 raw runners.
  The raw runner's main use is the agent's check followed by the official judge in a job;
  `tests/test_raw_runner_reuse.py` proves with Visual Studio that a kept raw runner equals a fresh build section by
  section.

| Run | TC (s) | PS2 (s) |
|-----|--------|---------|
| Base (proof 5) | 375.3 | 453.0 |
| Final, cold | 275.9 | 239.2 |
| Final, warm | 201.9 | 193.0 |

So a warm verify takes 46% less time on TC and 57% less on PS2, with the same verdicts. The runs were on a shared
machine, so single timings carry noise of some seconds; the direction held in every pair.
