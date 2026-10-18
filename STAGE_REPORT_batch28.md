# Stage report: batch 28

**Verdict: READY TO DEPLOY.**
- Item 1 is fixed generically: call targets from the inputs are invalid vectors (oracle), and the readable runner
  reports the function under test's own address as its guest address.
- Item 2 was already done in batch 2c.
- Each fix has a pass fixture and a fail control.
- tests/test_tooling.py: 240/240 OK.
- Gate PASS: 393 items. Only the 8 known Spawn samples fail.
- Every harness becomes stale (Java change): TC and Spawn need `reprepare_stale.py --stamps`.

Brief: the coordinator's tooling batch 28 message (items 1 and 2). Work: .work/b28/:
- tc: the TC copy (pilot harnesses whole, the rest meta + summary only). Its Ghidra project is
  tests/ghidra_work/b28tc/ghidra (project.json `ghidra_dir`).
- before/: the live harnesses of the pilot functions; fx/: fixture drafts.
- scripts/: scratch drivers (scan_targets, pilot, judge); m_*.txt / m_*.tsv: measurements.

## Item 2: fr12..fr14 as a vector accumulator: already done

Batch 2c (deployed 2026-10-11 09:09) made fr12..fr15 runtime state:
- random at entry (line H);
- compared at exit (FH) and at calls of functions that read them;
- readable C uses `sh4_fr`/`sh4_set_fr`.

Today 66 TC harnesses use it. 65 are readable; the 66th is engine-blocker 8c07f4b8. No TC function is skipped for it:
the ledger's 3 skipped and 1 failed are the four engine blockers. Nothing to change. The "Batch-2 candidates" bullet in
PROGRESS.md is out of date.

## Item 1: guest function pointers used as data, and random pointers

### Scan (before any fix)

Mocked calls whose target the original's run reached, that are outside guest RAM (0x8c010000..0x8d000000) and no
callee constant (expected.txt CALL lines against meta callees):

| workspace | functions | with far targets (>= 0x10000) | vectors with far targets |
|---|---|---|---|
| TC | 86 | 78 | 1594 |
| Spawn main | 30 | 28 | 447 |
| Spawn ovl | 100 | 97 | 1916 |
| Spawn gend | 31 | 31 | 910 |
| sstex01..06 | 3 each | 3 each | 154..170 each |
| sstma01 / 05 / 07 / 08 / 09 | 8 / 1 / 7 / 3 / 4 | 7 / 1 / 7 / 3 / 4 | 322 / 65 / 230 / 110 / 184 |

Functions that load their own entry address as a value: TC 1 (8c06dc28), Spawn 0.

**Why this matters.**
- These targets are random input words: a function pointer read from input memory, an argument register, or a
  scripted call result.
- In the raw judge they are harmless, because tc_call logs any target.
- In the 32-bit readable runner, guest RAM is the only memory that traps calls. A random word can be a mapped host
  DLL's code: system DLLs sit near 0x75xxxxxx-0x77xxxxxx, and their bases change with every boot.
- The readable function then runs that code natively and crashes. Seen on 8c08d48c (0x76386e41) and on 8c01e97c
  (0x75edf1fa, 8 attempts).
- So a readable file that passes today can fail after a reboot, and the judge is flaky for every function in the table.

### Fix 1: call targets from the inputs are invalid vectors (oracle, TcCalls.java)

**Rule.** A mocked call to a target that is not NULL, outside guest RAM, and no constant of the body is an invalid
input. On the Dreamcast that address has no memory, so the game would crash on such an input.
- `TcOracle.runOracle` checks every call (`TcCalls.fromInputs`) before logging it. On a hit, the run ends as a
  `callFault`.
- `TcVectors` repairs it like a memory fault (`TcCalls.repair`, within MAX_REPAIRS). It looks for the word that held
  the target, in this order:
  1. the latest logged 4-byte load of exactly that value (a RAM word the vector may set);
  2. else an entry register (r4..r7, the low or GBR-relative entry registers);
  3. else a scripted call result the run used.
- That word becomes a fresh chunk pointer: data in RAM. The oracle never runs a mocked callee, and the readable runner
  traps and logs a call there.
- Exact matches only, since a small target equals many unrelated words.
- If no word matches, the vector stays invalid.

**Kept unchanged.**
- NULL: a pointer legitimately holds it, and both judges trap a call to it.
- Constant targets (body pool values and references).
- `inputJump` (non-call jumps), which now shares the body-constant set (`bodyConsts()`).

**Generic.** It is target-level (TcOracle, TcVectors), so it applies to every project and every game's TcInputs. No
per-function hint is used.

**Cost: every harness is stale.** Any Java change changes the oracle stamp (core/oracle_stamp hashes every script).
TC needs `tools/reprepare_stale.py --stamps` before its next final audit (about 3.5 min per 100 harnesses), and Spawn
when its audit runs. Only the functions in the scan table get different vectors. For the rest, re-preparing gives the
same vectors under the new stamp.

### Fix 2: the function under test's own address (runtime_rd)

