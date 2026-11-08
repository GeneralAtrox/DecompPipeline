# Pikmin truth run (GPIE01 rev 1): scorecard

This run measured DecompPipeline's GameCube route on the whole of Pikmin's main.dol, scored against the public
matching decomp. The key was read only by `tools/gc_truth.py` and a scoring script outside the repository, after DP's
output existed. Nothing from it (code, names, types) went into DP, its catalog, a prompt or a translator. This file
holds numbers only. Per-function lists stay outside the repository, in the projects folder's `GameCube\Pikmin\.work\score\`.
Ran 2026-11-07 to 2026-11-08 on build box A (prepare, judge, kill rate); disc check and scoring ran on the main laptop.
DP commit 6512c902, DP defaults throughout (no limits raised).

## Step 0: disc check

| Check | Result |
|---|---|
| `gc_truth.py check-disc` on the .ciso | pass: main.dol SHA-1 02204260b7efe8742d34572e58ba3dfecd92e4e9, the key's GPIE01_01 |
| Planted: the same image with one byte flipped | rejected (exit 1) |

## Step A: function list (main.dol, 8,068 truth functions, 6,618 DP functions)

| Class | Functions | Share of truth |
|---|---|---|
| exact | 6,585 | 81.6% |
| boundary differs | 10 | 0.1% |
| split | 4 | 0.0% |
| merged | 5 | 0.1% |
| missed | 1,464 | 18.1% |
| extra (DP function outside every truth function) | 0 | |
| right start / size equal / end equal | 6,599 / 6,544 / 6,585 | |
| DP starts that are no truth start (inside one) | 19 | |

These match the Ghidra-only figures in `ZKIT_TRIAL.md` exactly (6,599 right, 1,469 missed starts, 19 extra), so DP's
catalog is Ghidra's discovery and adds nothing to it on this game. The 1,464 missed functions are out of reach for
step B.

Plant proofs (scorer must move by what was planted, seeded 5% = 329 functions): shifting 329 starts by 4 bytes takes
exact from 6,585 to 6,256 and right starts from 6,599 to 6,270; dropping 329 takes exact to 6,256 and missed to 1,793.

## Step B: test vectors, translation, judge (all 6,618 DP functions)

| Result | Functions | Note |
|---|---|---|
| PASS | 6,361 | 96.1% of DP's functions; 336 of them thin (vectors reach under 70% of blocks) |
| FAIL (a vector differs) | 29 | below |
| unproven (no or too few valid vectors) | 130 | below |
| skipped (oracle cannot run it) | 91 | below |
| no verdict (judge could not set up the call) | 7 | a float parameter Ghidra put in an integer register |
| translator gave no C | 0 | ppc-translate produced C for every function |

Pass rate over functions with a verdict: 6,361 of 6,390 (99.5%). Against the key: 6,354 PASS are truth functions,
78.8% of the game's 8,068. PASS coverage: median 100% of blocks, 10th percentile 84.6%, 5,217 at 100%; median 96
vectors.

### Failures by cause (29)

| Cause | Functions |
|---|---|
| call argument differs (r3, r4 x2, r8 x2, r9, f1 x2) | 8 |
| call target differs | 2 |
| call count differs | 1 |
| return value differs | 4 |
| hardware register access differs (width, read vs store) | 6 |
| failures only in the hidden vector set | 6 |
| C reads invalid guest memory | 1 |
| callee-saved r14 not restored at exit | 1 |

### Unproven by cause (130)

| Cause | Functions |
|---|---|
| valid vectors below the judge's minimum (1 to 31, 0 failing) | 36 |
| oracle: random pointer inputs fault on read | 68 |
| oracle: write outside RAM | 18 |
| oracle: mocked callees loop (over 4,096 calls) | 4 |
| oracle: step limit | 2 |
| oracle: emulation error (divide by zero) | 1 |
| strict-flags replay failed | 1 |

### Skipped by cause (91)

| Cause | Functions |
|---|---|
| oracle vector generation did not finish in its time limit | 49 |
| unsupported instruction (mfmsr/mtmsr/mftb 20, sc 3, paired-single 4, FPSCR 2, dcbi 1, others 3) | 33 |
| executes code outside its body | 7 |
| stack parameter beyond the registers | 2 |

### The two TcTrack gaps fixed elsewhere in 8f72a3fa

