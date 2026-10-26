# Xbox 360 (Xenon PowerPC) target: design

Baseline title: **Too Human** (Silicon Knights, 2008). Status on branch `xbox360-ppc`:

- **Built, offline:** the disc and XEX reader, the catalog inputs, the C runtime, the judge glue (check, compare,
  agent text) and the tests.
- **Written, never run in Ghidra:** the Ghidra side (setup, fixups, flow, oracle, prepare, catalog scripts). They
  compile, and their pure parts are tested (§5, "The scripts"). The first run is the proof (PROOF_PLAN.md).

The model is `targets/gamecube_ppc/`, which shares the instruction set's 32-bit core. This file covers what differs
from it and why.

All code here is DecompPipeline's own. XenonRecomp and its adapter were read for orientation only; nothing was copied
from them (REFERENCES.md).

## 1. XEX loading

A retail game is an XDVDFS disc image holding `default.xex`, an XEX2 container around a PE image.
`tools/catalog_inputs.py` takes either the disc or the XEX.

| Step | Tool | What happens |
|---|---|---|
| disc | `xiso.py` | Finds the game partition by probing 0, 0x18300000, 0xFD90000 (XGD2: Too Human) and 0x2080000 for the volume descriptor at sector 32. Walks the binary-tree directories and copies `default.xex` out. |
| headers | `xex.py` | Reads the XEX2 header, the optional-header table (file format, entry point, image base, import libraries, TLS, execution ID, original PE name) and the security info (image size, load address, page descriptors, encrypted file key). |
| decrypt | `aes.py` | Decrypts the file key with the console's retail key (then the devkit key, all zero). The retail key is **not in the repository**: it comes from the git-ignored `config.local.json` (`"xex_retail_key"`) or the environment variable `DP_XEX_RETAIL_KEY`, so a public push never carries it. Decrypts the payload with AES-128-CBC (IV 0) under the file key. Uses Windows bcrypt when present, else pure Python; both are tested against FIPS-197. |
| expand | `xex.py` | **Basic** compression is runs of (data, zero-fill) blocks. **Normal** (LZX) and **delta** patches are refused with a clear error (open question 6). |
| check | `xex.py` | The result must start with `MZ`, otherwise the key is wrong. It must also be a big-endian PowerPC PE (machine 0x1F2). |
| facts | `xex.py` | Reads the PE sections (clipped to the image; `.reloc` lies past it and is not loaded), the `.pdata` function table (begin; length in words in bits 8..29 of the packed word) and the import records. |
| resolve | `xex.py` | Plays the loader: each **function** import's slot gets its thunk's address, and each **variable** import's slot gets a 0x100-byte block in the kernel region (0x7FFF8000 + 0x100·k). |
| out | `catalog_inputs.py` | Writes `image.bin` (the loaded image, imports resolved), `image.json` (facts, `image_sha1`, `loaded_sha1`), `catalog_inputs.json` and `code_ranges.txt`. |

Too Human facts (verified; repeated by the tests when the disc is present):

| Fact | Value |
|---|---|
| Title ID | 0x4D5307DE |
| PE name | `TH1-Release_LTCG.exe` |
| Image | 0x82000000, size 0x1460000, entry 0x82A46B18 |
| Compression | **basic**, not LZX (the earlier xenon-dp notes said LZX; that was wrong for this title) |
| Sections | 18. Code: `.text` 0x82330000..0x8312F400 plus eight `.embsec_*` sections |
| `.pdata` entries | 72,768 |
| Imports | 253: 160 xboxkrnl functions, 82 XAM functions, 11 xboxkrnl variables |
| Thunks | from 0x8312E38C, four words each: two import-record words the console's loader rewrites into the slot load, then `mtctr r11; bctr` (the judge never runs them: calls are mocked) |
| Image SHA-1 | `dac2c9fe…5648` |
| Loaded image SHA-1 | `b6b88660…20f6` |

Reading the disc to the finished catalog takes about 7.5 s with no third-party code.

## 2. The catalog (`tools/ppcscan.py`)

**Entries**, in order of trust:

1. `.pdata`: exact bounds from the compiler.
2. `bl` targets that no `.pdata` function covers.
3. Data words that point at uncovered code (vtables, callbacks).

An added entry ends at the next known bound, with trailing zero padding dropped.

**Frame helpers.** `__savegprlr_N`, `__restgprlr_N`, `__savefpr_N`, `__restfpr_N` and the VMX save/restore pairs are
found **by their encodings**, not by name (an `std r14,-0x98(r1)` run, an `lfd f14,-0x90(r12)` run, and so on). They
have `.pdata` entries but are not functions of their own: they run inline in their callers' frames. So they are
cataloged as kind `frame_helper` and refused, and a call to one counts as a frame helper, not a callee.

