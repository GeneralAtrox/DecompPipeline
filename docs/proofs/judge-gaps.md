# Judge gaps: what changed and the proof

Branch `judge-gaps` closes eleven holes in the judge. Each one let a wrong C function pass. None of the changes
loosens a check. Where a fix does not apply, the oracle's files are byte for byte what master writes.

## The changes

| # | Target | Hole | Fix | Commits |
|---|---|---|---|---|
| 1 | Saturn | 1- and 2-byte reads of listed hardware registers were not mocked, so a polling loop ran out of steps and the function was refused | the reads come from the hardware script like 4-byte ones | 10d684e |
| 2 | GameCube, Xbox 360 | Ghidra's float-to-single rounds every value below 2^-149 to 0; the 750CL (and the C's cast) gives 2^-149 above 2^-150 | the flushed step is recomputed with IEEE rounding; shared as `core/ghidra_shared/ppc/TcSingle.java` | 6a924b7, 183d7ef, 9775e8b, f9657e4 |
| 3 | Dreamcast | FPSCR (rounding mode, precision, size, enables) was not compared, so a C that left PR, SZ or RM wrong passed | for bodies with `sts`/`lds` FPSCR: entry FPSCR in the vectors (`N`), FPSCR at exit (`FS`) and at each call (`FC`) | 0001b72, 31dbedf |
| 4 | all | the NaN tolerance (any NaN equals any NaN) applied to every memory word, so an integer word holding NaN-looking bits could differ | the tolerance applies only to words a float store wrote (`FN`) | ba813f3 |
| 5 | all | the arguments of a call through a pointer were never compared | argument registers set since entry or the last call are compared at each pointer call (`CA`) | ba813f3, 6f890ad |
| 6 | Saturn | the interrupt mask at exit was always 0 (the SH-2 SLEIGH has no IMASK register) | taken from SR (`IM`) | 8349bb0 |
| 7 | GameCube | a mocked call sets both r3 and f1, but the C could read only one of them | `tc_lastr()` and `tc_lastf()` give the other | 3c54d53 |
| 8 | GameCube | an argument slot the C never set held whatever the host stack held, so the runner's output changed between runs and a slot could match an expected value by luck | the runner fills the stack with one pattern before each vector | 7bb1632 |
| 9 | GameCube (Xbox 360: wiring for its owner) | Ghidra makes every NaN it computes or loads the canonical NaN, the hardware keeps the operand's payload; through an integer register (stfs then lwz) the judge compares the bits exactly, so a correct C failed | the step's NaN result is recomputed by the PowerPC rules; shared as `core/ghidra_shared/ppc/TcNaN.java` | 8ddaf86 |
| 10 | GameCube | the Gekko SLEIGH shifts `slw`/`srw` by the whole of rB; the 750CL uses its low 6 bits, so for rB >= 64 with bit 0x20 clear Ghidra gave 0 and a correct C failed | the step's result is recomputed by the 750CL's rule (and cr0 for `slw.`/`srw.`) | the commit after 8ddaf86 |
| 11 | GameCube | Ghidra splits CodeWarrior's register save/restore chains into one-instruction functions, so the fall-through into the next entry was mocked as a call and a correct C failed | a save helper runs inline from its entry to its `blr` (the walk `TcFlow.isSaveHelper` already does) | the commit after 8ddaf86 |
| 12 | Dreamcast | a 2-byte access at an odd address or a 4- or 8-byte one off a multiple of 4 ran on in the oracle and the runner, though the SH-4 stops there with an address error; the expected results then held what that access read or wrote, so a correct C failed (and a wrong one could pass) | the oracle leaves out a vector whose run makes one (`TcFold`, `TcAlign`), generation unchanged; the runner faults on one (`tc_rt.c`) | judge-gaps-2r: d7a953f, 534c0d1, 4428c79 |

6f890ad fixes hole 5 on the Dreamcast. SH-4 p-code for an FPU instruction contains both its single and its double
form, so `fmov.s @r0,fr6` also seemed to write fr7. A step now records only the registers of the form it runs (by
FPSCR.SZ and PR at that step).

## Probes: master's oracle against judge-gaps', same functions, same matched C

Every function was prepared fresh on both sides through the broker. "Added" lines are the new kinds above. Vectors
are byte-identical unless noted.

