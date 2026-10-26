# Proofs of the second waste audit (2026-10-23)

Each proof ran on private copies or scratch folders. Nothing live was written. The scripts are in this folder.

## Proof A: the GameCube readable judge patch (rows 3 and 5)

**What changed** (`docs/proposals/gc_readable_judge.patch`, not landed):

- the GameCube readable build uses the shared runtime cache (`readable_check.runtime_rd_objs`) and the kept runner,
  with its own runtime folder. `tc_ppc.c` is kept compiled unoptimised in the cache, so it is no longer recompiled
  in every build;
- `rd_mem.c` skips the 4 KB pages that a test vector did not change. Before, it compared all 24 MB of guest memory
  byte by byte to find the writes.

**Run.** `gc_rd_ab.py`: every matched raw C of Skies of Arcadia (83 functions) through `ppc_readable.check_raw`.
That is the build, the runner and the compare. Builds and caches went to a scratch folder. Three trees ran one after
the other:

- **old**: the branch before the patch;
- **mid**: the build changes only (`rd_mem.c` as before), on the first 40 functions;
- **new**: the whole patch.

**Result** (`gc_readable_ab.json`; `rdab_cmp.py` compares the sides):

| Side | Functions | PASS | Total | Median per judgement |
|------|-----------|------|-------|----------------------|
| old | 83 | 83 | 491.5 s | 5.79 s |
| new | 83 | 83 | 216.3 s | 2.48 s |
| old, first 40 | 40 | 40 | 237.5 s | 5.79 s |
| mid, first 40 | 40 | 40 | 153.7 s | 3.70 s |
| new, first 40 | 40 | 40 | 108.1 s | 2.68 s |

- **Same verdicts:** 83 of 83.
- **Byte-identical runner output:** 81 of 83 on both sides, and 40 of 40 for mid.
- **The 2 others** (8020cba4, 80213920) change on every run, also on the old code. Three runs of the old code gave
  three different hashes. The changing fields are float register words that hold host addresses (for example
  `0077f828…` and `0086f828…`). The compare does not read them, so all runs PASS. This is a pre-existing
  run-to-run difference in the GameCube readable runner, not caused by the patch. It is reported to the user, not
  changed here.
- **Split of the saving:** the build cache is 5.79 → 3.70 s (2.1 s per judgement), and the page skip is
  3.70 → 2.68 s (1.0 s per judgement).
- **Tests with the patch applied:** 231 passed. They cover the readable runtime cache, the runner reuse, big-endian
  readable, the raw runner and runs caches, headers, the PCH pass, the judge window, hw ops, the GameCube match, REL
  and disc tests, and batches 18, 19, 20, 30 and 32.

## Proofs B and C: served prepares with the Lease, on GameCube and Dreamcast (rows 2, 13 and 16)

**Run.** `lease_proof.py` on two private workspace copies:

- **B:** Skies of Arcadia, GameCube, 20 functions;
- **C:** Toy Commander, Dreamcast, 30 functions.

The functions were evenly spaced over each game's matched functions. Each copy was prepared twice, one prepare at a
time, on one Ghidra slot:

- **cold:** the master project, a new Ghidra and a broker token per prepare (the order live runs use today);
- **served:** read-only copy 1 through its long-lived Ghidra, which holds one Lease (162714e).

Skies ran cold first, Toy Commander served first, so both orders were tried. The runs were under the RAM guard
(pause at 95%), and it never paused. RAM was 72–74% before and after. The broker pass was read from the HOLD into
the runner's own environment only.

`waitwatch.py` sampled the broker queue every 0.5 s through 10 minutes before, all four runs, and 10 minutes after.
It measured every other session's ticket.

**Byte-identical: yes.**

- Skies: 140 of 140 harness and packet files, served against cold (`lease_skies_out.sha256`). This is the first
  served prepare on a PowerPC target, so it also proves the TcServe facts fix (39dae1b, row 13).
- Toy Commander: 213 of 213 files (`lease_tc_out.sha256`).

**Timing** (`lease_skies_timing.json`, `lease_tc_timing.json`):

| Run | Wall | Median prepare | Queue turns taken |
|-----|------|----------------|-------------------|
| Skies cold | 582.1 s | 7.43 s | 20 |
| Skies served | 118.3 s | 0.79 s | 3 |
| Toy Commander cold | 652.3 s | 6.98 s | 29 to 30 (one granted between samples) |
| Toy Commander served | 200.2 s | 0.86 s | 3 |

- In each served run the Lease was contested twice and handed back. Each hand-back cost one queue turn and one Ghidra
  start, 24–29 s. The first prepare took 45 s on Skies and 33 s on Toy Commander: the copy's refresh, a queue turn
  and the Ghidra start.
- One Toy Commander function (8c07dd46) takes about 84 s either way. That is its oracle's own work.

**Other sessions' queue wait** (`lease_queue_wait.json`; seconds from a ticket's creation to its grant):

| Phase | Length | Their tickets | Median | 90th percentile | Longest |
|-------|--------|---------------|--------|-----------------|---------|
| 10 min before | 601 s | 86 | 7.1 | 12.7 | 35.8 |
| Skies cold | 583 s | 53 | 16.4 | 34.8 | 62.4 |
| Skies served | 119 s | 9 | 30.6 | 31.8 | 32.2 |
| Toy Commander served | 202 s | 25 | 13.8 | 33.2 | 38.0 |
| Toy Commander cold | 653 s | 110 | 12.3 | 20.8 | 28.8 |
| 10 min after | 601 s | 123 | 7.0 | 14.3 | 18.8 |

**Not starved.** No other ticket waited more than 38 s while a Lease was held, against 62 s during a cold run. A
contested Lease is given back once a waiter has waited `lease_s` (30 s). That is the 31–33 s at the 90th percentile.

