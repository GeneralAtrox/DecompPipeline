# Xbox 360 translator on build box A (2026-10-30): one shared cause fixed, 89.5% -> 99.8% on 533 functions, 0 lost

Branch `x360-tx` in the build box A work tree `dp` (bundle: see the end). Base: DP master e148817 + the unlanded `x360-landing`
(0125511) merged as 40d4e6c, then the translator fix c2848d0, then the regress baseline. Nothing pushed, nothing landed.
Workspace `th` on build box A (Too Human, 533-function judge set). Everything ran on build box A through `tools/jobreg.py`, bounded.

## 1. Where the translator stood

The translator (xenon-translate, imported into `targets/xbox360_ppc/translator/`) builds 88,111 of 88,187 functions (offline,
README) but had never been judged: Too Human had no harness. `x360-landing` (the judge: TcTrack call-argument compare, mock-call
register rules) is not on master, so I merged it into my branch to get a judge at all. No judge or oracle code was changed
by me, only the merge. Oracle stamp and verdicts are the landing branch's.

## 2. Pass rate before and after (judge = unchanged, user decision B: judge strict, fix the C)

| Set | Before (40d4e6c) | After (c2848d0) | Lost |
|---|---|---|---|
| Seeded sample, 250 functions (found the cause) | 222 / 250 = 88.8% | 250 / 250 = 100% | 0 |
| Held-out, the other 283 of the 533-function judge set | 255 / 283 = 90.1% | 282 / 283 = 99.6% | 0 |
| All 533 | 477 / 533 = 89.5% | 532 / 533 = 99.8% | 0 |

The 533 are functions of 3 to 333 instructions, seeded from the judgeable set (86,565 functions). The 28 failures in the sample
and 27 of the 28 in the held-out all had **one** cause, found on the sample and then confirmed on functions not used to find it.

**The cause.** A mocked callee sets both r3 and f1 (tc_rt.h). The C received only the one the call's kind names
(`r3 = tc_call`, `f1 = tc_callf`). When the other one was read after the call (a later pointer call passes it as an
argument, or the function returns it), the C used a stale `0.0`/`0` instead of the oracle's scripted value: 27 of 28 held-out
failures read `argument fN (args.f[N]) expected ...`. Fix in `xenontranslate/passes.py` (`other_result`, called at the end of
`choose_calls`): a liveness pass inserts `f1 = tc_lastf()` / `r3 = tc_lastr()` after the call when the other register is live;
`translate.py fill_auto_args` counts both r3 and f1 as defined after any call. 37 added lines, no judge change.

