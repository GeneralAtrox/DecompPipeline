# Junk-RAM judge pass: what it adds and the proof

Branch `junk-judge` (laptop2, 2026-10-30) adds the junk round's check (`land\junk`, tools/junk_ram.py `--original`)
to the Dreamcast judge as a fifth judge flag, `"junk_ram"`. It is off by default: absent or false, it runs nothing,
and check results, harnesses, the facts file and the oracle stamp are byte-identical. The user approved it on
2026-10-30.

## What it does

When the flag is on, a check that passes today's comparison runs the C a second time. Every never-set byte of RAM
past the image is filled with a fixed junk pattern (seed 1), and the result is compared with xemu's run of the
original on the same junk (`core/junk_ram.py`). Only "fit" vectors are judged:
- xemu reproduces every vector of the function on zero RAM. If it misses even one, the whole function is skipped:
  a model gap such as the SR mask, store queues or mock_junk would otherwise hide in the other vectors.
- the original's junk run finishes without a fault.

A word that is NaN on both sides matches, because xemu has no float-store tracking and its NaN bits aren't the
runner's. The original's two runs (the "junk oracle") don't depend on the C, so they're made once per harness and
kept in `build/junk_ram/`.

`"strict_judge": {"junk_ram": "report"}` is report mode: the differences go to `strict_report`, and the verdict is
today's. Other targets are refused.

## The proof (private copies of TC, PS2, Spawn main and V8)

- **Off.** 40 TC check results are byte-identical between master 5af902a and the branch (a named cross-check),
  and `tests/test_junk_ram.py` covers the same.
- **The junk round's 6 caught wrong reads** (PS2 8c03eee4 x2 and 8c171288 x2, Spawn 8c04e620 x2) now FAIL on all
  96 vectors. Their correct C passes.
- **Mutation audit, frozen DC sample, flag on.** 0 kills are lost to the flag: the junk pass runs only after
  today's pass, and can only add failures. The 7 lost against the committed baseline.json also survive with the
  flag off, and on master's code. They're the baseline's age: it predates judge-gaps, as prewave-judge.md found.
  Figures: `land\jjudge\jjudge.result.md`, task 3.
- **Wider set, report mode.** 1,568 matched functions that pass today (400 per workspace, seed 1). The final code
  flags 1, a real fault: TC 8c0149b4 reads a callee's stack out-parameter as a host local 0. The audit sample adds
  one more, TC 8c03561c, whose C starts uninitialized stack locals at 0. There were 0 false alarms; the first run's
  15 were xemu gaps, removed by the two rules above.

## Landing

1. Commit (a), the flag (off). The judge stamp moves (core/*.py), as for any judge change. No re-stamp, no
   re-prepare.
2. Commit (b), `"junk_ram": true` in targets/dreamcast_sh4/target.json. It's a separate decision:
   - Each Dreamcast game takes it at its next re-judge. The first one costs about 3.5 to 4.5x today's check time;
     later ones about 2.5x.
   - Expect about 0.4% of TC's matched functions to flip to FAIL, and few or none elsewhere.
   - Re-baseline `tools/mutation_audit/baseline.json` from the flag-on audit.
