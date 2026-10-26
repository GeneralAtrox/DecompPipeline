# Xbox 360 target: the first Ghidra proof (plan only)

**Status: ready, not started.** The user gave the go; the run waits for its turn after the waste audit's proofs
(the coordinating session says when). Everything that needs no Ghidra is done: the scripts are written and compile,
and the Too Human workspace (`TooHuman/decomp` beside the projects) is set up (see "The workspace" below).

## What the proof must show

1. Ghidra 12.1.2 (`PowerPC:BE:64:64-32addr`, BinaryLoader at 0x82000000) can hold Too Human's image and decompile
   its functions.
2. The oracle produces valid 64-bit vectors.
3. The judge (built and tested offline) passes correct C and fails wrong C.
4. The open questions that only Ghidra or the game can answer get measured answers: fma sensitivity, r13 use, stack
   arguments and VMX128 decoding.

## The 50 functions

Chosen offline by `tools/proof_pick.py` from the catalog of the real disc. The list is in `proof_set.json`, with the
image SHA-1 `dac2c9fe5b5df7a18570f694e63066f91e385648`. Every pick:

- is phase-1;
- has exact `.pdata` bounds;
- is 8..400 bytes;
- has no indirect call, jump table or tail call;
- has a body of a different shape from all the others.

Picks are spread from small to large within each group.

| Group | Count | Why |
|---|---|---|
| integer leaf | 8 | the simplest path: 64-bit r3, the integer helpers |
| frame helpers | 4 | inline `__savegprlr`/`__restgprlr` in Ghidra's flow and the agent's C |
| 64-bit leaf | 5 | `ld`/`std` and 64-bit arithmetic |
| r13 user | 4 | which PCR offsets the game reads (open question 5) |
| caller | 14 | mocked calls with 64-bit arguments (97% of the game calls something) |
| float, no fma | 6 | f1..f13, single rounding, `fctiw*` and `stfs` fixups |
| fma | 5 | the dual-model flag (open question 1) |
| kernel import | 4 | calls through import thunks, mocked results |

| # | Address | Group | Bytes | Callees | Imports | Frame helpers | Classes |
|---|---|---|---|---|---|---|---|
| 1 | `82a4a96c` | integer leaf | 12 | 0 | 0 | 0 | - |
| 2 | `82a513d0` | integer leaf | 32 | 0 | 0 | 0 | - |
| 3 | `82a63354` | integer leaf | 36 | 0 | 0 | 0 | - |
| 4 | `82c55848` | integer leaf | 36 | 0 | 0 | 0 | - |
| 5 | `82a4be14` | integer leaf | 40 | 0 | 0 | 0 | - |
| 6 | `82eef37c` | integer leaf | 40 | 0 | 0 | 0 | - |
| 7 | `82eef4a0` | integer leaf | 40 | 0 | 0 | 0 | - |
| 8 | `82ef4d48` | integer leaf | 40 | 0 | 0 | 0 | - |
| 9 | `82e4d7b0` | frame helpers | 160 | 0 | 0 | 2 | - |
| 10 | `82c41300` | frame helpers | 248 | 0 | 0 | 3 | - |
| 11 | `82b4dd70` | frame helpers | 296 | 0 | 0 | 2 | - |
| 12 | `82b719c8` | frame helpers | 376 | 0 | 0 | 2 | - |
| 13 | `82a468a8` | 64-bit leaf | 132 | 0 | 0 | 0 | ld64 2 |
| 14 | `82cf30a0` | 64-bit leaf | 160 | 0 | 0 | 0 | ld64 4 |
| 15 | `8274dc60` | 64-bit leaf | 200 | 0 | 0 | 2 | ld64 2 |
| 16 | `82c531f0` | 64-bit leaf | 232 | 0 | 0 | 0 | ld64 4 |
| 17 | `82802e40` | 64-bit leaf | 320 | 0 | 0 | 0 | ld64 2 |
| 18 | `82925ea0` | r13 user | 120 | 2 | 0 | 0 | ld64 2, r13 1 |
| 19 | `8296bf58` | r13 user | 196 | 4 | 0 | 2 | r13 1 |
| 20 | `8298b570` | r13 user | 312 | 2 | 0 | 2 | r13 1 |
| 21 | `829e6ab0` | r13 user | 368 | 13 | 0 | 2 | r13 1 |
| 22 | `824625f8` | caller | 56 | 2 | 0 | 0 | - |
| 23 | `82f60ea8` | caller | 56 | 1 | 0 | 0 | - |
| 24 | `82f8fba8` | caller | 56 | 1 | 0 | 0 | - |
| 25 | `82fbe758` | caller | 56 | 1 | 0 | 0 | - |
| 26 | `82fed4a0` | caller | 56 | 1 | 0 | 0 | - |
| 27 | `83020c60` | caller | 56 | 1 | 0 | 0 | - |
| 28 | `83056d78` | caller | 56 | 1 | 0 | 0 | - |
| 29 | `823673b0` | caller | 88 | 1 | 0 | 0 | ld64 2 |
| 30 | `830d0b90` | caller | 92 | 1 | 0 | 0 | - |
| 31 | `83089900` | caller | 96 | 1 | 0 | 0 | - |
| 32 | `83090e50` | caller | 100 | 1 | 0 | 0 | - |
| 33 | `82a790e0` | caller | 108 | 1 | 0 | 2 | - |
| 34 | `828050e0` | caller | 144 | 4 | 0 | 0 | ld64 4 |
| 35 | `824f5fd8` | caller | 244 | 4 | 0 | 2 | - |
| 36 | `8273da28` | float, no fma | 88 | 1 | 0 | 0 | fpu 6, ld64 1 |
| 37 | `826b3278` | float, no fma | 128 | 1 | 0 | 0 | fpu 8, ld64 2 |
| 38 | `826d32d0` | float, no fma | 168 | 2 | 0 | 0 | fpu 4, ld64 2 |
| 39 | `825132a8` | float, no fma | 208 | 4 | 0 | 0 | fpu 2, ld64 2 |
| 40 | `82ea7690` | float, no fma | 260 | 5 | 0 | 2 | fpu 8 |
| 41 | `82e97a88` | float, no fma | 344 | 4 | 0 | 2 | fpu 8 |
| 42 | `82d471c0` | fma | 136 | 3 | 0 | 0 | fpu 9, fma 1, fconv 1, ld64 3 |
| 43 | `82976860` | fma | 212 | 2 | 0 | 0 | fpu 27, fma 8, fconv 2, ld64 4 |
| 44 | `828c9a10` | fma | 268 | 1 | 0 | 0 | fpu 34, fma 6, ld64 4 |
| 45 | `8253a818` | fma | 328 | 2 | 0 | 2 | fpu 32, fma 1 |
| 46 | `829f6e20` | fma | 380 | 0 | 0 | 0 | fpu 81, fma 24 |
| 47 | `82a4f3a8` | kernel import | 88 | 0 | 2 | 0 | ld64 4 |
| 48 | `82e1e5f0` | kernel import | 116 | 2 | 1 | 0 | ld64 4 |
| 49 | `82d348c0` | kernel import | 160 | 1 | 1 | 2 | - |
| 50 | `82db5368` | kernel import | 260 | 3 | 1 | 2 | - |

