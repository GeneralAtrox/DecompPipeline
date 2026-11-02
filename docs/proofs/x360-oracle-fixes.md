# Xbox 360 and GameCube oracle fixes: pass-through registers and self-calls

Branch `x360-oracle-fixes`, off `xbox360-ppc` 6505588. Not merged, not landed. This is a judge change: it goes in
the user's judge-change batch with `judge-gaps`, under one yes and one re-stamp.

## The two holes

| # | Target | Hole | Fix |
|---|---|---|---|
| 1 | Xbox 360, GameCube | A call record compares the argument registers in the callee's signature. Some functions pass an entry register on to a callee without writing it. Their own signature did not take that register, so the C could not receive it and passed 0. TcFlow looks through callees to a fixed depth only, and that need not reach what a callee's record compares. | Shared rule `core/ghidra_shared/ppc/TcPassThrough.java`. An argument register that is live at entry, when every call reads exactly the registers its record compares, is an input. It is added as `param_rN` / `param_fN`, like the other registers Ghidra's signature misses. The rule repeats until nothing more is added. |
| 2 | Xbox 360 | `bl` to the function's own entry stayed inside the body, so the oracle ran the recursion inline and logged no call. The C makes a logged `tc_call`, and its fixed stack addresses cannot hold a second frame. | A call instruction to the function's own entry is mocked and logged like any other call (`TcBase.selfCall`, `TcOracle`). The function is listed as its own callee, with its own signature (`TcPrepare.calledBy`, `TcVectors.callSites`). |

GameCube keeps its inline recursion: the GameCube translator writes real self-recursion (ppc-tx-100 dd82675). On
GameCube a self-call's entry in the rule reads the function's own registers, so it can add nothing on its own.

### Code

- **`core/ghidra_shared/ppc/TcPassThrough.java`** (new): the rule. One implementation, used by both targets.
- **Each target's `TcFlow.java`**:
  - `implements TcPassThrough.Flow`;
  - `inputsAt` solves with the record's registers at each call;
  - `reads()` is the hook that `step` uses for calls and tail branches.
- **Each target's `TcPrepare.java`**:
  - the callee prototypes are built once into a map;
  - `passThrough` calls the shared rule, then the callee records are written from the map.
- **Xbox 360 only (hole 2)**:
  - `TcBase.selfCall`;
  - `TcOracle`: a call to its own entry is mocked;
  - `TcPrepare.calledBy`: fn when it calls itself;
  - `TcVectors.callSites`: the self-call is a call site.
- **`target.json`**:
  - `TcPassThrough.java` is locked on both targets;
  - GameCube now names `"shared_scripts": ["ppc"]`, the same line `judge-gaps` adds.

## Proof

**Method.**
- Proof-only scripts (TcSigsX for Xbox 360, TcSigsG for GameCube; kept in the session scratchpad, not committed)
  output each function's tested signature, refusal and compared callee registers. They compute these exactly as
  TcPrepare does before it makes vectors, once with the base scripts and once with the branch's.
- A function whose output is the same on both sides gets the same harness: the vectors depend only on the
  signature and the callee records.
- So only the functions that differ were re-prepared, through the broker, in a copy of each workspace. Every
  function was then judged on both sides by the same translator snapshot.

### Xbox 360: Too Human, the 533-function judge set

- **Affected: 5 of 533.**

  | Function | Change |
  |---|---|
  | 82782480 | + `param_r7`, `param_r8` |
  | 827a8ee0 | + `param_r7`, `param_r8` |
  | 82d5abf8 | + `param_f8`..`param_f13`, `param_r8`, `param_r9` |
  | 82929e18 | itself as a callee (self-call) |
  | 82eac7f0 | itself as a callee (self-call) |

- The refactor onto the shared rule gives byte-identical output to the first version over all 533.
- **Re-prepared:** 48/48 dev and hidden vectors each.
- **Judge:** xenon-translate at xenon-tx b9fadab plus its uncommitted cfgstruct fix, the same code for both runs.

  | | PASS | FAIL |
  |---|---|---|
  | base (live workspace) | 528 | 5 |
  | branch (re-prepared copy) | 532 | 1 |

  - **FAIL → PASS:** 82782480 (94/96 failing → 0), 827a8ee0 (91 → 0), 82929e18 (2 → 0), 82d5abf8 (3 → 0).
  - 82eac7f0 stays PASS (0/96 both). Its C changes: the self-call now passes exactly the compared registers.
  - **The other 528: identical C, identical verdicts and identical failing counts.**
  - The 1 left is 82635508, the NaN payload, which belongs to `judge-gaps`.