**Instruction classes** decide refusal before Ghidra is ever opened. Order: VMX, then FPSCR, estimate, atomic, cache,
system. Ghidra cannot be trusted to decode VMX128 at all (§5).

| Class | Instructions | Phase 1 |
|---|---|---|
| vmx | opcode 4/5/6, the opcode-31 vector loads and stores, VRSAVE | refused |
| fp_mode | mffs, mtfsf, mtfsfi, mtfsb0/1 | refused |
| estimate | fres, frsqrte | refused (Ghidra computes them exactly; hardware does not) |
| atomic | lwarx, stwcx., ldarx, stdcx. | refused |
| cache | dcbf, dcbst, dcbi, dcbz, dcbz128, icbi | refused (dcbt/dcbtst are hints and allowed) |
| sys | sc, mftb, an SPR other than XER/LR/CTR, the MSR | refused |
| trap | tw, twi, td, tdi | allowed: a trap that fires makes that vector invalid |
| barrier | sync, lwsync, eieio, isync | allowed: no-ops for one thread |
| fma, fconv, fpu, ld64, r13 | | allowed and counted (the proof picks use them) |

**Too Human:**

| Measure | Count |
|---|---|
| Functions | 88,193 (72,768 `.pdata`, 2,520 from `bl`, 12,905 from pointers) |
| Frame-helper entry points | 236 |
| Mid-function entries | 34 |
| Phase 1 | **86,559 functions, 3.33 M instructions**; 1,073 of them use a multiply-add |
| Refused | VMX 1,403 · atomic 110 · system 84 · cache 27 · FPSCR 4 · frame helpers 6 |

Bounds check: every `.pdata` function ends in `blr`, `b` or `bctr`. So do 2,492 of 2,520 `bl` entries and 12,902 of
12,905 pointer entries.

**Shape of the game's code.** It was built with link-time code generation. Only about 2% of the `.pdata` functions
call nothing, and the game has **no true tail calls**: every branch out of a body goes to a register-restore helper
or an import thunk.

## 3. Registers and calling convention

**General registers are 64-bit.** Titles run in 64-bit mode and use 32-bit effective addresses: the upper word of an
address is ignored (open question 8).

- The runtime passes every integer as `u64`.
- A function may declare `u32`/`s32` parameters when the original only reads the low word.

**The result r3 is compared in all 64 bits.** This is stricter than the GameCube, and on purpose: a caller may use
the upper word. The declared return type says how the original fills the upper word:

| Declared type | Upper word |
|---|---|
| `u32` | zero-extended |
| `s32` | sign-extended |
| `u64` / `s64` | computed in full |

When only the upper word differs, the compare says so, and the blocker kind `upper_word` points the fix at the
signature, not the body.

| Item | Registers |
|---|---|
| Integer arguments | r3..r10 |
| Float arguments | f1..f13 (always doubles in the register) |
| Vector arguments | v2..v13 (phase 2) |
| Results | r3, f1 (v2 in phase 2) |
| Stack arguments | doublewords at r1+0x50+8N at the call (open question 4) |
| Callee-saved | r14..r31, f14..f31 |
| Fixed, never inputs | r1 (stack pointer), r2 (unused by titles), r13 = 0x7FFF0000 (the processor control region; open question 5) |
| Return address | the sentinel 0x700FF000 |

**Calls are mocked.** Every call is recorded with its target, r3..r10 (64-bit), f1..f13 and the eight stack
doublewords (s0..s7), and it returns the vector's mocked r3 and f1. This covers direct calls, calls through pointers
and calls to imports through their thunks. The agent writes `tc_call` / `tc_callf`; the call order and the targets
must match the original's.

## 4. Memory map (judge side)

| Region | Range | Compared | Use |
|---|---|---|---|
| image | 0x82000000 .. +size | yes | `image.bin`, imports resolved; reads of code bytes fail (R8) |
| heap | 0x40000000 .. 0x40100000 | yes | scratch 0x40000000..0x40001000 for pointer inputs |
| stack | 0x70000000 .. 0x70100000 | **no** | SP 0x700FE000 at entry, return sentinel 0x700FF000 |
| kernel | 0x7FFF0000 .. 0x80000000 | yes | r13's PCR at 0x7FFF0000; imported kernel variables from 0x7FFF8000 |

Anything else is a fault: the vector stops, and the fault is part of the result.