Two picks worth knowing in advance:

- `82a4a96c` is `li r3,1; or r8,r8,r8; blr`. Its correct C is one line, so it is the sanity check: it must PASS, and
  a mutant of it must FAIL.
- `82a513d0` reads **r12 before writing it** (`addi r31,r12,-0x70`). It is an exception-handler funclet that gets
  its parent's frame in r12. The expected outcome is a refusal for an input outside the ABI. That tests the refusal
  path, and funclets may need a catalog kind of their own (finding for after the proof).

## Ghidra scripts (written offline; never run yet)

They are modelled on `targets/gamecube_ppc/ghidra_scripts`. All compile against Ghidra 12.1.2's jars, and
`tests/test_xbox360_scripts.py` tests every part that runs without Ghidra.

| Script | Job |
|---|---|
| `TcBase` | shared: the memory map (the four regions), the 64-bit register names, the tested prototype (Ghidra's signature plus the registers TcFlow sees read) |
| `TcClass` | the instruction classes from the instruction word, the twin of `ppcscan.classify` (tested equal on every primary and extended opcode) |
| `TcSetup` | maps the heap, stack and kernel blocks; turns auto-analysis off except the constant-reference analyzers; creates exactly the functions of `ghidra_setup.txt` (proof mode: the 50, what they reach in 4 calls, every helper family and the import thunks they call; 368 functions on Too Human). **Full mode** (every catalog function) comes later (step 6). |
| `TcFixups` | DESIGN.md §5: saturating `fctiw*`/`fctid*`, bit-selecting `stfs`, once-rounded fma when `"fma": "fused"`, traps that invalidate the vector, barriers and touch hints as no-ops, refusals by instruction word |
| `TcFlow` | the argument registers a function reads (64-bit registers found by containment); helper families read nothing and run inline; import thunks are call targets |
| `TcOracle` / `TcVectors` | 64-bit inputs with zero-, sign- and random upper words; call records in `x360_compare.py`'s format (target, r3..r10, f1..f13, s0..s7); the width probe (open question 3); the dual-model fma run (`fma_sensitive`) |
| `TcPrepare` | the packet and harness: `meta.json` (with `"import"` on thunk callees, `frame_helpers`, `cpu: ppc64`), vectors, expected results, `oracle_summary.json` (adds `fma_checked`, `fma_sensitive`, `widened`) |
| `TcCatalog` | the catalog, counted with TcClass (`vmx`, `atomic`, `traps` apart; `kind` frame_helper / import) |
| `TcDecodeAudit` | Ghidra's mnemonic for every word of the 50; `tools/decode_audit.py` compares it with the scanner (gate G2) |

## The workspace

`TooHuman/decomp` beside the projects, WS below (made offline; Ghidra has not touched it):

- `image.bin` (the loaded image; `project.json` pins its SHA-256), `image.json`, `catalog_inputs.json`,
  `code_ranges.txt`: the output of `catalog_inputs.py` on the disc;
- `ghidra_setup.txt` beside `image.bin`, where TcSetup reads it (`tools/ghidra_setup.py --proof proof_set.json`);
- `proof_set.json` and `proof_set.txt` (the 50, one per line);
- `oracle/TcInputs.java` from `template/`;
- `project.json`: target `xbox360_ppc`, `game_ranges` the code sections (82330000-83188e00), `global_ranges` the
  `.data` section (the only globals a vector randomises), `fixed_regs` r13 = 0x7fff0000, `priority` the 50,
  `harness_reuse` on (this workspace only), `ghidra_setup_timeout_s` 1800, port 8770.

## The commands, in order

Run from the `xbox360-ppc` worktree (the target is not on master yet); WS is the workspace and GHIDRA_HOME
config.local.json's `ghidra_home`. Check RAM first (under 80%).

```
python tools/bounded.py 1800 -- python decomp.py --project WS setup
python tools/bounded.py 600 -- GHIDRA_HOME/support/analyzeHeadless.bat WS/ghidra TooHuman -process image.bin -noanalysis -readOnly -scriptPath WS/state/ghidra_scripts -postScript TcDecodeAudit.java WS/state/decode_audit.tsv ADDRS_OF_THE_50
python targets/xbox360_ppc/tools/decode_audit.py WS/state/decode_audit.tsv WS/image.bin
python tools/bounded.py 3600 -- python tools/prepare_list.py --project WS --slots 2 @WS/proof_set.txt --out WS/state/proof_prepare.tsv
```

## Steps, slots and minutes

Every command runs under `tools/bounded.py`. RAM must be under 80% before steps 1 and 3. Pause at 85% or above;
stop at 95%.

| Step | What | Ghidra slots | Minutes (estimate) | Bound |
|---|---|---|---|---|
| 0 | Preconditions: the user's go, no other judge proof running, RAM under 80%, a broker slot free | 0 | 1 | |
| 1 | Import `image.bin` and run `TcSetup` in proof mode, then `TcCatalog` (`decomp.py setup`; the workspace is already made) | 1 | 5-10 | 1800 s |
| 2 | Decode audit: `TcDecodeAudit` on the 50, compared with the scanner | 1 (same) | 2 | 600 s |
| 3 | Prepare the 50 harnesses: `tools/prepare_list.py --slots 2 @proof_set.txt` | 2 | 25-35 (about 1 slot-minute each: Toy Commander took 0.5; this program is about 10× larger) | 3600 s |
| 4 | Agents on the 50, with harness reuse **on for this workspace only**, so no job opens Ghidra again | 0 | 30-60 of agent time | per run |
| 5 | `tools/mutation_check.py --gate` on every match (host only) | 0 | 15-30 | 7200 s |
| 6 | Later, separately: `TcSetup` full mode (88,193 functions, 3.3 M instructions), to measure the real project setup | 1 | 30-90 (unmeasured) | 7200 s |

**Ghidra time** for the proof itself (steps 1-3) is about 35-50 minutes, at most two slots at once. Step 6 is not
needed for the proof's verdict.

**Resources (estimates):**

- **RAM:** about 1.5-2 GB per slot for the proof-only program; the full program (step 6) perhaps 3-4 GB.
- **Disk:** the master project plus one read-only copy for slot 1, under 1 GB in proof mode.

Step 1 measures both, and the plan is corrected before step 3.

## Go / no-go gates

| Gate | After step | Passes when | If not |
|---|---|---|---|
| G1 setup | 1 | the project opens; the 50 functions exist with the catalog's bounds; RAM and time are within the estimate × 2 | stop; report the measurements |
| G2 decode | 2 | **no** instruction of the 50 decodes as a vector op or as invalid, and Ghidra and the scanner agree on every class | stop: VMX128 mis-decoding reaches phase 1; widen the scanner before going on |
| G3 harnesses | 3 | 45 or more of 50 have 16 or more valid dev vectors; every other one lands in a known blocker kind; `82a513d0` is refused for its r12 input | below 45: fix the oracle (no judge change) and re-prepare only the failures |
| G4 judge | 4-5 | `82a4a96c` PASSes; every PASS survives `mutation_check --gate` (a mutant killed); no PASS on a vector set the oracle marked invalid | stop: a judge defect blocks everything after it |
| G5 questions | 4 | measured answers recorded: how many of the 5 fma functions are fma-sensitive; the PCR offsets the r13 users read; whether any pick passes stack arguments | none: these feed the decisions in DESIGN.md §8 |

A matched count is **not** a gate. Agents may need retries on a new target. The proof is about the pipeline being
correct, not about agents being fast.

## After the proof

- **Decide with the user:** open question 1 (fma: recommend `fused`) and question 3 (the input upper-word policy).
- **Then:** VMX128 phase 2 (DESIGN.md §5), LZX for other titles, and the full-catalog setup.
