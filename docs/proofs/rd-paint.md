# Readable runner stack paint: what changed and the proof

The GameCube readable runner (`runtime_rd/rd_runner.c`, 32-bit) now paints 64 KB of stack with 0xa5 before each
vector, as the matched runner does (change 8). The matched runner's paint covers only the raw judge. A C that leaves
argument slots unset (Skies 8020cba4 and 80213920: `TcArgs` with only r3 and up set) wrote leftover stack bytes
into the readable runner's call log on every run.

Proof: tools/readable_parity.py over all 83 matched Skies functions (raw judge and readable runtime on the
project's harnesses), on bae7b5a and on rd-paint (311538a, rebased unchanged onto master as cd075d3), three runs of
each on each laptop (14.44 / laptop2 and the main laptop):
- **Verdicts unchanged:** 83/83 agree, raw PASS 83, readable PASS 83, on every run, both versions, both laptops.
- **Output unchanged except in the two functions with unset slots.**
  - For 81 functions, the readable output is byte-identical between bae7b5a and rd-paint.
  - 8020cba4 and 80213920 differ only in CALL lines, in the f1..f8 and s0..s7 columns. Old: leftover stack bytes,
    different on each run. New: a5…a5 on every run. Neither function's compare reads those columns.
  - Their raw output also varies from run to run on both versions. bae7b5a's matched runner predates the paint
    (7bb1632 on judge-gaps), so it holds host pointers there.
- **Deterministic:** two rd-paint runs on one laptop give byte-identical readable output for all 83.
- **Across laptops** (kept outputs of the third run, all 166 files compared): rd-paint's readable output is
  byte-identical on both laptops for all 83 functions apart from the `HOSTSTACK` lines (7968 of them: the host
  stack's address, 000a0000 on laptop2 and 000b0000 here). The runner reports it so the compare can map host stack
  pointers back to guest ones. The only other differences are the raw outputs of 8020cba4 and 80213920 (bae7b5a's
  unpainted matched runner) and, on bae7b5a, their readable outputs. All of these already vary between runs on one
  laptop. So the paint removes nondeterminism and adds none.
- **Mutation audit through the readable runtime** (core/readable_check.check_raw; every mutant core/mutation makes
  of 8020cba4 and 80213920, bae7b5a vs 311538a, main laptop):

  | Function | Killed before | Killed after | Lost | Gained |
  |---|---|---|---|---|
  | 8020cba4 | 320/545 | 320/545 | 0 | 0 |
  | 80213920 | 86/136 | 86/136 | 0 | 0 |

  In the first pass, 18 mutants (16 on bae7b5a, 2 on rd-paint) failed to build with no fault in their C. That
  made 8 bae7b5a "kills" look lost. Every mutant that failed to build on either side was judged again on both: the
  two runtimes then agree on all 57. The 39 true build failures are compiler warnings under /WX. The 18 transient
  failures are a separate problem in the readable build, not caused by this change (see Limits).
- The full suite passes on rd-paint rebased onto master a0e8ac5: 1995 passed, 12 skipped, 14.44 / laptop2.
- `tests/test_gc_rd_paint.py` builds the real readable runner and checks the pattern in unset slots and that two
  runs agree. With the paint removed it fails.

## Limits
- The Dreamcast matched runner (targets/dreamcast_sh4/runtime/runner.c) has no stack paint yet. Its output
  varies run to run for C with an uninitialised `TcArgs` (Spawn 8c017460, 8c033980, 8c051fb0, 8c049a40), as
  the GameCube runners' did. Verdicts are stable. Not fixed here.
- The readable build sometimes fails with no fault in the C (18 of 1366 builds in this audit). Counted as a
  kill, such a failure inflates a mutation score and can hide a survivor. Not fixed here.

## Overlap
- No change to `core/strict_judge.py` or `tc_strict.h`. The matched (raw) judge is untouched.