**The cost to others.** Their median wait was higher during the short Skies served run (9 tickets). It was about the
same as cold during the Toy Commander served run. Any run of ours on one slot slows them, cold or served. A served run
holds that slot about a fifth as long, for the same work:

- Skies: 118 s served against 582 s cold;
- Toy Commander: 200 s served against 652 s cold.

**Watcher note.** Git Bash converted only the first path of the watcher's comma-separated PID-file list. So the Toy
Commander runner's tickets were first counted as others'. The tables above use the four runner PIDs, recovered from
which PID's tickets appear only within each run.

### Row 16 rerun: serving on by default, on laptop2

Row 16 turns serving on in live runs (`config.json` `"ghidra_serve": {"enabled": true}`, de02aab). Proofs B and C
were run again on the result, on the second laptop, because this laptop's RAM was held at 89–94% by the user's game.
The runs are Ghidra prepares only, with no judging, so the compiler does not matter.

**Setup.** waste-audit-2 at fb70727 in a worktree, with the two workspace copies from proofs B and C (Skies, 20
functions; Toy Commander, 30). The broker used a private queue folder and the shipped setting (`by_ram`). Ghidra
was a private copy of the laptop's Ghidra 12.1.2 with the GameCubeLoader extension, since the shared install there
lacks the Gekko language. `live` mode is the tree's config as live runs take it: serving on, `ghidra_parallel` 4.
`cold` is serving off, one slot. Each phase ran both workspaces at once, so each was the other's "other session" in
the queue (`proof16_l2.py`, results in `serve_on_laptop2.json`):

| Phase | Skies | Toy Commander |
|---|---|---|
| 1 | cold | cold |
| 2 | **live** | cold |
| 3 | cold | **live** |

**Byte-identical: yes.** Live against cold, every harness and packet file:

- Skies: 140 of 140, against both cold runs (phases 1 and 3);
- Toy Commander: 213 of 213, against both cold runs (phases 1 and 2).

**Timing:**

| Run | Wall | Median prepare |
|---|---|---|
| Skies cold (phases 1, 3) | 332.1 s, 361.3 s | 16.2 s, 17.3 s |
| Skies live (phase 2) | 56.8 s | 1.49 s |
| Toy Commander cold (phases 1, 2) | 636.4 s, 593.2 s | 14.7 s, 13.0 s |
| Toy Commander live (phase 3) | 313.5 s | 1.41 s |

Toy Commander's live wall includes 8c07dd46, whose oracle takes about 84 s either way.

**The other session's wait.** The cold runner beside a live one never waited more than 0.15 s for a slot
(per prepare, from its own timing). Beside another cold runner it waited at most 0.38 s. With `by_ram` and room
on the machine, the cold runner's tickets were granted at once, so a Lease had no waiter to give way to. This rerun
does not test hand-back. The contested case is proof C's on
this laptop, on one slot: no other ticket waited more than 38 s while a Lease was held, against 62 s during a cold
run. Row 16 changes only the setting, not the Lease.

**Notes from the run.**

- **The broker counts every Ghidra JVM on the machine**, other sessions' too, even with a private queue folder. A
  first attempt with a one-slot setting hung: other sessions' long-lived JVMs took the slot. A one-slot private
  broker on a shared machine therefore waits on other sessions' work.
- **Remote processes did not end when the SSH call was stopped.** The runner's processes there were ended by PID
  tree.

## Proof D: seeding without a second analysis (row 20), not landed

**What was tried.** `core/ghidra.seed` runs a whole-program auto-analysis before TcSeed, although setup has already
analysed the program. The change tried was `-noanalysis` on that run.

**Run.** `seed_proof.py` on a private Toy Commander copy, one Ghidra at a time on one slot, under the RAM guard:

- setup from scratch (import, analysis, fixups, catalog), 108.2 s;
- three copies of that state, seeded: the current way twice, and once with `-noanalysis`.

**Result** (`seed_results.json`):

| Seed | Time | Analysis | Functions added | catalog.json | function_bodies.txt | code_ranges.txt |
|------|------|----------|-----------------|--------------|---------------------|-----------------|
| current | 73.0 s | 26 s | 187 | d6f13100… | 98a83385… | 04ae1c86… |
| current, again | 76.9 s | 28 s | 187 | d6f13100… | 98a83385… | 04ae1c86… |
| `-noanalysis` | 53.1 s | none | 187 | af9e0ea0… | c20ffa2c… | 04ae1c86… |

- **The current way is repeatable:** both runs gave the same bytes.
- **Without the analysis the catalog changes.** 4 of 4,203 records differ:
  - function 8c0b0848 is missing; its code is merged into 8c0b0866's body;
  - 8c07d0ec has different callees and jump tables;
  - 8c0905cc has different callers.
- **So the second analysis is needed.** It analyses the functions TcSeed adds. The change was reverted, and row 20
  is unavoidable. The graph run is a separate whole-program run, 7.4 s once per game: negligible.

## Proof E: keeping the raw judge's PASS across readable runs (row 22)

**What changed** (`docs/proposals/raw_pass_memo.patch`, not landed): a readable run judges each function's raw C
again before it starts (`refine.harness_stale`). The patch keeps each PASS in `state/raw_pass.jsonl` under the
verdict-memo key: the matched C, the harness, the packet's asm, the judge's files and the config. A later run takes
the PASS only when that key is unchanged. A FAIL is never kept.

**Run.** `raw_memo_proof.py` on private copies: 60 Toy Commander functions and 83 Skies functions (matched C,
harness, packet). Each copy ran three times, each in a new process:

- **plain:** the current code, no memo;
- **memo1:** the patch, starting with no memo;
- **memo2:** the patch again, on unchanged inputs.

**Result** (scratch JSON, summarised here):