### Toy Commander (Dreamcast): 25 functions
- 21 have identical expected results plus new lines (CA, FN). 3 are fully identical.
- **8c0611d8: PASS → FAIL (79 of 96).** The original sets FPSCR's V, Z and O enable bits; the C does not. Genuine.
- Every other verdict is unchanged, including the existing failures and their counts.

### Spawn (Dreamcast): main, gend, ovl
- **main 8c010080: PASS → FAIL (57 of 98).** The original clears FPSCR.RM; the C does not. Genuine.
- **gend 8c565968 (PASS → FAIL 96/96), 8c565b30 (PASS → FAIL 7/96), 8c5659b2 (FAIL 70 → 12):** FPSCR at exit. The
  original ends with `sts fpscr,r0; or #0x80000,r0; lds r0,fpscr`, leaving PR=1. The matched C says in a comment it
  is "NOT bit-exact". Genuine.
- **gend 8c55c620: PASS → FAIL (53 of 98).** The original loads fr4=2.1, fr5=1.2 (or 0.8) and fr6=1.5 and calls
  through a pointer; the C passes zeros. Genuine (re-checked after 6f890ad: no false fr7 entries remain).
- **ovl 8c5f7c8c (96/96), 8c5f7cd6 (10/96), 8c5f7e54 (15/96): PASS → FAIL.** FPSCR.PR at exit. Genuine.
- **ovl 8c5f6ef0: PASS → FAIL (1 of 96).** The new entry-FPSCR draw shifts the vectors; vector 88 has DN=1, where
  the original's `fcnvsd` flushes a denormal to 0 and the C converts it exactly. Genuine.
- The 12 pointer-call functions (main 8c010c98, 8c0110a0, 8c011200, 8c0112a0; gend 8c55bf80, 8c55bfa6, 8c55c980;
  ovl 8c4580c0, 8c458686, 8c4587f6, 8c458924) keep PASS with CA lines added. Before 6f890ad, 8c55c980 and 8c458686
  failed only on a false fr7 entry; with it they pass and their expected results are otherwise identical.
- 8c565534, 8c5f7858: FS added, still PASS.

### Skies of Arcadia (GameCube): 19 functions, at the branch head (03f3936)
- All 19 functions' vectors are byte-identical to master's. Expected results are identical apart from the added
  lines (CA, FN), except in 800cf988 and 800d1a34 (fix 2 below).
- **800d1a34: PASS → FAIL (1 of 96).** Memory 0x802e111c expected 0x00000001 (2^-149), got 0: the C was matched
  against the old, flushing oracle. This is the function fix 2 was found on. Genuine.
