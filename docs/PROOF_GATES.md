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
