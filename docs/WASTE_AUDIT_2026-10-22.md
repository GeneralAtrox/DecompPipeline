# Waste audit, 2026-10-22

Scope: the code in `core/`, `tools/` and `targets/*/`, and the evidence in five workspaces: ToyCommander (TC),
Spawn main, PowerStone2 (PS2), Grandia ws-1pct8 and SkiesOfArcadia decomp-gekko. Also
`ToyCommander/handoff/HANDOFF.md`, the wave state in `%LOCALAPPDATA%/DecompPipeline/wave/`, each workspace's
`prepare_timing.jsonl` and ledger, and `tests/ghidra_work`.

The lens is the user's: "I don't want a single second wasted unless it's productive." A 30 s step that cannot be
avoided is fine. **The same step run again on unchanged inputs** is the waste this audit hunts, flow by flow:
prepare → judge → readable → verify → wave → regress.

Method: reading, grepping, short Python over existing logs, and offline unit tests. Nothing started Ghidra, and
nothing ran a prepare, regress, wave, oracle probe or mutation. A saving is **measured** (offline, on real data)
or **reasoned** (from logged timings).

Rules kept:
- No safety, identity or evidence gate is removed, weakened or merged. The one exception is two checks of the
  same fact on the same inputs in one run.
- `core/compare.py`, the oracle scripts and the runtimes are untouched. `core/check.py` is touched only in the
  judge set (a lock in its runtime cache, finding 6; the kept raw runner, finding 1).
- No matched or readable C was edited.
- Every reuse below is keyed by hashes of every input it stands for, never by a time or a name alone.

## State on `waste-final` (2026-10-23)

`waste-final` is cut from the integration branch `claude/integration-2026-10-22-7e0060` (3736d25), which already
holds every commit of `claude/eloquent-morse-aaecfa` up to this report. On top of it, nothing pushed:

| Commit | What | Lands |
|--------|------|-------|
| 851b18c, 4b90443, 264d93f, ab36d66, b978b4b | The proof records (`docs/proofs/2026-10-22/PROOFS.md`, proofs 1–6) | with the branch |
| 7cb763a | The proof records name no machine paths (`tests/test_batch26b`) | with the branch |
| 3b6d386 | `docs/proposals/ghidra_serve_on.patch`: the slot servers on, one config line (F7) | the user's go: `git apply` |
| 6680db3 | Readable judge links its runtime compiled once per content (F6) | **the user's yes** (judge set) |
| ed6b23f | The official readable judge takes the agent's runner built from the same inputs (F1) | **the user's yes** (judge set) |
| a4efcdb | The raw C's out-pointer runs are cached per function (F5) | **the user's yes** (judge set) |
| d805dc1 | Two tests the judge set broke (a fake compiler file; no machine path) | with the judge set |
| c2f885e | The official raw judge takes the agent's runner built from the same inputs (F1, raw) | **the user's yes** (judge set) |
| 5596ba5 | The out-pointer size probe is kept per function by all it reads (F5) | **the user's yes** (judge set) |
| ddcdbd9 | Ghidra broker: a served slot's waiter closes its server when another ticket is ahead (F11, the deadlock proof 4 found); files from before a restart are reaped (F12) | with the branch; **before** the serve switch |
| d7e541b | `ghidra.slot` refreshes a stale copy under the master before the token, never holding one (F13) | with the branch; to every checkout that prepares in parallel |
| 262283a | A process the HOLD stops waits outside the queue; the test suite has its own broker folder (F14) | with the branch; to every checkout |