| Game | Functions | Answer, every run | plain | memo1 | memo2 | Taken from the memo in memo2 |
|------|-----------|-------------------|-------|-------|-------|------------------------------|
| Toy Commander | 60 | 59 PASS, 1 FAIL (8c033f90, 95 of 96) | 27.8 s | 17.1 s | 1.6 s | 59 |
| Skies of Arcadia | 83 | 83 PASS | 65.5 s | 65.6 s | 1.5 s | 83 |

- **Same answers:** every function gave the same answer in all three runs. The FAIL was judged in every run.
- **memo1 took nothing from the memo** (0 hits on both games). Its shorter Toy Commander time is not the memo's
  doing; the plain run before it had already filled the raw build caches.
- **Tests with the patch applied:** 78 passed (verdict memo, refine, the batch 21 runs, records, retire, sync).

## Proof F: keeping a translator's FAIL for a later job (row 23)

**What changed** (`docs/proposals/translated_fail_memo.patch`, not landed): a translated attempt 0 that FAILs is kept
in the function's ledger row, under a key of everything the translator and the judge read: the translator's
revision, the verdict-memo key (the C, the harness, the packet's asm, the judge's files, the config), every packet
file and the function bodies. A later job of the same function on the very same inputs takes the kept attempt instead
of translating and judging the same C again. `translate_backlog --record` now records these FAILs too. A PASS, a
tamper, and a "-dirty" or unknown translator revision keep nothing.

**Run.** `tx_fail_proof.py` on a private Skies of Arcadia copy: every function with a full harness and packet
(97). `translate_stage.attempt`, the job's attempt 0 with the official judge, ran for each function three times, each
in a new process:

- **plain:** the current code;
- **kept1:** the patch, with an empty ledger (it translates, judges and keeps its FAILs);
- **kept2:** the patch again, on unchanged inputs (a later job).

The patched tree was a scratch copy with a fixed `REVISION` file for the translator, since a scratch copy has no git
revision and an unknown revision keeps nothing.

**Result** (`tx_fail_results.json`):

| Run | PASS | FAIL | FAILs taken from the ledger | Total | The 8 FAILs |
|-----|------|------|-----------------------------|-------|-------------|
| plain | 89 | 8 | 0 | 125.3 s | 10.3 s |
| kept1 | 89 | 8 | 0 | 108.6 s | 10.5 s |
| kept2 | 89 | 8 | 8 | 98.3 s | 0.21 s |

- **Same verdicts:** for all 97 functions the verdict, the score, the summary and the first differences are the same
  in all three runs. The FAILs are 800c24cc, 800cf554, 800cf988, 800d0e78, 800d1a34, 800dbb4c, 801dd5e8 and 801f7500.
- **The saving** is one translate and one judgement per kept FAIL: about 1.0–2.6 s each here on GameCube, and more
  where the translator is slower (Xbox 360). The PASSes are not affected: they become matches and are not re-run.
- **Tests with the patch applied:** 853 passed, 7 skipped (translator stage, own translators, tooling, GameCube REL),
  with five new tests for a kept FAIL (taken on the same inputs; judged again after a change, with an unknown
  revision, or after a PASS; recorded by the backlog tool).

## Proofs G, H and I: the Xbox 360 fixes on other branches (rows 19, 18 and 21)

These fixes are commits on the owners' branches, not on `waste-audit-2`: `xenon-tx` b9fadab (row 19) and
`xbox360-speed` 1481dae (rows 18 and 21). Each was checked on a clean copy of its commit and of its parent
(`git archive`), against private copies of the Too Human workspace. Nothing live was written, and the other sessions'
worktrees were not touched. Numbers are in `x360_results.json`.

### Proof G: the translator keeps its whole-program return analysis (row 19, b9fadab)

**Run.** `x360_tx_proof.py`: 25 functions spread over the Too Human translator judge set. `translate_stage.attempt`
(attempt 0, the official judge) ran for each one in the same order, on its own workspace copy per tree: the parent
21b7a39 and then b9fadab.

| Tree | Translate, first function | Translate, median of the other 24 | Translate total | Wall |
|------|---------------------------|-----------------------------------|-----------------|------|
| 21b7a39 (before) | 94.9 s | 12.74 s | 418.3 s | 433.6 s |
| b9fadab (after) | 109.2 s | 1.15 s | 137.1 s | 152.5 s |

- **Same C and the same verdicts:** for all 25 functions, the C is byte-identical, and the verdict, score, summary
  and first differences are the same: 24 PASS, 1 FAIL.
- **The first function** builds the shared summary cache in both trees (about 2 minutes on 88,187 functions). After
  that, the kept answer saves about 11.6 s per function. That is the commit's own figure (11.7 → 1.2 s).

### Proof H: TcFixups' mnemonic cache and TcFacts' compiled split (row 18, 1481dae)

**Run.** `x360_prep_proof.py`: 20 of the Too Human 50, removed from two workspace copies (the Ghidra project
included, each with its own server port). `prepare_list.py` of each tree (6505588, then 1481dae) prepared them
cold, on one Ghidra slot, through the broker. The four long ones were then prepared twice more per tree,
interleaved (old, new, new, old), to separate the change from machine load.

