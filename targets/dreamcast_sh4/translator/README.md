# sh4-translate

Turns a Dreamcast game's SH-4 machine code into C, one function at a time. The C is in the form DecompPipeline (DP)
uses for its raw stage, so DP's judge, unchanged, can check it: DP's lint, an MSVC build against DP's SH-4 runtime
(`tc_rt.h`), and a run over DP's test vectors, compared with what DP's reference (its Ghidra oracle) recorded.

It started as a copy of sh2-translate (the Saturn translator) and adds the SH-4: the FPU and its modes, the vector
instructions, the banked registers, the store queues and the cache operations. The code is our own (see
REFERENCES.md for what was read, and for what).

## Results (DP's judge, 2026-10-23, fresh test vectors)

| Game | Matched functions that pass | Matched left over | Unmatched harnesses that pass |
|---|---|---|---|
| Toy Commander | 2,698 of 2,698 | none | 2 of 4 (1 oracle refusal, 1 with too few vectors) |
| Power Stone 2 | 6,536 of 6,541 | 1 NaN judge gap, 2 oracle refusals, 2 with too few vectors | 22 of 39 (1 NaN judge gap, 11 oracle refusals, 5 with too few vectors) |
| Spawn | 1,875 of 1,875 | none | 4 of 33 (3 NaN judge gaps, 11 oracle refusals, 15 with too few vectors) |

Every function that did not pass on the workspaces' harnesses was given new test vectors (`tools/oracle_probe.py`,
in private work dirs) and judged on them (`tools/dp_judge.py --harness-from`). DP's judge is unchanged. No function
fails because of the translator. The rest are:

- **NaN judge gap**: the only differences are NaN bits stored unaligned or bytewise. The reference writes 7fc00000
  and x86 writes ffc00000, and the compare forgives that only in aligned float words.
- **oracle refusal**: the oracle made no vectors. It either ran code outside the function's body, ran out of time,
  or hit the call limit.
- **too few vectors**: fewer valid vectors than DP's minimum (16).

`results/fresh_2026-10-23.json` holds each re-judged function's verdict, class and cause.
`results/verdicts_<game>.json` stays the baseline on the workspaces' own harnesses, which is what `tools/regress.py`
checks.

Of the 11,089 functions translated, 11,086 come out without a single goto. Three keep gotos: TC 8c024080 and PS2 8c096b38 (a
switch case that runs into the next one), and TC 8c08d638.

## Layout

- `sh4translate/decode.py`: the SH-4 instruction decoder.
- `sh4translate/translate.py`: one function to C (`translate_function`): its flow, the instructions' C, the calls and
  the runtime state as DP's runtime models them.
- `sh4translate/cfgstruct.py`: the goto-free control flow (loops, if blocks, switches) built from the block graph,
  including node splitting for loops with several ways in. `sh4translate/structure.py` holds the shared parsing.
- `sh4translate/cfgcheck.py`: a check that the structured C runs the same statements in the same order as the goto
  graph it came from, on many paths (`SH4_CFG_CHECK=1`).
- `sh4translate/simplify.py`: switchable simplification passes (constants, copies, dead code, inlining).
- `tools/dp_judge.py`: judge the translated C (or, with `--baseline`, DP's matched agent C) with DP's judge. The game
  folder is only read: the judge runs in a mirror under `--out`.
- `tools/regress.py`: rerun the judge on all three games in bounded pieces and compare each verdict with
  `results/`; `--classify` says why each non-passing function fails.
- `tools/struct_check.py`: run the structure check on every function of a workspace (no judge, under a minute).
- `tools/coverage.py`: how much of each function the vectors reach, and whether the judge catches small changes.
- `tools/check_decoder.py`: the decoder against the instruction table.

## Use

```bash
python tools/dp_judge.py --ws <projects>/DreamCast/ToyCommander/decomp --out out --part 0/4
```

```bash
python tools/regress.py --games tc --budget 500
```

Each regress call judges until its budget is spent and stops; call it again to go on (exit status 2 means parts are
left, 1 that a saved PASS was lost, 0 that all is as saved). Ghidra is never started: only the workspaces' existing
harnesses are used.

## What the translator does about the judge's compiler and reference

Some C that is right for the SH-4 is compiled or compared differently by DP's judge. Each case is handled in the
translator, never in the judged C by hand:

- **MSVC /O1 steps a derived induction variable early** when an 8- or 16-bit read uses it before a call
  (`sink_steps`).
- **`x * 1.0f` is folded away**, and with it the SH-4's flush of a denormal (FPSCR.DN): the operand is flushed
  explicitly (`f_dn`).
- **The reference makes one NaN** (0x7fc00000) for any arithmetic NaN result; its bits are taken that way where they
  move to an integer register (`f_bits`), or are stored to a word of the own frame that the function reads back
  with an integer load (`nan_stores`: Toy Commander 8c09a518 tests a stored NaN's sign).
- **Machine code must not be read** (DP's R8): the store-queue model hands stored bytes over directly instead of
  reading them back.
- **Wait loops** the project names (`spin_waits`) call `tc_spin()` where they repeat.
- **FPSCR**: DP's runtime keeps only FR, SZ and PR; a read-modify-write that changes only other bits is left out
  (`fpscr_free`); any other FPSCR access is refused. Where the harness makes FPSCR state (meta `fpscr_state`, compared
  at exit and at calls), such a read-modify-write goes through `sh4_sts_fpscr`/`sh4_lds_fpscr` instead. An `and`
  that clears SZ or PR counts as keeping them where the function has no `fschg`/`fpchg` (they are 0 throughout:
  Spawn's library code goes back to single precision before its calls). An `or` that sets PR is double-precision
  code, which DP's raw C (f32 only) cannot hold: it is refused with that reason.
- **Compacted C** (over 58 KB even without comments) ends a line after each call, so one line holds one call's TcArgs
  statements (DP's R12 lint reads them line by line).
- **Calls through a computed target** (`jsr @rN` to a target meta does not list) hand over every argument register
  set since the entry or the last call (`set_args`), which is what DP's judge compares there (the oracle's CA).
- **Bodies**: the catalog's ranges (`state/function_bodies.txt`) count as the body where the harness's blocks miss
  some.