- **800cf988: PASS → FAIL (1 of 96).** The same cause: a product between 2^-150 and 2^-149 is now 2^-149 (the
  750CL's result) where master's oracle had 0 (memory 0x81000f24 and 0x81001160, and f1 returned). The matched C
  has a helper that sets such a product to zero, copying master's oracle. Genuine; re-match it through the pipeline.
- **80096260, 800c86ec, 80226984: PASS → FAIL (42, 3 and 76 of 96).** At a call through a pointer, f1
  holds a value the original put there (a constant it loaded to store into a structure, a field it loaded to
  compare, or the float result of the call just before), and the C passes 0. The judge cannot see the target, so it
  counts every argument register set since the last call. The callee may never read f1. These are **correct by the
  new rule but may be incidental**; a matched C passes the value, or `tc_lastf()` for the previous call's result.
- The other 14 keep PASS.
- 03f3936 fixed a bug found in this run: the GameCube oracle's check for a stored float re-read the word through
  the emulator, which added it to the step's load list while the step iterated it. 80100bf8 got no valid vectors
  (`ConcurrentModificationException`), and others lost vectors. The word is now read past that list. Dreamcast and
  Saturn do not have this code.

### Grandia (Saturn)
- **060398ae, 060398cc: REFUSED → 48/48 valid vectors.** Both poll a hardware register with 2-byte reads; master's
  oracle ran out of steps. Fix 1 working as designed.

### Dragon Force 2 (Saturn): 060182d4, 06019464
- Vectors identical; the only changed lines are `IM`. Master wrote `IM 0` for every vector.
- 060182d4 is a 22-byte "set interrupt mask from r4": the new IM is r4's low 4 bits in every vector.
- 06019464 leaves the mask alone: the new IM equals the entry mask in all 96 vectors.

## Mutation audit

`tools/mutation_check.py --report` on the same matched C, the same mutants (up to 32 per function), master's judge
on master's harnesses and judge-gaps' on judge-gaps'. A lost kill is a mutant master catches that judge-gaps lets
through.

| Project | Functions | Kill rate before | Kill rate after | Lost | Gained | Matched C now caught |
|---|---|---|---|---|---|---|
| Toy Commander | 25 | 256/315 (81.3 %) | 264/315 (83.8 %) | 0 | 8 | 1 |
| Spawn main | 8 | 170/180 (94.4 %) | 175/180 (97.2 %) | 0 | 5 | 1 |
| Spawn gend | 12 | 137/217 (63.1 %) | 146/217 (67.3 %) | 0 | 9 | 3 |
| Spawn ovl | 14 | 193/286 (67.5 %) | 203/286 (71.0 %) | 0 | 10 | 4 |
| Skies of Arcadia | 19 | 341/396 (86.1 %) | 341/396 (86.1 %) | 0 | 0 | 5 |
| Grandia | 3 | 30/33 (90.9 %) | 33/33 (100 %) | 0 | 3 | 0 |
| **All** | 81 | 1127/1427 (79.0 %) | 1162/1427 (81.4 %) | 0 | 35 | 14 |

Rates count only functions whose matched C passes on both sides. "Matched C now caught" is the functions listed
above as PASS → FAIL, so all their mutants are caught too. The Skies row is at the branch head (03f3936); the
others were run before f9657e4 and 03f3936, which change only GameCube oracle code.

An earlier Skies run (8 functions, before 03f3936) lost one kill: 800cf988's mutant 1 turns the zeroing helper off,
and master caught it only because master expected 0 for a 2^-149 result. At the head, the matched C itself fails on
that vector, so the kill is not lost.

## GameCube runner: the same output on every run (change 8)

Waste audit 2 found that the runner's output for Skies 8020cba4 and 80213920 changed on every run, on master too.
Both matched C functions declare a `TcArgs` and set only r3 and up before `tc_call`, so f1..f8 and the stack slots
s0..s7 of the call log held old host stack contents (often host addresses, which move between runs).

**It could not hide a difference in a compared field.** At a direct call the compare reads only the slots the
callee's signature declares, and at a pointer call those in the oracle's `CA` line. A slot it reads that the C
never set is a real fault in the C. The risk was the other way: such a slot could hold, by luck, the value the
oracle expects (0 is common), and pass.

The runner now fills 64 KB of stack below `main` with 0xa5 bytes before each vector. An unset slot reads
`a5a5a5a5a5a5a5a5` on every run and machine, which no oracle value is in practice.

Proof, all 83 matched Skies functions on the project's harnesses, runner before and after (everything else at the
branch head):
- Every verdict and failing count is unchanged (69 PASS; the 14 FAIL are harnesses without FN lines, see Landing).
- Two runs of the new runner give byte-identical output for all 83.
- Old and new output differ only in 8020cba4 and 80213920, only in CALL lines, and only in the f1..f8 and s0..s7
  columns, which neither function's compare reads.
- Mutation audit on the two functions (same 62 mutants): 46/62 killed before and after, 0 lost, 0 gained.
- `tests/test_gc_paint.py` builds the real runner and checks the pattern in unset slots and identical runs; with
  the paint removed it fails.

## NaN results (change 9)

Xenon's judge run found Xbox 360 82635508 failing on 6 of 96 vectors: a call argument in integer register r6 was
expected `7fc00000`, the hardware (and the C) gives `ffffffff`. The function loads the NaN `ffffffff` with `lfs`,
carries it through `fmuls` and `fadds`, stores it with `stfs` and passes the stored pair to a call in r6. **Change 4 (FN) does not cover it**, and should not: FN tolerates a NaN
difference only in a memory word a float store wrote. r6 is an integer register, compared bit for bit; tolerating
NaN-looking bits there would let a wrong integer pass. The fault is in the oracle, so the fix is too.

