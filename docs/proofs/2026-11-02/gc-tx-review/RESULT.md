# GameCube translator: review findings, final tree (2026-11-02)

Skies of Arcadia only, numbers only. Nothing here is a whole-game run.

## Rates

- Random 200 of the 6751 catalogued functions (seed 20261101): 189 PASS (94.5%, Wilson 95% about 90.4 to 96.9), 9 FAIL, 2 ERROR. A harness: 198 of 200.
  Median 56.5 instructions against 57 for the whole game. These verdicts predate the r4 change; the functions it touches were judged again (below).
- The earlier set of 271 was not random (239 PASS, 88%) and is not a rate.
- Time: 84 to 108 slot-seconds a function, so 158 to 200 slot-hours for the game, about 40 to 50 hours on 4 slots with nothing else running.

## r4 (a 64-bit result's low word)

- C reads the callee's r4 with `tc_lastr4`. r4 counts as set after a callee that sets it on every path, so a pointer call compares it, and an unlisted call passes it.
- Re-judged, final tree, the 114 functions the change can touch or the mutation proof uses: PASS 109, FAIL 4, ERROR 1. None lost against the verdicts before the change.
- Planted 0 in every `tc_lastr4` of a function: 8 of 49 caught. Per site, 91 sites: 13 caught, 47 never reached, 31 reached and compiled differently but not caught.
  Those 31 are read as: the value is overwritten or goes to a callee that does not read it, or to a call where the callee before it does not set r4 on every path, so r4 is junk there. Not checked one by one.
- Planted hardware address (+4 at the first hardware register): 12 of 13 caught. The survivor's site is taken to be unreached, not proven.

## Mutation proof

Same C and mutants on old and new harnesses, 25 functions, 200 mutants: killed 109 old, 125 new, gained 18, lost 2 (802205e0 mutant 2, 80252530 mutant 5).
Both lost mutants are killed when the final judge runs them on the old vectors, so the loss is in the vectors drawn, not in the compare: in 80252530 the mutated case is no longer reached (though the function's coverage went from 20 to 57 of 84 blocks), in 802205e0 one vector reaches that line and the value does not reach the store it checks.

The translator's own C on the final tree, 67 functions, 515 mutants:

| | |
| --- | --- |
| killed | 356 |
| survived, compiled code identical (equivalent) | 27 |
| survived, line never reached | 57 |
| survived, reached and compiled differently | 75 |

So 73% of the mutants that are not equivalent are killed, 83% of those whose line is reached. The 75 may include more equivalents; they were not read by hand.

| blocks covered | functions | killed of all mutants |
| --- | --- | --- |
| 100% | 36 | 76% |
| 90-99% | 6 | 70% |
| 70-89% | 13 | 76% |
| 50-69% | 6 | 55% |
| under 50% | 6 | 28% |

Proposed floor: a PASS under 70% of blocks counts as thin, not as proven. Under 70% the kill rate falls and most survivors are lines never reached (43 of 55). In the random 200 that is 22 of 189 PASS, leaving 167 (83.5%) above it. Now a report label, `thin` in `dp_judge.py`'s results, not a FAIL.

## Other

- A baseline FAIL in a new workspace (800bdcc8, 8058e830, 800679b0, 80071fc8, and now 800136dc, 801cc6bc) is the old translator's C on the new harness, which expects more than it did. The new C passes.
- 80096260: the translated C passes 96 of 96. The agent's matched C in that workspace fails 31 of 96, so the judge catches it.
- Answer keys were not judged again on the final scripts: 337 of 448 is from before the TcFlow fix.
- Failing on the base tree as well, not from this work: `test_batch26b` (machine paths), `test_fsca_rm`, `test_headers`.