| Gap | Functions on Pikmin | Of them PASS / FAIL / no verdict |
|---|---|---|
| (a) a recorded call to a target no callee record lists, with no CA line ("none") | 0 | 0 / 0 / 0 |
| (a') pointer call with an empty CA line (recorded, not marking) | 3 | 3 / 0 / 0 |
| (b) body runs on past its end (static scan of main.dol) | 10 | 0 / 0 / 10 (2 skipped, 8 unproven) |
| 8f72a3fa's stamp scope applied to GameCube (no computed call, and a branch out to no function or a run-on) | 11 | 0 / 0 / 11 |

GameCube's TcPrepare lists every `bl` target and jump target that is a function as a callee record, and its oracle
refuses to leave the body for anything else ("executes code outside its body"). So both gaps end as skipped or
unproven, never as a false PASS, and none of the 29 failures or 7 no-verdicts come from them. Of the 7 outside-body
skips, 1 is a run-on. Porting 8f72a3fa to `gamecube_ppc` (TcTrack.of `unlisted()`, a run-on record in the oracle,
a target.json stamp scope and hooks.py `in_scope`, a `gamecube_ppc` entry in `core/unchecked_args.WORDS` with
DOL-section word reading, tests) is about 150 lines and half a day. It would affect at most 11 functions here,
re-prepared in minutes. Not worth doing for Pikmin. Run the static scan on the next game's DOL first: it takes
seconds.

A gap this run does not measure: at a listed callee the judge compares the registers of the callee's Ghidra
signature, which is not proven on GameCube (X360's `callee_drift.call_regs` uses proven signatures only).

### Kill rate (seeded sample of 400 translator PASSes, 16 mutants each)

| Measure | Value |
|---|---|
| mutants tried / killed / survived / dropped (lint, build) | 5,512 / 4,507 / 1,005 / 329 |
| kill rate (all survivors) | 81.8% |
| kill rate (equivalent arg-slot survivors left out) | 84.2% |
| per-function score mean / median | 85.1 / 92.3 |
| functions below 80 | 94 of 397 |
| functions with a logic survivor (harness weakness) | 271 |
| survivors by operator | const 314, delete 274, relop 129, arith 96, swap_args 50, negate 42, bitwise 41, shift 38, return0 21 |

### The judge checked against the key

The key gives byte-level truth for function boundaries only. It gives no behaviour to compare with, and its code is
not compiled here, so no PASS can be shown wrong from the key. What it does show:

| Check | Functions |
|---|---|
| PASS on a function whose DP boundary differs from the truth | 7 |
| PASS on half of a split truth function | 2 |
| PASS on a DP function starting inside a truth function | 7 |
| merged functions: both skipped (no verdict) | 2 |
| thin PASS, all on exact functions | 336 |

These 16 PASSes are right for DP's body and wrong for the game's function. That is a catalog error, not a judge
error.

Planted-verdict proof of the scorer: flipping 100 PASS to FAIL and 10 FAIL to PASS moves 6,354 / 29 to 6,264 / 119,
exactly the plant.

## Cost and time per stage

| Stage | Wall | Machine time | Agents |
|---|---|---|---|
| disc check | under 1 min | main laptop | $0 |
| step A: Ghidra import, catalog, decode check | 0.05 h | Build box A, 1 Ghidra | $0 |
| step B prepare (6,618 functions) | 12.6 h (pilot 0.1, chunks 3.0, 3.0, 5.0, 1.5) | 46.0 slot-hours on 4 slots | $0 |
| step B translate and judge | 0.85 h at 6 jobs | translate 408 s, judge 4.7 thread-hours | $0 |
| kill-rate sample (400) | 0.78 h at 6 workers | | $0 |

Preparation's cost is its tail: median 2.1 s per function, but 162 functions ran over 300 s (most to the oracle's
~733 s limit) and took 25.9 of the 46.0 slot-hours (56%).

### Estimates (scaled per function from these figures, build box A, 4 prepare slots)

| Game | Functions | Prepare | Translate and judge | Readable C (step C), Opus |
|---|---|---|---|---|
| Pikmin (this run) | 6,361 PASS | done | done | $910 to $2,280, about 54 h at 4 workers |
| Skies of Arcadia | about 6,750 | about 47 slot-hours, 12 to 13 h wall | about 0.9 h | about $0.9k to $2.3k |
| Too Human (Xbox 360) | about 50,000 | about 350 slot-hours, about 88 h wall | about 6.5 h | about $7k to $18k |

Readable C uses the Xbox 360 measured range, $0.143 to $0.358 per function (`docs/proofs/2026-11-06/x360_readable.md`,
`x360_reference_run.md`), and 123 agent-seconds per function. GameCube readable C has not been measured. Too Human
is another target, so its figures are a GameCube proxy only. Step C was not started: it waits for the user's decision.

Redundancy check: reused DP's prepare_list, dp_judge, mutation_check, gc_truth score and core/unchecked_args scans
unchanged, jobreg for every heavy job (one re-run marked --changed after the first kill-rate start selected no
functions); nothing new in DP except this file; two scoring scripts live outside the repository in the run's .work
folder.