Ghidra's FloatFormat turns every NaN it touches into `7ff8000000000000` (sign at most kept): `lfs` widening,
arithmetic, `fneg` and `fabs`. An invalid operation without a NaN operand (inf - inf, 0 / 0) gives `fff8...`. The
hardware rules (PowerPC Book I, 750CL, Xenon) and what `TcNaN` computes:
- `lfs` widens a single NaN's fraction without quieting it (`ffffffff` loads as `ffffffffe0000000`);
- an arithmetic result is the first NaN of frA, frB, frC (fmadd's assembly order is fD,fA,fC,fB), quieted, and a
  single-precision result keeps the top 23 fraction bits; with no NaN operand, the default `7ff8000000000000`;
- `fnmadd`/`fnmsub` do not negate a NaN; `fneg` and `fabs` change only the sign bit.

The fixup runs only for a step whose Ghidra result is a NaN, and sets ps1 too where the SLEIGH copies the result
there. `tests/test_gc_nan.py` checks, over 1.8 million random operand sets for 19 instructions, that Ghidra's result
is a NaN exactly when IEEE's is (so no non-NaN value can change) and that every recomputed result is a quiet NaN,
plus 17 fixed cases (82635508's `ffffffff`, the operand-order and sign cases, Ghidra's own `fff8...`).

Proof, all 83 matched Skies functions prepared fresh through the broker on 7bb1632 and on the change:
- vectors byte-identical in all 83; expected results identical in 67;
- in the other 16 every differing word or field is a NaN on both sides (W words, RET and CALL float fields, and in
  80090f9c and 800d07f8 an integer CALL argument holding NaN bits: `7fc00000` → `ffffffff`, the 82635508 case);
- no verdict or failing count changes (the five Skies PASS → FAIL above fail the same way on both sides);
- mutation audit on the 16 (same mutants): 246/312 (78.8 %) killed before and after, 0 lost, 0 gained.

Xbox 360 part. This branch does not edit `xbox360-ppc`. Its `TcFixups.java` needs the same wiring, plus one line in
its fused multiply-add path, and `TcNaN.java` in `locked_scripts`. The patch is for the target's owner; it was tested
in a scratch checkout of `xbox360-ppc` (6505588), not committed. Too Human 82635508 and the 50 functions of the
proof set, prepared fresh on both sides, with the translator's C (byte-identical on both sides) judged on each:
- vectors byte-identical in all 50 with vectors (82a513d0 is refused on both sides);
- expected results identical in 47; 828c9a10 and 829f6e20 differ only by NaN (W and RET);
- 82635508 differs only in 6 CALL lines (the 6 failing vectors), every changed field a NaN: `7fc00000` →
  `ffffffff` in one 32-bit half of a 64-bit integer field (r6 among them), `7ff8000000000000` →
  `ffffffffe0000000` in float fields;
- **82635508: FAIL (6 of 96) → PASS**; every other verdict unchanged (48 PASS, and 82976860 has no vectors on
  either side);
- mutation audit on the three: 42/56 (75.0 %) before and after on 828c9a10 and 829f6e20, 0 lost, 0 gained;
  82635508 could not be audited before (its C failed) and kills 24/30 after.

## slw / srw and save-helper chains (changes 10, 11)

Both were found by the GameCube translator (ppc-translate): its C failed the judge, and the fault was the oracle's.

Change 10. The Gekko SLEIGH writes `A = S << B` and `A = S >> B`, and Ghidra's 4-byte INT_LEFT / INT_RIGHT give 0
for any count of 32 or more. The 750CL gives 0 only when bit 0x20 of rB is set, and otherwise shifts by rB & 0x1f.
The two differ only for rB >= 64 with bit 0x20 clear (Skies 801ab6e8, 802377f8, 8023fef8). The fixup writes only
when a step's result differs from the 750CL's. `tests/test_gc_shift.py` runs Ghidra's own shift code against the
750CL's rule over 3,612,288 (op, rS, rB) triples: the fixup is right for every one, and Ghidra differs (1,019,552
times) only in that rB range, so no other result can change. `rlwnm` masks its count with 0x1f and is already right.
The Xbox 360 language (stock `ppc_instructions.sinc`) masks `slw`/`srw`/`sraw` with 0x3f and `sld`/`srd`/`srad`
with 0x7f, so it does not have this problem and needs no change.