- **Byte-identical:** 140 of 140 harness and packet files after every run, with `meta.json`'s `oracle_stamp` aside.
- **The judge, the same:** `translate_stage.attempt` (b9fadab's translator, the official judge) on both harness
  sets: 19 PASS and 1 FAIL each (82976860: no valid vectors), the same score, summary and C for all 20.
- **Time.** The gain is in the oracle script's emulation, so it shows only where a function emulates for long.
  Here is the Ghidra script time, as the median of 3 runs:

| Function | 6505588 (before) | 1481dae (after) | Change |
|----------|------------------|-----------------|--------|
| 82e97a88 | 329.4 s | 297.8 s | −10% |
| 82b4dd70 | 148.2 s | 137.5 s | −7% |
| 82a468a8 | 70.5 s | 59.7 s | −15% |
| 82c531f0 | 40.0 s | 33.6 s | −16% |
| the other 16 | 1.0–3.4 s each | the same within noise | — |

- **The first single pass of all 20 is not a fair comparison.** It took 817 s on the old tree and 934 s on the new.
  The machine was busier during the second run: the start-up time outside the script rose for every function
  (128 → 162 s in all), and 82e97a88 alone varied by 89 s between runs of the same tree. The interleaved runs
  above put the new tree ahead on every long function.
- **The 1–1.4 h per game in the report's row 18** was an estimate from the regex count. Measured here, the saving is
  7–16% of the script time of the long-emulation functions, and about nothing on the short ones.

### Proof I: `x360setup` in place of the hand setup steps (row 21, 1481dae)

**Run.** `x360setup.py setup` from the Too Human disc image, into two scratch workspaces:

- with `--pick` (the 50);
- with `--roots` set to the hand workspace's own `setup_roots.json` (550), and then `x360setup.py ghidra`, on one
  slot through the broker.

**Result.**

- **Time.** The setup steps took 8 s, and the Ghidra half 51 s (setup, then the decode audit).
- **Same files as the hand workspace (550 roots):** `catalog_inputs.json`, `code_ranges.txt`, `ghidra_setup.txt`,
  `image.bin`, `image.json`, `oracle/TcInputs.java` and the root list. After the Ghidra half, `state/catalog.json`,
  `code_ranges.txt` and `function_bodies.txt` are byte-identical too.
- **`project.json`** differs only in `harness_reuse`, `port`, `priority` and the name given.
- **With `--pick`**, the 50 are the hand `proof_set` byte for byte. The setup list (368 functions) is a strict
  subset of the hand list, which was widened to 550 roots later.
- **Gate G2 passes,** with 0 disagreements.
- **What falls short: the decode audit covers less than the hand step did.**
  - The hand audits covered every function in the setup list: 368 functions (22,449 words) for the 50, and 2,443
    functions (136,798 words) for the 550.
  - `x360setup ghidra` audits only the roots: 550 functions, 21,162 words.
  - So the functions the roots call are not decode-checked. The oracle runs through them.
  - **The fix needed:** make the default audit set the setup list's functions, not the roots. Until then, row 21
    stays open.
- **Rechecked on `xbox360-speed` 1ef98e4, which makes that the default.** Its `x360setup.py ghidra` ran on a
  private copy of the hand workspace's setup files, from a clean copy of the commit, on one slot through the broker
  (55 s). The tree's local config held only the Ghidra and Java paths, so no key was copied.
  - `state/decode_audit.tsv` is byte-identical to the hand `decode_audit550.tsv`: 2,443 functions, 136,798 words.
    `catalog.json`, `code_ranges.txt` and `function_bodies.txt` are byte-identical to the hand workspace's too.
  - Gate G2 passes: 0 disagreements in prepared functions. 1,314 are in refused functions, as before.
  - **New in this commit:** 82 words are counted apart as import records. They are the first two words of 41
    import thunks: the import record (library and ordinal), which the loader overwrites with code. Every one of
    them is a gap ("Ghidra decoded no instruction"). Only an import thunk's first two words are exempt (484 words
    for the 242 thunks); a disagreement anywhere else still fails G2. The oracle never
    runs a thunk (import calls are mocked). The thunks' other two words are still checked.
  - So the Xbox 360 half of row 21 holds.

## Proof J: one setup command for Dreamcast, Saturn and GameCube (row 21)

**What changed** (on `waste-audit-2`):

- `tools/game_setup.py setup <disc> <workspace>` reads the console from the disc's boot header and does the hand
  steps in one go, by calling each target's own disc tool (it copies none of their code):
  - Dreamcast: `gdrom.py` extracts the boot file that IP.BIN names. A boot file linked at 0x0c... gets
    `seed_functions` (`gdrom.py scan`'s rule).
  - Saturn: `satdisc.py` and `overlay_image.py` build `binary/image.bin` from the first-read file at its IP.BIN
    address, plus each `--file` (and `--context`) overlay. `image_gaps` and `game_ranges` come with it.
  - GameCube: `gcdisc.py setup`.
  - Then `project.json` and the template `TcInputs.java`. It never overwrites a workspace. `--fields` carries a
    workspace's own decisions (such as `catalog_overrides`) into `project.json`.
  - Then `decomp.py setup` (the hand step itself), then the new decode check.
- **The decode check** is shared by all three consoles. `tools/ghidra_tools/TcDecodeCheck.java` (read-only, outside
  every stamp) lists every instruction of every function in the Ghidra project. `tools/decode_check.py` then
  checks three things for each one:
  - the bytes are the binary's bytes;
  - Ghidra decoded it;
  - the target's own decoder (its translator's `decode.py`, written from the manuals) gives it the same name.
    Ghidra's short forms are named the way the decoder names them: PowerPC `li` is `addi`, `blr` is `bclr`, and so
    on; SH delay slots lose Ghidra's `_` prefix.
  - A function with no instruction at all (a one-byte body, or a call target past the image) is listed apart for
    the catalog's owner, and does not fail the check.
- No judge, oracle script or target file changed, so no harness or verdict stamp moves.

**Run.** `setup_proof.py`, on one Ghidra slot through the broker. Numbers are in `setup_results.json`.

- **Before (the hand step):** a private copy of each live workspace's `project.json`, with its binary and
  `TcInputs.java`, then `decomp.py setup`.
- **After (the new command):** `game_setup.py setup` from the disc image into an empty folder. `--fields` gave it
  the live `project.json`'s own decisions: everything except what the disc gives and the paths and port. For Dragon
  Force 2 it also got `--file BTL_MAIN.BIN@0x06060000`.