8c06dc28 (`Lib_MovieInstallUserHooks`) stores its own address in the player's hook table (+0x34).
- In the verification game.h, every other function's name is a pointer to its guest address.
- The function under test is compiled, not mocked, so its name is a host address.
- The readable file stored 0x5a.... instead of 0x8c06dc28, and 96/96 vectors differed at that word.

The fix:
- core/readable_abi.thunk_c emits `rd_set_self((u32)(uintptr_t)&NAME, 0xADDR)` before the call.
- runtime_rd reports that host address as the guest one wherever it leaves the function:
  - rd_mem.c tcrt_report: the result (RET), and changed words of guest memory;
  - rd_veh.c log_call: call arguments (r4..r7, stack words, r0..r3).
- rd_abi_reset clears it per vector.

The raw runtime is unchanged: raw C already uses guest addresses.

### R8 (8c01c0e8, 8c01e97c): rechecked

Both were resolved before this batch: TcOracle treats a read of machine code as a fault and repairs the pointer. Both
are readable, and both pass on the batch 28 harness (pilot below). 8c01e97c also had the far call target class: before,
it had 56 far and 46 low targets; after, 0 and 0.

## Pilots (TC copy, staged pipeline, re-prepare then judge)

Pilot 1 had 16 functions and pilot 2 had 60: the 78 affected TC functions minus engine-blocker 8c08d638, plus
8c08d48c, which was done separately. For each, the scripts re-prepared the harness with the staged oracle, then judged
the matched raw C and the readable file (scripts/pilot.py; m_pilot1.tsv, m_pilot2.tsv).

- **Call targets:** far and low (non-NULL) targets went to 0 in all 77 harnesses, for example:
  - 8c08d48c: 29 far and 33 low before, 0 and 0 after;
  - 8c06ead6: 177 far before, 0 after.
  - NULL targets are kept.
  - Valid vectors are still 48 + 48 everywhere they were before. The one exception, 8c053bde (24/20 before, 17/18
    after), is a large function.
- **Matched raw C:** 76 of 77 pass 96/96. 8c0633cc fails 1/96: see the follow-ups (unaligned pointer, not a call
  target).
- **Readable files:** all pass except 4.
  - 8c0633cc: the same vector.
  - 3 that also fail on live, before any change:
    - 8c07b0c8: signature drift;
    - 8c097534 and 8c09774c: R3 lint, because the files still use the raw names the registry has since replaced.
      `refine.py sync-names` or `verify` fixes this.
- **R8:** 8c01c0e8 and 8c01e97c pass, both raw and readable.
- **Coverage (blocks hit):** 971 before, 921 after, out of 1259 blocks; 4 functions went up, 5 went down.
  - The losses are mostly 8c06419c (84 to 41 of 114) and 8c03d8c8 (50 to 40 of 118).
  - Re-preparing both with the live pipeline on the copy reproduces the old harnesses exactly. On 8c06419c, the first
    11 vectors are identical; from the first repaired vector on, every vector differs, because the repair draws from
    the random sequence.
  - The old deep paths came from 2 of 96 vectors (41 and 59 blocks). This is random variance in which vectors are
    drawn, not a path the new rule forbids.
  - Weak coverage on such functions is an existing property of the input generator.
- **Prepare time:** unchanged for most functions (12 to 70 s). 8c065d24 took 771 s, and the gate turned up 8c04af62,
  which runs past 585 s. Live is just as slow on 8c04af62 (also killed at 585 s), so this predates the batch.

## Tests

- tests/test_batch28.py (3 tests, imported by test_tooling.py). It checks that thunk_c names the function under test
  with its own guest address, before the call, and that the runtime maps the result, stored words and call records.
  TcCalls is a Ghidra script; the fixtures judge it end to end.
- `python tools/bounded.py 900 python -W error tests/test_tooling.py`: 240/240 OK.
- tests/patterns.json, 2 new fixtures (both deterministic):
  - `tc-call-target-8c08d48c` (raw):
    - pass: the matched C that makes the hook call only for a NULL or in-RAM target. PASS 96/96 on the batch 28
      harness; FAIL 62/96 on the batch 27 harness.
    - fail control: also skips the NULL calls. FAIL 21/96, which shows NULL is kept.
  - `rds-self-addr-8c06dc28` (readable-src, names dict):
    - pass: stores `Lib_MovieInstallUserHooks` at +0x34. PASS 96/96.
    - fail control: stores `Lib_MovieAudioShutdown` there. FAIL 96/96.
  - `regress.py --projects tc --only ...`: 4 items, 0 unexpected.

## Gate