Change 11. Skies' `_savegpr`/`_restgpr` chain (8025a2b0..8025a2c8, one `stw`/`lwz` per entry, each falling into the
next) is split by Ghidra into one-instruction functions. The oracle ran a save helper inline, but only over the
called entry's Ghidra body, so the step into the next entry looked like a call and was mocked (802790e8, 80270a98:
every vector failed). Now the inline set also holds the instructions from the entry to the `blr`, at most 40: the
same walk `TcFlow.isSaveHelper` uses to recognise a helper. The old body stays in the set; the walk only adds.

Proof on laptop2 (MSVC 14.44), all prepared fresh through laptop2's own broker on 8ddaf86 and on the change: the
83 matched Skies functions and 32 others (the 24 that use `slw`/`srw`, the 13 that call a save helper, with overlap,
judged with the translator's C, byte-identical on both sides):
- the 83: vectors and expected results byte-identical, verdicts identical (78 PASS, the same 5 FAIL);
- the 32: harnesses (vectors and expected results) byte-identical in 25 of the 30 prepared (8026af94 and
  8027ff18 are refused on both sides); 801723a4, 8017c6bc and 80260e64 correct shifts only in search runs that are
  rejected the same way on both sides, so their harnesses do not change. In 801ab6e8, 802377f8 and 8023fef8 the
  vectors are byte-identical and the expected results differ; in 802790e8 and 80270a98 the vector search itself
  changes (it runs the oracle), so the vectors are new ones, and it covers more (802790e8 26 → 35 of 83 blocks,
  80270a98 92 → 110; 801ab6e8 24 → 26), with no block lost in any function. The 5 that differ:
  - 801ab6e8 (RET and the COV path), 802377f8 (W words) and 8023fef8 (W words and a CALL argument): a value
    that came from a shift by rB >= 64 with bit 0x20 clear;
  - 802790e8 and 80270a98: the mocked CALL lines into 8025a2bc, 8025a2c8 and 8025a308 are gone (192 and 66), the
    helper's stores show as W lines, and the real calls (80285544 and others) are still mocked and compared;
- **801ab6e8 FAIL (7 of 96), 802377f8 FAIL (2), 8023fef8 FAIL (6), 802790e8 FAIL (96), 80270a98 FAIL (96) → PASS**,
  the same C; every other verdict unchanged (21 PASS, 4 FAIL with too few vectors, 2 with no vectors);
- mutation audit on the 32 (same mutants): 122/324 (37.7 %) killed before and after on the 22 that pass on both
  sides, 0 lost, 0 gained; the 5 could not be audited before (their C failed) and kill 58/76 after.

801eff90, the third case the translator found, is fixed by the shared pass-through rule (`TcPassThrough`) on
`x360-oracle-fixes` (f7bf3fa), not here.

## SH-4 address errors (change 12, branch judge-gaps-2r)

Branch `judge-gaps-2r` is on `prewave-rebased` (582735a). It touches only `targets/dreamcast_sh4` and tests, so
every other target (Grandia, Skies, Xbox 360) is byte-identical by construction. Every run below was on laptop2,
with compiler 14.44 / laptop2, Ghidra through the broker.

**What it does.**
- `TcFold` records the first data access an instruction makes at a misaligned address (2 bytes at an odd one, 4 or
  8 off a multiple of 4), only while `TcOracle` steps, never on the instruction fetch. The run goes on as before.
- `TcVectors.writeVector` leaves such a vector out of `vectors.txt` (after prewave's strict replay). Generation, its
  repairs, the rescue, reach and bound passes count it as before, so every other vector keeps its bytes and its id.
- `TcAlign.replace` then draws as many replacements from a generator of its own (ids after all others), and
  `TcAlign.fix` makes the summary count the written vectors only. `oracle_summary.json` gets `"address_errors"`
  only when a vector was left out; otherwise every file is what prewave writes.
- The Dreamcast runner (`tc_rt.c`) faults `read-misaligned` / `write-misaligned` in `rd16/rd32/wr16/wr32`, as the
  SH-4 does. This sits next to prewave's stack paint; neither changes the other.

**Why not refuse the access as a fault (the first version).** Making it the vector's fault re-steered the repairs
and the generators (ids, tries), so other vectors changed and kills were lost: Power Stone 2 8c0a19f2 went from
372 to 270 killed of 974 mutants. Leaving the vector out after generation keeps every other vector as it was.

**Flags.** Every flags-on prepare and mutation run used exactly the six flags Dreamcast will run with:
branch_coverage, targeted_vectors, mock_junk, mock_writes, call_snapshots, exit_frame (the set of prewave's
l2full.py ON). coverage_inputs stayed off. The flags were set over the project's config for each run only;
`target.json` has all seven off, and no workspace's project.json sets any of them.

### The five targets flip, with the same C
The 25 functions of the mutation lists (TC 3, PS2 16, Spawn 6) were prepared on `prewave-rebased` and on this
branch, both with flags on, and judged with the same matched C:

| Game | Function | prewave | judge-gaps-2r | Why it failed |
|---|---|---|---|---|
| PS2 | 8c052cbe | FAIL | PASS | vector 62 stores through a pointer from the inputs at a misaligned address |
| PS2 | 8c159102 | FAIL | PASS | vectors 96..111 make a misaligned access |
| Spawn | 8c05fec2 | FAIL | PASS | 73 vectors make one; 23 replaced |
| Spawn | 8c07cfe0 | FAIL | PASS | 24 vectors make one; 13 replaced |
| Spawn | 8c07d140 | FAIL | PASS | 9 vectors make one; all replaced |

In every case the expected results held what a run of the original that the SH-4 stops at would have read or
written: for example, a NaN stored at 8cc00507 or 8cc00f9b. The C, compiled for the host, cannot reproduce that.

### No other verdict changes
Each of the 25, prepared on both sides, was compared block by block (`vectors.txt`, `expected.txt`). Not one kept
vector or expected block changed (changed = 0), and every header is the same. The only differences are vectors
left out, the replacements added after every other id, and the summary's counts:

Left out and replaced are given as dev + hidden.

| Game | Function | Left out | Replaced | Note |
|---|---|---|---|---|
| TC | 8c01a50c | 0 + 1 (id 81) | 0 + 1 | |
| TC | 8c06377a | 1 + 0 (id 8) | 1 + 0 | |
| PS2 | 8c052cbe | 0 + 1 (id 62) | 0 + 1 | target |
| PS2 | 8c1047f8 | 0 + 1 (id 95) | 0 + 1 | |
| PS2 | 8c13f102 | 1 + 1 (ids 14, 56) | 1 + 1 | |
| PS2 | 8c14b544 | 1 + 0 (id 18) | 1 + 0 | |
| PS2 | 8c159102 | 8 + 8 (ids 96..111) | 8 + 8 | target; blocks hit 130 → 64, dev 125 → 15 |
| Spawn | 8c05fec2 | 35 + 38 | 13 + 10 | target; 100 → 50 vectors |
| Spawn | 8c062920 | 15 + 12 | 15 + 12 | |
| Spawn | 8c064220 | 36 + 36 | 36 + 36 | blocks 11 → 9 |
| Spawn | 8c07cfe0 | 15 + 9 | 13 + 0 | target; 96 → 85 vectors |
| Spawn | 8c07d140 | 6 + 3 | 6 + 3 | target |

For every other function among the 25, `vectors.txt`, `expected.txt` and `oracle_summary.json` are the same
(`meta.json` differs only in `oracle_stamp`). Every verdict apart from the five targets is unchanged.

The coverage drops (8c159102, 8c05fec2, 8c064220) are coverage that only an address error reached. The console
never runs those paths from these inputs. The coverage verdicts see the written vectors only.

### Mutation: zero lost on vectors the console runs
A kill that only a left-out vector made is not counted (the user's rule): the console stops at that vector's
access, so no run of the original reaches what it checked. Every lost kill below was tested the same way. The test
uses prewave's judge on prewave's harness with only the left-out vectors removed ("prewave without them"). A lost
kill is excused only if that harness does not make it either. Kills are by the same matched C's mutants (same
source hash, same ids) on each side.

**Union audit** (`tools/mutation_audit.py gate`, 10 mutants a function, time cap 3600 s). The new side is this
branch's judge on this branch's harnesses (flags on). The old side is prewave's own audit
(`docs/proofs/2026-10-24/prewave/audit_rb_rb_on.json`). The functions are prewave's audit sample. Integration need not
run its own union audit.

| Workspace | Functions | Mutants | Killed: prewave → this branch | Gained | Lost | Lost, without left-out vectors |
|---|---|---|---|---|---|---|
| TC | 149 | 1218 | 994 → 1014 | 22 | 2 | 0 |
| PS2 | 150 | 1263 | 977 → 998 | 21 | 0 | 0 |
| Spawn main | 147 | 1030 | 801 → 812 | 15 | 4 | 0 |

The lost kills, mutant by mutant:
- TC 8c0103c4 m03, m06. Left out: vectors 97 and 99. Outcomes: prewave `KSKKBKSSSK`; prewave without them
  `KSSKBSSSSK`; this branch `KSSKBSSSSK`.
- Spawn 8c01bcc8 m03, m05, m10. Left out: 15 vectors. Outcomes: prewave `SSKKKKKKKK`; prewave without them
  `SSSKSKKKKS`; this branch `SSSKSKKKKS`.
- Spawn 8c029060 m08. Left out: 2 vectors. Outcomes: prewave `KKSKKBKKKK`; prewave without them `KKSKKBKSKK`; this
  branch `KKSKKBKSKK`.

In each case, prewave without the left-out vectors gives exactly this branch's outcomes. No flips, nothing stale,
nothing missing. Three Spawn and one TC function have no vectors on either side. In the union's harnesses, no kept
vector or expected block changed in any of the 450 functions. The 428 with nothing left out also have the same
`oracle_summary.json` (PS2 checked key by key: `meta.json` differs only in `oracle_stamp`).

**The 25 functions, every mutant** (1000 mutants a function, time cap 5000 s). The 16-mutant read of all 25 lost
nothing (TC 33 → 34 of 46, PS2 153 → 155 of 213, Spawn 28 → 29 of 44). The full run was then done on every function
whose harness differs.

The other functions' harnesses are byte-identical on both sides, and the runtime change can only add kills there: a
misaligned access now faults, and a fault is a failure. Their figures with every mutant: TC 8c066388 268 → 272 of
322, 0 lost.

| Game | Function | Killed: prewave → this branch | Lost | Lost, without left-out vectors |
|---|---|---|---|---|
| TC | 8c01a50c | 180 → 178 of 282 | 18 | 0 (prewave without vector 81: 161; this branch keeps all 161 and gains 17) |
| TC | 8c06377a | 30 → 34 of 58 | 0 | |
| PS2 | 8c1047f8 | 169 → 184 of 280 | 0 | |
| PS2 | 8c13f102 | 277 → 289 of 378 | 0 | |
| PS2 | 8c14b544 | 223 → 243 of 389 | 0 | |
| Spawn | 8c062920 | 70 → 72 of 87 | 0 | |
| Spawn | 8c064220 | 226 → 229 of 885 | 6 (122, 254, 340, 918, 978, 979) | 0 (see below) |

The five targets have no prewave kills to lose: their matched C fails prewave's harness (the "baseline" check), so
the mutation check does not run there. On this branch: 8c052cbe 52 of 58, 8c159102 291 of 961, 8c05fec2 575 of 864,
8c07cfe0 181 of 402, 8c07d140 282 of 343.

Spawn 8c064220 has 72 of its 97 vectors left out (36 dev, 36 hidden). The 25 that are left fall under the
16-vectors-a-set minimum, so `check` would fail even the matched C on them (0 of 25 failing). For this one run, the
minimum was set to 0 over the project's config, through the proof wrapper's `MIN_VALID=0`. A mutant then counts as
killed exactly when it fails one of the 25 kept vectors. Result:
- prewave without the left-out vectors kills 218 of 885. All 6 lost kills are among the ones it does not make.
- This branch keeps all 218 and gains 11.
So every one of the 6 came only from a left-out vector.

### The runtime's flips
With the faulting runtime, 579 functions that passed on their existing (old-oracle) harnesses fail. Those harnesses
hold expected results from misaligned runs. They are **re-prepared by the re-check**. A sample of 40 (TC 12,
PS2 18, Spawn 10) was re-prepared with this branch's oracle (flags on) and judged with the same C:

| Game | Sampled | PASS | Not prepared |
|---|---|---|---|
| TC | 12 | 9 | 3 |
| PS2 | 18 | 17 | 1 |
| Spawn | 10 | 10 | 0 |

Every sampled function that got a harness passes, so the runtime is not over-strict and no sampled C is wrong. The
four not prepared:
- TC 8c05b87c, TC 8c06fc14, PS2 8c185f80: the 900 s Ghidra timeout. laptop2 was running three Ghidra slots and two
  mutation runs at the same time.
- TC 8c09d660: cut by the run's own time limit (2700 s).

They were not re-run with the same limits. The re-check re-prepares them, and it reports any Ghidra timeout.

### Not re-run (covered by the re-check)
- **11 TC functions with no prewave-side harness** (the base run hit its limit): 8c071b88 8c074e08 8c074e3c 8c074e84
  8c074f18 8c07564c 8c07e49c 8c07eb14 8c07f6e4 8c080938 8c092514. 8c09d9dc had no vectors on either side.
- **Spawn 8c07cfe0's flags-off run** (900 s timeout). Its flags-on run is in the tables above.

### Tests
- New tests: `tests/test_sh4_align.py` (TcFold, TcAlign, the wiring) and `tests/test_sh4_rt_align.py` (the
  runtime's faults, compiled).
- On laptop2, the Dreamcast and strict-judge tests passed except one: 141 of 142 (`test_sh4_align`,
  `test_sh4_rt_align`, `test_branch_cov`, `test_batch32`, `test_dc_fpscr`, `test_strict_judge`, `test_r14_live`,
  `test_targeted_vectors`, `test_reach`, `test_spin`, `test_track`).
- The one failure is `test_strict_judge::Wiring::test_lines`, because laptop2's checkout uses `core.autocrlf=true`.
  It fails the same way on prewave's own tree there. An LF checkout passes it.
- The full suite is owed on integration's merge with master (971ce15). The merge is clean, and in the merged tree
  `TcOracle.java` and `TcVectors.java` are still exactly 400 lines.
- Tests that would fail only because of this branch: the two new ones; the 400-line pins in
  `test_strict_judge::Wiring::test_lines`, `test_branch_cov` and `test_dc_fpscr`; and the exact wiring strings in
  `test_sh4_align.Wiring`, if `TcOracle`, `TcVectors`, `TcPrepare` or `TcRescue` move.

## Landing

judge-gaps changes the oracle and the compare together. The NaN tolerance now needs FN lines, which only the new
oracle writes, so a harness from the old oracle judged by the new compare fails on NaN words (14 of the 83 Skies
functions above; the GameCube translator saw 12). As with the ftrc runtime change: land with a re-stamp and
rejudge only on re-stamped harnesses. Saturn Bomberman 0KRNL's 36 `sr_ops` functions and Dragon Force's 11
(06010228 06011000 0607d724 06085db8 060876ec 06087850 06088074 06088148 060881ac 06088206 06088284) need
re-prepared harnesses for the IM fix.

The Xbox 360 session's `x360-oracle-fixes` (pass-through registers, self-call, and the shared `TcPassThrough`) and
`xbox360-speed` go to the user in the same decision, and also need a re-stamp. The order, so that every project is re-stamped and re-prepared once:
1. The user approves the three branches.
2. Merge `judge-gaps` into master.
3. The Xbox 360 owner merges master into `xbox360-ppc`, applies the TcNaN wiring for its `TcFixups.java` (the patch
   is in the hand-over note), merges `x360-oracle-fixes` and `xbox360-speed`, and adds one `stamp_scopes` entry covering all three.
4. Only then: one re-stamp and one re-prepare per project, every target. Rejudge only on re-stamped harnesses.
No re-prepare runs between steps 2 and 4.

Open, not fixed here: for Skies 800553b4 (declared void) the oracle seeds f1..f8 at entry, and a callee whose Ghidra
signature lists float arguments then compares values no C can see. A decision for the user: zero the argument
registers a function's signature does not declare, or fix the signatures.

## Limits (unchanged by this branch)
- Stack arguments of a pointer call are not compared, only registers.
- An argument register set only incidentally (as in the three Skies functions above) is still compared.
- Readable C passes integer arguments before float ones; the register order is what is compared.

## Overlap
- No change to `core/strict_judge.py` or `tc_strict.h`.
- Change 12 calls prewave's `TcStrict.replay` first and leaves `TcStrict` itself unchanged. `TcOracle.java` and
  `TcVectors.java` are each exactly at the 400-line cap. `tc_rt.c` merges with prewave's stack paint.
- The branch `prewave-judge` touches some of the same files (9 textual conflicts in a trial merge). Both Dreamcast
  `TcOracle.java` files are at the 400-line cap.