**Same files.**

| Game | Binary | `project.json` | `TcInputs.java` | Setup state files, vs the hand step | vs the live workspace |
|------|--------|----------------|-----------------|--------------------------------------|-----------------------|
| Toy Commander (Dreamcast) | identical | identical | same text | 13 of 13 identical | differs: later seed and catalog runs (4,183 functions live, 4,052 from setup) |
| Dragon Force 2 (Saturn) | identical | identical (new: `references: []`) | identical | 13 of 13 identical | 13 of 13 identical |
| Skies of Arcadia (GameCube) | identical | GQR1 differs (below) | identical | 3 of 3 identical | 3 of 3 identical |

- The paths and port are workspace-specific. The binary path is set aside when comparing; its bytes are compared.
- The hand Toy Commander `TcInputs.java` is the template with CRLF line endings.
- **Skies GQR1.** The live workspace pins GQR1 to 00000000. The game's start-up code writes only 0x08040804 to it,
  and `gcdisc.py` (written after that workspace was made) pins that value. The Ghidra output is the same either
  way. But `fixed_regs` also sets the registers the oracle starts from, so changing the live workspace would change
  oracle inputs. That is for the GameCube owner and the user to decide; it is not changed here.

**The decode check covers every function Ghidra found, and passes on all three.**

| Game | Functions | Instructions | Disagreements | Functions with no instruction |
|------|-----------|--------------|---------------|-------------------------------|
| Toy Commander | 4,052 | 283,493 | 0 | 1 (8c099470) |
| Dragon Force 2 | 1,675 | 127,117 | 0 | 2 (0603e328, 060fc000) |
| Skies of Arcadia | 6,751 | 689,141 | 0 | 0 |

- The check is not passing trivially. Before the name rules, Ghidra's and the decoder's names differed on 188,643
  Skies instructions (28 distinct pairs: `li`/`addi`, `beq`/`bc` ...), 31,784 on Toy Commander and 12,337 on Dragon
  Force 2. Every pair is a fixed naming rule. Each test also feeds it a wrong name, wrong bytes and a gap
  (`tests/test_game_setup.py`).
- The three functions with no instruction are in the hand catalogs too. Each is a one-byte body with no code:
  0603e328 is inside 0.BIN, and 060fc000 is past the image, in RAM that this image does not fill. They are the
  catalog owners' to mark as data. Before, nothing flagged them.

**Time.** Before = the hand steps' commands, measured here. After = the one command.

| Game | Hand commands | `decomp.py setup` | Before (machine) | After: one command (of which the new decode check) |
|------|---------------|-------------------|------------------|----------------------------------------------------|
| Toy Commander | `gdrom.py` info, scan, extract: 38.8 s | 115.9 s | 154.7 s | 127.0 s (18.2 s) |
| Dragon Force 2 | `satdisc.py` info, scan, extract and `overlay_image.py`: 4.0 s | 71.8 s | 75.8 s | 94.2 s (23.4 s) |
| Skies of Arcadia | `gcdisc.py setup`: 1.1 s | 111.9 s | 113.0 s | 135.0 s (27.9 s) |

- The machine time is about the same. The new decode check adds 18–28 s, and it checks every function, where the
  hand steps checked none on these three consoles. Toy Commander saves the 38 s disc scan: the command scores only
  the boot file.
- **The real saving is the hand time between the commands:** reading the disc, choosing the files and addresses,
  writing `project.json` with its SHA-256, and copying the template. That is one agent turn per step. The logs show
  it took much longer than the commands:
  - Toy Commander's disc inventory, file list and boot file were written over 53 minutes (10:36 to 11:29 on
    7 October);
  - Dragon Force 2's disc extraction and scan took 15 minutes (16:48 to 17:03 on 22 October), and the image and
    `project.json` came 3.5 hours later.
  - Now it is one command, about 2 minutes, with no hand step.

**So row 21 holds on all four consoles.**

## Proof K: the Ghidra slot cap by measured RAM (row 17)

