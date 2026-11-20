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
| Functions translated to C (judge form: `tools/compile_all.py`, 2026-11-13, x360-tx-big) | 87,595 of 88,187 |
| **Functions that build** (the Xbox 360 judge's lint, then MSVC `/O1 /fp:strict /W4 /WX`) | **87,594 of 88,187 (99.33%)** |
| Port form (what the game's build translates: `port/tools/gen_xbox.py`, the live build's `gen/` and `obj/`) | 87,759 of 88,187 translated and compiled; the other 428 are XDK Direct3D library code (`port/games/too_human/boundary.json`), replaced by the kit, never translated |
| Of the 86,565 functions DP's phase 1 can judge (not refused) (earlier run) | 86,537 build (99.97%) |
| Vector (VMX128 + AltiVec) instructions lifted (earlier run) | 83,116 of 83,210 (99.89%): 144 of the 146 vector ops in the game |
| Functions with vector code that build (earlier run) | 1,345 of 1,353 |
| Time for the whole game (build box B, 3 workers) | 37 min to translate, about 27 min to compile. The slowest function, 8237bd08 (22,655 instructions), takes 37 s alone (96 s beside two other workers); it took over 15 minutes before |

Why the other 593 functions do not build in the judge form:

| Count | Cause |
|---|---|
| 592 | refused: they read registers before setting them that the judge's harness does not hand over (452 r12; 66 and 3 the CR fields; 21 v1, 5 v1 and v2, 4 v69 and v72; 6 r30, 2 r31; 2 others), so the judge would run them with zeros there. The port form hands the caller's registers over and translates them all |
| 1 | 82b01e98 (2,359 instructions): MSVC C4702 (unreachable code) under `/WX` |

The 60 KB cap that used to stop 38 large functions is core/lint's limit for agent-readable files. The Xbox 360 judge
raises it to 2 MB (`x360_lint.py`), and the port build does not lint at all, so `compile_all.py` now lints with the
judge's rule. Of the 37 functions over 60 KB, 36 build (82b01e98 is the other).

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
  lwarx / stwcx. (judge form: a plain load and an always-successful store, as one thread runs; port form: a compare and
  swap, and sync / lwsync / eieio are real fences there), the time base, the MSR, mfocrf, and
  the vector estimates.

- **Exit-state form** (`Facts.exit_state`, `exit_form.py`; judge form only, refused with the port form). The same C,
  plus entry reads (`tcx_in_r` / `tcx_in_f` / `tcx_in_cr`) of the registers whose entry value may be nonzero, and
  before each return a `tcx_*` record of every register written; stack stores are kept (no stack_dce). `dp_judge.py`
  builds it beside the normal form when the harness has exit lines. With the flag off the C is byte for byte the same.

## Layout

- `xenontranslate/decode.py`: the decoder (all forms, including 64-bit, AltiVec and VMX128). `image.py` reads
  `image.bin`, and `workspace.py` reads a DP Xbox 360 workspace (read only).
- `xenontranslate/translate.py`: control flow, frame helpers, jump tables, and lifting each scalar instruction.
- `xenontranslate/vmx.py`: the vector instructions and their C helpers.
- `xenontranslate/passes.py`: propagation, store-to-load forwarding, dead code, and the tc_call / tc_callf choice.
- `xenontranslate/exit_form.py`: the exit-state form (judge only).
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
    case where rounding through double gives a different answer;
  - the exit-state form and its planted wrong answers (DP's `tests/test_x360_judge_exit.py`, `_oracle.py`).

Every tool takes the workspace as `--ws` (`--workspace`). DP is the checkout this folder is part of; the tools build
with its `targets/xbox360_ppc/runtime` and its `config.local.json` `vcvars`, and save only the compiler's environment
variables (`out/msvc_env.json`).