- **Stack writes are not compared.** Frames and spills are the compiler's business. Stack **arguments** of calls
  are still compared (s0..s7), because a callee sees them.
- **Reset between vectors.** The runtime keeps a list of written and poked bytes and restores only those. The full
  20 MB image is copied once, not once per vector.

The numbers live in three places: `target.json`, `runtime/tc_rt_internal.h` and `x360_compare.py`. A test keeps the
three equal.

## 5. Ghidra and VMX128

**Language:** `PowerPC:BE:64:64-32addr` (`ppc_64_be`: the common PowerPC core, AltiVec and the G2 extras). It has
64-bit registers and 32-bit addresses, which is the Xbox 360 model. The alternative, `PowerPC:BE:64:A2ALT-32addr`
(ISA 3.0), adds nothing for Xenon (open question 10). Import: `BinaryLoader` with the base 0x82000000 and
`image.bin`, so Ghidra never parses the XEX. `TcSetup` maps the heap, stack and kernel blocks and creates
exactly the functions of `ghidra_setup.txt` (`tools/ghidra_setup.py`, from `catalog_inputs.json`). Auto-analysis is
off except the constant-reference analyzers, which give the lis/addi data references the catalog counts.

**VMX128 is not decoded by Ghidra 12.1.2.**

- Neither language defines primary opcodes 5 or 6.
- The VMX128 forms that share opcode 4 with AltiVec can **mis-decode** as AltiVec instructions with the wrong
  registers. They do not fail cleanly.

So phase 1 trusts DP's own scanner: any function containing a VMX word (opcode 4/5/6, the vector loads and stores,
VRSAVE) is refused before Ghidra sees it. That is 1,403 of Too Human's functions (1.6%). The proof plan adds a
decode audit: Ghidra's mnemonics for the 50 picks against the scanner's classes.

**Phase 2 (designed, not built):**

- **Sleigh.** A Sleigh include `vmx128.sinc` for opcodes 4 (VMX128 forms), 5 and 6, built into a language variant
  of `ppc_64_be`. It adds the 128-entry vector file (vr0..vr127, the split VD128/VA128/VB128 register fields) and the
  operations as `CALLOTHER` p-code ops. This is decode only: no semantics are written in Sleigh.
- **Oracle callbacks.** Java callbacks in the oracle's emulator implement each op from the Xenon manuals.
  They follow non-Java mode (denormals flush to zero) and the VMX128 specials: `vpermwi128`, `vrlimi128`,
  `vpkd3d128`, `vupkd3d128`, `vmsum3fp128`, `vmsum4fp128`, the dot products and the estimates. The estimates need
  the hardware's tables, so they stay refused until those are known.
- **Runtime.** Runtime `ppc_v*` helpers mirror the callbacks bit for bit, with a test of each against the other.
- **Fallback.** Any op without a proven callback keeps its function refused, under the blocker
  `oracle_emulation_error`. Nothing is guessed.

**p-code audit** of the instructions phase 1 allows. Ghidra's p-code against the IBM architecture books:

| Instruction | Ghidra p-code | Architecture | What DP does |
|---|---|---|---|
| fmadd family | two roundings (multiply, then add) | **one rounding** (fused) | `"fma"` switch, both sides (open question 1) |
| fmadds family | product, sum, then `float2float` | fused, rounded once to single | same switch |
| fctiwz / fctiw | truncate to 4 bytes, sign-extend | saturates; NaN gives 0x80000000; upper word undefined | `TcFixups` replaces with the saturating form; runtime `ppc_fctiw*` saturate (open question 2) |
| stfs of a double | `float2float` (rounds) | out of single range: bits are **selected**, not rounded | `TcFixups`; runtime `wrf32d` / `ppc_single_bits` |
| fres / frsqrte | exact division / square root | table estimates | refused |
| divw / divwu | `sext` / `zext` of the 32-bit quotient | upper word undefined | judged as Ghidra models it; flagged in open question 2 |
| mulhw / mulhwu | `zext` of the high word | upper word undefined | same |
| mullw | sext × sext, 64-bit | the same | runtime matches |
| rlwinm and other 32-bit rotates | low word doubled, then rotated; the mask may wrap into the upper word | the same (ROTL32 of low‖low) | runtime `ppc_rlw*` match, tested |
| slw / srw / sraw | shift amount `B & 0x3f` | the same (32..63 give 0 / all sign) | runtime matches, tested |
| carries (addc, adde, subfc, subfe, sraw) | 64-bit carry | CA from the 64-bit result in 64-bit mode | runtime 64-bit carry (open question 7) |
| tw / twi / td / tdi | trap pcode op | trap | firing makes the vector invalid |
| sync / lwsync / eieio / isync | pcode op | ordering only | no-ops |