**What changed.** `core/ghidra_broker.py` `by_ram`, on in `config.json` (`docs/proposals/slot_cap_by_ram.md`, "As
built"). At each grant the broker counts RAM now, plus what each running Ghidra JVM may still grow to the measured
peak, plus a full peak for each granted token whose JVM has not started. It admits the first 2 while that count plus
one more peak stays under 95%, and more, up to 6, while it stays under 85%. Nothing starts at 88% or more, as before.

**Tests** (`tests/test_ghidra_broker.py` `ByRam`, with the merged `tests/test_broker_fast_start.py`: 48 pass):

- the fill: with no JVM running, 6, 4, 3, 2, 2 and 2 JVMs at 50%, 72%, 75.9%, 76%, 79% and 81.9% RAM;
- growth: two JVMs at the start of their life count as two peaks to come;
- the stop line: 0 at 88% and above, 1 or 0 near it with a large peak, and never one that would pass 95%;
- the peak is measured (1 GB until a first sample, then 952 MB), and the queue grants in order up to the cap.

**Replay of the sampled hour** (`ram_cap_replay.py`, `ram_cap_replay.json`). It took the hour of live samples
(1,779, every 2 s) behind the proposal and replayed it with the new rule against the old. Each JVM the new rule adds
is charged its full peak (952 MB, 2.96% of the machine) for the median prepare JVM's life (8.4 s).

| Figure | Old rule (real) | New rule (replay) |
|--------|-----------------|-------------------|
| JVMs running, mean | 2.44 | 5.05 |
| RAM, highest sample | 83.9% | 85.81% |
| Samples at 85% or more | 0 | 2 |
| Samples at 95% or more | 0 | 0 |

- **More work started:** of 278.7 waiting ticket-minutes in the hour, 130.6 are served sooner. That is 933
  prepares started sooner (the upper bound is 934).
- **No stop-line crossing:** the replay never reaches 95%. The two samples at 85.81% are JVMs that were admitted
  under 85% and then grew as counted, and they stay far under the stop line.
- With the old rule, the queue sat at the cap with tickets waiting in 75.5% of the samples.

**The 99% readings today.** No machine-wide RAM log existed. The readings come from each session's own RAM guard,
in its transcript (times in UTC):

| When | Whose guard | What it ran | What drove it, as far as the record shows |
|------|-------------|-------------|--------------------------------------------|
| 22 Oct 23:31, about 30 min at 95–99% | Fix stricter judge flips | waiting to prepare PS2 | other sessions' work (its own note); no breakdown |
| 23 Oct 10:00:47 | Xbox 360 target | a Too Human prepare, 2 slots | checked 6 s later: RAM back at 69%, none of its processes among the largest, Windows Update's SetupHost running |
| 23 Oct 10:00:49 | Saturn new games | a Dragon Force 2 prepare chunk, 1 slot | the same moment as the row above: one machine-wide spike |
| 23 Oct, between 15:47 and 16:44 | Saturn new games | a Dragon Force prepare chunk, 1 slot | not recorded; RAM 70–71% right after |
| 23 Oct 18:58 (this proof) | mine | the row 24 checks | the user's game, 6.7 GB; one Ghidra JVM at 0.6 GB |

The Saturn chunks ran one Ghidra JVM (about 3% of the machine) when their guard stopped them. So no reading points at
the number of Ghidra JVMs; two point at other programs. What the new cap does about each cause:

- **Ghidra JVMs growing after they started:** counted now. The old rule did not count growth. Each JVM is now
  measured with the processes under it (Ghidra's decompiler runs as its child `decompile.exe`).
- **A burst of starts:** counted now as well. A granted token whose JVM has not started counts as a full peak.
- **Another program's memory** (Windows Update, the user's game): no Ghidra cap can hold that back. At 88% the cap
  starts nothing, as before, and the 95% rule pauses the work.

**A RAM log, so the next spike can be traced.** No machine-wide record existed, so the broker now keeps one,
`ram_log.jsonl` in its folder. At most once a minute, while a ticket waits at the queue's head, it writes:

- the time, RAM in use, and each Ghidra JVM's MB (with its children);
- the machine's five largest processes, only at 85% or more, where a spike is worth tracing.

It costs the waiter nothing. The line is written on a thread of its own, because reading every process's memory took
5.4 s on this machine at 90% RAM. The log keeps about the last 1 MB (`tests/test_ghidra_broker.py`
`test_tree_counted`, `test_ram_log`).

## Proof L: the warm agent check (row 24)

An agent judges its C with `check.py` in its sandbox. Each check was a new Python process that loaded the judge's
code, and the readable check ran its header lint again each time. Now each agent run keeps one spare process ready
(`core/warm_check.py`), with the judge's code already loaded. `check.py` hands its command line to the spare
(`core/warm_client.py`), which runs the check's main exactly as `python check.py` would: the same arguments, folder
and environment. The output and exit code go back. A spare serves one check and ends, and the next spare starts, so
nothing one check works out is kept for the next. When no spare answers, the check runs cold, as before. It changes
how the judge runs, not what it decides.

**Proof** (`warm_check_proof.py`, results `warm_check_raw_tc.json`, `warm_check_raw_gc.json`,
`warm_check_rd_tc.json`). Private workspace copies were used, and the sandbox's real `check.py`. Each function had three
versions of its C:

- as it is;
- one that does not build;
- one with its first hex constant plus one, which mostly FAILs on the vectors.

Each version was checked five times: cold, warm, cold, warm, cold. Every run of a check had to print the same bytes and
exit with the same code: the verdict, the score and the first differences are all in that output.

| Workspace | Judge | Checks | All the same | All served warm | Cold (median) | Warm (median) |
|-----------|-------|--------|--------------|-----------------|---------------|---------------|
| Toy Commander (Dreamcast) | raw | 57 | yes | yes | 0.546 s | 0.370 s |
| Skies (GameCube) | raw | 60 | yes | yes | 1.196 s | 0.956 s |
| Toy Commander (Dreamcast) | readable | 45 | yes | yes | 2.567 s | 2.300 s |

Both PASS and FAIL verdicts are among them, including the builds that fail. A warm check saves about 0.18–0.27 s.
That is the process start and the judge's code being loaded. The times were taken while the machine was at 85–90% RAM
(the user's games were running), so all of them are slower than usual. The saving per check is small against an
agent attempt of minutes, as the audit said; it adds up over the thousands of checks of a wave.

Tests (`tests/test_warm_check.py`):

- the sandbox's `check.py` gives the same output and exit code warm as cold, and the check sees the same arguments,
  folder, environment and script path;
- a spare serves one check, and the next spare serves the next;
- a refused check runs cold: a wrong token, a command that is not an agent check of this workspace, a `project.json`
  changed since the spare loaded it;
- config `"warm_check": false` gives no warm check, and closing a session ends its spare.

## Proof M: stamps that travel between machines, and the compiler in the judge stamp (rows 25 to 28)

Found while the user's second laptop was set up.

### Row 25: the oracle stamp hashed the Ghidra folder's path

`core/oracle_stamp.py` put `ghidra_home`, the install's path, into every harness's stamp. The same Ghidra in another
folder, on another machine or after a move, gave every harness a new stamp, and each would be prepared again for
nothing. The stamp now holds `ghidra_identity`, which is what decides the output:

- the version and build lines of `Ghidra/application.properties`. Its first line is a comment with the time the file
  was written, so it is left out;
- the decompiler program;
- every file of the folder that defines the target's language: the `.ldefs`, `.sla`, `.slaspec`, `.sinc`, `.pspec`
  and `.cspec` files. The SH-4 tcfix language that `targets/dreamcast_sh4/ghidra_lang` installs is one of them;
- for a language from an extension, that extension's jars too. GameCube's `Gekko_Broadway` comes from the
  GameCubeLoader extension, not from a processor folder.

The `.sla` names its source files, not their paths (checked on the installed tcfix `.sla`), so it hashes the same in
any folder. Each process works the identity out once per install. It is taken again when `application.properties` or
the language's `.ldefs` changes, as `install_lang.py` rewrites the `.ldefs`. On this machine:

| Language | Identity | First time |
|----------|----------|------------|
| SuperH4:LE:32:tcfix (Dreamcast) | 691b8c3a3b01a48f | 0.77 s |
| SuperH:BE:32:SH-2 (Saturn) | be18f8b5fa806ae8 | 0.14 s |
| PowerPC:BE:32:Gekko_Broadway (GameCube) | 6f58a28f1d6a5bbd | 0.18 s |
| PowerPC:BE:64:64-32addr (Xbox 360) | 43c35ccfeb85ffbe | 0.23 s |

After the first time it takes 0.2–0.4 ms.

Tests (`tests/test_ghidra_identity.py`):

- two identical installs in different folders give the same identity and the same stamp, for SH-4 and the GameCube
  extension;
- each of these changes the stamp: another version line, a changed `.sinc`, a recompiled `.sla`, another
  decompiler, an extension's changed jar;
- the time comment alone does not change it;
- a reinstalled language is seen by a running process;
- no install gives `-`.

The stamp of every harness changes once, so all are prepared again once. The coordinator puts this with the judge
set's re-stamp, which re-prepares everything anyway.

### Row 26: the judge stamp did not name the compiler

The judge stamp hashed the cached 64-bit compiler environment (`state/msvc_env.json`), but not the compiler itself,
and not the 32-bit environment at all. It now holds, for each cached environment, 64-bit and 32-bit
(`toolcfg.compiler_identity`, `verdict_memo.compiler_identity`):

- the full version line of the `cl.exe` it runs;
- its `WindowsSDKVersion`;
- its `VCToolsVersion`.

The SDK is there because two machines with the same cl can still differ in it: vcvars takes the newest SDK unless it
is told one. Another compiler or SDK gives another stamp, so no verdict made with one is reused under another. This is
stricter, not weaker: it goes with the judge set for the user's yes.

The new config key `"vcvars_args"` (in `config.local.json`) pins both. It is one string of vcvars' own arguments, for
example `"10.0.28000.0 -vcvars_ver=14.44"`: the Windows SDK, then the toolset. It goes after the quoted script path
at all four places that load an environment:

- `core/check.py`, the raw judge's;
- `core/readable_check.py`, the readable judge's (its `vcvars32.bat` is found beside `vcvars`);
- `port/tools/build.py`, the port kit's, with the `vcvars32.bat` that `port/tools/game_config.vcvars32()` finds
  (`game_config.toolchain()` gives it the pipeline config).

All four share `core/toolcfg.vcvars_command`. Only an SDK version, `-vcvars_ver=` with a version and the Spectre
libraries are taken; anything else is refused, so the string cannot carry anything else to `cmd`. A cached
environment that does not hold the SDK and toolset named is loaded again (`toolcfg.pinned`). The default is
unchanged: no key, no arguments.

**Live** (`vcvars_pin_proof.py`, `vcvars_pin.json`). Each environment was loaded with each setting, on both
`vcvars64.bat` and `vcvars32.bat` (the same results on both):

| vcvars_args | Loaded | Toolset | SDK | cl |
|-------------|--------|---------|-----|----|
| not set | yes | 14.38.33130 | 10.0.28000.0 | 19.38.33145 |
| `-vcvars_ver=14.44` | yes | 14.44.35207 | 10.0.28000.0 | 19.44.35229 |
| `10.0.28000.0 -vcvars_ver=14.44` | yes | 14.44.35207 | 10.0.28000.0 | 19.44.35229 |
| `10.0.26100.0 -vcvars_ver=14.44` | **no**, vcvars refuses it | — | — | — |
| `10.0.26100.0 -vcvars_ver=14.38` | **no**, vcvars refuses it | — | — | — |
| `-vcvars_ver=14.29` | yes | 14.29.30133 | 10.0.28000.0 | 19.29.30159 |

Without the key, the command is byte-identical to the one before.

**A failed load stops; it is never cached.** Each load now goes through `toolcfg.loaded`. If vcvars exits
nonzero, the judge stops, even when the variables it printed look right. A pinned SDK that is not fully installed
gives exactly that. A load whose environment does not hold the pinned SDK and toolset stops as well. Nothing from a
failed load is written to the cache. On this laptop, `"10.0.26100.0 -vcvars_ver=14.44"` stops with "could not load
the Visual Studio environment (vcvars64.bat exited 1)", because SDK 10.0.26100.0 here is only the driver kit's
headers: its UCRT (`Include/10.0.26100.0/ucrt`, `Lib/10.0.26100.0/ucrt`) is missing.

**For the switch to 14.44** (the user's choice, at landing; the coordinator set the value):

- **The landing value is `"10.0.28000.0 -vcvars_ver=14.44"`.** 10.0.28000.0 is the complete SDK today's judge
  already uses here. Live on a private workspace copy, both caches loaded with it: cl 19.44.35229, SDK
  10.0.28000.0, toolset 14.44.35207, for x64 and x86. Removing the key loaded the default again: cl 19.38.33145,
  SDK 10.0.28000.0, toolset 14.38.33130.
- **This laptop's judge runs toolset 14.38 today, not 14.44.** Visual Studio's newest toolset here is 14.44, but
  vcvars takes the v143 default, `Microsoft.VCToolsVersion.v143.default.txt`, which is 14.38.33130. Every live
  workspace's cached environment holds 14.38.33130.
- The switch changes every judge stamp, so it re-judges everything once.

**The SDK's exact build is in the stamp too.** `WindowsSDKVersion` cannot tell two builds of one SDK apart: the
two laptops both said 10.0.28000.0, but one had build 10.0.28000.1839 and the other 10.0.28000.2114, with different
`um` and `shared` headers and libraries (the runtime includes `windows.h`). The stamp's compiler line now also holds
`files <sha256>`: a hash of every file in the SDK folders the compiler reads, as INCLUDE and LIB name them
(`Include/<version>/ucrt`, `um` and `shared`, and `Lib/<version>/ucrt` and `um` for the target's architecture). Each
file's path below its folder goes into the hash, never the machine's install path, so two laptops with the same SDK
files get the same hash. It is computed once per environment load and kept in the cache key with the folders'
listing (file count, bytes, newest mtime). While the listing is unchanged, the key's hash is used and nothing is
read. A changed SDK file loads the environment again.

Live, on a private workspace copy (`toolcfg.sdk_hash` through the judge's two loads):

| `vcvars_args` | Load | Load with the hash (s) | cl | SDK files hash (first 16) |
|---|---|---|---|---|
| none | x64 | 5.57 (first: SDK read from disk) | 19.38.33145 | f04393a07d7e71a7 |
| none | x86 | 2.82 (headers' hash reused) | 19.38.33145 | 0f4da175e638fd40 |
| `10.0.28000.0 -vcvars_ver=14.44` | x64 | 4.66 | 19.44.35229 | f04393a07d7e71a7 |
| `10.0.28000.0 -vcvars_ver=14.44` | x86 | 2.95 | 19.44.35229 | 0f4da175e638fd40 |
| none, again | x64 / x86 | 4.67 / 2.75 | 19.38.33145 | the same two |

The hash follows the SDK files, not the toolset: 14.38 and 14.44 read the same SDK, so they give the same hash for
the same architecture. The x64 and x86 hashes differ because their libraries do. A load without the hash took about
2.7 s, so the hash adds about 2 s to the x64 load and almost nothing to x86. A load happens only when a cache key
changes. A cache hit now also lists the SDK folders: 3,049 files in about 5 ms, so a hit takes about 6 ms in all
(cl is not run: 0.5 ms).

Tests (`tests/test_compiler_identity.py`):

- the command without the key is the old one byte for byte; the arguments go after the quoted path; anything else
  is refused (`14.44` alone, `x86`, `&`, `>`, a quote);
- `pinned` checks the SDK and the toolset a cache holds;
- a load that exits nonzero stops and caches nothing, even with plausible variables, and so does one whose
  environment misses the pin; the port build stops the same way;
- the stamp's compiler line holds cl, the SDK, the SDK files' hash and the toolset, and changes with each;
- the SDK files' hash covers just the five folders (not winrt, not NETFXSDK, not the other architecture's
  libraries), differs for a changed `windows.h` of the same size, and is the same for two install folders
  with the same files; a key keeps it without reading the SDK, and a changed file or a key without it loads
  the cache again;
- both judges' loads and the port build keep a cache of the pinned setting, and load one of another again;
- the judge stamp changes with the compiler's version (a stand-in `cl`).

### Row 28: the compiler environment cache was kept whatever made it

Each workspace keeps the Visual Studio environment in `state/msvc_env.json` and `msvc_env_x86.json` (the port kit
keeps its own). The cache was used whenever it existed. A workspace copied to another machine, a changed `vcvars`
or another toolset therefore kept the old compiler paths without a word. Each cache now has a key beside it
(`msvc_env.key.json`, `msvc_env_x86.key.json`), written when the cache is made:

- the vcvars script it was loaded from;
- `vcvars_args`;
- the full version line of the `cl.exe` it runs, and that file's path, size and time.

The cache is used only while all of these hold (`toolcfg.cache_current`); otherwise it is loaded again, and the new key
written. It counts cl's full version, not the toolset folder's name: one folder, 14.44.35207, gives cl 19.44.35219 on
one machine and 19.44.35228 on another. cl is run only when its file changed, so a cache hit costs no compiler run.
The judge stamp's compiler line (row 26) reads the same key.

The translator judges copy the key with the cache into their mirror workspaces, so they do not load the environment
again on every run.

**Live**, on a private copy of the Toy Commander workspace:

- the first load in each workspace finds no key and loads the environment once, 2.7 s for each of the 64-bit and
  32-bit caches;
- each load after that is a cache hit, 1 ms in a new process, without running cl;
- the key holds cl 19.38.33145 for x64 and x86, as the live workspaces' caches do.

Every live workspace cache on this laptop holds toolset 14.38.33130: Power Stone 2, Toy Commander and Spawn.

Tests (`tests/test_compiler_identity.py`, `Cache` and `PortBuild`), with a stand-in `cl`:

- an unchanged setting is a cache hit with the same environment;
- each of these loads it again: another vcvars, other arguments, the arguments removed, another cl in the same folder (19.44.35219 →
  19.44.35228), a missing key, a copied cache whose PATH is gone;
- the key's version is taken without running cl while cl.exe is unchanged;
- the port kit's cache follows the same rule.

### Row 27: networkx and pycparser were needed and not listed

There was no `requirements.txt`. Now there is one, with the packages and the versions tested:

- the pipeline: networkx, psutil, pycparser;
- the port kit: numpy, Pillow, zstandard;
- the tests: pytest.

The README's "Run it" section names it. pycparser was needed too (`core/stack_overread.py` imports it), as well as
networkx.

`tests/test_requirements.py` reads every tracked Python file's imports. It fails when a package from outside the
standard library and this tree is not in the list, so the list cannot fall behind again. `pip install --dry-run
--no-index -r requirements.txt` reads the file.

