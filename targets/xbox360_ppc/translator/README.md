# xenon-translate

Xbox 360 (Xenon PowerPC, with AltiVec and VMX128) machine code to C. It writes one C function per original function
in DecompPipeline's Xbox 360 raw-C form (`targets/xbox360_ppc/runtime/tc_rt.h`), so DP's own
judge can check the C against the original. Agents then only work on the functions it gets wrong.

This is our own code. It started from our GameCube translator, ppc-translate. XenonRecomp and xboxrecomp were used
only as reference and for comparison: see [REFERENCES.md](REFERENCES.md).

## Results on Too Human (offline; no judge yet)

| | |
|---|---|
| Code words that decode | 3,693,903 of 3,695,596 (99.95%); the rest are data words inside code |
| Decoder agreement with a reference disassembler | every one of the 3,695,596 code words |
| Functions translated to C | 88,149 of 88,187 |
| **Functions that build** (DP's lint, then MSVC `/O1 /fp:strict /W4 /WX`) | **88,111 of 88,187 (99.91%)**, holding 94.0% of the instructions |
| Of the 86,565 functions DP's phase 1 can judge (not refused) | 86,537 build (99.97%) |
| Vector (VMX128 + AltiVec) instructions lifted | 83,116 of 83,210 (99.89%): 144 of the 146 vector ops in the game |
| Functions with vector code that build | 1,345 of 1,353 |
| Time for the whole game | 26 min to translate (3 workers), 17 min to compile |

Why the other 76 functions do not build:

| Count | Cause | Instructions |
|---|---|---|
| 38 | over DP's 60 KB file cap even in the short form (one `px_a` / `px_b` per call, chained zeros, no optional whitespace); 2,000 to 8,000 instructions each | 187,393 |
| 37 | use `vupkd3d128` / `vpkd3d128` (the Direct3D pack/unpack formats), which are not modelled yet | 9,924 |
| 1 | translation takes over 5 minutes (8237bd08, 22,655 instructions) | 22,655 |

**Not yet judged.** DP's judge needs harnesses (test vectors and expected results) from the Xbox 360 target's
proof in Ghidra. That proof is queued, and Too Human has no harness yet. `tools/dp_judge.py` is written for when
it does, but it is untested.

## How the output is shaped

- **Registers are 64-bit.** Every GPR is a `u64`, and 32-bit operations cast explicitly. The 32-bit rotates use
  the 64-bit mask, so it can wrap into the upper word. Carry comes from the 64-bit result. These rules come from
  DP's DESIGN.md.
- **Calls go through `TcArgs`.**
  - Each call passes r3..r10, f1..f13 and the 8 stack doublewords at r1+0x50, because DP compares all of them.
  - Within a block, a field that already holds the same value is not written again (`tc_call` takes the argument
    block as read-only).
  - The stack doublewords are read by one `px_sargs` call.
  - After a call, r0, r4–r12, f0, f2–f13 and CTR are cleared, as the judge's mocked callee does.
- **Frame helpers run inline.** These are `__savegprlr` / `__restgprlr`, the FPR helpers and the four VMX
  save/restore families. Jump tables become `switch`es.
- **Vectors are static helpers in work.c.** A vector register is `typedef struct { u32 w[4]; } xv;`, with the
  words big-endian. Each instruction calls an `xv_*` helper written from the AltiVec manual and the VMX128
  definitions. DP's lint already allows a top-level typedef and static helpers, so `tc_rt.h` needs no change for
  this.
- **Vector model** (unproven until DP's vector phase exists; all in `vmx.py`):
  - non-Java mode, so denormals flush to zero;
  - the multiply-adds round once;
  - the dot products `vmsum3fp128` / `vmsum4fp128` round once;
  - the estimates are computed to single precision;
  - `lvewx` leaves the other elements 0;
  - VSCR[SAT] is not recorded.
- **Notes.** What the C cannot model exactly is written as a note beside the function. Examples: traps,
  lwarx / stwcx. (treated as a plain load and an always-successful store), the time base, the MSR, mfocrf, and
  the vector estimates.

## Layout

- `xenontranslate/decode.py`: the decoder (all forms, including 64-bit, AltiVec and VMX128). `image.py` reads
  `image.bin`, and `workspace.py` reads a DP Xbox 360 workspace (read only).
- `xenontranslate/translate.py`: control flow, frame helpers, jump tables, and lifting each scalar instruction.
- `xenontranslate/vmx.py`: the vector instructions and their C helpers.
- `xenontranslate/passes.py`: propagation, store-to-load forwarding, dead code, and the tc_call / tc_callf choice.
- `xenontranslate/returns.py`: works out, across the whole executable, whether each function returns in r3, in
  f1, or in neither.
- `xenontranslate/emit.py`: the C text. `structure.py` and `cfgstruct.py` lay it out as loops and ifs.

## Tools and tests

```bash
python tools/translate_one.py --ws <workspace> 82596940
python tools/compile_all.py --ws <workspace> --out out/all --workers 3
python tools/coverage.py --ws <workspace> --out out/coverage.json
python tools/check_decoder.py --workspace <workspace> --dis <reference dis.exe>
python tools/dp_judge.py --ws <workspace> --out out/judge --all
python tools/regress.py --ws <workspace>
python -m unittest discover tests
```

- `translate_one.py` prints one function's C.
- `compile_all.py` translates the whole game, then lints and compiles each function. It is bounded and resumable
  (`--skip-translate`).
- `coverage.py` reports decode and vector coverage.
- `check_decoder.py` compares the decoder against the reference disassembler.
- `dp_judge.py` runs DP's judge once harnesses exist.
- `regress.py` translates the recorded list again and shows the C is byte for byte the baseline
  (`regress/toohuman_set.json`, Too Human's 500 translator-set functions, every signature generic).
- The tests cover:
  - decoder encodings worked from the manuals, plus one golden word per op;
  - every vector helper, compiled with MSVC and run on hand-worked cases, such as the once-rounded `vmaddfp`
    case where rounding through double gives a different answer.

Every tool takes the workspace as `--ws` (`--workspace`). DP is the checkout this folder is part of; the tools build
with its `targets/xbox360_ppc/runtime` and its `config.local.json` `vcvars`, and save only the compiler's environment
variables (`out/msvc_env.json`).
