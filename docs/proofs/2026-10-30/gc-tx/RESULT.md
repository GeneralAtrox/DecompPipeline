# gc-tx: GameCube translator, widened Skies test set and the largest shared cause (2026-10-30)

Branch `gc-tx` (from `gccatch`, DP master 455cfce lineage). Judge: ppc-translate's C through `dp_judge.py --all`
(DP's own check, strict-judge flags on, GQR1 = 0x08040804), 271 Skies of Arcadia harnesses: the 83 refreshed ones
plus 188 newly prepared (a stratified sample: float-special, fp-mode, jump-table, indirect-call, loop+float,
hardware, system-op, large and plain functions; `l/pick.py`, seed 20261030).

## Pass counts

| | before | after |
|---|---|---|
| the 83 refreshed harnesses | 83 PASS | 83 PASS (4 of them have new C) |
| the 188 new harnesses | 116 PASS | 134 PASS |
| all 271 | 199 PASS, 46 FAIL, 26 ERROR | 217 PASS, 28 FAIL, 26 ERROR |
| lost (PASS before, not PASS after) | | 0 |
| gained | | 18 |

## Failures by cause (before -> after)

| Cause | Kind | Before | After |
|---|---|---|---|
| A call through an unlisted target is compared on r3 and f1 as the last call left them; the translator passed only registers its own code wrote | translator | 18 (+0.5-style f1 args) | 0 |
| The oracle made no valid test vector ("vector generation did not finish", dev_valid 0): harness unusable, not a translator verdict | tester | 25 | 25 |
| The oracle does not model the instruction: dcbt/dcbst/dcbi/icbi/sync (11), mfmsr/mftb (5), psq_l/ps_mul paired singles (5), mffs/mtfsf (4), frsqrte (1) | tester | 26 | 26 |
| A call argument register r6/r7 the original set (801759f0, 80233c84) | translator | 2 | 2 |
| A stored word differs (80249cec) | translator | 1 | 1 |

## The fix

`translate.py` `fill_auto_args`: a call whose callee DP's meta does not list passes every argument register the
function set. r3 and f1 now count as set when the function makes any call, since after a call the original holds
the callee's results there and the judge compares them (tc_rt.h: "after a call: its results r3 and f1"). Passing
extra registers is safe: the judge compares only the registers the original set. The existing `both` rule then
reads the call's other result with `tc_lastf()` / `tc_lastr()` (no second call).

## Not done

- **Answer-key scoring (gc_truth).** None of the five answer-key discs is dumped on either machine
  (the GameCube game folder is empty), so `tools/gc_truth.py score` could not run. Needs the user's dumps.
- The 83 are the only harnesses in the shared workspace before this run; the 188 new ones were prepared into
  `dpwork/gc/skies/decomp-gekko/harness/` (additive; the ledger is untouched).