`python tools/bounded.py 7200 python tools/regress.py --projects all --sample 15`, run in bounded foreground chunks
(gate/*.log, *.json). **Result: PASS.**

| chunk | items | unexpected |
|---|---|---|
| tc: fixtures `^rds?-` | 74 | 4, then 0 after the fixture update below |
| tc: fixtures `^tc-[a-o]` | 46 | 0 |
| tc: fixtures `^tc-[p-z]` (includes tc-call-target-8c08d48c) | 47 | 0 |
| tc: the gate's 15-function sample (`--list` gate/tc_sample15.txt; seed `1:tc`, fixture functions skipped; same 15 as batch 27) | 15 | 0 |
| sp, ovl | 43 | 0 |
| gend, sst00, sstch01 | 27 | 4: the known gend 8c565534, 8c565868, 8c5661fc, 8c56643c |
| sstch02..05, sstex01..06 | 58 | 0 |
| sstma01..06 | 35 | 0 |
| sstma07 | 12 | 2: the known 8c9c8f80, 8c9c9100 |
| sstma08..12 | 36 | 2: the known sstma08 8c9c89a0, sstma09 8c9c92c0 |

Total: 393 items (batch 27 had 389; the 4 new ones are this batch's fixtures). 8 are unexpected, all of them known.

**Fixture update (TC drift, not batch 28).** At 18:01 today, `raw_ret` overrides were applied to TC 8c0268a2 and
8c05cc40, changing them from void to u32 (S09.json, S17.json). After that, `rds-callee-rstate-8c0268a2` and
`rds-callee-storage-8c05cc40` failed lint with "signature must be exactly u32 ...", on live as well. Their 4 C files
now return the callee's value, and each control keeps its one change. Rerun: 4 items, 0 unexpected.

## Deploy list

New:
- targets/dreamcast_sh4/ghidra_scripts/TcCalls.java
- tests/test_batch28.py
- tests/fixtures/tc-call-target-8c08d48c.{pass,fail}.c
- tests/fixtures/tc-rds-self-addr-8c06dc28.{pass,fail}.c

Changed:
- targets/dreamcast_sh4/ghidra_scripts/TcOracle.java, TcVectors.java
- targets/dreamcast_sh4/target.json (`TcCalls.java` in locked_scripts)
- targets/dreamcast_sh4/runtime_rd/rd_veh.c, rd_mem.c, rd_internal.h
- core/readable_abi.py
- tests/patterns.json, tests/test_tooling.py
- tests/fixtures/tc-rds-callee-rstate-8c0268a2.{pass,fail}.c, tc-rds-callee-storage-8c05cc40.{pass,fail}.c (TC drift)
- README.md (one row), targets/dreamcast_sh4/README.md (Known limits: R8, call targets; Readable C: own address)

Every file is 400 lines or fewer, and line endings match live.

## After deploy (TC, servers stopped)

1. Re-prepare the affected TC functions except engine-blocker 8c08d638; judge their matched C and requeue failures.
2. `refine.py verify` their readable files.
3. 8c06dc28 and 8c08d48c: re-judge the existing passing attempts and adopt them, or else `refine.py run --redo`.
4. Full re-stamp: `tools/reprepare_stale.py --stamps` (all harnesses are non-current after the Java change).

## Post-deploy on TC (done 2026-10-15 22:25)

- Deployed at 20:10 (backup/batch28-20261015-201053); live tests 240/240, fixtures 8 items 0 unexpected. Batch 29
  (engine-blockers) deployed after it at 21:59 and kept these changes.
- Re-prepared 76 live TC harnesses (the 78 affected minus 8c08d638 and 8c0633cc) under ghidra-heavy, in Ghidra slots
  (scratch .work/b28/scripts/post_tc.py, the reprepare_stale --stamps machinery on a fixed list; live harnesses
  backed up in .work/b28/post/live_before). 0 prepare errors, 0 matched raw C failing; far/low call targets 0 in all 76.
- `refine.py verify --adopt` on their 76 readable files: 76/76 pass, nothing to sync.
- 8c06dc28 and 8c08d48c: readable via `refine.py adopt` (attempt-03 and attempt-06 pass the current judge); no Haiku.
- R8 8c01c0e8 and 8c01e97c: matched and readable pass on the new harnesses.
- Not re-prepared: 8c08d638 (engine-blockers'); 8c0633cc (stays on its passing harness; see follow-ups).
- Still to do (coordinator): the full re-stamp (`tools/reprepare_stale.py --stamps`, about 2229 TC harnesses) before
  the next final audit; Spawn's when its audit runs.

## Follow-ups (not fixed here)

- **Unaligned input pointers (latent class).** After re-preparing, 8c0633cc's matched C fails 1/96. The vector has an
  odd pointer r4 = 0x8ca99097 and a NaN global, so an unaligned f32 store defeats the aligned NaN-tolerant memory
  compare. On hardware an unaligned access is an address error, so such a vector is invalid input. The fix belongs in
  the input generator or oracle: treat unaligned 2/4-byte accesses as faults and repair them.
- **Watcher staging gap.** `locked_scripts` does not list TcText, TcCont and TcSaves, which locked scripts reference.
  The watcher's staged copy depends on them (latent).
- **Spawn:** re-stamp and re-prepare the functions in the scan table when its audit runs.
- **8c08d638** (engine-blockers) has far call targets. Its harness should be re-prepared by its owner.
