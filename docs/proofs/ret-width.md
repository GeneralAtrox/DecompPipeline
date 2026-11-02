# Narrowed returns: results the callers use and the judge never compared (2026-11-01)

Toy Commander's readable `Lib_MathPow` (8c093e94) was void and dropped its result, so `powf` read a stale fr0. Sound pitch
broke and a mission ran at half speed. Both functions passed, because the judge compares only the result register the
signature names.

## Cause

The fr0 list (`state/fr0_results.txt`) was stale, written by hand with `tools/float_gaps.py --write`. TC's list dates from
2026-10-19 and 8c093e94's harness from 2026-10-30. `core/ret_reg.py` follows that function correctly today (exits `call`
and `fr0`) and `ret_use.fr0_confirmed` agrees. The list also had no r0 equivalent, so a void function whose callers read
r0 could never be compared.

## The change

- `ret_use.Project.narrowed()` finds the functions whose callers use a register the signature does not name. A real reader
  is an instruction that reads it after the call. Passing it on counts only when that caller has a real reader itself, so
  raw f32 signatures, often leftovers, prove nothing. A function that cannot write the register on any path is left out.
- `state/fr0_results.txt` lines may now name registers (`8c010100 r0`, `8c010200 fr0 r0`). A bare address still means fr0.
  `ret_reg.judged` sets `fr0_result` and `r0_result`, and `compare_return` compares them.
- `core/doctor.py` fails a workspace whose list misses a narrowed function, and `final_audit` runs doctor, so a stale list
  cannot reach a final pass. The fix is `tools/float_gaps.py --project WS --write`.
