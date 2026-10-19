# Proof gates: coverage and mutation

A PASS proves that the matched C equals the original on the vectors the oracle ran: return value, written bytes,
calls (target, order, declared arguments) and XMTRX, bit for bit, on 48 dev and 48 hidden vectors. That proof has
two gaps byte matching would not have:

1. **Untested paths.** Blocks of the original that no valid vector reaches are never compared, so the C there is
   unjudged. The oracle has always reported block coverage (`oracle_summary.json` `blocks_total`,
   `blocks_hit_all`), but low coverage did not fail a function.
2. **A blunt harness.** A harness can reach a block and still not notice when the C computes something else there,
   for example when the result is never observed or the inputs never separate `<` from `<=`.

The two gates close most of both gaps. Neither needs an agent or Ghidra: they read the harness the function
already has.

## 1. Coverage gate (core/proof.py)

**What it proves:** the valid vectors run (almost) all of the original's basic blocks, so the PASS covers
(almost) all of the C.

Config `coverage_gate` (config.json; a project's `project.json` `coverage_gate` overrides it key by key):

| key | default | meaning |
|---|---|---|
| `min_block_pct` | 90 | pass when `blocks_hit_all / blocks_total` is at least this share |
| `allow_uncovered_blocks` | 1 | ... or at most this many blocks are unreached |
| `min_blocks` | 3 | ... where that allowance applies only from this many blocks up (a 2-block function needs both) |
| `enforce` | true | false: report only. The final audit's `proof` check is INFO instead of FAIL; the functions stay `matched_low_coverage`, counted and in the backlog |
| `waive` | {} | `{addr: reason}`: blocks no input can reach, judged by hand; counted as verified, reason shown |

A function may leave `max(floor(total * (100 - min_block_pct) / 100), allow_uncovered_blocks if total >=
min_blocks)` blocks unreached. Under the defaults that is one block up to 19 blocks, then 10 %.

These defaults come from the data. Across TC, Spawn and PS2, about 70 % of matched functions reach every block. The
next largest group is functions with exactly one unreached block, and most of those have 6 to 10 blocks. At 90 %
alone, every function of 6 to 9 blocks with one unreached block would fail. "All but one" accepts them, and above
19 blocks the 10 % rule takes over. No matched function today has 2 blocks with one unreached, so `min_blocks` 3
costs nothing now and keeps a later 1-of-2 case out.

**States.** The ledger status stays `matched`: the C passes every vector, and the readable stage, doctor and retire
need the matched C. The proof state is derived from the harness summary:

- `verified`: the coverage gate holds (or is waived), and in mutation `enforce` mode the score holds too;
- `matched_low_coverage`: below the coverage gate. This is not a FAIL of the agent's C and the function is not
  skipped, but it is not counted as verified. It is work for the reach pass, the watcher's hints or more vectors;
- `weak_harness`: in mutation `enforce` mode, a mutation score below `min_score`. In report mode this is only a
  flag.

**Where it shows:**

- the ledger row's `proof` (written by `job.finish` at a match), with the block counts appended to
  `verdict_detail`;
- the server overview (`verified`, `matched_low_coverage`, `weak_harness`) and the monitor UI cards;
- the doctor's figure (a note, never a failure);
- the final audit's `proof` check, which FAILs while a function is `matched_low_coverage` and the gate enforces;
- `tools/coverage_gate.py`.

```
python tools/coverage_gate.py --project WS [--project WS2] --report [--top 15] [--list low.txt] [--json out.json]
python tools/coverage_gate.py --project WS --min-pct 80 --allow 2      # try other settings, this run only
```

`--list` writes the low functions for `@FILE`. Today's reach pass (`tools/call_coverage.py reach @low.txt`)
targets unreached **call sites**, so it helps only where unreached blocks hold calls. A block-targeting reach pass
is staged for later (Proposals, below).

**Which blocks.** `TcPrepare.java` writes `blocks_unhit` to `oracle_summary.json`: the start addresses of the blocks
no valid vector (dev, hidden or reach pass) reached. It is left out when every block is reached and on a refused
harness. It is output only: vectors, expected results, meta.json and verdicts are unchanged, so selective_restamp's
identity sample (vectors.txt, expected.txt, meta.json) still holds. `proof.unhit_blocks` reads it (None for a
summary from before the field). The coverage verdict keeps it as `unhit`, and `describe` names the blocks in
`verdict_detail` and the backlog queues. The watcher's brief and try results add them after the block counts. The
field rides r14's re-stamps: every TC harness gets it, and on Spawn and PS2 each harness gets it when it is next
re-prepared (r14's candidates, then the backlog).

## 2. Mutation check (core/mutation.py, core/mutation_ops.py)

**What it proves:** the harness tells the matched C from small wrong versions of it, so a PASS means something
specific about this C and not merely that the code ran.

After a PASS, deterministic mutants of the matched C are generated on the host. A small C tokenizer lists every
candidate edit, and a seeded draw (seed, address, source hash) takes up to `max_mutants`, round-robin over the
operators:

| operator | change |
|---|---|
| relop | `<` `<=`, `>` `>=`, `==` `!=` |
| arith | binary `+` `-`, `+=` `-=` |
| bitwise | binary `&` `\|`, `&=` `\|=` |
| shift | `<<` `>>`, `<<=` `>>=` |
| const | an integer literal +1 or -1 (not in initializers, `case` labels, array sizes or preprocessor lines) |
| negate | `if (c)` / `while (c)` -> `(!(c))` |
| delete | one store or assignment statement (`x = ...;`, `a.r[0] = ...;`, `wr32(...);`) becomes `{ }` |
| swap_args | two adjacent different call arguments, or the values of two adjacent TcArgs slot assignments |
| return0 | `return e;` -> `return 0;` |

Each mutant is judged with the official check (`check.run_check`: lint, the `/W4 /WX` build, dev + hidden vectors,
the byte-exact compare) on the function's stored harness. The outcomes are:

- **killed**: it FAILs, its runner times out (`mutant_timeout_s`), or its result file outgrows the judge's cap.
  The cap is max(16 MB, 8 x `expected.txt`), in `check.run_runner`, which both judges use. It catches C that
  writes across the whole guest RAM, for example a mutant turning `r5 += 0x34` into `-=` in a fill loop: the
  runner stays bounded, but the compare would parse GBs into memory;
- **survived**: it PASSes;
- **dropped**: lint or the build refuses it (an unused variable under `/WX`, a type error). Dropped mutants are
  counted but not scored.

The matched C itself is judged first. A baseline that does not PASS stops the check for that function (`error`),
since a stale harness says nothing.

**Survivor classes.** The judge deliberately ignores two things, so survivors there are equivalent mutants by
design and not harness weaknesses:

- `arg_slot`: a TcArgs slot assignment (`a.r[3] = ...`, `args.s[i] = ...`, `tc_args4(...)`, or the `for` header of
  a loop that only fills slots). A call is compared only on the registers the callee's signature declares, so a slot
  no callee reads is not behaviour;
- `stack`: a store to the guest stack (`wr32(sp + 4, ...)`, `wr32(0x8cffdff4, ...)`) or to the stack pointer
  variable. Writes at or above `compare.STACK_LO` are scratch and never compared.

The class comes from the statement holding the edit. `sp`/`frame`/`r15`/`fp` name the stack, and so does any
variable set from a `0x8cffXXXX` address or from another stack name plus an offset (`u32 fb = 0x8cffdfacu;`).
Everything else is `logic`.

- `score` = killed / (killed + logic survivors) x 100;
- `score_raw` counts every survivor;
- logic survivors flag the harness `weak_harness`. Some may still be equivalent (`<=` where equality cannot occur,
  a store overwritten later), and each is listed with its diff for a reader.

Config `mutation_check`:

| key | default | meaning |
|---|---|---|
| `mode` | report | off / report (records and flags only) / enforce (`score < min_score` is not verified) |
| `min_score` | 80 | the enforce threshold |
| `max_mutants` | 16 | per function |
| `time_cap_s` | 240 | per function; the rest is not tried (`time_capped`) |
| `mutant_timeout_s` | 10 | the runner's limit per mutant (a looping mutant is killed) |
| `seed` | 1 | the mutant draw |
| `on_pass` | false | run it in the worker right after each PASS (`job.prove`) |

Cost: about 1-2 s per mutant (one `/W4 /WX` build and one run of 96 vectors), so 15-35 s per function with 16
mutants. `on_pass` stays off until the monitor has RAM and CPU room for one extra `cl.exe` per worker.

```
python tools/bounded.py 7200 python tools/mutation_check.py --project WS --sample 50 --report --json out.json
python tools/bounded.py 1800 python tools/mutation_check.py --project WS --references --gate --report
```

Without `--report` the records are merged into `state/mutation.json`, which the proof state reads.

## 3. Float gaps: fr0 results and float constants (2026-10-18)

Phase D found two ways a wrong readable function passed in Toy Commander.

**The float result was not compared.** The oracle records r0 and fr0, but `compare_return` compared only the
register the signature names. TC 8c01becc is `rts; fldi0 fr0` (it returns 0.0f), and Ghidra typed it
`u32 fn_8c01becc(void)`.
- `core/ret_reg.py` follows every path of the packet listing to each `rts` (delay slot included). It notes
  whether fr0 was written after r0 and not read again. "Returns in fr0" means some exit is like that and no exit
  writes r0 last. A write to fr0 while `frchg` has swapped the banks is XF0, not the result (TC 8c0613cc and
  8c061780 load XMTRX; the first gate run flagged their fixtures).
- The listing alone over-reports. TC 8c04ea84 sets r0 first, then loads fr0 for a loop its early exit skips, so
  that exit has fr0 "written last"; its 12 callers all read r0. A function counts only when its callers agree
  (`ret_use.fr0_confirmed`: a call site reads fr0, none reads r0). `tools/float_gaps.py --write` puts those in the
  workspace's `state/fr0_results.txt`: TC 8c05dd10, 8c0611c8, 8c061ba4, 8c061c88; none in Spawn main/ovl or PS2.
- `check.run_check` and `readable_check.compare` set meta `fr0_result` for a listed function whose signature is
  void, u32 or s32 (regress copies the list into its private state). `compare_return` then compares fr0 too, and
  the message asks for a raw_ret f32 override.
- The runner leaves fr0 at 0.0 for a non-f32 thunk, so a 0.0f return like 8c01becc still passes the compare.
  The signature is the real fix: `tools/sig_override.py --candidates` lists these functions as `fr0_exit`.

**Float callee results and pool floats were not tested.** TcInputs draws small integers and pointers as mocked
callee results (0..3 for the first calls). As f32 bits these are denormals, so an f32 callee always "returned"
about 0.0. TC 8c035a0c compares 8c0612e4's f32 result with the pool word at 0x8c035b10 (1e-06). The readable C
wrote 1.0f there and passed every vector.
- **TcFret** (oracle, locked). For a callee whose prototype returns f32 (TcPrepare collects them), a result that
  is not a plain number becomes one of 16 plain floats, picked by its bits. TcOracle writes the value back into
  the vector, so vectors.txt and the runner agree. Plain numbers, 0, the denormal 1 and scratch pointers (tiny
  negatives) are kept, so the paths for a tiny result stay tested (the first gate run lost two fail fixtures,
  tc-fpscr-dn-8c033f22 and tc-fr-through-call-8c03a2f6, while every result was mapped).
- Re-stamp: `tools/selective_restamp.py --change fret`. Its candidates are the harnesses with an f32 callee
  record: TC 281, Spawn main 154, Spawn ovl 44, PS2 720.
- **`core/float_lits.py`** (host only) reads the f32 constants the original takes from its literal pool:
  - the words a pool `mova` hands to `fmov.s @rN` / `@rN+`;
  - `mov.l` loads the listing notes as an f32.
  It checks whether a C text writes each one as a literal, a readable header name, a folded quotient, an integer
  of the same value in f32 arithmetic (a `*` or `/` operand, or under an `(f32)` cast, with header names put in
  place), or a read of the pool address. The agent sandbox already notes `mova` words (batch 29), and `mov.l` words
  are noted in the packet. The packet stays unchanged, since it is an oracle input.
  - Until 2026-10-19 an integer of the same value anywhere in the text counted. TC 8c078f94's C has KC = 0.5f for
    the pool's -6.0f at 0x8c079348, and its `6u` offsets hid that. The function was still listed, because its
    readable C lacks -6 too, but it was listed as `readable_lost` (matched C right). PS2's TC staging proof found it
    on the fixed ftrc runtime: 8/102.
  - The narrower rule leaves the listed functions as they were on every game: TC 16, Spawn main 1, ovl 12 and PS2
    16 literal rows.
  - It corrects two TC cases from `readable_lost` to `matched` (8c078f94, 8c0495b0's 7.0f).
  - It adds constants that were hidden this way to listed functions: ovl 8c59f960 399.0f; PS2 8c072c58 9.0f,
    8c0afe94 nine and 8c0d64a6 three.

**The scan.** `tools/float_gaps.py --project WS --write` writes `state/float_gaps/` in each workspace:
- `requeue.txt`: the confirmed fr0_result functions, plus pool-literal suspects. A readable C can lose a constant its
  matched C had (`readable_lost`), or the matched C can lack it too (`matched`).
- `rejudge.txt`: functions that read fr0 after an f32 call. Re-judge them after their TcFret re-prepare.
- `summary.json`.

It requeues nothing, because each game's session runs its own list. The literal suspects are for review: a
constant the C computes some other way is a false suspect.

**Float arguments were drawn too coarsely (r15).** TcInputs draws an f32 parameter with niceFloat (0, +-1, 0.5,
integers, spreads), so a value next to a pool constant the code compares against almost never comes up. TC
8c023148 sets a rectangle's translucent flag with `fcmp/gt fr8,fr3` against the pool's 0.999; its matched C wrote
`1.0f > a`, which differs only for a in [0.999, 1.0), and passed.
- **TcFguide** (oracle, locked; TcFret's counterpart for arguments). The locked side harvests the f32 constants
  an `fcmp/gt` or `fcmp/eq` tests (a pool word loaded through `mova` + `fmov.s @rN` / `@rN+`, or copied with
  `fmov`). In about a third of the vectors (every vector of a rescue pass) each f32 parameter (fr4..fr11, or
  r4..r7 typed f32) gets, with even odds, one of them or its neighbour one ulp down or up.
- Its generator is its own, seeded from the vector's (seed, id, try): TcInputs' and TcGuide's sequences are
  unchanged, and a function with no such constant or no f32 parameter keeps its vectors. It does not wait for
  `coverage_inputs`. TcVectors calls it after TcGuide and before the hints.
- Re-stamp: a script change, so every harness looks stale; only functions with an f32 parameter and a harvested
  constant get different vectors. `tools/selective_restamp.py --change fargs` (or `fret+fargs`) lists a superset:
  an f32 parameter, and a `mova` and an `fcmp/` in the packet (TC 35, Spawn main 21, ovl 31, PS2 27).
- Measured (2026-10-18, regress private copies, before/after on the same 50 matched functions: all 34 TC
  candidates, 16 seeded ovl ones): vectors per harness unchanged (4852 in all), 824 vectors (17%) different in 29
  functions, prepare time 756 s before and 732 s after (noise). Every matched C still passes; 8c023148's
  `1.0f > a` fails (1/96, hidden set) and the form with the pool's 0.999 passes.

## 4. RAM views: the mirror rule (r15, 2026-10-18)

**Where it came from.** A coverage sample (r15 item 3) drew 100 functions below the coverage gate, then raised
their vectors: `coverage_inputs`, then 4x, then 16x the default count.
- It cost 3.02 prepare hours, and 37 of the 100 reached the gate.
- Three previously passing PS2 functions failed: 8c106034, 8c182850 and 8c073ba8. All three are one class. The
  pool holds a RAM address as 0x0c...... (P0/U0), the matched C writes it as 0x8c...... (P1), and the value goes
  to a callee as an argument.
- The runtime and the port fold the two views onto the same 16 MB (`tc_rt_internal.h`), so memory access through
  either view is identical.
- The full backlog run (about 61 prepare hours for about 60 failures of this one class) was dropped: the gain isn't
  worth the cost.

**The rule.** `core/compare.same_word` treats 0x0c000000..0x0cffffff and 0x8c000000..0x8cffffff as equal views
of one word. The judge applies it wherever it compares a value that can be a RAM pointer:
- a call's integer argument (r0..r14 and the stack words);
- the result in r0 (u32/s32), or in a nonstandard result register;
- r8..r14 at exit;
- a stored word, but only when both sides store all four bytes. A byte stored alone stays a value.

A wrong address still fails (0x8c86d08c for 0x0c86d088), and so does another area (0x0d, 0xac).

Proof (2026-10-18), on the sample's own harnesses (judge only):

| | master judge | with the rule |
|---|---|---|
| 8c106034 | FAIL 1/1544 | PASS |
| 8c182850 | FAIL 1/96 | PASS |
| 8c073ba8 | FAIL 2/1538 | PASS |
| 8c182850 with 0x8c86d08c | FAIL | FAIL |

**Review list.** The two views differ only where code compares the pointer itself, and the judge no longer sees
that. `tools/mirror_ptrs.py --project WS --write` writes `state/mirror_ptrs/` in each workspace:
- For every matched function that loads a 0x0c...... data word from its pool, the pointer is followed through the
  function's own asm and into the callees that get it in r4..r7, two calls deep. This is a may-flow over the
  listing (core/sh4_asm.Flow).
- A function is listed when it, or such a callee, compares the pointer with a register that doesn't hold it, using
  `cmp/eq`, `/hs`, `/hi`, `/ge` or `/gt`. A list walk's `p == head` is an example.
- Two such pointers compared (a `p < end` over one table) aren't listed.
- Neither is a function whose C writes every word as 0x0c.
- `review.txt` holds `addr reason`; `summary.json` holds the rows and the callees with no packet to read.
- Nothing is requeued.

As of 2026-10-18, the functions writing the other view and the review rows are:

| workspace | functions writing the other view | review rows |
|---|---|---|
| TC | 0 | 0 |
| Spawn main | 552 | 2 |
| Spawn ovl | 573 | 1 |
| PS2 | 1,860 | 8 |

**Mixed views in the C (`tools/ptr_views.py`, 2026-10-18).** PS2's review found two real differences:
- 8c02e910 → 8c07bbf4's `cmp/eq` on +0x377c;
- 8c0bbb9a's self-skip.

The cause is matched C that mixes the views: PS2 has 297 files that write 0x0c532698 as 0x0c, and 105 that write it as 0x8c.
- 8c07cedc stores a player pointer at +0x377c. The original computes it from the pool's 0x0c532698, but the C computes
  it from `0x8c532698u`. 8c07bbf4 then compares it with the 0x0c pointer that 8c02e910 reads from its own pool, and
  the self-skip never matches.
- `tools/ptr_views.py --project WS --write` writes `state/ptr_views/` (`requeue.txt`, `summary.json`). For every
  matched function, it compares the pool's RAM data words with the hex literals in its C (comments taken out). A
  literal is listed when it is the other view of a pool word, or the other view of an address up to 64 KB above one.
- Only value sites requeue: the pointer is kept as a call argument, a stored word, a return or a compare, directly or
  through the locals it is assigned to, up to their next assignment.
- An access address (`rd32(T + 4u)`) reaches the same RAM in either view, as does a `(u8)` store. Those functions
  are `access_only`.
- Nothing is requeued automatically. It's static only: no Ghidra, no runner.

| workspace | other view in the C | access only | requeue (value sites) |
|---|---|---|---|
| TC | 0 | 0 | 0 |
| Spawn main | 565 | 502 | 63 (218) |
| Spawn ovl | 598 | 521 | 77 (245) |
| PS2 | 1,894 | 1,615 | 279 (730) |

- It lists 8c07cedc (the +0x377c writer) and 8c0bbb9a.
- 20 random requeue rows read by hand: 18 keep the pointer as a value. The other 2 are a helper that returns the
  address, and a `q < lim` over one table in one view.

## Read-only results (2026-10-17, harnesses as they were; PS2 mid re-stamp)

Full reports are in the monitor's notes folder (`proof-gates/coverage_gate.*`, `proof-gates/mutation_*.*`).

**Coverage gate** under the defaults (`tools/coverage_gate.py --report`, every matched function):

| workspace | matched | below the gate | share | 0 unreached | 1 unreached |
|---|---|---|---|---|---|
| Toy Commander | 2676 | 257 | 9.6 % | 2155 | 215 |
| Spawn main | 1646 | 160 | 9.7 % | 1370 | 104 |
| Spawn ovl | 1340 | 270 | 20.1 % | 908 | 136 |
| Power Stone 2 main | 6444 (10 without block data) | 1244 | 19.3 % | 4482 | 571 |

The worst cases are large switch or dispatch functions whose vectors reach one arm (TC 8c027bd6: 4 of 234 blocks;
PS2 8c0d64a6: 14 of 136). A looser trial on TC (`--min-pct 80`) flags 178, and `--min-pct 70` flags 131.

**Mutation check** (`tools/mutation_check.py --sample 50 --seed 1 --report`, 16 mutants per function):

| workspace | scored | mean score | median | raw mean | with logic survivors | below 80 | mutants tried / killed / dropped |
|---|---|---|---|---|---|---|---|
| Toy Commander | 48 (2 empty) | 92.7 | 100 | 75.6 | 18 | 7 | 652 / 484 / 62 |
| Spawn main | 45 (4 empty) | 94.6 | 100 | 88.4 | 12 | 3 | 517 / 445 / 88 |
| Spawn ovl | 50 | 76.4 | 86.2 | 65.9 | 33 | 20 | 679 / 434 / 95 |
| Power Stone 2 main | 49 (1 empty) | 81.7 | 90.9 | 74.0 | 30 | 17 | 683 / 502 / 77 |

Survivors by class: TC 91 `arg_slot`, 32 `stack`, 45 `logic`; Spawn main 32 / 15 / 25; Spawn ovl 58 / 59 / 128;
PS2 46 / 16 / 119.
The logic survivors are mostly of three kinds:

- boundary relops (`>` vs `>=` where the vectors never hit equality);
- `+`/`-` or ±1 on an address or offset whose result is never observed;
- a deleted load or store whose value is overwritten or never compared.

**The two gates overlap.** About half of Spawn ovl's logic survivors (67 of 128, in 11 functions) sit in functions
the coverage gate already flags. They are in code no vector runs, for example everything after an early return
that every input takes (8c458880). PS2 has 68 of 119 in 12 functions, TC 15 of 45 in 4, and Spawn main 2 of 25. The
rest are in reached code. In Spawn ovl most of that is arguments to calls through function pointers, where the
judge compares only the registers a declared signature names.

**Proposed defaults.** Keep `mode` at `report`. With `enforce` and `min_score` 80, 7 to 40 % of the sampled
functions would drop out of verified (TC 7/48, Spawn main 3/45, Spawn ovl 20/50, PS2 17/49), and part of that
is still equivalent mutants. The gap is best closed by the coverage gate first (it explains about half of the logic
survivors), then by reading the logic survivors of the reached code, before any enforcement.

## Stage -> gate -> deploy

- **Stage.** Branch `proof-gates`, built on `r14-live` because r14 changes the oracle. Unit tests
  (`tests/test_batch32.py`, in `tests/test_tooling.py`) and the read-only reports above. One judge change: the
  runner output cap (`check.run_runner`, used by `check.run_check` and `readable_check.run`). It only touches a
  result over max(16 MB, 8 x `expected.txt`), which no passing C produces, so no verdict of a passing function
  changes. The mutation check hit this on PS2: a runaway write loop pushed RAM to 97 %. The same can happen with an
  agent's C in a worker. `core/lint.py` and the runtime are untouched. One oracle change: `TcPrepare.java` writes the
  output-only `blocks_unhit` field. Like any Java edit it changes `oracle_stamp`, so it lands in r14's landing
  window, right after r14 merges, under the same regress gate, and rides r14's re-stamps (no second re-stamp).
- **Gate**, after r14 lands and with the monitor's go (servers stopped, Ghidra slots free):
  1. `python -W error tests/test_tooling.py`
  2. `python tools/bounded.py 3500 python tools/regress.py --projects all --sample 15`: the standard gate. It must
     exit 0 exactly as on master: no verdict of a passing function changes.
  3. Per workspace, the host-only gate fixture: `python tools/bounded.py 1800 python tools/mutation_check.py
     --project WS --references --gate --report`. Every reference function's matched C must pass its harness
     (baseline), and each that has a mutant at all (an empty body has none) must have one killed.
  4. `python tools/coverage_gate.py --project ... --report`: the counts match the staged report, give or take
     re-stamps.
- **Deploy.** Rebase on master after r14, then each existing game's own session switches its project to report
  only (Rollout, step 1) before its servers restart. Each new match then gets its `proof` row, and the UI shows
  the counts.

## Rollout: report only for existing games, enforce for new ones

The user's decision (2026-10-17): the coverage gate lands report-only for the existing games. Their flagged
functions are queued for extra test inputs in the background, and each game switches to enforce once its backlog is
clear. New games enforce from the start.

- **New games** get `coverage_gate.enforce: true` from `config.json`. Nothing to do.
- **Existing games** (TC `ToyCommander/decomp`, Spawn `decomp/main` and `decomp/ovl`, PS2 `decomp/main`): each
  game's own session applies this when the branch lands. It sets `"coverage_gate": {"enforce": false}` in
  `project.json`, and only that key:

  ```
  python tools/proof_backlog.py enforce --project WS off            # shows the change
  python tools/proof_backlog.py enforce --project WS off --apply    # writes it
  ```

  Report only still shows every flagged function as `matched_low_coverage`: not verified, counted in the UI and the
  doctor, and listed by the final audit. Only the audit's `proof` check is INFO instead of FAIL.
- **The backlog**, per project:

  ```
  python tools/proof_backlog.py queue --project WS     # state/proof_backlog/: reach.txt vectors.txt harness.txt no_data.txt
  python tools/proof_backlog.py status --project WS    # CLEAR when nothing is below the coverage gate
  ```

  The queues, closest to the gate first:
  - `reach.txt`: an unreached call site and no reach pass yet, for
    `tools/bounded.py 14400 python tools/call_coverage.py --project WS reach @state/proof_backlog/reach.txt`;
  - `vectors.txt`: other unreached blocks. These need more or better vectors (a re-prepare, the watcher's
    `range`/`ptr` hints aimed at the blocks `blocks_unhit` names, or the block-targeted reach pass proposed below),
    or a reasoned `waive` (for example dead code after an early return);
  - `harness.txt`: weak harnesses (mutation check), for hints that separate the surviving mutant;
  - `no_data.txt`: no block counts in the summary (re-prepare).

  All of this is Ghidra work. It runs only in free Ghidra slots, with the servers stopped and under the machine's
  RAM rules, after PS2's stamp and r14. `proof_backlog.py` itself runs no Ghidra and builds nothing.
- **Switching to enforce.** When `status` says CLEAR for a project, its session runs
  `proof_backlog.py enforce --project WS on --apply`. From then on, a new low-coverage match FAILs that project's
  final audit. Weak harnesses and no-data functions are listed by `status` but do not block CLEAR: the mutation
  check stays report-only.

Backlog at queue time (2026-10-17, `proof-gates/backlog/` in the monitor's notes):

| workspace | below the gate | reach | vectors | weak harnesses (of 50 sampled) | no block data |
|---|---|---|---|---|---|
| Toy Commander | 257 / 2676 | 3 | 254 | 18 | 0 |
| Spawn main | 197 / 1713 | 0 | 197 | 12 | 0 |
| Spawn ovl | 307 / 1411 | 1 | 306 | 33 | 0 |
| Power Stone 2 main | 1244 / 6444 | 3 | 1241 | 30 | 15 |

Almost all of it is in `vectors`, because the reach pass has already run on nearly every function with an unreached
call site. That makes block-targeted reach (below) the main lever for clearing the backlog. `blocks_unhit` names
the blocks, but adds no vectors.

## Proposals (not done)

- **Block-targeted reach (staged for later, not built).** `TcReach.pass` collects its targets from the call sites
  with zero hits (`TcReach.java`, `for (... g.siteHits ...) if (e.getValue() == 0) targets.add(...)`). Its search is
  already address-based: `flow.frontier(covered, flow.block(t))` finds the branches toward the block holding `t`,
  and `burst` mutates the inputs read before them. Adding the start of each block not in `covAll` as a target is
  about 5 lines, behind a facts switch like `reach_calls` (for example `reach_blocks`, default off). Its found
  vectors are emitted like the call-site ones, after the first pass's, and the blocks they reach drop out of
  `blocks_unhit`.
  - It is **not** output only. It adds vectors and expected results, and so may turn a passing match into a FAIL
    on newly reached code. That is the point: it is real wrong C found. A function whose blocks are all reached has
    no target, so its harness stays byte-identical.
  - Re-stamp cost: the switch is in the facts file, which `oracle_stamp` hashes. Turning it on is a selective
    re-stamp whose candidates are the functions with an unreached block, about 3,300 across the four workspaces
    (the backlog plus fully passing functions with their 1 allowed block). That is too large to bundle into r14.
  - Run it per game from `proof_backlog` queues (`vectors.txt`) in free Ghidra slots, under the RAM rules, then
    `proof_backlog.py status` until CLEAR, then `enforce on`. Each re-prepare's must-phase judges the matched C on
    the stronger harness. A new FAIL goes back to the queue as a real defect.
  - Budget: the reach pass shares `reachRuns`/`reachS` with the call-site targets. Block targets probably want
    their own run budget, so a function with many unreached blocks does not starve its call sites.
- **Feed weak harnesses to the watcher.** The watcher takes only oracle skips today. `matched_low_coverage` and
  `weak_harness` functions could become a second, lower-priority queue for hints (`range`, `ptr`) that separate
  the surviving mutant.
