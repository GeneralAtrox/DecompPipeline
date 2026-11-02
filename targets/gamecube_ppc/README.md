# Target: gamecube_ppc

GameCube main executables (`main.dol`) on the PowerPC 750CL ("Gekko"). Ghidra language `PowerPC:BE:32:Gekko_Broadway`
with the GameCubeLoader extension (`-loader GameCubeLoader`). The loader maps the DOL's sections; `TcSetup` maps the
rest of MEM1 (0x80000000..0x817fffff) as zero-filled RAM so stack, heap and scratch accesses are memory, and pins the
project's fixed registers (`project.json` `fixed_regs`: r2/r13, the small-data bases, and the GQRs). A game whose code
is partly in REL modules works on `linked.dol` (below); `TcSetup` then also makes each REL function listed in
`seed_entries` an entry point, so analysis finds the module code no main.dol code calls.

## Getting code off a disc (read-only on the image)

```
python tools/gcdisc.py info    <image>                     # disc header: game id, title, region, main.dol's hash
python tools/gcdisc.py list    <image> [out.tsv]           # every file: size, disc offset, path
python tools/gcdisc.py scan    <image> [out.tsv]           # main.dol's sections and every REL module on the disc
python tools/gcdisc.py extract <image> <new_dir> [--code | NAME ...]   # default main.dol; --code adds the REL files
python tools/gcdisc.py dol     <main.dol>                  # a DOL's sections and the project.json fields from it
python tools/gcdisc.py setup   <image> <new_disc_dir> <workspace> [--rels all|none|ID,ID...]
python tools/gcrel.py  link    <main.dol> <new_dir> <module.rel> [...]   # the REL link on its own
python tools/gcrel.py  where   <link.json> <addr> [...]                  # an address's module, section and offset
python tools/gcrel.py  addr    <link.json> <module> <section> <offset>   # and back
python tools/gcrel.py  ids     <link.json> state/function_bodies.txt [out.tsv]   # every catalogued function's identity
```

