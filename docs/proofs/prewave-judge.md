# Pre-wave judge: what changed and the proof

Branch `prewave-rebased` puts the pre-wave judge items 1-3 on master (judge-gaps 70ab762). It makes the judge
stricter and loses no mutation kill against master: with the new rules on, the judge kills 72 more of the same
mutants and every one master kills. With them off, it judges exactly as master does.

Every rule is behind a `target.json` flag, all off by default. Turning them on is a separate decision.

## The changes

| Commit | What |
|---|---|
| 789f901 | `prewave-judge` (d17cdc7..d3d5629, 8 commits) squashed onto master, its conflicts with judge-gaps, waste-audit-2 and compiler-proof resolved. Coverage in verdicts and the mutation audit baseline; targeted vectors (`branch_coverage`, `targeted_vectors`); the stricter judge (`mock_junk`, `mock_writes`, `call_snapshots`, `exit_frame`). The Dreamcast `TcOracle.java` stays at its 400-line cap |
| 5c30c57 | `test_r14_live`: the vectors header now comes in two parts (the head, then `TcStrict.header`) |
| 9027a57 | Float-register junk is a normal float in +-[1, 2) (below) |
| 0f4232e | The Dreamcast and Saturn runners paint the stack before each vector, as the GameCube runner has done since judge-gaps 7bb1632 (below) |

What the strict flags do, in short:
- `mock_junk`: a mocked callee leaves junk from a fixed random stream in the scratch registers it does not return
  in. A C that reads one back after the call matches only by chance.
- `mock_writes`: a callee known to write through a pointer argument (listed by hand, with evidence) writes junk
  bytes there at each mocked call, on both sides.
- `call_snapshots`: at each call, the bytes stored through each compared pointer argument are compared.
- `exit_frame`: the callee-saved registers are compared at exit; the stack pointer is reported only.
- `branch_coverage`, `targeted_vectors`: extra inputs are chosen to reach branches the random ones miss, and the
  verdict reports how many blocks the vectors cover.

`core/strict_judge.py` describes the four strict flags in full.

## Decisions taken, each the strictest option

The coordinator asked me to make the user's calls overnight and take the option that keeps the judge strictest.

1. **Squash and rebase.** The 8 branch commits conflicted with three branches that landed since. I put them on
   master as one commit and resolved each conflict once, keeping both sides' checks. The original 8 commits stay on
   `prewave-judge` for the history.
2. **Inputs chosen with the flags off; the flags-on run replays them.** The strict flags change what the original
   computes (junk in registers), so choosing the inputs with them on would choose different, fewer inputs. The
   inputs are chosen as master chooses them, then re-run with the flags on. A replay that fails is dropped. No
   function in the five games lost a valid input this way (table below).
3. **The reach pass keeps master's constants** (d3d5629). The targeted-vector pass reads the constants a function
   compares against. It reads them whether or not `targeted_vectors` is on, so the flags-off inputs are byte for
   byte master's.
4. **Float junk is a normal float in +-[1, 2)** (9027a57). `mock_junk` first wrote raw random bits into fr1..fr11.
   Toy Commander 8c06161c multiplies two junked matrices (`ftrv`); random exponents made denormal results, which the
   oracle refuses, so 68 of its replays failed. It fell below 16 valid inputs, and 8c061698 and 8c061e54 lost 15
   and 19. The junk now keeps the stream's sign and 23 fraction bits with 1.0's exponent: every register is still
   junked, junk never matches a real value by chance, and its product with a normal float stays normal. The other
   options were to leave those registers un-junked or to let the functions be refused. Both judge less.
   All three keep their flags-off counts (48 of 48).
5. **Stack paint on the Dreamcast and Saturn runners** (0f4232e). Grandia 0605a282 lost two kills with the flags
   on: its mutants m05 (deletes `a.r[1] = 0`) and m06 (changes a constant). The C fills only some of a `TcArgs`, and
   an unset slot reads whatever the host stack holds. With the flags on, that was the runner's own leftovers, which
   happened to be 0, the expected value. The runner now fills 64 KB of stack with `0xa5` before each vector, so an
   unset slot reads the same bits on every run, machine and flag setting. This is the GameCube fix (judge-gaps
   change 8). The two kills are back. `tests/test_sh_paint.py` builds the real runners and checks it.
6. **Comparing per mutant against master.** Master's `core/mutation.py` does not report each mutant's outcome.
   The proof's master tree used the branch's `core/mutation.py`, whose only change is that report. The verdicts
   are master's. Without it, a lost kill could hide behind a gained one.

## The proof (laptop2, MSVC 14.44)

All of it ran on laptop2, on private copies of five workspaces: Grandia, Skies of Arcadia, Toy Commander, Power
Stone 2 and Spawn (main). The functions are the mutation audit's standing sample (`tools/mutation_audit/sample.json`).
Every one was prepared fresh in three runs:
- `land_off`: master, flags off;
- `rb_off`: the branch, flags off;
- `rb_on`: the branch, all six flags on.

The scripts and results are in `2026-10-24/prewave/`.

### Mutation audit: 0 kills lost

The same mutants (10 per function), judged by each tree on its own harnesses (`l2audit.py`). The gate is
`tools/mutation_audit.py gate RUN --baseline audit_land_land_off.json`.

