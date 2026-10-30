# GameCube strict-judge flags and GQR1: the mutation-audit proof (gccatch, laptop2, 2026-10-30)

Skies of Arcadia's mutation-audit sample (`tools/mutation_audit/sample.json` "skies", its 83 matched functions;
10 mutants each, seed 1), judged by `tools/mutation_audit.py run` on two sets of harnesses made today by DP master
415057ef's oracle, one prepare each (1 Ghidra slot):

| Run | Harnesses | Flags (target.json) | project.json GQR1 | File |
|---|---|---|---|---|
| off | `tools/prepare_list.py` | all six off (master) | 00000000 | `audit_off.json` |
| on | `tools/reprepare_stale.py --stamps` | branch_coverage, targeted_vectors, mock_junk, mock_writes, call_snapshots, exit_frame | 08040804 | `audit_on.json` |

`mutation_audit.py gate audit_on.json --baseline audit_off.json`: 713 mutants compared, killed 608 -> 617
(85.3% -> 86.5%), **gained 9, lost 0**, flips 0. Against the standing `baseline.json` (2026-10-24): 606 -> 617,
gained 11, lost 0.

Gained (all survived -> killed; mutant id as `core/mutation.generate(seed 1, cap 10)` numbers them):
800c5530 #3 (`&` -> `|`), 800c79c4 #10 (`+` -> `-`), 800d1630 #5 (0x104 -> 0x105), 80118368 #3 (1 -> 0),
80118368 #9 (0x8030a380 -> 0x8030a381), 80202004 #6 (a `wr32(r30, 0)` deleted), 802057e0 #2 (`+` -> `-`),
80215444 #4 (8 -> 9), 80226ae8 #1 (`>=` -> `>`).

Five matched functions do not pass either set of today's harnesses (so they have no outcomes in either run):
80096260, 800c86ec and 80226984 (a pointer call's float argument f1 passed as 0; judge hole 5, ba813f31),
800cf988 and 800d1a34 (a tiny single result: the old oracle's flush to 0 copied; the 750CL's rounding since
6a924b75). These come from master's judge and oracle changes after the old harnesses were made (2026-10-18 to
10-20), the same with the flags off and on.