- `float_gaps` rows `narrowed` and `dropped` (a reader whose readable C discards the callee's result). Both go in its
  requeue list. `dropped` is a lint: see the false alarms below.
- The readable runner's call trap (`rd_p4.c rd_guess_ret`) now follows a `ret` one frame up. `return handler(x);` left the
  float to the caller's caller, so the trap chose an integer result and the function read NaN. Without this a correct
  float pass-through could not pass (TC 8c01c0e8).

## Numbers

`narrowed()` on each workspace, then the real judge on every hit with the list off and on, read-only:

| Workspace | Functions | Hits | Raw C newly fails | Readable judged | Readable newly fails |
|---|---|---:|---:|---:|---:|
| Toy Commander | 2734 | 16 | 16 | 16 | 3 |
| Power Stone 2 | 6588 | 23 | 22 | 21 | 3 |
| Spawn main | 1910 | 8 | 8 | 3 | 0 |
| Spawn ovl | 1574 | 11 | 10 | 0 | 0 |
| Vigilante 8 | 55 | 6 | 6 | 0 | 0 |
| **Total** | | **64** | **62** | **40** | **6** |

No other Spawn overlay has a hit. Saturn has 7 raw-only hits (Dragon Force 2, Dragon Force 2 2, Bomberman 3, Grandia 0) and no
readable C; the real judge was not run on them. GameCube and Xbox 360 use PowerPC and have no readable C,
and this rule does not read their listings.

Nothing newly fails unless its workspace writes its list. The workspaces' state was not touched.

**Planted wrong answers.** Each readable that returns a value was given `return (X) + 1;`, 36 functions:

| | List off (the gap) | List on |
|---|---:|---:|
| Plants accepted | 33 | **0** |

The other 3 plants fail with the list off too. The original wrong answers: `Lib_MathPow`'s raw and
readable C both pass with the list off and fail with it on (`return value fr0: expected 0x9972e44d, got 0`).

**Plants in the static check.** 30 real returning functions with a real reader were flipped to void in memory, on each of TC
and PS2: 60 of 60 found. 30 near misses on each, flipped to void with no reader: 0 of 60 flagged.

**Correct functions.** 34 of the 40 readable functions pass with the list on, and their readable C declares the value, which
agrees that the readers are real. Of the 6 that fail, all were read: each drops or hardcodes the result. A correct raw C fails
until its `raw_ret` override, because a void thunk stores no result (62 functions): that is a requeue, not a wrong function,
except that the raw C does lose the value. 14 pointer-call functions re-judged with the new trap guess keep their verdicts.

**False alarms.** 5 raw-only hits were read (Spawn 8c0508f0, 8c0509a0, 8c02eccc, 8c5b99a2, 8c5b9146): all real reads. The
other 19 raw-only hits were not read one by one. `dropped` gave 9 rows. TC 8c082c0c and PS2 8c0b2db4 and 8c0c29c8 are real;
TC 8c01c7a4 is a false alarm in effect (`Script_EvalInt`'s r0 goes to a routine that ignores it, and returns 1). The other 5
TC `Script_Eval` rows were not read.

## Not closed

- A 0.0 result still passes where the thunk leaves fr0 at 0.0 (section 3 of `PROOF_GATES.md`).
- A hardcoded result in a caller no vector brings to the call, and one whose result changes nothing on the vectors that do:
  see "Float mocks" below.
- PowerPC has the compare but no detector. `ppc_compare.compare_return` compares r3 and f1 for a function `state/fr0_results.txt`
  names with them, and only `narrowed()` on SH-4 writes that list, so nothing is listed yet and no verdict changes. No readable
  C exists. Real judge on Skies of Arcadia's matched functions, a register listed in memory the signature doesn't name: 12 run
  (10 void with r3, 2 s32 with f1), 11 pass with it off and all 11 fail with it on. The twelfth fails with it off too, on a
  call (80096260). No correct function with a listed register exists to show the other side, beyond the unit test.
- A function whose callers read both registers while it declares f32 can list r0, but a thunk returns one value.
- A function called only through a pointer has no call site to read.

## Float mocks for listed callees

A mocked callee's result reads as about 0 in fr0 unless Ghidra's prototype says f32. So a caller that drops a void callee's
float and hardcodes 0 passed (PS2 8c0b2db4 `chance = 0.0f`).

- `float_gaps --write` also writes `state/fret_callers.txt`: `caller callee...`, where the caller really reads fr0 after a
  callee that is listed, narrowed or overridden to f32. The override case found more than the list: PS2 8c179820 has 348
  readers, Spawn 8c0508a0 25, TC 8c0611c8 11.
- `core/ghidra.py` passes those callees to `TcPrepare` as argument 15. For them `TcFret` draws floats, and a scratch pointer
  (0x8cc...) becomes a real number. 0 and 1 stay. The caller's stamp takes a line for its list, and `core/doctor.py` fails a
  stale file. Saturn has no FPU and PowerPC has its own oracle: neither changes.
- `dropped` rows say `unreached` when no vector reaches the call.

**Restamp.** Editing the two Java files makes every Dreamcast stamp old. Each workspace runs `float_gaps --write`, then
`tools/selective_restamp.py --change fret` with `--old-scripts` the scripts its harnesses were stamped with:

| Workspace | Callers on the list | Callees |
|---|---:|---:|
| Toy Commander | 72 | 7 |
| Power Stone 2 | 361 | 8 |
| Spawn main | 37 | 4 |
| Spawn ovl, Vigilante 8 | 0 | 0 |
| **Total** | **470** | **19** |

The dry run on Toy Commander, with the scripts from before 8951320 (its harnesses still carry those): 2731 refreshable, 0 stale for
other reasons, 358 to re-prepare (298 with an f32 callee record, 60 more from the list). The live restamps have not run.
Toy Commander's harnesses also wait for 8951320's own restamp, which goes first, so that change's effect is not stamped away.

**Trial.** 56 listed callers (PS2 20, TC 23, Spawn main 13), each prepared afresh in a private folder and judged on the old and
the new harness:

| | Count |
|---|---:|
| Harnesses whose expected results changed | 52 of 56 |
| Raw C newly fails | 0 of 56 |
| Readable C newly fails | 0 of 36 |

**Planted wrong answers.** In the 31 readable callers that use the value, every use was replaced by `(Name(...), 0.0f)`. 20 fail
on the old harness too. The other 11:

| Plant, old harness passes | New harness | Why |
|---|---|---|
| TC 8c0499b0, 8c028114 | **fails** | Caught |
| TC 8c017040, 8c01937c, 8c038008 | passes | The result reaches 8, 4 and 7 times and changes nothing the judge sees. In 8c017040 it is a temporary change around a mock. |
| PS2 8c17bfc0..8c17c060 (6) | passes | The value goes to 8c179870 as fr4 and fr5, whose record says `void(void)` with no registers, so those arguments are not compared. |

So 2 of 11 are caught, and 9 stay accepted, for two causes float mocks cannot reach.

**PS2 8c0b2db4 itself still passes.** Its harness changed, but none of its 115 vectors reaches the call to 8c0b1de8. Of the 9
`dropped` rows it is the only one unreached. TC 8c082c0c and the `Script_Eval` rows are reached on every vector, and PS2 8c0c29c8
on 2 of 106. The row reports it; a vector that reaches the call would catch it. The judge does not fail it, since `dropped`
has false alarms (TC 8c01c7a4) and no way to waive one.

TC 8c082c0c passes on both, rightly: its readable C reads `sh4_fr(0)` after the call. Only that row and 8c0b2db4 were in the
trial's sample, so whether the other 7 reached rows fail is not measured. Most name r0, which already took scripted results.

## Landing check

dp-land ran the same seeded mutants on the base and on this change, 30 functions across TC, PS2 and Spawn: 286 of 364 killed on
both, 0 lost (job 20261101-022203-71fe5b61-364f). That run predates moving `rd_guess_ret` to `rd_p4.c`, which changed no logic.