- **Mutation audit** (`tools/mutation_check.py`, the 5 affected; the mutants come from each side's own C):

  | | Scored | Killed | Score |
  |---|---|---|---|
  | base | 1 (82eac7f0) | 5 of 15 | 45.5 |
  | branch | 5 | 56 of 78 (57 with the 82d5abf8 hint below) | mean 78.7 |

  - On the base side, the other 4 fail their baseline (the C does not pass its harness), so they cannot be scored.
  - On the branch side, 82eac7f0 kills 6 of 16 (46.2), and its coverage is unchanged at 5 of 11 blocks.
  - **82d5abf8's coverage fell from 25 to 18 of 53 blocks.** Its 8 new parameters shift the generator's random
    draws, and its branches depend on the word at `[r3+0x34]+8` (2, 3 or anything else).
    - The normal fix is a hint: `oracle/hints/82d5abf8.txt` = `genrange r3+0x34+0x8 1 4`. It is in the proof
      copy only. **Add it to the live workspace when this lands.**
    - With the hint: 26 of 53 blocks, 48/48 vectors, still PASS, and 9 of 16 mutants killed (60.0; 53.3
      without it).
  - **Totals with the hint: 57 of 78 killed. The kill rate is not lower.**

### GameCube: Skies of Arcadia, all 535 harnesses in the GameCube translator's workspace

- Copied at 17:28 on 2026-10-23; the copy is ppc-tx-100's private workspace.
- **Affected: exactly the 4 known cases.**

  | Function | Change |
  |---|---|
  | 800553b4 | + `param_f1`..`param_f8`, `param_r7`..`param_r10` |
  | 801a47d8 | + `param_f1`..`param_f8`, `param_r7`..`param_r10` |
  | 801d8818 | + `param_f2` |
  | 801eff90 | + `param_r5` |

  The other 531 are identical.
- **Re-prepared:** 48/48 each, coverage 100%.
- **Judge:** ppc-translate at ppc-tx-100 dd82675.

  | | PASS | FAIL | ERROR |
  |---|---|---|---|
  | base | 467 | 57 | 11 |
  | branch | 469 | 55 | 11 |

  - The 11 ERRORs are refused harnesses with no vectors, on both sides.
  - **FAIL → PASS:** 801d8818 (83/96 failing → 0) and 801eff90 (80 → 0).
  - **800553b4 (96 → 30) and 801a47d8 (12 → 9) still fail, on a different cause.** At the second call, f1 holds
    the first call's mocked f1 result, and the C passes 0. That is the "one result per call" gap, which
    `judge-gaps` 3c54d53 (`tc_lastf`) closes.
  - **Judged with judge-gaps 7bb1632's DP and the same translator:** all 4 PASS on the branch's harnesses and all
    4 FAIL on the base's. So with both branches in, all 4 flip.
  - **The other 531: identical C, identical verdicts and identical failing counts.**
- **Mutation audit:**

  | | Scored | Killed |
  |---|---|---|
  | base | 0 (all 4 fail their baseline, under either DP) | — |
  | branch, master's runtime | 2 | 22 of 23 |
  | branch, judge-gaps' runtime | 4 | 49 of 53 (mean 95.3) |

  **The kill rate is not lower.**

### Tests

- `tests.test_xbox360_scripts` (14) passes, and the GameCube scripts compile with javac against Ghidra 12.1.2.
- `test_checkout_state.SharedScripts.test_shared` now expects every file of the shared folder, TcPassThrough
  included.
- **Full suite: 1556 passed, 2 skipped.** `test_fsca_rm::PrepareHeals::test_cause_named` is deselected (it
  takes a real broker token).

## Merging and landing

- **Text.**
  - The branch merges with `judge-gaps` without conflicts (`git merge-tree`).
  - GameCube's `shared_scripts` line is identical to judge-gaps'.
  - `TcPassThrough.java` sits one line away from judge-gaps' locked-script additions.
- **One ordering step after merging with judge-gaps.**
  - judge-gaps' GameCube `TcPrepare` builds `TcTrack.on` from `proto` right after `prototypeOf(di, fn)`. That is
    before `passThrough` adds its registers.
  - Move that line below `passThrough(proto, called);`. Then the pass-through registers count as set at entry
    for pointer-call arguments (`CA`).
  - Left as it is, those registers are only missing from CA; nothing is compared wrongly.
- **Stamps.**
  - Every Xbox 360 and GameCube stamp changes: a new shared script, and edits of TcFlow and TcPrepare.
  - Land with the judge-gaps re-stamp, then re-judge only on re-stamped harnesses.
  - Xbox 360's `stamp_scopes` entry ("single_fp") stays valid: neither TcFixups nor TcSingle changed.
- **Known limit.**
  - A callee's own record uses the callee's TcFlow prototype, without its own pass-through registers.
  - If a callee passes a register through to a deeper callee, the caller compares only what the callee's
    signature lists. That is one level, consistent with what the callee's own C receives.