| Game | Functions | Mutants | Master kills | Branch, flags on | Gained | Lost |
|---|---|---|---|---|---|---|
| Grandia | 119 | 601 | 482 | 485 | 3 | 0 |
| Power Stone 2 | 150 | 1263 | 952 | 977 | 25 | 0 |
| Skies of Arcadia | 83 | 713 | 608 | 617 | 9 | 0 |
| Spawn (main) | 147 | 1030 | 784 | 801 | 17 | 0 |
| Toy Commander | 149 | 1218 | 976 | 994 | 18 | 0 |
| **All** | **648** | **4825** | **3802** | **3874** | **72** | **0** |

- **Flags on against master: PASS.** 72 gained, 0 lost, 0 outcome flips.
- **Flags off against master: PASS.** Every mutant's outcome is the same, with one exception: Spawn 8c053e00's
  m10 survives on master and is killed on the branch. The paint is the reason.
- Before the paint, the branch's flags-off outcomes were the same as master's for all 648 functions.

### Flags off: the same harnesses as master

`l2cmp.py` compared every flags-off harness with master's, byte for byte (`meta.json` without its script hash).
All 652 are identical:

| Game | Functions | Identical |
|---|---|---|
| Grandia | 119 | 119 |
| Skies of Arcadia | 83 | 83 |
| Toy Commander | 150 | 150 |
| Power Stone 2 | 150 | 150 |
| Spawn (main) | 150 | 150 |

With the flags on, 648 harnesses differ, as expected: the inputs carry the junk seeds and the targeted inputs, and
the expected results carry the new comparisons. 4 are unchanged by the flags (Toy Commander 1, Spawn 3).

### Flags on: no function loses an input

For every function, the valid inputs with the flags on (dev and hidden sets) are compared with those flags off. In
all five games, 0 functions have fewer, and none drops below 16. The flags-off counts equal master's in every function.

### The paint changes no flags-off verdict

The paint can change a result only where C declares a `TcArgs` without setting all of it. Every matched C function
like that was judged with the flags off, by master and by the branch, on the same harness (`l2paint.py`). That is
2610 functions:

| Game | Functions | PASS, both sides |
|---|---|---|
| Grandia | 13 | 13 |
| Toy Commander | 420 | 392 |
| Spawn | 340 | 323 |
| Power Stone 2 | 1837 | 1735 |

The verdict, the stage, and the failing and total counts are identical for all 2610. The failures fail the same way
on master. 8 Power Stone 2 candidates have no harness on either side.

### The committed baseline is out of date

Gated against the committed `tools/mutation_audit/baseline.json`, the branch shows 3 lost kills: Power Stone 2
8c06dd80 m10, 8c0e55a6 m02 and 8c12f844 m10. It also shows outcome flips in 14 functions:
- Power Stone 2: 8c03cbf6, 8c0d761c;
- Skies of Arcadia: 80096260, 800c86ec, 800cf988, 800d1a34, 80226984;
- Spawn: 8c0184ba, 8c022420, 8c053680, 8c060a00, 8c063620;
- Toy Commander: 8c01c58e, 8c053a90.

None of this comes from this branch. Master itself fails that gate: the same 3 kills lost, the same 14 flips, and
one more lost kill (Spawn 8c057a80 m08) that the branch wins back. The three Power Stone 2 outcomes are identical on
master and the branch (`KKKKKBBSKS`, `SSSSSKSBSK`, `KKSKKKBBKS`). The baseline was recorded before judge-gaps landed.
judge-gaps changed these functions' verdicts on purpose; its proof explains the Skies functions, for example.
Integration should refresh the baseline from master's audit when this lands.

### Excluded

Toy Commander 8c061fbc: Ghidra times out preparing it in every run, master's included. It is left out on both sides.

## Owed: the same judging on the main laptop

This check was not run here, because the main laptop is in use. The coordinator ruled that it does not block
landing. It is owed before any judging restarts on the main laptop, and at the latest when TC, Spawn or PS2 game
work resumes there.

**What it shows.** The main laptop's judge gives every mutant the same outcome as laptop2's on the same harnesses.

**How to run it**, on the main laptop with `config.local.json`'s vcvars pinned to 14.44 (as in
`2026-10-23/compiler_14.44.md`):
1. Make a folder, for example `C:\dpwork\prewave-local`, laid out like laptop2's `%USERPROFILE%\dpwork\prewave`:
   - `ws\` copied from laptop2;
   - `repo-rb`: a worktree of this branch's head;
   - `repo-rb\tests\.work\probe-KEY_rb_on\harness`: the five keys' harness folders, copied from laptop2.
2. Run, from `repo-rb`:
   ```
   $env:DP_PREWAVE_DIR = "<the folder of step 1>"
   python tools\bounded.py 21600 py -3.12 docs\proofs\2026-10-24\prewave\l2audit.py 4 rb:rb_on
   python tools\mutation_audit.py gate "$env:DP_PREWAVE_DIR\proof\audit_rb_rb_on.json" --baseline docs\proofs\2026-10-24\prewave\audit_rb_rb_on.json
   ```
3. **It passes** when every game shows gained 0, lost 0 and flips 0. A PASS with gains is not enough: the two
   laptops must agree on every mutant.

## Landing

- The oracle's scripts change (TcJunk, TcStrict, the runners), so land with a re-stamp, as for judge-gaps. Rejudge
  only on re-stamped harnesses.
- With the flags off, harnesses and verdicts are master's, so no re-prepare is needed until a flag is turned on.
- Refresh `tools/mutation_audit/baseline.json` from master's audit (above).
- Turning the flags on, for each target, is a separate decision for the user.