**The scripts (written, not run).** The decisions they make, beyond the GameCube model:

1. **Boundaries come from the offline catalog only.** Ghidra creates no function of its own, so packets, oracle
   runs and catalog rows all see the `.pdata` bodies.
2. **Refusals by instruction word.** `TcClass` (the Java twin of `ppcscan.classify`, tested equal) decides VMX,
   FPSCR, estimate, atomic, cache and system instructions, never Ghidra's mnemonic, which can be a mis-decoded
   VMX128 word. Any CALLOTHER without an emulator callback is refused too.
3. **Helper families run inline** in the oracle and are never called by the C (`meta.frame_helpers` names them).
4. **Import calls.** The thunk reads nothing (`TcFlow`); the compared registers are the argument registers the
   caller writes between the previous call (or the entry) and the call to the thunk. `meta.callees[]` carries
   `"import"` for them.
5. **Widths after the vectors.** With the vectors done, `TcVectors.probeWidths` flips each 32-bit parameter's upper
   word; a parameter whose results change becomes `u64`. `retKind` checks the result's upper word in every run.
   The signature is rewritten, and `oracle_summary.json` lists the changes in `widened` (open question 3).
6. **fma, both models.** A function with multiply-adds runs every valid vector in the other model too;
   `fma_checked` and `fma_sensitive` count them (open question 1).
7. **Traps** whose condition holds end the run and invalidate the vector (the original would stop there).
8. **Catalog counts.** `TcCatalog` counts with `TcClass`. Atomics and traps are game code, not hardware access, so
   they are counted apart (`atomic`, `traps`) and do not exclude a function from the total; phase 1 still refuses
   atomics.

## 6. Runtime (`runtime/`, C)

`tc_rt.h` is the only interface a decompiled function may use. It provides:

- **Memory:** big-endian reads and writes of 8 to 64 bits, singles and doubles, the byte-reversed loads and stores,
  and `wrf32d` (stfs of any double).
- **Calls:** `tc_call` / `tc_callf` with `TcArgs { r[8], f[13], s[8] }`.
- **Integer helpers:** `ppc_rlwinm`, `rlwnm`, `rlwimi`, the `rld*` rotates, shifts with carry, `cntlz*`, multiply
  high, divide, carry arithmetic.
- **Float helpers:** `ppc_fctiw*`, `fctid*`, `fcfid`, `single_bits`, `frsp`, the eight multiply-adds, `fsqrt*`,
  `fsel`, `fabs`, `fnabs`, `fneg`.

Every helper is written from the architecture. The tests check about 17,900 cases per fma mode against an
independent Python model built on exact fractions.

