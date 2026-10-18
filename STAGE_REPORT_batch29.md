# Stage report: batch 29 (engine blockers)

**Verdict: GATE PASS; deployed (see Deploy).**

Brief: the coordinator's engine-blockers message. It covers the 7 TC engine functions that are not matched or not
readable for reasons other than pointers-as-data. Work is in work29/:
- tc: the TC copy (the 7 harnesses whole, the rest meta + summary only). Its Ghidra project is
  tests/ghidra_work/b29tc/ghidra (project.json `ghidra_dir`).
- scripts/copy_tc.py: makes the copy.
- m_*.txt: prepare outputs.
- test_tooling.txt: the test run.

Three generic changes are below. The per-function outcomes are under "Functions", with what was done on live TC.

## Item 1: the words a `mova` points at, in the Haiku sandbox

**Root cause (TC 8c07f4b8: dev 48/48, hidden 47/48 on all three attempts).**
- The function compares a length against the f32 at 0x8c07f658 (0.0001).
- It loads that word with `mova 0x8c07f658,r0; fmov.s @r0,fr3`.
- asm.txt annotates the pool word of every `mov.l`/`mov.w` pool load, but not `mova` targets. The agent saw only the
  address, guessed 1e-5 in all three attempts, and failed the one hidden vector whose length fell between the two.
- The fix was confirmed by hand: attempt 3 with `rdf32(0x8c07f658u)` in place of the literal passes 96/96.

**Class.**
- TC packets have 1834 `mova` lines in 287 functions. Every one targets a literal pool (outside the code ranges).
- The matched corpus is clean, because agents that read the pool with rdf32 pass. The cost is the failed attempts.

