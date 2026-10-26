# References and clean-room note

xenon-translate is the user's own work. This file lists what was consulted while writing it, and what each source
was used for.

**No code was copied from any third-party tool, and nothing was ported from one.** XenonRecomp and xboxrecomp
(`<projects>/External/`) were used only as described under "Reference tools". Their sources were not used as a
model for this code.

## Manuals: the specification the code is written from

- **PowerPC Microprocessor Family: The Programming Environments Manual for 64-bit Microprocessors** (IBM).
  - Chapter 8, "Instruction Set", gives every instruction's encoding and semantics. It is the source for the
    decoder (`decode.py`: the D, DS, X, XO, XL, XFX, XS, M, MD, MDS and A forms) and the lifters in
    `translate.py`.
  - The lifters covered are integer (with the 64-bit forms and the 32-bit rotates' 64-bit masks), branch,
    condition register, XER and carry, load/store (with byte-reversed, update and multiple forms), and floating
    point (fctid / fctidz / fcfid, fsel, the fused multiply-adds).
  - The FPSCR and the single-precision rounding chapters cover `frsp`, `stfs` and `lfs`.
- **AltiVec Technology Programming Environments Manual** (Freescale/IBM).
  - The VA, VX and VC forms of opcode 4 and the opcode-31 vector loads and stores, used in `decode.py`.
  - Each vector instruction's operation description, used for the `xv_*` helpers in `vmx.py`. These cover:
    - element order (big-endian), modular and saturating arithmetic, and pack/unpack;
    - merges, splats, permutes, and whole-register shifts by bits (vsl / vsr) and by octets (vslo / vsro);
    - lvsl / lvsr, compares and their CR6 summary bits;
    - float to integer and integer to float conversions with a scale, and the four round-to-integral modes;
    - non-Java mode denormal flushing.
- **The Xenon VMX128 extension** (opcodes 4, 5 and 6): 128 vector registers whose numbers are split across the
  word, and the instructions added to AltiVec. These are the dot products, vpermwi128, vrlimi128, vsldoi128, the
  128-register forms of the loads and stores (with lvlx / lvrx / stvlx / stvrx), vcsxwfp128 / vcfpsxws128 and the
  Direct3D pack/unpack pair. The field layouts were taken from public descriptions of the encodings. Each form's
  layout and operand order were then confirmed against the reference disassembler's output on every code word of
  Too Human (see "Reference tools").

## DecompPipeline's own Xbox 360 target: the output form and the judge model (the user's own code)

These live in DecompPipeline (first on its branch `xbox360-ppc`). They were read only; nothing was
edited.

- `targets/xbox360_ppc/runtime/tc_rt.h`: the raw-C API the output is written against. That means the
  rd/wr helpers including byte-reversed, `TcArgs` with r[8], f[13] and s[8], `tc_call` / `tc_callf`, and every
  `ppc_*` helper with what it computes.
- `targets/xbox360_ppc/DESIGN.md`:
  - The judge model: the entry state (r1, LR sentinel, r13), what a mocked callee clobbers, the stack window
    that is not compared, and the frame helpers that run inline.
  - The register widths, how the return type fills r3, and the multiply-add rounding mode.
  - The instruction classes DP refuses in phase 1.
  - The VMX128 semantics DP plans for its own vector phase (non-Java mode, vpermwi128, vrlimi128).
- `targets/xbox360_ppc/target.json`: the ABI facts: entry SP, return sentinel, stack window, and the vector
  argument and return registers.
- `core/lint.py`: the raw-C rules (banned words and prefixes, the 60 KB cap, the signature and name rules, and
  that only static helpers and typedefs may sit at top level).
- `core/check.py`: the MSVC flags. `tools/compile_all.py` builds the same way, and `tools/dp_judge.py` calls
  `core.check.run_check` unchanged.
- The Too Human workspace (`<projects>/TooHuman/decomp`): `image.bin`, `image.json` and `catalog_inputs.json`.
  The catalog gives the function bodies, the frame-helper ranges and DP's refusal classes.

## Our own GameCube and Saturn translators (structure; the user's own code)

- `<projects>/External/ppc-translate` (GameCube) was the starting point. The following were copied and then
  changed for 64-bit registers, the Xbox 360 ABI and vectors:
  - the IR (`ir.py`) and expressions (`expr.py`);
  - the dataflow passes (`passes.py`: propagation, store-to-load forwarding, dead-code removal and the
    tc_call / tc_callf choice);
  - the emitter (`emit.py`) and the whole-executable return analysis (`returns.py`);
  - the translator's control-flow and jump-table code;
  - the tools (`translate_one.py`, `compile_all.py`, `dp_judge.py`).
  The paired-single and quantised-load parts were dropped, since the Xenon has neither.
- `structure.py` and `cfgstruct.py` (loops and ifs without gotos) came to ppc-translate from
  `<projects>/External/sh2-translate`. They are used here unchanged.

## Reference tools: compared against, never used as code

- `<projects>/External/xenon-dp/tools/dis.exe` is a build of XenonRecomp's disassembler.
  `tools/check_decoder.py` runs it on the Too Human image. It reads only the text it prints (mnemonic,
  instruction id, operand values) and compares that with our decoder's result. On the 3,695,596 code words this
  found two disagreements, both fixed:
  - vsldoi128 must be recognised before the VA-form bit is tested;
  - three operand-display differences were in the checker itself, not the decoder.
  After the fixes, every word agrees. `tests/golden_words.json` keeps one word per op from that run as a
  regression test.
- The XenonRecomp and xboxrecomp folders were only listed, to find that disassembler build. None of their
  source files were read.