**Imports.** A call to an import thunk is an ordinary mocked call: the agent never implements the kernel, and its
result comes from the vector. Imported kernel **variables** are 0x100-byte blocks in the kernel region that reads may
use (they start as zero, or as the vector's patches). So no import is emulated. A function's behaviour around a
kernel call is judged by what it passes and what it does with the mocked result.

**The runner** (`runner.c`) loads `image.bin` and `code_ranges.txt` and runs one vector set. Its report has five
record types:

| Record | Contents |
|---|---|
| `RET` | r3, f1 |
| `W` | writes in compared regions |
| `CALL` | 30 fields per call |
| `FAULT` | a read or write outside the memory map |
| `CODEREAD` | R8: a read of code bytes |

It builds with `cl /O1 /fp:strict /W4 /WX /TC` and `/DTC_FMA_FUSED=0|1` from `target.json`.

## 7. Judge facts

The verdict code is core/check's, unchanged: **PASS only when every vector of the requested sets matches byte for
byte.** What one vector compares:

1. r3 in all 64 bits, with a hint when only the upper word differs. f1 when the return type is float.
2. Every byte written outside the stack region: address, value, and the bytes before and after (via the image or
   zero).
3. The call sequence: count, order, target, r3..r10, f1..f13, s0..s7, each mismatch named by register.
4. Faults and R8 code reads must happen on the same vectors with the same address.

Nothing is loosened from the GameCube judge. Stack writes were never compared there either. The 64-bit r3 compare
is **stricter** than the GameCube's.

**fma.** `target.json` `"fma"` selects `two_roundings` (default) or `fused`.

- The build passes `/DTC_FMA_FUSED`. In fused mode the doubles use C `fma()`. The singles use a round-to-odd fused
  multiply-add, then one rounding to single, so the single result is rounded once, as the architecture says.
- The oracle has to use the **same** model: `TcFixups` will rewrite the fma p-code when the switch says `fused`.
  Each harness will record the mode it was made in; a mismatch will make it stale, never a pass.
- **Dual-model flag.** The oracle will run both models on each fma function's vectors. A function whose results
  differ between them is flagged `fma_sensitive`, so the open question is visible per function and not hidden.

## 8. Open questions (each needs a decision or a measurement)

1. **fma rounding.** The IBM books define fmadd and the rest with a single rounding (a fused multiply-add). Ghidra models two roundings. DP defaults to `two_roundings` because the brief marks the question
   unproven. **Recommendation:** decide `fused`, as was done for the SH-4 fmac on 2026-10-21. The runtime already
   supports it. In the tests, 84 of 2,480 fma cases give different bits under the two models, so the choice matters.
2. **Hardware-undefined upper words.** After mulhw/mulhwu, divw/divwu and fctiw[z] (in a register), the
   architecture leaves the upper word undefined. Ghidra picks one value. If the original code later uses that upper
   word, the oracle's value is not the hardware's. **Proposal:** treat a function whose r3 comes straight from one
   of these as returning `u32`/`s32` (the compare's hint already points there), and record which upper-word rule
   the oracle used in the harness.
3. **Input upper words and the width probe.** Random 64-bit inputs exercise upper words, but most parameters are
   32-bit. **Proposal:** the oracle generates zero-extended, sign-extended and random upper words for each integer
   input. A width probe marks a parameter 32-bit when no result depends on its upper word. The signature then says
   u32/s32, and the agent need not handle the bits the original ignores.
4. **Stack arguments at r1+0x50.** The parameter save area follows the 64-bit PowerPC ELF layout: 8 doublewords at
   0x30 for r3..r10, so the 9th argument is at 0x50. This has to be confirmed on a real call with 9 or more
   arguments in Too Human before a function that uses stack arguments is judged.
5. **r13, PCR and TLS.** r13 points at the processor control region. TLS lives through a pointer in it, and
   `.tls` / `tls_info` give the template. Phase 1 gives r13 a fixed value and a zeroed 64 KB region. The proof's r13
   picks will show which PCR offsets the game reads, and whether the TLS block needs a seeded pointer.
6. **LZX titles.** Too Human uses basic compression. Other titles use LZX ("normal" compression), which is refused
   for now. Adding it means a DecompPipeline-own LZX decoder, written from the published format and tested, not
   copied.
7. **CA in 64-bit mode.** Carry comes from the 64-bit result, which matches both Ghidra and the architecture in
   64-bit mode. A title running 32-bit code in 64-bit mode would see a different CA for `addc` on 32-bit values. Too
   Human's compiler output does not rely on it (it re-extends), but the first carry functions in the proof should
   confirm this.
8. **Address truncation.** The runtime takes `u32` addresses. If a computed address had a non-zero upper word, the
   hardware (in 64-bit mode with 32-bit translation) would ignore it, and C truncates it the same way. The judge
   does not test addresses at or above 2³². This is acceptable while no title builds 64-bit pointers.
9. **fres / frsqrte.** Refused until the estimate tables are known, either from hardware or from a published
   description.
10. **Language choice.** `64-32addr` is chosen. `A2ALT-32addr` would only matter if Ghidra later adds VMX128 there.
    The phase-2 Sleigh include is planned against `ppc_64_be`.

## 9. Files

| File | Purpose |
|---|---|
| `target.json` | ABI, memory map, fma switch, phase, blocker kinds |
| `hooks.py` | `run_check`, `build_sandbox`, `uninit_stack` for core |
| `x360_check.py` | lint, the thunk generator, build, the official check |
| `x360_compare.py` | vector, expected and result parsing; the byte-exact compare |
| `x360_agent.py` | the agent's task text and sandbox |
| `runtime/` | `tc_rt.h`, `tc_rt.c`, `tc_ppc.c`, `runner.c`, `tc_rt_internal.h` |
| `tools/` | `xiso.py`, `xex.py`, `pe.py`, `aes.py`, `ppcscan.py`, `catalog_inputs.py`, `proof_pick.py`, `ghidra_setup.py`, `decode_audit.py` |
| `proof_set.json` | the 50 functions of the first Ghidra proof (PROOF_PLAN.md) |
| `ghidra_scripts/`, `template/` | the Ghidra scripts and the project's starting `TcInputs.java` (ghidra_scripts/README.md) |