**Regress.** `tools/regress.py` (500 baseline functions, generic signatures): 336 of 500 C files change, 164 are byte-identical;
the pre-fix tree matches the old baseline for all 500. The 336 changes are all this one family (a `tc_lastr()` / `tc_lastf()`
read of the call's other result, a stale `r3`/`f1` declaration or argument replaced, a pointer call now passing f1/r3). I checked
the classes by pattern over all 336, and by eye for several; I did not read all 336. Baseline updated (500 of 500 identical after).
Translator tests + `tests/test_own_translators.py` on build box A: 21 passed, 4 skipped (the skips are the game-binary tests).

## 3. The next three causes (the first two are not proven failures of the translator)

Measured where I could; counts are of the 86,565 judgeable functions unless noted.

1. **Big functions have no harness yet: 895 functions over 333 instructions (1.0%).** A seeded sample of 14 (334 to 1,128
   instructions): 10 PASS, 0 translator FAIL, 3 NOVECTORS (the prepare found no vectors: "vector generation did not finish in
   time", "executes code outside its body", 0 of 189 blocks), 1 judge crash. So about 4 in 14 (~255 of the 895) need prepare work
   before the translator can even be judged. Prepare mean 83 s per function (median 17 s, one took 742 s).
2. **The tester crashes on a float stack parameter.** `x360_check.gen_thunk` raises `parameter param_7 in r6 has kind f64` for
   82aa9648 (371 instructions, 7+ parameters), and the exception kills the whole `translate_backlog` run (no JSON written), it is
   not a FAIL row. It is a tester change (`x360_check.py`), so it comes back as a decision: a fix needs your proof of 0 lost kills.
   Frequency unmeasured (1 of 14 in the tail, 0 of 533 in the judge set, which is why it never showed before).
3. **The 76 functions that do not build** (README, offline): 38 over DP's 60 KB file cap even in short form (2,000 to 8,000
   instructions each), 37 use `vupkd3d128` / `vpkd3d128`, 1 takes over 5 minutes to translate (8237bd08, 22,655 instructions).
   0.09% of functions, but 14% of the instructions that do not build.

Smaller, measured: **alloca** (`stwux r1,r1,rX`, frame grows at run time) gave the one remaining FAIL (82967760): the C keeps a
fixed frame and passes `r1+0x50` as the pointer, the oracle has `r1` moved and (64-bit add, no wrap) `0x1_700fd300`. Only 42
judgeable functions call the stack probe 82ec4914 (0.05%). Not fixed: it needs a variable-r1 frame model.
Not translator failures but still unjudged: **VMX128** 1,397 functions (1.6%, "unsupported: VMX", waits for judge phase 2),
atomic 110, system 84, cache 27, FPSCR 4. The translator builds 1,345 of 1,353 vector functions; whether it is right is unknown.
The pointer-call register rules (kept registers, trap) were not exercised by anything in the 533 (0 failures of that kind).

## 4. Too Human as a whole, measured on build box A, extrapolated to build box B

Measured on build box A (i7-8750H 6c/12t, 15.9 GB): Ghidra setup of the **whole catalog** (86,565 functions): **300 s**, JVM peak
1.44 GB, project 199 MB (5,014 functions 32 s; 20,000 functions 89 s, so linear). Translator summary cache (once per game):
94 s. Prepare per function (TcServe slot): median 1.05 s, p90 1.8 s, p95 7 s, p99 196 s, max 466 s; **mean 7.6 s**. 88% of the
prepare time is the slowest 10% of functions (loops or long paths). Weighted to the catalog (53k functions of 11 to 30
instructions, 15k of 31 to 100, ...): **176 h single-slot (bootstrap 95% range 94 to 275 h)** for the 85,670 functions up to 333
instructions, plus the 895 larger ones at the tail's 83 s each: **21 h**. Translate: the DP adapter starts one process per
function and takes 1.9 to 2.2 s; the judge itself is 1.02 s; 283 functions took 158 s on 6 workers (0.56 s each).

| | Prepare (197 h of one slot) | Translate + judge | Total |
|---|---|---|---|
| Build box A, 2 Ghidra slots + 6 judge workers (load measured safe) | **~98 h** (range 55 to 150) | 13.5 h as it is; **4.5 h** if the translator runs in batch (README: 26 min for the whole game on 3 workers) | **~103 to 112 h** |
| Build box B (14c/20t, 47.6 GB; busy with other cards, so not measured, ~20 GB free now), 6 slots if it were free | 33 h at build box A's speed per slot, 22 h at 1.5x | 2 to 6 h | **~25 to 40 h**, guess |
| Both | | | ~20 to 30 h, guess |

Against gate 5 (72 h): **Build box A alone misses it by about 1.5x; build box B alone, or both, meets it, on a guess.** Build box B under
load was measured at 1.7 to 3.4 s per judged function against build box A's 0.9 to 1.0 s, so I used 1.0 to 1.5x per slot, not more.
The prepare tail is the lever, not the machine: capping a function's prepare at 120 s drops the 176 h to 103 h and costs about
1,940 functions (2.2%); at 60 s, 69 h and 2,140 functions (2.5%). Those would become NOVECTORS for an agent or a harder prepare.
That is a prepare/oracle change, so a decision for you, not mine.
**The scorecard's gate 5 formula uses the prepare median (1.05 s, over `ghidra_parallel`), the cost is in the mean (7.6 s):
a 7x difference that makes today's projection too kind.** The agent cost side is not a worry: after the fix 1 of 533 translator
FAILs (0.2%), so at most a few hundred functions of the 86k reach an agent, plus the ~1,500 refused.

## 5. Not done / not verified

- Scale numbers rest on a 434-function prepare sample (heavy-tailed: the slowest 5 functions are over half of the time) and 14 tail functions; the
  CI above is a bootstrap and wide. A real full run on build box A would take ~4 days and was not started.
- Build box B was not run (brief: no heavy jobs there); its row is a scaled guess.
- The regress baseline is updated for 336 functions on the strength of pattern checks plus 0 lost verdicts on 533 judged.
- `x360-landing` itself is still unlanded; my branch contains it and a hand-resolved merge (see below). The `dp-land` card
  must land `x360-landing` first or together.
- Merge conflicts resolved by hand in 40d4e6c: `core/oracle_stamp.py` (both sides' imports kept) and
  `targets/gamecube_ppc/ghidra_scripts/TcPrepare.java` (callee loop, both sides kept). The GameCube oracle stamp will move.
  Please review these two files before landing.

Redundancy check: reused the landing branch's judge, `prepare_list.py` / `translate_backlog.py` / `regress.py` / `jobreg.py` /
`bounded.py`, the existing 533 harnesses, `x360setup.project_fields` and `core.ghidra.setup`; did not run the whole-game
prepare/judge (it is the estimate), did not re-run anything the registry already held (two jobreg refusals, one with a changed
input); new: `setup_slice.py` (a seeded-slice Ghidra setup timer, kept in my scratchpad not in DP; nearest was
`x360setup.py`, which sets up the roots' closure and cannot time a slice) and `cluster.py` / `rgdump.py` throwaway helpers.
