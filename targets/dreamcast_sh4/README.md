# Target: dreamcast_sh4

Dreamcast main binaries (1ST_READ.BIN-style, loaded at 0x8c010000) on the SH-4. Ghidra language
`SuperH4:LE:32:tcfix`: install it once with `python ghidra_lang/install_lang.py <ghidra_home>`.

Getting the binary off a disc image (Redump `.cue` or `.gdi`; read-only on the image):

```
python tools/gdrom.py info    <image>             # IP.BIN: title, product, boot file, peripherals
python tools/gdrom.py list    <image> out.tsv     # every file: size, LBA, path
python tools/gdrom.py extract <image> <new_dir>   # the boot file named in IP.BIN (or NAME ...)
```

It reproduces Toy Commander's `1GUTH.BIN` byte for byte from both its `.gdi` and its `.cue`. That includes the
225-sector pregap Redump keeps in every high-density track after the first.

## Ghidra fixes

Stock Ghidra 12.1.2 emulates some SH-4 instructions wrongly. Every one of these would have produced a wrong expected result.
The patched language adds files beside the stock ones and leaves those untouched:

1. `fschg` did not flip FPSCR.SZ, so paired moves were emulated as single moves.
2. `frchg` did not swap the FR and XF banks, so matrix loads never reached XMTRX.
3. `fpchg` did not flip FPSCR.PR.
4. `bsr` never set PR, so every `bsr` callee returned to the caller's caller.
5. `fipr`/`ftrv` read FV vectors in pair-swapped order (fr1, fr0, fr3, fr2) in little-endian mode.

`pref` (a cache hint) and `movca.l` (emulated as a plain store) no longer exclude a function.

## RAM mirror (0x0c...)

With the MMU off, main RAM is the same bytes at 0x8c... (P1) and 0x0c... (P0) (and 0xac..., 0xcc..., and through the
ignored top address bits). Some games (Spawn) are linked against the 0x0c... form while their binary loads at
0x8c010000. Everything folds a 0x0c... address to its 0x8c... form (`TcBase.fold`, `tcrt_fold`). Only P0 folds: no code
uses the other segments, and folding them made random input pointers (0x2c..., 0x4c...) valid far more often:
- `TcSeed` (opt-in `seed_functions`) moves references auto-analysis recorded against unmapped 0x0c... addresses to
  0x8c..., and adds call references for constant calls through such an address;
