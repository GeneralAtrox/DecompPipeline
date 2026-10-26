# References

ppc-translate is our own work. This file lists what was consulted while writing it, and for what. No code was
copied from any third-party tool, and nothing was ported line by line.

## Manuals (the specification the code is written from)

- **PowerPC Microprocessor Family: The Programming Environments for 32-Bit Microprocessors** (IBM/Motorola).
  Chapter 8, "Instruction Set", gives every instruction's encoding and semantics: the decoder (`decode.py`) and
  the integer, branch, condition-register, XER, load/store, string and floating-point lifters (`translate.py`).
  Chapter 3 covers the FPSCR, the rounding rules and the single-precision conversions.
- **IBM PowerPC 750CL RISC Microprocessor User's Manual**. Covers the paired-single instructions (opcode 4), the
  quantised loads and stores (psq_l / psq_st, opcodes 56–61), the GQR layout (type and scale fields),
  dequantisation, and quantisation with clamping (`translate.py` ps_op / psq, `emit.py` px_quant / px_dq / px_q).
  Also the fctiw upper word, the single-precision behaviour of fres / frsqrte, and the 750CL-only
  instructions (dcbz_l and the supervisor set).

## DecompPipeline's own GameCube target (the output form and the judge model; the user's own code)

`<projects>/DecompPipeline/targets/gamecube_ppc/`:

- `runtime/tc_rt.h`, `tc_rt.c`, `tc_ppc.c`: the raw-C API the output is written against (rd/wr, TcArgs,
  tc_call / tc_callf, the ppc_* helpers and what each computes).
- `target.json`: the ABI facts (entry SP, return sentinel, stack and RAM windows).
- `ghidra_scripts/TcOracle.java`, read only, to model what the judge compares: the call record, the registers a
  mocked callee sets and clobbers, the stack window that is not compared, the GQR refusal.
  `TcFixups.java` gave the fctiw upper word and the stfs conversion, and `TcFlow.java` how CodeWarrior's
  save/restore helpers (isSaveHelper) are recognised.
- `ppc_check.py`, `ppc_compare.py`: the judge's lint / build / run / compare steps, which `tools/dp_judge.py`
  calls unchanged.
- `core/lint.py` and `core/check.py`: the raw-C rules (banned words and prefixes, size cap, the signature and
  name), and the MSVC environment and flags reused by `tools/compile_all.py`.

## Our own Saturn translator (structure; the user's own code, all commits by GeneralAtrox)

`<projects>/External/sh2-translate`:

- `structure.py` and `cfgstruct.py` were copied, as the user's own code, to lay the output out as loops and ifs
  without gotos. Two changes were made here: `splice` gathers jump targets bottom-up, which gives the same
  output faster, and the layout search has a fixed work budget (`SEARCH_WORK`).
- The overall shape follows that project: a typed expression AST, an IR of statements, optimisation passes, an
  emitter, and a judge driver that mirrors the workspace and calls DP's check. So do the `compact` form for
  large functions and the regress script.

## Third-party tools: reference and comparison only

- **DolRecomp** (GPL). It was read and run only to learn how it handles tricky cases, and its results were used
  for comparison. Its handling of quantised stores (clamp, then truncate) was checked against the 750CL manual,
  and its judge results on Skies of Arcadia (81 of 83 matched) are the score compared against. No code was
  taken from it.
- **`<projects>/External/dolrecomp-dp`** (our DolRecomp adapter) was not used in any way, since it may carry
  pieces of DolRecomp.

## Ground truth for checking

- Ghidra's disassembly of Skies of Arcadia's `main.dol`, taken from the workspace's existing listings (DP
  packets' asm.txt and the research instruction dumps; Ghidra itself was not run). `tools/check_decoder.py`
  compares the decoder against it: 0 mismatches over 27,871 addresses.
- The workspace's judge harnesses (`harness/<addr>/`: meta.json, vectors, expected results), run through DP's
  own check.