**Mechanism.**
- core/asm_pool.py: annotate() appends to each pool `mova` line in the sandbox copy of asm.txt (and its parts):
  `; r0 = 0x8c07f658 (literal pool): words 0x38d1b717 (f32 0.0001), 0x8c0635b8`.
  - It shows the first two words.
  - The f32 reading is the shortest decimal that gives the same f32. It is omitted for NaN, infinity and
    pointer-looking words.
  - These are left alone: a `mova` into a code range (a jump table's base), a target outside the image, and a line
    that already has a comment.
- core/agent.build_sandbox applies it. The packet itself is unchanged: it is an oracle input, and its stamp is
  unchanged.
- The TASK.md file list says what a `mova` line shows, and that further words of a table are read with rd32/rdf32.

**Controls (tests/test_batch29.py).**
- Pass: a pool `mova` gets its words, aligned with the listing's own notes.
- Fail controls (left unchanged): a `mova` into code, a `mova` outside the image, and an already-commented line.
- The f32 readings are checked, including `-0.0`, FLT_MAX, and none for NaN, infinity or a pointer.

## Item 2: stores outside RAM in the oracle's rescue pass

**Root cause (TC 8c079c1e: dev 13, hidden 11 of 384 tries; skipped).**
- Every random input stores through a base that a mocked callee returned as 0 (a store at 0x10).
- TcOracle.collect sees the write only when the run ends, by which time the epilogue's register pops fill the load log.
- TcInputs.repairVec patches the latest load whose value is near the fault. Those are the popped stack words (value
  0, 0x10 away), so all 12 repairs went to stack slots. repairRet, which would fix the real source, never ran.

**Class.** The same refusal ("write outside RAM" after 12 repairs) appears in TC 1 and Spawn 10:
- main: 8c031eda, 8c032028, 8c032d60, 8c032dda, 8c034900, 8c035380, 8c058300;
- sstma01: 8c9c8520; sstma07: 8c9c8940; sstma08: 8c9c8440.

**Mechanism (rescue pass only, so first-pass vectors are unchanged).**
- TcLoads.writesAt / storeOutside: the memory operands of storing instructions. Pushes (@-rN) and pc-relative
  operands are left out, as are cache address array stores, which collect logs.
- TcOracle.storeFaults (set by TcVectors to `rescue`): a store outside RAM stops the run at the store.
  - The reason text is the same: "write outside RAM".
  - Result.storeBase holds the base register's value.
  - The load log gains a fifth word: the number of calls made before each load.
- TcProvenance.repair finds the most recent input that produced exactly the base value:
  - a mocked return (Vec.rets[k]); or
  - a 4-byte load the vector can set, outside the own frame [STACK_LO, SP).
  - The call count decides which came last. That input gets a generated-chunk pointer, as repairVec and repairRet
    give.
  - With no exact source, the usual repairs follow, on the load log without the own frame's words.
- TcVectors stays under 400 lines (390).

**Controls (TC copy).**
- 8c079c1e: dev 48, hidden 48 (66 and 74 tries), all 33 blocks hit by dev.
- 8c07f4b8 (first pass) re-prepared with the stage scripts: vectors.txt and expected.txt are byte-identical to live.
- 8c077c30 and 8c0923e0 (rescue pass, other refusals): the same counts as before.
- Gate fixture tc-store-fault-8c079c1e (fail only: a stub that writes nothing). It must FAIL. Without the change the
  harness has too few vectors and the item reads ORACLE.

## Item 3: TcCatalog, a hardware base OR-ed with an offset

**Root cause (TC 8c0923e0: reads VRAM at `0xa5000000 | offset`).**
- `mov.l 0xa5000000,r4; or r5,r4; mov.w @r4,r6`.
- usedAsAddress drops the register on any write but `add`, so the function was never tier "excluded" and was queued.

**Mechanism.** `or` into the tracked register keeps it, like `add`.

**Scan.** Emulating the rule on every TC and Spawn packet, the change classifies one function more: 8c0923e0 (TC),
none in Spawn.
- It takes effect on a catalog re-run (`decomp.py catalog`; the gate with `--catalog`).

## Item 4: the locked scripts are closed under use

`gate.staging_scripts` (the watcher's staged prepare, TcInputs) compiles target.json `locked_scripts` alone.
- TcVectors now uses TcProvenance, so that list needs it.
- The new check (test_batch29 `test_locked_scripts_closed`) also found two gaps already on live: TcFlow uses TcSaves
  and TcOracle uses TcText, and neither was locked.
- All three are now in `locked_scripts`.

## Merge with batch 28

tooling-b28 deployed at 20:10 (backup batch28-20261015-201053). Its live changes are merged into this stage:
- three-way merge with that backup as the base;
- TcOracle and TcVectors merged cleanly (callFault / TcCalls.repair sit next to storeFaults / TcProvenance.repair);
- README, patterns.json and test_tooling.py: both sides appended, both kept (b28's rows first);
- line endings follow live (LF for the Java files and README, CRLF for patterns.json and test_tooling.py).
The stage's pre-merge copies are in .work29_premerge_b28/.

Tests after the merge: `python -W error tests/test_tooling.py`: 249 tests OK.

## Gate

Store-fault fixtures alone (`--projects tc,sp --only store-fault --sample 0`, work29/gate/sf.log): 2 items, 0
unexpected. Both FAIL on 96/96 vectors.

Full gate: `python tools/bounded.py 7200 python tools/regress.py --projects all --sample 15 --catalog`
(work29/gate/all.log, all.json; one run, 20:24..21:59, `--jobs 1` for low free RAM). **Result: PASS.**
- 395 items: batch 28's 393 plus this batch's 2 fixtures. The tc sample is the same 15 functions as batches 27 and 28.
- The catalog fixups and TcCatalog re-ran on every project's private copy.
- 8 unexpected, all of them known (the same 8 as batch 28): gend 8c565534, 8c565868, 8c5661fc, 8c56643c; sstma07
  8c9c8f80, 8c9c9100; sstma08 8c9c89a0; sstma09 8c9c92c0.
- tc: 183 results, 0 unexpected (tc-store-fault-8c079c1e FAIL 96/96).

## Deploy list

New:
- core/asm_pool.py
- targets/dreamcast_sh4/ghidra_scripts/TcProvenance.java
- tests/test_batch29.py
- tests/fixtures/tc-store-fault-8c079c1e.fail.c, tests/fixtures/sp-store-fault-8c031eda.fail.c

Changed:
- core/agent.py
- targets/dreamcast_sh4/ghidra_scripts/TcLoads.java, TcOracle.java, TcVectors.java, TcCatalog.java
- targets/dreamcast_sh4/target.json (`locked_scripts`)
- tests/patterns.json, tests/test_tooling.py
- README.md (one row)

## Functions (live TC, per-function mechanisms)

| Function | Result |
|---|---|
| 8c07de6c | readable. Oracle hint `genrange 0x8c0a0ee4 0x7f7fffff 0x7f7fffff`: g_floatMax is read-only FLT_MAX, and random bytes there made impossible states (infinity vs NaN in one store). Re-prepared 48+48; matched C 96/96; readable attempt-03 adopted (all three old attempts pass). |
| 8c07e4be | state/vectors.json 192 dev vectors (hidden unchanged). The hidden-only failure was frame-slot reuse: the original's `closest` buffer is axis's stack slot, and the mocked Lib_ClosestPointOnSegment writes nothing, so the original copies axis into out->point. 192 dev vectors expose it (dev 51, 75, 89 fail on the old readable); matched C 240/240. Haiku readable redo: attempt 5 passes 240/240, status readable. |
| 8c07f4b8 | Item 1. Requeued after deploy: matched on attempt 1 (48/48 + 48/48, `0.0001f`), readable on attempt 1. |
| 8c079c1e | Item 2. After deploy: prepared 48+48 (33/33 blocks), matched on attempt 1, readable on attempt 1. |
| 8c0923e0 | Item 3 plus an intent note in HARDWARE_FUNCTIONS.md. Live catalog re-run at 22:20: tier excluded (five out-of-scope library functions also gain hw_refs, none in the ledger). |
| 8c077c30 | Blocker documented. A modal loop: it clears bit 0x400 of `obj->flags` (mask 0xfffffbff) and loops until a callee sets it again. Mocked callees write no memory, so every vector passes 4096 calls. It needs a callee side-effect model in both the oracle and the runner. |
| 8c08d638 | Readable. Re-prepared after both deploys (batch 28's TcCalls repairs its random call targets): 48+48, matched C 96/96; Lib_MovieStop is s32 in game.h; Haiku readable on attempt 1, 96/96. |