- `TcCatalog`, `TcGraph`, `TcPrepare` fold pool values (globals, callees, the packet's annotations);
- `TcOracle` wraps the emulator's RAM bank so every access folds, folds the PC after a jump, and folds the write set;
- `tc_rt.c` folds every access, call target and poke; `runtime_rd` maps one RAM section at both 0x8c000000 and
  0x0c000000 (page guards set on both views) and logs call targets in their 0x8c... form;
- `target.json` `address_mirrors` (`{"0c": "8c"}`) tells the tooling the same: `tools/retire.py` and `tools/doctor.py`
  treat a readable names key in the 0x0c... form as that function's name (reported: key it by the 0x8c... form), not
  as an orphan.
For a game without mirror addresses (Toy Commander) only one thing changes: a random input pointer that happens to
fall in 0x0c... now reads RAM, as on hardware, instead of faulting and being redrawn. In a 40-function regression
sample, 3 harnesses changed that way; every matched function still passed.

## Prototypes (TcBase.prototypeOf)

The game is single-precision, but Ghidra's SH-4 model often types two single floats as one 8-byte pair: a
parameter in `drN`, a result in `dr0` (or `fr1`). Such a prototype is rebuilt from the code instead of skipped:
the result is the f32 in fr0; integer and stack parameters stay; every float register that some path reads
before writing becomes an f32 parameter named after its register (`param_fr4`; integer ones become `param_r4`).
Ghidra's own float parameters are dropped: the SZ=1 path of a plain `fmov.s fr4,@r3` is enough for it to type
fr4 as `dr4`, and splitting that gave a phantom fr5. Still skipped: such a function that reads fr0..fr3 or
fr12..fr15 at entry (the C runner passes only fr4..fr11, so its tests would be degenerate) or that switches FPU
modes (`fschg`, `frchg`, `fpchg`).

Every other prototype also keeps only the float parameters the code reads or passes on: Ghidra's stored signatures
can name registers a function never touches (8c021474 had an integer `param_1` in fr5), and callers were then
compared on a register they never set. Functions that switch FPU modes keep Ghidra's floats (unsafe masks there).

**Entry reads.** The function under test must not read r0..r3, fr0..fr3 or fr12..fr15 before writing them: the C
runner cannot pass them, so the original would run on whatever the emulator left there and the match would be
degenerate. That reason wins over Ghidra's own (the SHC divide helpers take r0..r2; the vector helpers work on fv12).
Prologue saves (`fmov.s fr12,@-r15`, also in a delay slot) are not reads.

**Pass-through arguments.** A function may hand its caller's r4..r7 or fr4..fr11 to a callee without touching
them (`mov #1,r6; jsr` forwarding its entry r4 and r5). The judge compares those registers at the call, but Ghidra
only sees them as inputs if it knows the callee takes them, so the C code had no way to read them. Every prototype
now also gets, as `param_rN`/`param_frN`, each argument register that reaches a call (or tail jump) unwritten
and that the callee's prototype takes. Callee prototypes are looked through `PASS_DEPTH` (3) levels deep, and
TcPrepare compares each callee with the same prototype the caller's analysis used.

The dataflow lives in `TcFlow.java`, a must-define pass over basic blocks with the r0..r7 and fr0..fr15 bits.
Ghidra merges a delay slot's operands into its branch; the branch itself only counts its target register.
It ignores the 8-byte operands of the PR=1/SZ=1 path that the patched SLEIGH lists beside the real ones, treats a
call (after its delay slot) as clobbering r0..r7 and fr0..fr11, and takes `fipr`/`ftrv`/`fsca` operands from the
instruction text.

## Known limits

- Sampled testing: a difference only at an exact boundary (`>` vs `>=` on random floats) can pass.
- Functions touching hardware registers or system instructions (153) are excluded.
- A vector making more than 4096 calls is rejected (`TcOracle.CALL_LIMIT`, equal to `MAX_CALLS` in tc_rt.c): its
  loop waits on a callee result the mocks never give. Logging those calls ran Ghidra out of heap.
- `frchg` bank swaps that leave entry fr0..fr15 in XMTRX (8c0613b8) are not detected as entry reads yet; such
  functions fail instead of being skipped.
- R8 (no reads of machine code): a random input pointer that leads the original to read a code address as data is
  an invalid vector, repaired like a fault (8c01c0e8, a bytecode interpreter).
- Call targets from the inputs (TcCalls, batch 28): a mocked call to a target outside guest RAM that is no constant
  of the body (a function pointer the function reads from its inputs, a register or a call result) is an invalid
  vector, and the word holding it becomes a fresh chunk pointer. On the Dreamcast such an address has no memory; in
  the 32-bit readable runner it can be a host DLL's code, run natively (8c08d48c called 0x76386e41 and crashed).
  NULL stays a valid target: both judges trap a call to it.
- `fmac` is emulated with two roundings; whether SH-4 rounds once is unverified (8c01ac9a differs by 1 ulp).
- Paired 64-bit FPU moves (`fschg` + `fmov` on DRn) in little-endian mode are not yet verified against hardware.
- The oracle assumes FPSCR PR=0, SZ=0 at entry (true for all game code; only the SDK changes FPSCR).
- `fsrra` and `fsca` use exact maths and a 64K-entry table, not the hardware's approximations.
- Ghidra's float encoder turns results with 2^-150 < |x| < 2^-149 into ±0 where IEEE (every C build) gives
  ±2^-149. TcOracle checks each fmul/fdiv/fmac/fipr/ftrv exactly and rejects such runs as test vectors.
  (Real hardware runs with FPSCR.DN=1 and flushes all subnormals; neither the oracle nor C models that.)

## Readable C (runtime_rd)

The readable stage turns matched raw C (`rd32`/`tc_call` on u32 addresses) into C with structs, pointers, named
globals and calls by name, and judges it on the same vectors (`core/readable_check.py`, rules in
`core/readable_lint.py`, registry and headers in `core/readable.py`). The runner is a 32-bit build
(/LARGEADDRESSAWARE) that maps the guest RAM at 0x8c000000, so a guest pointer is a host pointer and structs have
the SH-4 layout:

- Calls: in the verification `game.h` every other function's name is a pointer to its guest address. Guest RAM
  is not executable, so the call faults; a vectored handler logs it (cdecl stack arguments placed in the SH-4
  registers the prototype gives them), and resumes in a trampoline returning the scripted value (in ST0 when the
  prototype returns f32; for an unknown pointer target, when the caller pops ST0 after the call). Calls through
  function pointers work the same way.
- Globals: `#define g_name (*(T *)0x8c...)` from the registry.
- Writes: memory diff against a snapshot taken just before the run (final contents, as `check.py` compares).
- R8: pages holding machine code are inaccessible during the run; data accesses there are single-stepped and a
  read of an instruction byte is reported. Pages below 0x8c010000 and past RAM fault.
- Stack arguments: locals live on the host stack, so an argument the original points into its own frame
  (0x8cff0000..0x8d000000) matches a host-stack pointer, as long as the pairing is one-to-one within the vector.
  The mocked callees never read or write through those pointers, so which slot is passed is only tested through
  what the function itself later reads (true of the raw judge as well).
- Regression of the runtime itself: all matched raw C passes when built against it (`check_raw`), and the
  broken control fails with the same count (11/96).
- The function under test's own address: its name is a host address (it is compiled, not mocked). The thunk
  names it to the runtime (`rd_set_self`), and a stored word, a call argument or the result equal to it is reported
  as its guest address (8c06dc28 stores its own address in a hook table).
- x86 returns f32 through the x87 stack, which quiets signalling NaNs; the judge already treats all NaNs alike.