It reads GCM/ISO images (a whole-disc dump; a Wii disc is refused) and WIA/RVZ files (`tools/rvz.py`, written from
the format description: bzip2, LZMA, LZMA2 and Zstandard groups, RVZ's packed padding; `python tools/rvz.py <in.rvz>
<new.iso>` writes the plain image). A Zstandard RVZ reads at 3-4 MB/s (the decoder is plain Python): Skies of
Arcadia's main.dol in under a second; converting a whole disc to ISO is better left to Dolphin. `setup` is the hands-off step for a new game: it
writes main.dol, every REL (decompressed, under `files/`) and `disc.json` (header, file list, sections, modules) to
`<new_disc_dir>`, and `project.json` and `oracle/TcInputs.java` to `<workspace>` (it refuses a workspace that already
has a project.json). The project.json fields it fills from main.dol:

- `binary`, `binary_sha256`: the extracted main.dol, relative to the workspace (`linked.dol` with REL modules);
- `game_ranges`: the text sections (and each linked module's code);
- `global_ranges`: the writable data in CodeWarrior's layout: `.data` (the last data section before the bss), then
  inside the bss range `.bss`, `.sdata`, `.sbss` (up to `.sdata2`, which is read-only, as is `.sbss2` after it); for
  each linked module its `.data` (its last data section) and its bss;
- `fixed_regs`: r2 and r13 as the start-up code loads them (`__start` and the routines it calls, `lis`/`ori` or
  `lis`/`addi`); `.sdata` and `.sdata2` are the sections 0x8000 below them. A GQR is pinned when the program writes it
  with one constant value only; writes that put a saved value back (a thread context, `lwz`/`lmw` before the `mtspr`)
  do not count, any other write (an argument, a computed value) means the GQR changes and is not pinned.

`name`, `ghidra_name` (the game id) and `port` are defaults to change freely; `references` starts empty (NEW_GAME.md
step 2). Anything the rules cannot settle is printed as a note.

### REL modules

A REL module has no load address: the game's loader (OSLink) places it at run time and patches its references to
main.dol and to other modules. `setup` does that once (`tools/gcrel.py`) and the workspace works on the result,
`linked.dol`: main.dol's sections unchanged, plus every module's code in a free text section and its data in a free
data section, so Ghidra's DOL loader, the C runner, the judge and ppc-translate read it as they read main.dol. The
addresses are fixed by the inputs alone: modules in module-id order, all code, then all data, then all bss (no bytes:
MEM1 reads as zero), each aligned to its module's alignment, from the end of main.dol's bss upwards, never inside
the scratch window, the generator heap or the stack. By default every module is linked, one per module id (a second
file with the same id is noted and left out); `--rels 12,13` picks modules, `--rels none` keeps main.dol alone. A
set that does not fit (24 MB of MEM1, or more runs than main.dol has free section slots) stops setup before it writes
anything.

`link.json` records where each module went (every section's address, prolog/epilog/unresolved, the relocations
applied and those against modules not linked: their calls go to the module's `unresolved` function, as the game sets
them, other words stay as the file has them). A function's identity is its module id, section and offset
(`gcrel.py where`; main.dol's functions are module 0, their address), so it names the same function whatever else is
linked; its address is the one in this workspace. `seeds.txt` lists the module functions the relocations show (bl
targets inside module code, and prolog/epilog/unresolved) for `seed_entries`.

## ABI and memory map (target.json)

- Arguments r3..r10 and f1..f8; results r3 (r3:r4 for 64-bit, r3 high), f1. Stack arguments: the words at r1+8..r1+36.
- Every float register holds a double: parameters and results in f1..f8 are `f64` in the C signature.
- At entry r1 = 0x817fe000 and LR = 0x817ff000 (the return sentinel). Writes at and above 0x817f0000 (the stack window)
  are not compared.
- Test pointers point at the scratch area 0x81000000..0x81001000 and the generator heap 0x81001000..0x81040000
  (`template/TcInputs.java`). Writable globals (`global_ranges`) named by the code are randomised, except words that
  hold a code address (switch and function-pointer tables: `TcPrepare.codePointer`).
- The uncached view 0xc0000000.. folds to 0x80000000.. everywhere (`TcBase.fold`, `tcrt_fold`).

## Oracle (ghidra_scripts)

`TcPrepare` exports the packet (`asm.txt` annotated by `TcAsm`, `ghidra.c`), the prototype (`TcBase`, plus the
argument registers `TcFlow` finds read before written) and runs the vectors (`TcVectors`) through `TcOracle`:

- a fresh `EmulatorHelper` per vector; RAM through `TcBank` (folding, the write set, R8 code reads);
- a call (a `bl` / `bctrl` / tail `b` to another function) is mocked: the call record is target, r3..r10, f1..f8 (64-bit
  bits), s0..s7, and the callee "returns" r3 = `C[k]`, f1 = `D[k]` and leaves 0 in every other volatile register
  (r0, r5..r12, f0, f2..f13, ctr), as `TcFlow` assumes a call clobbers them. r4 is the low word of a 64-bit result (r3:r4): with
  `mock_junk` it holds the junk for that call, the C reads the same word with `tc_lastr4()`, and the judge allows 0 for a passed-on
  r4 only where the callee does not set r4 (`TcFlow.returnsR4`). r4 counts as set after a callee that sets it on every path, so a
  pointer call compares it there (`TcTrack.call`) and the C passes it on. Without that, a caller that passes its
  own entry f2..f8 on to a later callee (80213fe8 into 80155994) had expected values no C signature can carry;
- CodeWarrior's register save/restore helpers (`_savegpr_NN` and family, `TcFlow.isSaveHelper`) run inline: they are
  part of the frame, not calls.
- a store into a hardware register (`memory.mmio`: 0xcc000000..0xcc00ffff, the GX FIFO at 0xcc008000 and the video,
  DMA and audio registers; 0xe0000000..0xe0003fff, the locked cache) is not memory. `TcBank` logs it in order (address,
  size, value; a store of the same step to the same address and size, the `stfs` rewrite, replaces the entry) and does
  not pass it on; expected.txt carries `IO addr size value` lines, the runtime (`tcrt_mmio`, `wr_be`) logs and reports
  the same, and `ppc_compare.compare_io` compares the two sequences (count, address, size, value, order: the FIFO takes
  its words in order). Before, any such store made the vector invalid ("write outside RAM"), so a function that sends a
  vertex to the FIFO had no usable vector at all. A read of a hardware register takes the vector's next scripted word (`vectors.txt` H, a device's answer is an input,
  from its own generator so that no other input changes; beside the Vec in `TcOracle.hwOf`, as Vec is each workspace's own)
  and is logged in the same order with its kind (`IO addr size value r`); with no words scripted it faults on both sides.
  A store past a window is still a write outside RAM, and the run stops there, while the load log still holds the pointer's
  source. At most 4096
  stores per run (`TcBank.IO_LIMIT`, `MAX_IO`).
- a function's inputs include everything its callees read before writing, whole: the registers compared at a call are
  the callee's own record (`TcPrepare.calleeRegs`), so the caller's signature carries the same set (`TcFlow.calleeReads`
  walks the callee's full record; before, it stopped one level short and a float argument passed through two calls, f1..f8
  of Melee 8001e6d8 into 803644cc, was missing from the signature). Calls followed one inside another are capped at
  `MAXNEST` and a result computed with a cut is not memoised.

Variadic callees: a variadic function's register save area (`stwu r1`, a cr1 branch around `stfd f1..f8`, the
`stw r3..r10`) is not a read for `TcFlow`. Without that, a stub like OSReport made every caller, and every function
reaching one through pass-through, "read" r3..r10 and f1..f8. The cost: the variable arguments of a direct call to a
variadic function are compared only where the callee reads them as registers.

## Known limits: what both sides share, so the judge cannot see it

Nothing here has been compared with a real console. These are modelled once and used by both the oracle and the C runtime, so a
wrong model gives the same wrong answer twice and passes:

- `fres` and `frsqrte`: the estimate tables have one source, used on both sides;
- `ConvertToSingle` (a double going to a single-precision result or store);
- the double-rounded `fmadd` family in single precision.

Not modelled, refused by the oracle with their reason: non-zero GQR quantized `psq_l`/`psq_st`, the `ps_*` arithmetic, `mfmsr`,
`mtmsr`, `mftb`, FPSCR moves (`mffs`, `mtfsf`), `dcbi`, `sc`.

A `blr` is a return: a `mtlr` of something other than the saved LR followed by `blr` (5 sites in Skies of Arcadia) is a computed
jump the C would not make. That fails the judge rather than passing it, when a vector reaches it.

r4 after a callee that sets it on only some paths stays uncompared at a pointer call, and a direct callee's r4 is compared only if
that callee reads it: a wrong r4 there passes.

Functions that walk long chains of game objects have few or no valid random vectors (`MAX_REPAIRS`): they end as FAIL with 0 vectors.

## Runtime and check (runtime/, hooks.py)

`hooks.py` installs the PowerPC check (`ppc_check`, comparison in `ppc_compare`) and agent sandbox (`ppc_agent`)
through `core/target_hooks`. The runner (`runtime/runner.c`, `tc_rt.c`, `tc_ppc.c`) loads the DOL's sections into
a big-endian guest MEM1 and reports in the oracle's `expected.txt` format. The check is core's (lint R2-R6 with the
float rule worded for `f64`, a /W4 /WX /fp:strict build, every dev and hidden vector bit for bit; `core/check._finish`).

`translator/tools/dp_judge.py` labels a PASS `thin` when its vectors reached under 70% of the original's blocks, in the results file and the run's summary line. The verdict stays PASS: the label says the proof is weak there (`docs/proofs/2026-11-02/gc-tx-review/RESULT.md`).

## Readable stage (runtime_rd/, ppc_readable*.py, readable_task.md)

MEM1 mapped at its own address with the guest's bytes; readable C uses rd_be.h's be_ types for guest memory. The
judge glue (`ppc_readable.check_readable`, hook `readable_check`) places the readable prototype by the EABI
(`ppc_readable_abi`) and compares with `ppc_compare`; the sandbox hooks give context.md the EABI mapping. See
docs/READABLE_SATURN_GAMECUBE.md.

## Byte-match check (ppc_match.py)

Games built with Metrowerks CodeWarrior can be matched exactly: `ppc_match` compiles a function's original-style C
with the game's `mwcceppc` and diffs the object against the original's, split from the DOL by decomp-toolkit, with
`objdiff-cli diff`. Three verdicts, each with the check that gave it (`grade`, `check` in the check result and the
ledger attempt): **matched** (objdiff 100%, `objdiff`), **equivalent** (behaviour PASS only, `behaviour`), **FAIL**. A
full match is PASS even when the behaviour check fails, with a `grade_note` (that disagreement is an oracle defect).

Nothing is vendored. This machine's programs are `config.local.json` keys: `mwcc_root` (dtk-template's compiler
pack, holding `GC/<version>/mwcceppc.exe`), `objdiff_cli`, `dtk`. The game's settings (compiler, flags, split objects,
symbols) are the workspace's `project.json` `"match"` object; the dtk config, splits and symbols live in the
workspace, never here. Original-style C is `<work>.mw.c` beside the behaviour C or `<src_dir>/<addr>.c`;
`match_include/gc_types.h` gives the fixed-width types. Standalone (pilots, flag searches):
`python targets/gamecube_ppc/ppc_match.py --project <ws> <addr> <file.c> [--compiler GC/x] [--cflags "..."]`.
Tests: `tests/test_gamecube_match.py` (synthetic objects, fake compiler and objdiff).

Checked against objdiff-cli 3.8.2: `diff -1 <target.o> -2 <base.o> <symbol> --format json -o <file>`; the report's
`left.symbols[]` entries carry `name` and `match_percent`. Diff split units, not dtk's whole-section `auto_*` objects (a
4 MB object's report is over 1 GB). A float literal matches only when both objects define it: give the function's
split unit its `.sdata2` words (`.sdata2 start:.. end:..` in splits.txt), or objdiff reports `lbl_XXXXXXXX` against the
compiler's `@N` as an argument mismatch. With `-nodefaults` mwcc does not search the source's folder for
`#include "..."`, so the check adds it before the project's include folders.

## Emulation audit (Ghidra 12.1.2, GameCubeLoader Gekko SLEIGH)

Read from `ppc_instructions_gekko_broadway.sinc` and `ppc_gekko_broadway.slaspec` for the instructions the script
handlers use. Each entry: what the p-code does, and what the target does about it.

| Instruction | SLEIGH p-code | Hardware (750CL) | Target |
|---|---|---|---|
| fctiwz / fctiw | `trunc()`, sign-extended into the whole FPR | saturates (>= 2^31 -> 0x7fffffff, < -2^31 or NaN -> 0x80000000); upper word 0xfff80000 (0xfff80001 for a negative operand that converts to 0) | **wrong**: `TcFixups` rewrites fD after the step; C uses `ppc_fctiwz` / `ppc_fctiw` |
| stfs / stfsu / stfsx / stfsux | `float2float` (rounds the double to single) | the store conversion takes the bits without rounding (Dolphin ConvertToSingle) | **wrong** for doubles no single holds: `TcFixups` rewrites the 4 stored bytes; C uses `wrf32d` (exact for every value) |
| cntlzw / cntlzw. | `countLeadingZeros(S:4)`, a CALLOTHER | count of leading zeros, 32 for 0 | **not emulated** (the run stopped with "Unimplemented CALLOTHER"): `TcFixups.install` registers the callback; C uses `ppc_cntlzw` |
| any other CALLOTHER | user-defined op, no callback | - | the function is refused (`TcFixups.refuse`: "CALLOTHER name") |
| frsp | `float2float` twice | round to single | correct, except below the smallest single subnormal: Ghidra's `FloatFormat.getEncoding` gives 0 for every value under 2^-149, IEEE (and the 750CL) gives 0x00000001 above 2^-150. **Wrong** there: `TcFixups` ("single") writes the IEEE result to fD (and ps1) after the step, for this and every single-result instruction below (tests/test_gc_single.py); C's `(f32)` cast is IEEE |
| fadds / fsubs / fmuls / fdivs | double op, then `float2float` | one rounding to single | equal: the double result of two singles rounded again to single is the correctly rounded single (double has more than 2p+2 bits). The C writes `(f64)(f32)(a op b)`, the same two steps; the tiny results the `float2float` flushes are corrected as for frsp (also fsqrts) |
| fmadds / fmsubs / fnmadds / fnmsubs | double product, double sum, then single; not fused, no 25-bit rounding of fC | fused multiply-add; Gekko rounds fC's mantissa to 25 bits (Dolphin Force25Bit) | equal for single-valued inputs (the product of two singles is exact in double); differs for full doubles. Documented, no fixup; `ppc_fmadds` and family mirror the p-code. None of the 32 handlers use them |
| fmuls with a double fC | double multiply, then single | Force25Bit on fC | differs only for a full-double fC; documented |
| lfs | `float2float` of the loaded single | exact widening | correct, except a signalling NaN's payload may be quieted; masked: every comparison treats two NaNs as equal |
| lfd / stfd / fmr / fneg / fabs / fnabs | moves, `f-`, `abs()`, sign bit set | bit operations | correct; `ppc_fabs` / `ppc_fnabs` work on the bits |
| fsel | `fA f>= 0` -> fC, else fB (NaN -> fB) | the same | correct; `ppc_fsel` |
| fcmpu / fcmpo | ordered/unordered compare into the CR field | the same (fcmpo also sets VXVC, unobserved) | correct |
| rlwinm / rlwinm. / rlwimi | rotate via `S<<n | S>>(32-n)` (0 at n = 0 in p-code), wrap-around masks, cr0flags | the same | correct; `ppc_rlwinm` / `ppc_rlwimi` |
| divw / divwu | plain `s/` and `/` | undefined result for /0 and 0x80000000 / -1 | a vector dividing by 0 stops the emulator (invalid vector); the helpers guard both cases |
| psq_l / psq_st (and u/x forms) | `load_ps` / `store_ps` macros: type from the GQR at run time, an `ldexpf` CALLOTHER when the scale is not 0, the unsigned integer types converted as signed, no store saturation | quantized load/store | **wrong** for quantized types: refused. Allowed when the GQR named has type and scale 0 (float, no scale; GQR0 = 0 in every handler): then the p-code is a plain single load/store, the hardware's but for rounding ps0 on the store, a frame word no check compares and the epilogue's lfd overwrites |
| ps_* arithmetic | per-slot ops | - | refused (none in the handlers) |
| fres / frsqrte | exact division / sqrt | table lookup with linear interpolation (relative error under 1/256 for fres, 1/32 for frsqrte) | **wrong**: `TcFixups` ("estimate") writes `TcEstimate`'s result to fD (and ps1) after the step; C uses `ppc_fres` / `ppc_frsqrte` (same tables and algorithm; the tables are the hardware-measured ones Dolphin publishes, not checked against a console here). `tests/test_gc_estimate.py` proves Java, C and a Python version agree and the error bounds hold |
| mtfsf / mtfsfi / mtfsb0/1 / mffs | FPSCR moves | the rounding mode changes | refused: the p-code float ops always round to nearest |
| dcbf, dcbst, dcbt, dcbtst, icbi, sync, isync, eieio | CALLOTHERs with no callback | no register or memory effect a single-threaded program sees | **modelled**: `TcFixups.install` registers empty callbacks; the C drops them |
| dcbz | `dataCacheBlockClearToZero(ea)` CALLOTHER | zeroes the 32-byte block holding ea | **modelled**: the callback zeroes that block in RAM; the C already zeroed it |
| dcbi, dcbz_l, mtmsr, mfmsr, sc, rfi, tw, twi, mftb | cache/system | dcbi discards a dirty line (no cache is modelled), dcbz_l writes the locked cache, mfmsr/mtmsr touch machine state, mftb reads a clock | refused: none can be modelled faithfully |
| lmw / stmw | load/store multiple (lmwInstructions.sinc / stmwInstructions.sinc) | the same | correct |
| crxor 6,6,6 / creqv 6,6,6 | CR bit operation | tells a variadic callee whether floats are passed | correct; skipped in C |

What the audit does not cover: it was read for the instructions of the first 32 handlers, and the target now runs whole games,
so any instruction not in the table has not been read against the SLEIGH (string loads and stores, `lswi`/`stswi`,
`eciwx`/`ecowx`, the remaining paired-single forms, among others). FPSCR exception bits (the commented-out flag CALLOTHERs in
the SLEIGH) are not modelled: no check observes FPSCR. See also Known limits.

## Test truth (tools/gc_truth.py)

Five fully matched public decomps (pikmin, melee, ac-decomp, pikmin2, tp, cloned locally)
are the answer key for this target. `tools/gc_truth.py` reads only their `config/` (dtk symbols, splits, hashes)
and scores a workspace: catalog boundaries (exact / boundary_differs / split / merged / missed), translator pass
rate per judged function, readable naming agreement as numbers. Clean room: nothing of theirs enters DP or reaches
an agent; reports hold addresses and numbers and are refused inside the repository or a workspace. `check-disc`
tells which version a dumped .iso's main.dol is. REL modules are scored too, per module, through the workspace's
`gcrel` link record (`link.json`). Code: `tools/gc_truth.py` (commands), `gc_truth_decomp.py` (the decomp's
config), `gc_truth_dp.py` (DP's side and the scores). Tests: `python -W error tests/test_gc_truth.py`. Games, discs
and REL scoring: [TRUTH_GAMES.md](TRUTH_GAMES.md).
