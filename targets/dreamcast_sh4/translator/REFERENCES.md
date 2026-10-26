# References

What was read while writing sh4-translate, and what for. Everything in this repository is our own code. No code was
copied from, or ported line by line out of, any project below that is not the user's own.

## The user's own projects (used as sources)

- **sh2-translate** (`<projects>/External/sh2-translate`): the Saturn translator this repository started as a copy
  of (its history is kept here), and its later commit 0b28828 (jump tables in more shapes, `const_in`, two MSVC
  warning fixes), carried over.
- **DecompPipeline, the Dreamcast target** (`<projects>/DecompPipeline/targets/dreamcast_sh4/`): the model the C must
  match.
  - `runtime/tc_rt.h`, `tc_rt.c`: the runtime's calls, state (r8..r14, fr12..fr15, the FPU mode bits FR, SZ, PR),
    `tc_spin`, `sh4_ftrc`, `sh4_fmac`, `sh4_fsca`, `sh4_fsrra`, `sh4_ftrv`, the store-queue window, DN as SSE
    DAZ|FTZ, and the R8 machine-code read check.
  - `ghidra_scripts/TcOracle.java`, `TcBase.java` (the P0 mirror fold of addresses and jump targets), `TcFpu.java`
    (the FPU model, NaN payloads not modelled), `TcSq.java` (the store queues).
  - `target.json`: image base, byte order, the facts the translator takes.
- **DecompPipeline core** (`<projects>/DecompPipeline/core/`): `check.py` (the judge: lint, build flags
  `/O1 /fp:strict /W4 /WX`, runner, compare), `lint.py` (the raw-C rules, no pragmas), `compare.py` (how results are
  compared: NaN-tolerant floats, the mirror rule, spin and halt), `paths.py`, `ret_reg.py`, `scope.py`.
- **The game workspaces** (Toy Commander, Power Stone 2, Spawn): read only, for their binaries, harnesses,
  `state/function_bodies.txt`, `project.json` (`spin_waits`) and matched C (judged as the agents' baseline).

## The SH-4 itself

- The Renesas SH-4 software manual: instruction encodings and semantics (FPU, FPSCR, fmac's single rounding, fsca,
  fsrra, ftrv, fipr, fcnvsd/fcnvds, banked registers, MAC, movca/pref/ocbi, the store queues).

## Third-party projects (reference and comparison only)

- **dcrecomp** (`<projects>/External/dcrecomp`, GPL): run and its results read only to compare pass rates and to
  learn which kinds of function were hard. None of its code is in this repository.
- **dcrecomp-dp** (`<projects>/External/dcrecomp-dp`): only its saved judge results
  (`results/final_7ae9e0d.json`, `results/parity/*_a483fa3.json`) were read, to compare verdicts per function.
  Nothing else was used.