Every finding is now fixed and proven, or proven negligible (the table below). The judge set's proof: `refine.py
verify` over 300 sampled readable files each of TC and PS2 gives the same 600 verdicts, field by field, before and
after, cold and warm (proofs 5 and 6). A warm verify takes 201.9 s against 375.3 s (TC) and 193.0 s against
453.0 s (PS2). The full suite (every `tests/test_*.py` under pytest, one bounded process per file, as the integration
run did): 1,783 passed, 0 failed, 0 skipped over 101 files; 1,746 on 3736d25, plus the 37 tests these commits add.

## Branches and commits

**`claude/eloquent-morse-aaecfa`** (based on master d5cd42c; nothing merged or pushed):

| Commit | What | Proof |
|--------|------|-------|
| 10314a8 | Oracle stamp scan hashes the shared inputs once per scan (F2) | old vs new on 11,414 real functions: 0 differences |
| 7735605 | `ghidra.slot` prefers a slot whose server runs (F7) | **proven**: 50 TC prepares, 353 files byte-equal (proof 2) |
| 1a8bb09 | Wave re-stamp brakes new prepares at 85% RAM (F8) | offline test; **proven** byte-equal: 712 files of 100 PS2 re-prepares (proof 4) |
| 2da1869 | Wave step 5 takes step 4's PASS on the very same inputs (F9) | offline test (6 cases) |
| dda1aec | `_prepare` writes meta.json once (F10) | byte comparison of old and new writes |
| 41a02a1 | Draft cycle: refine takes the draft's raw-judge PASS on the same inputs (F4) | offline test |
| c309426 | `readable.Registry` duplicate-name check: one count, not O(n²) (F10) | old vs new on PS2 and TC registries: equal |
| 7bdd400 | Regress keeps separate private copies for gate and `--targeted` runs (F3) | **proven**: 9 sst00 runs, equal verdicts (proof 3) |
| 4d22a2d | `ghidra.export` keeps its copy of slot 1 while slot 1 is unchanged (F10) | **proven**: 20 TC exports byte-equal (docs/proofs) |
| f1cb292 | `wave.py run --requeue`: the fmac flips go to `requeue.py` at the end (opt-in) (F10) | offline test |
| 244109d | `prepare_list --harness-stale` takes the ledger's harness_stale rows (F10) | offline test |
| (this) | This report and `docs/proposals/efficiency_pass_rule.patch` | — |

**`claude/eloquent-morse-aaecfa-judge-cache`** (based on 2da1869; **proposal**: lands only after the
verdict-by-verdict proof below and the user's yes):

| Commit | What |
|--------|------|
| 81fdc3f | Readable judge links its runtime compiled once per content; fixes the `check.runtime_objs` race (F6) |
| f4c5c45 | The official readable judge takes the runner the agent's last check built from the same inputs (F1) |
| 5ba1f7a | The raw C's out-pointer runs are cached per function, not per build folder (F5) |

## Total saving

| Where | Saving | Status |
|-------|--------|--------|
| Per full re-stamp wave | about 1 h (F2) + 6.5 min (F9), plus the guard kills turned into pauses (F8) | landed on the branch; F8 proven byte-equal (proof 4) |
| Per readable pass (draft, refine) | about 45 min (F4) + about 12 min PS2 (F10, duplicate names) | landed on the branch |
| Per `--targeted` regress run after a gate | about 21 min (F3, measured: the catalog step, about 52 s per SST project) | **proven** (docs/proofs) |
| Per long live run with `ghidra_serve` on | 8.4 s of Ghidra time per prepare (F7, measured: 10.7 s → 1.4 s median) | code landed; **proven** (proof 2); the switch is the user's go |
| Per readable pass, judge set | about 4.4 h (F1) + 0.5–1 h (F1 raw) + about 31 min (F5) + 1.8–2.1 s per readable check (F6) | on waste-final; **proven** (proofs 5, 6); the user's yes |
| Per readable verify, judge set | 300 files: TC 375 s → 202 s, PS2 453 s → 193 s warm (measured) | on waste-final; **proven** (proof 6); the user's yes |

Landed and proven offline: **about 1 h per wave plus about 1 h per readable pass**. With the Ghidra runs below
done, add about 21 min per `--targeted` run after a gate and 13–18 min per long live run. With the judge branch, add about 5 h per readable
pass across PS2, TC and Spawn.

---

## Findings, ranked by saving

| # | Finding (flow) | Saving | Status | Byte-identical |
|---|----------------|--------|--------|----------------|
| 1 | Official judges recompile the runner the agent's last check built (readable, raw) | about 4.4 h + 0.5–1 h per readable pass (reasoned) | **fixed** on waste-final (ed6b23f, c2f885e); lands on the user's yes | **proven**: 600 verdicts equal (proofs 5, 6); runners equal section by section |
| 2 | Oracle stamp scan re-hashed 54 shared files per function (wave) | about 1 h per wave (measured) | **fixed** 10314a8 | yes, proven on 11,414 functions |
| 3 | Regress copies every project again when the mode switches (regress) | about 21 min per `--targeted` run after a gate (measured) | **fixed** 7bdd400 | **proven**: 9 sst00 runs, equal verdicts (proof 3) |
| 4 | Draft cycle runs the raw judge twice on the same inputs (readable) | about 45 min per pass (measured per judge) | **fixed** 41a02a1 | yes, offline |
| 5 | Raw C's out-pointer runs and size probe redone per build folder and run (readable, verify) | about 31 min per pass + 13 min per PS2 verify (reasoned) | **fixed** on waste-final (a4efcdb, 5596ba5); lands on the user's yes | **proven**: 600 verdicts equal (proofs 5, 6) |
| 6 | Readable judge compiles its runtime on every check (readable, verify) | 1.8–2.1 s per check (measured) | **fixed** on waste-final (6680db3); lands on the user's yes | **proven**: sections byte-equal; 600 verdicts equal (proof 5) |
| 7 | Live prepares start a cold JVM; the slot server is wave-only (prepare) | 8.4 s of Ghidra time per prepare (measured, proof 2) | slot order **fixed** 7735605; the switch is `docs/proposals/ghidra_serve_on.patch` (the user's go) | **proven**: 353 files byte-equal (proof 2) |
| 8 | Wave RAM guard kills chunks instead of braking (wave) | 8 kills in 9 chunks avoided (reasoned) | **fixed** 1a8bb09 | **proven**: 712 files byte-equal with and without it (proof 4) |
| 9 | Wave step 5 re-judges what step 4 just judged (wave) | about 6.5 min per wave (reasoned) | **fixed** 2da1869 | yes, offline |
| 10 | Small repeats and manual steps (all flows) | seconds to minutes each | 5 **fixed**; the rest proven negligible (under 0.2 s a run) or gates | yes |
| 11 | A served slot's waiter keeps its server's JVM while it waits behind a ticket that needs it: the machine-wide broker deadlocks (prepare, wave; found by proof 4) | every session's Ghidra stalled (about 40 min seen) | **fixed** ddcdbd9 | yes: only when a server closes changes; offline test reproduces it |
| 12 | Broker files of processes from before a restart can outlive it when Windows reuses their PID (all flows) | queue slots held by the dead | **fixed** ddcdbd9 | yes: offline test |
| 13 | A copy's thread holds a token while it waits for the master, whose thread waits for a token: the broker stops (prepare; seen 2026-10-23) | every session's Ghidra stalled (about 10 min seen) | **fixed** d7e541b | yes: only when a copy is refreshed changes; offline test reproduces it |
| 14 | A process the broker's HOLD stops still queues, and at the head keeps every pass holder behind it waiting; the test suite filed tickets in the machine's folder (tests; seen 2026-10-23) | every session's Ghidra stalled (30 min, until the run's timeout) | **fixed** 262283a | yes: offline tests reproduce it |

---

### 1. The official readable judge recompiles the runner the agent's last check built — FIXED on waste-final (ed6b23f; raw: c2f885e), the user's yes

**Evidence.**
- The agent's sandbox `check.py` runs `refine.py check --agent`, which runs `check_readable` into
  `build/ADDR/rd_agent`. The official judge after the attempt (`core/refine.py:267`) runs `check_readable`
  again into `build/ADDR/readable`. Both use the same header snapshot (`header_snapshot.ACTIVE`).
- The official `work.c` was byte-identical to the agent's last checked one in PS2 2,425/2,428, TC 2,605/2,620
  and Spawn 438/440 attempts (the repeats agent, from the transcripts).
- Readable compile+link median: 3.18 s PS2, 2.20 s TC.

**Saving (reasoned).** One compile+link per such attempt, after the runtime share (F6): about 2.4 h PS2, 1.6 h
TC and 0.4 h Spawn per readable pass.

**Fix (f4c5c45, judge branch).**
- `readable_check.build` keeps a clean runner (built, no message) in `build/ADDR/rd_runners/KEY`.
- KEY is the sha256 of everything the build reads:
  - the compiler (path, size, mtime), INCLUDE, LIB, `C_FLAGS` and the link options;
  - every source as copied (work.c, thunk.c, protos.c);
  - every file of each include folder, by position, name and content;
  - `runtime_rd/` and `runtime/`;
  - the build code (`readable_check.py`, `pch_warnings.py`, `check.py`).
- A build with the same KEY copies the kept runner. **The vectors always run afresh**; lint, the lean proof and
  the compare are untouched.
- `build_prune` removes the `.exe` files after each job, so a kept runner lives about one refine cycle.

**Byte-identical.**
- `tests/test_rd_runner_reuse.py`: built once for agent then official; each of 11 changed inputs builds again;
  failed or noisy builds are never kept.
- A real Visual Studio build in two folders gives code and data sections equal byte for byte (link stamp
  masked). So the folder never reaches the runner, and a kept runner is the one a fresh build makes.
- Proven: 600 verdicts equal before and after (docs/proofs, proofs 5 and 6).

The raw judge has the same repeat: the official raw judge (`core/job.py:345`) rebuilds the work.c the agent's
`check.py` built last. That is about 0.9 h PS2, 0.3 h TC and 0.2 h Spawn. **Not built**: `core/check.py` is a
named judge file. The same design applies once F1 is proven.

---

### 2. Oracle stamp scan: 54 shared files hashed again for every function — FIXED (10314a8)

**Evidence.**
- `oracle_fresh.scan()` called `Stamper.today(addr)` for every matched function. `today()` ran `file_hash()`
  on every `*.java` script, on `TcInputs.java` and on the catalog: 54 `stat()` plus `resolve()` calls per
  function. cProfile: 18 ms per function.
- `wave.log`: every re-stamp chunk spent 28–83 s before its "stamps: N not current" line. Two TC chunks were
  guard-killed after 27 s and 28 s, before the scan had even finished.

**Measured saving.** Old code (from `git show`) against new, same inputs, read-only:

| Workspace | Functions | Old scan | New scan | Differences |
|-----------|-----------|----------|----------|-------------|
| PS2 | 6,580 | 136.5 s | 0.6 s | 0 |
| TC | 2,702 | 45.7 s | 0.8 s | 0 |
| Spawn | 1,908 | 29.0 s | 0.2 s | 0 |
| Grandia | — | 1.4 s | ~0 s | 0 |
| SkiesOfArcadia | — | 0.4 s | ~0 s | 0 |

One scan runs per wave chunk (PS2 about 23 chunks), so about 1 h of machine-wide Ghidra HOLD per full wave. The
wave's `dry`, `fresh_oracle`, `final_audit`, `prepare_list` and `idle_prepare` gain the same way.

**Fix.** `oracle_stamp.common()` hashes the shared inputs once. `stamp_of(..., shared=)` continues that sha256
with the per-function lines in the same order. A prepare still computes its stamp exactly as before.

**Byte-identical: yes.** The same sha256 over the same byte stream; 0 differences on 11,414 functions; test
`test_batch19::FreshStamp::test_shared_inputs_give_the_same_stamp`.

---

### 3. Regress copies every project again when the mode switches — FIXED (7bdd400), proven (proof 3)

**Evidence.**
- `tools/regress_project.py:133` marks the private master `LIVESTAMP` on a gate run and
  `LIVESTAMP:catalog:SCRIPTS` on a catalog run. `--targeted` always adds `--catalog` (`regress.py:246`).
- So a gate after a `--targeted` run (or the reverse) threw away every private master and its slot copies and
  copied them again, with nothing changed in the live project.
- `tests/ghidra_work` holds 12 GB: about 105 MB master per SST project, plus slots. Both mark kinds are on disk
  side by side (for example sst00 catalog-mode, gend plain).
- Reasoned at about 80 s per project. **Measured** (docs/proofs): the copy is 0–1 s; the repeat that costs is the catalog step, 47–78 s on sst00 (median about 52 s), about 21 min across the 24 SST projects.

**Fix.**
- Each mode has its own folders: `master`/`slots` and `master_catalog`/`slots_catalog`. A mode switch now
  behaves exactly like two runs of the same mode in a row, which reuse their copy today.
- The catalog step's files (catalog.json, code_ranges.txt, function_bodies.txt) are kept with their master in
  `catalog_out/` and put back into the shared work state when that master is reused.
- **A bug found on the way and fixed by this:** before, a reused catalog master after a plain run (or a
  `needs_catalog` fixture, whose setup copies the live catalog first) could skip the catalog step and judge with
  the live catalog.

**Byte-identical.** `tests/test_regress_copies.py`: plain, catalog, plain, catalog copies each mode once. The
reused catalog master gets its own files back. Changed scripts or a changed live catalog copy again. A kept
catalog that is missing or stale runs the step again.
- Proven (Ghidra): 9 sst00 runs give equal verdicts, and the catalog step is what the kept copies save (proof 3).
- Cost: up to about 12 GB more disk under `tests/ghidra_work` once both modes exist everywhere.

---

### 4. Draft cycle runs the raw judge twice on the same inputs — FIXED (41a02a1)

**Evidence.** `fn_claim.cycle` runs `draft_run.first` (`core/draft_run.py:184`: `refine.harness_stale`, a full
`check.run_check`), then `refine.refine` (`core/refine.py:202`), which runs the same check again in the same
process under the same claim. 0.79–0.83 s each.

**Saving (measured per judge).** About 33 min PS2, 6 min TC and 6 min Spawn per readable pass.

**Fix.** `draft_run.first` calls `refine.harness_stale_kept`. It hashes every input the judge reads (the
`core/verdict_memo` key: the C, every harness file, the packet asm, fr0 list, code ranges, msvc env, config,
all of core and the target) before judging, and keeps the key on a PASS. `refine.refine` takes that PASS once,
only while the key is the same. A FAIL is never kept. Every other caller is unchanged.

**Byte-identical: yes.** `tests/test_verdict_memo.py::DraftCycle`: same inputs judge once; each changed input
judges twice; a FAIL judges twice; a kept PASS is used once. Key cost: about 0.05 s.

---

### 5. The raw C's out-pointer runs are cached per build folder — FIXED on waste-final (a4efcdb; size probe 5596ba5), the user's yes

**Evidence.** `readable_outrun.RawRuns` cached its trace and painted runs in
`build/ADDR/<folder>_orig_runs.json`. The key was content-based (`core/readable_outrun.py:32`), but the file was
per folder. So the official judge, verify and sync each built and ran the matched raw C again on vectors another
folder had just run. About 7 s per out-pointer function: about 23 min PS2 and 8 min TC per pass, plus about
13 min per PS2 verify.

**Fix.** One file per function (`build/ADDR/raw_runs.json`), written whole. The key gains everything a run reads
that the old key lacked: the thunk (harness meta), the runtime's content (the old key used mtimes), the link
options, the compiler environment, the build and run code, the binary, the code ranges and the FSCA table.

**Byte-identical.** `tests/test_raw_runs_cache.py`: a run made in one folder is taken in another with no build
and no run; each of 11 inputs gives another key; failed runs are not cached. Proven: 600 verdicts equal (docs/proofs, proofs 5 and 6).

---

### 6. The readable judge compiles its runtime on every check — FIXED on waste-final (6680db3), the user's yes

**Evidence.** `readable_check.build` compiled all 7 `runtime_rd` sources on every check. Measured 1.82–2.09 s
per check (TC, 3 runs). The raw judge already caches its runtime (`check.runtime_objs`). The PCH pass is not
worth caching (0.1 s, 2.9 MB).

**Fix.** `runtime_rd_objs` compiles the runtime once per content, keyed by the compiler, INCLUDE, `C_FLAGS` and
every file of `runtime_rd/` and `runtime/`. Built with `/showIncludes`. It is not used when a game include folder
holds a file named like a header the runtime opened.

**Also fixed there: the `LNK1181` race.** `check.runtime_objs` used to `rmtree` a cache set another build had
just completed. Both caches now replace a set only while it is incomplete, under a lock.

**Byte-identical.** A real Visual Studio build with and without the cache gives byte-equal code and data
sections (link stamp masked) and the same listing; 157 related tests pass. Proven: 600 verdicts equal (docs/proofs, proofs 5 and 6).

---

### 7. Live prepares start a cold JVM; the slot server is wave-only — slot order FIXED (7735605), proven; switch proposed

**Evidence.**
- `ghidra_serve` is `enabled: False` by default; only the wave turns it on. `slot()` took `free[0]`, the
  master, which never has a server.
- `prepare_timing.jsonl`: Grandia 124 prepares, none served, 772 s of 1,192 s in JVM start and project open.
  Spawn 156, none served, about 1,091 s of 4,586 s. Median 7.4–10.95 s cold against 0.80–1.09 s served.
- Ledger slot waits: Spawn 4,900 s plus 2,111 s of prefetch waits; Grandia 2,229 s plus 1,537 s.

**Fix.** `ghidra._order` prefers a free slot whose server is up, then other copies, the master last. The master
goes first once 50 marks wait, so marks are still written in batches. With servers off nothing changes.
**The switch:** turning `ghidra_serve` on for live runs is one config line, `docs/proposals/ghidra_serve_on.patch`
(3b6d386), for the user once RAM allows (one idle JVM per served slot for up to 120 s). It must land after the
broker fix ddcdbd9 (finding 11): with servers on and without that fix, a waiting owner's servers can deadlock the
machine-wide queue.
With that fix a waiting worker gives its server back whenever another session's ticket is ahead, so the saving
comes only while the queue is free: in proof 4's third run, with other sessions queuing throughout, all 200
prepares started a cold JVM.

**Byte-identical: yes.** Served prepares were proven identical to fresh ones (r15: TC 500/500, PS2 1000/1000,
GameCube 97/97). Proven live: 50 TC prepares, 353 files byte-equal (docs/proofs, proof 2).

---

### 8. Wave RAM guard kills chunks instead of braking — FIXED (1a8bb09), output proven

**Evidence.** `wave.py` checks RAM only at a chunk's start. Inside a chunk, `reprepare_stale` started prepares
whatever the RAM. `wave.log` (2026-10-21, 19:14–21:59): 8 guard kills in 9 chunks. Each kill lost the in-flight
prepares, the chunk's scan, the warm JVMs and 50 s to 28 min of waiting.

**Fix.** `reprepare_stale --brake 85` (the wave passes it). At 85% or more, a prepare starts only while no other
runs, so a chunk slows instead of being killed, and never stalls. The 88% hard kill stays as the safety gate.

**Byte-identical: yes.** Only when a prepare starts changes. `tests/test_wave.py::Brake` (4 tests). Proven on
PS2 (proof 4, run 3): 100 re-prepares with and without `--brake 85` rewrite the same 712 files, byte-equal, and the
run stays within its 2 granted slots. Not seen live: RAM sat in the brake's band (85–88%) for 6 s of 3,203 s, so
how many kills it turns into pauses is still reasoned, not measured. Proof 4's second run found finding 11.

---

### 9. Wave step 5 re-judges what step 4 just judged — FIXED (2da1869)

**Evidence.** Step 4's re-stamp already runs `check.run_check` on each re-prepared function's matched C
(`refine.harness_stale`). Step 5 then ran `oracle_fresh.recount` on the listed fmac functions: the same fact on
the same inputs in one run. 654 functions, about 6.5 min.

**Fix.** Step 4 records each PASS with the `core/verdict_memo` key of every input the judge reads. Step 5 takes
a PASS only while its key matches, and judges everything else (a FAIL is always judged again).
`final_audit` and `fresh_oracle` never pass a memo.

**Byte-identical: yes.** `tests/test_verdict_memo.py`: any changed input (C, harness file, new harness file,
packet, fr0 list, config, judge code) judges again.

---

### 10. Small repeats and manual steps

Fixed:

| Commit | Where | Repeat | Saving | Proof |
|--------|-------|--------|--------|-------|
| c309426 | `core/readable.py:87` | `names.count(n)` per name, O(n²), on every Registry build | 112.9 ms → 0.40 ms per build (PS2); about 12 min per PS2 pass | old vs new on PS2 (4,274) and TC (3,712): equal |
| dda1aec | `core/ghidra.py` `_prepare` | meta.json written, read back and written again for the stamp | one JSON read and write per prepare | old two-write bytes equal new one-write bytes |
| 4d22a2d | `ghidra.export` | whole project copied again per call (TC 46 MB, PS2 123 MB); `sdk_ref` calls it per 5 names | measured 0.04 s per call (negligible) | **proven**: 20 TC exports byte-equal |
| f1cb292 | after a wave | operator ran `requeue.py @fmac-flips-KEY.txt` per workspace by hand | the hand step | `wave.py run --requeue`, opt-in |
| 244109d | harness_stale rows (PS2: 66) | operator copied them into a file for `prepare_list` | the hand step | `prepare_list --harness-stale` |

Fixed on waste-final (the user's yes): `readable_outptr.type_sizes` probed again for every build folder and run
(PS2 101 s over 475 folders); 5596ba5 keeps it per function by all the probe reads (finding 5, proof 6).

Left as they are:

| Where | Why |
|-------|-----|
| Each worktree copies the Ghidra projects again for regress | The private copy is what keeps two worktrees' gates from writing one master (prepares on slot 0 write marks). Sharing it needs a cross-worktree lock that would serialize gates; hard links are unsafe because Ghidra writes in place. |
| `write_facts` runs twice per regress group (stamp, then prepare) | negligible: under 1 ms (one cached file, `utime`); changing it means changing `prepare`'s signature |
| `raw_names.find` in both sync and the verify report | negligible: sub-second per run, on different callers |
| `global_extents` probes once per process | negligible: one compile of 0.11 s per run (measured on TC's 926 and PS2's 1,416 globals, 2026-10-23) |
| `paths.fsca_table()` hashes the 1 MB table on every call | about 2 ms, and it is an identity guard (it rewrites a stale table); left |
| `readable_verify` re-judges every file | an evidence gate across runs; F1, F5 and F6 make each check cheaper instead |
| The PCH `/Zs` pass, per-attempt tamper hashing, verify's second judge of a failure, regress preparing afresh | gates, not repeats |

---

### 11. A served slot's waiter keeps its JVM and deadlocks the broker — FIXED (ddcdbd9)

Found by proof 4's second run (2026-10-23): the shared broker granted nothing for about 40 minutes, with 3 of this
run's slot servers up and 10 tickets of other sessions waiting.

**Cause.** `ghidra.slot` reserves a slot and then waits for a machine-wide token. A slot server's JVM counts
against every waiter's limit (`in_use` counts every Ghidra JVM) but holds no token, and the server's idle reaper
cannot close it while its slot is reserved. A waiter whose server is up (`jvm_up`) is granted only at the head of
the queue. With another process's ticket at the head that needs a JVM, and the owner's idle servers filling the
limit, nothing could ever be granted. `jvm_up` predates F7; served slots (F7, and the serve switch) make it common.

**Fix.** `ghidra_broker.token(give_way=...)`: once another ticket is ahead of a `jvm_up` waiter, it closes that
slot's server (outside the lock) and waits on as a ticket that starts a JVM. `ghidra.slot` passes
`ghidra_server.stop(i)`. A server's JVM is so either in use under its owner's token, or given back.

**Byte-identical: yes.** Only when a server closes changes; a closed server's next prepare starts cold, which proof 2
shows is byte-equal. `tests/test_ghidra_broker.py`: the deadlock reproduced (no grant until the stop flags), and with
`give_way` the head ticket is granted, then the owner's; `ghidra.slot` wires its own server's stop.

### 12. Broker files from before a restart — FIXED (ddcdbd9)

**Evidence.** After the restart of 2026-10-23 10:01 the broker folder held tickets and holds of four dead PIDs from
02:41. `_reap` removes a file whose PID is dead, or whose PID's process started after the file. Windows reuses PIDs
after a restart, and a process's start time cannot always be read (another user's process): such a file stayed.
These were reaped by the next waiter; none was left by the time this was checked.

**Fix.** `_reap` also removes every file from before the machine's boot time (`psutil.boot_time()`).
`tests/test_ghidra_broker.py::test_before_restart`: a file from before the boot is reaped, a newer one is kept.

### 13. A copy's thread holds a token while it waits for the master — FIXED (d7e541b)

**Evidence (2026-10-23, 11:05–11:17).** Three holds, no JVM on the machine, 9 tickets waiting. Two `prepare_list`
runs held them: Dragon Force 2's ws-btl (`--slots 3`, no Ghidra project yet) two, Skies (`--slots 2`) one. Each
also had a ticket waiting: its master's thread.

**Cause.** `ghidra.slot` took the machine-wide token, then, for a stale or missing read-only copy, waited for the
master (slot 0) to copy it. The master's own thread had reserved slot 0 and waited in the queue for a token. Holds
count against the limit until their JVM starts, so 3 such holds at 80% RAM (limit 3) stop every session's queue.
Not caused by ddcdbd9: neither run had it.

**Fix.** The copy is refreshed under `slot_master` before the token, holding none. If the master changed while the
token was awaited, the copy is refreshed under the token only when the master is free (no wait); when it is busy,
the token is given back and the copy refreshed again without one. The stuck runs freed their slots when their
owners stopped them; the fix reaches a checkout when it merges this branch.

**Byte-identical: yes.** The copy and what is prepared on it are unchanged; only the order of the waits changes.
`tests/test_ghidra_broker.py::Broker::test_copy_refreshed_without_a_token` reproduces it with the real broker (before
the fix the master's thread waits until its stop flag); `Wiring::test_copy_stale_again_under_the_token` covers both
branches.

### 14. A held process queues, and a test run queues in the machine's folder — FIXED (262283a)

**Evidence (2026-10-23, 12:42 on).** 8 tickets waiting, 0 holds, 0 JVMs, RAM 72%. The head ticket was a
`pytest tests -x` run's (another session's), from `test_fsca_rm::PrepareHeals::test_cause_named`, which prepares
through the real config, so the real broker folder. That folder has a HOLD with a pass (tools/wave.py's) since
2026-10-21. The pytest process had no `DP_BROKER_PASS`, so it could never be granted; at the head of the queue it
kept every ticket behind it waiting, the pass holders' too, until its 30-minute timeout.

**Cause.** Two defects. (1) A process `held()` stops still filed its ticket; the queue grants only its head, so a
held head stops everyone. (2) The test suite used the machine's broker folder: nothing gave tests their own.

**Fix.** (1) A held process waits outside the queue, with no ticket, and says so once on stderr; when the HOLD goes
or names its pass, it files its ticket under its first name (its place). It keeps waiting rather than failing: a
HOLD is a pause (`tools/wave.py` holds and releases), and refusing would fail every other run mid-way for each wave.
(2) `DP_BROKER_DIR` names the folder when the config does not; `tests/conftest.py` (pytest) and `tests/test_sync.py`
(unittest) set it to a folder of the suite's own. `test_cause_named` turns the broker off (Ghidra never runs in it),
so integration's runner need not leave it out any more.

**Byte-identical: yes.** Who waits where changes, not what is granted to whom under no HOLD.
`tests/test_ghidra_broker.py::EveryStart::test_held_waits_outside_the_queue`: a worker without the pass files no
ticket (before: it did), a pass holder in another process is granted at once, and the worker gets its token when the
HOLD goes. `test_suite_folder`: the suite's folder is not the machine's.

---

## Repeats on unchanged inputs, per flow

| Flow | Repeat | Time each | Reuse key | Per wave / pass / game | Status |
|------|--------|-----------|-----------|------------------------|--------|
| prepare | cold JVM start and project open | 6–7 s | the served slot (r15-proven) | 13–18 min per long live run | F7 code fixed; switch proposed |
| prepare | meta.json written twice | ms | — | — | fixed (dda1aec) |
| judge (raw) | draft check, then refine's check | 0.8 s | `verdict_memo` key | about 45 min per pass | fixed (41a02a1) |
| judge (raw) | official judge rebuilds the agent's last work.c | 0.34 s (measured, TC median) | build-input key (as F1) | about 0.5–1 h per pass (reasoned from 0.34 s; the audit said 1.4 h from 0.5–1 s) | fixed on waste-final (c2f885e) |
| readable | official judge rebuilds the agent's runner | 2.2–3.2 s | `_runner_key` | about 4.4 h per pass | judge branch (F1) |
| readable | runtime compiled per check | 1.8–2.1 s | runtime content key | per check | judge branch (F6) |
| readable | duplicate-name check O(n²) | 0.11 s | — | about 12 min PS2 | fixed (c309426) |
| verify | raw out-pointer runs again per folder | about 7 s per function | extended run key | about 13 min per PS2 verify | judge branch (F5) |
| wave | stamp scan hashes 54 files per function | 18 ms per function | `oracle_stamp.common` | about 1 h | fixed (10314a8) |
| wave | step 5 re-judges step 4's PASS | 0.6 s | `verdict_memo` key | about 6.5 min | fixed (2da1869) |
| wave | killed chunks redo their scan and in-flight prepares | 50 s – 28 min per kill | — | 8 kills in 9 chunks | fixed (1a8bb09) |
| regress | project copied again on a mode switch | catalog step 47–78 s per project (measured) | per-mode copy + live stamp | about 21 min per `--targeted` run after a gate | fixed (7bdd400) |
| export | project copied per call | 0.04 s (measured) | slot 1's copy mark | per 5 names | fixed (4d22a2d) |

---

## Every console's toolset, with Too Human (Xbox 360) as the baseline

| | Dreamcast (`dreamcast_sh4`) | Saturn (`saturn_sh2`) | GameCube (`gamecube_ppc`) | Xbox 360 |
|---|---|---|---|---|
| Ghidra language | patched SuperH4 (tcfix) | stock SH-2 | stock Gekko | — |
| Ghidra scripts | 49 | 50 (adds TcEmu) | 12 + TcFixups, `ppc_*`, `hooks.py`, `ppc_match` | — |
| Readable stage (`runtime_rd`) | yes | **no** | **no** (`readable_image_base` is a dead key) | — |
| Second emulator cross-check | xemu SH-4 + `xemu_check` | **no** | **no** | — |
| Port (`port/`) | yes | no | no | — |
| `harness_reuse` | off until after the wave | off | on | — |
| Regress fixtures (`tests/patterns.json`) | yes | **none** | **none** | — |

Gaps:
- **GameCube** lacks the reach and summary passes (TcReach, TcCfg, TcRepair, TcSum; `reach_calls`,
  `param_summaries`), TcHw and TcSpin, and the SH-4-asm lint modules (`param_unread`, `frame_args` and others;
  only `uninit_stack` is stubbed). It refuses `ps_*` (paired singles), `fres` and `frsqrte`.
- **Saturn** refuses `mac.l`/`mac.w` and supports the master SH-2 only. Its blocker list names FPU kinds an SH-2
  cannot have.
- **Xbox 360** has no target in DP. README:8 mentions one. `<projects>\DecompPipeline-xbox360` (branch
  xbox360-ppc) holds an untracked `targets/xbox360_ppc` with only `tools/` (`xex.py`, `ppcscan.py` refusing VMX128,
  `catalog_inputs.py`, `aes.py`), dated 2026-10-22: no `target.json`, scripts, runtime, Ghidra language, README,
  `REFERENCES.md` or `DESIGN.md`. `<projects>\TooHuman` holds the ISO and `xexmagic`. To be the baseline it
  needs the full set: target.json, a PPC64/VMX128 language or Ghidra's, the Tc* scripts, a runtime, a readable
  stage, regress fixtures and a second-emulator check.

---

## Disk (report only; nothing deleted)

| Folder | Size | Note |
|--------|------|------|
| `<projects>\DecompPipeline_stage31` | 17 GB | old stage checkout (2026-10-15) |
| `<projects>\DecompPipeline_stage_scope` | 12 GB | 12 GB of it `tests/` |
| `<projects>\DecompPipeline\tests\ghidra_work` | 12 GB | regress private copies (F3 can add up to 12 GB) |
| `<projects>\DecompPipeline-r15-phased` | 6.7 GB | 6.2 GB `tests/ghidra_work` |
| `<projects>\DecompPipeline_stage29` | 6.4 GB | old stage checkout |
| `<projects>\DecompPipeline_stage` | not measured | `du` did not finish within its bound |
| Other DP worktrees (ctxsize, gamecube, lane2-*, spawnboot, stage30) | 2.8–5.5 MB each | small |
| TC `harness/` | 2.9 GB | |
| TC `state/`, `backup/`, `ghidra_slots/` | 774 MB, 507 MB, 391 MB | |
| TC `export_copy/`, `probe_copy/`, `retired/` | 46 MB, 44 MB, 38 MB | export_copy is now reused (4d22a2d) |
| PS2 `state/`, `ghidra_slots/` | 821 MB, 730 MB | `harness/`, `build/`, `runs/` not measured |
| PS2 `ghidra/`, `export_copy/` | 123 MB, 123 MB | export_copy dated 2026-10-20 |
| Spawn `harness/` | 1.9 GB | |
| Spawn `state/`, `runs/`, `backup/` | 327 MB, 267 MB, 257 MB | |

The old stage checkouts and r15-phased hold about 42 GB that no current flow reads. Deleting them is the
user's call.

---

## Proofs (all run; `docs/proofs/2026-10-22/PROOFS.md`)

1. **Export copy (4d22a2d):** 20 TC exports byte-equal; saving 0.04 s a call.
2. **Served slots, live (7735605):** 50 TC prepares, 353 files byte-equal; 1.4 s against 10.7 s a prepare.
3. **Regress copies (7bdd400):** 9 sst00 runs, equal verdicts; the catalog step is what the kept copies save.
4. **Wave brake (1a8bb09):** 100 PS2 re-prepares with and without the brake, 712 files byte-equal, within 2 slots;
   the brake's band was not reached (6 s), so its kill count is reasoned. Run 2 found finding 11 (fixed ddcdbd9).
5. **Judge set (6680db3, ed6b23f, a4efcdb):** `refine.py verify` over 300 sampled files each of TC and PS2: 600
   verdicts equal field by field, cold and warm.
6. **Final tree (c2f885e, 5596ba5):** the same 600 verdicts; warm verify 201.9 s against 375.3 s (TC), 193.0 s
   against 453.0 s (PS2).

## Categories with nothing found

- **Fixed sleeps: none found.** Every wait is on a condition or a poll (`ghidra.slot` on a Condition, the broker
  every 0.25 s, the wave hold every 60 s, the RAM guard every 1 s).
- **Scripts that duplicate a command: none found.** `mirror_ptrs` and `ptr_views` run in opposite directions;
  `idle_prepare` and `prepare_list` overlap by design.
- **Harnesses re-prepared while their stamp was current: none found** in the stamp tools. The one known case,
  `harness_reuse` switched off for Dreamcast until after the wave, is planned in HANDOFF.
- **Shared prompt prefix: not waste.** In 100 Spawn transcripts with `shared_prefix`, 0% of attempts read
  `tc_rt.h` again.

## Standing rule (drafted, not landed)

`docs/proposals/efficiency_pass_rule.patch` adds a `CLAUDE.md` with "Before you hand off: one efficiency pass". It
asks one bounded question before every hand-off: "could the same result have been reached with fewer steps or less
time?" It uses only the task's own timings and logs, gives at most 3 findings with evidence, and fixes only small,
provably identical ones. Apply it with `git apply docs/proposals/efficiency_pass_rule.patch` on the user's go.

## Other notes

- **Security.** HANDOFF line 1097: `msvc_env.json` captured the broker pass key. Not waste, but the environment
  capture should drop that variable (a separate fix).
- **Test that waits on the live broker.** `tests/test_fsca_rm.py::PrepareHeals::test_cause_named` calls
  `ghidra.prepare` without a broker mock, so it waits for a real token while another session holds the slots
  (it timed out here). It is not related to these changes.
