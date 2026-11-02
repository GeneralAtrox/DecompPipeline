# Xbox 360 landing: judge-gaps, the TcNaN wiring, oracle fixes, speed, fused fma

Branch `x360-landing`, from `xbox360-ppc` 6505588. It is the landing list's §3 (the Xbox 360 owner's steps) plus the
fused multiply-add decision and the Xbox 360 judge gaps (`x360-judge-gaps`). Nothing is re-stamped here: every Xbox 360
stamp moves (TcFixups, TcFlow, TcOracle, TcPrepare and TcVectors change), and the landing's one re-stamp and
re-prepare per project follows.

| step | commit | what |
|---|---|---|
| 1 | 4bf7f3b, 095338e | master 70ab762 (judge-gaps) merged with `-X ignore-space-at-eol`; `xex.py` and `tests/test_xbox360_ppc.py` back to LF |
| 2 | 57adc6a | the TcNaN wiring (the judge-gaps hand-over patch) |
| 3 | d22645b | x360-oracle-fixes f7bf3fa; GameCube `TcTrack.on` moved below `passThrough` |
| 4 | c0db3f8 | xbox360-speed 1ef98e4; TcFixups' per-mnemonic cache carries TcNaN's kinds |
| 5 | (none) | stamp_scopes: no entry (see below) |
| 6 | (workspace) | `TooHuman/decomp/oracle/hints/82d5abf8.txt` = `genrange r3+0x34+0x8 1 4` |
| 7 | (machine) | `"xbox360_disc"` in the checkout's git-ignored `config.local.json` (no key printed or committed) |
| 8 | ee8ec69 | fma fused: `target.json` `"fma"`, `TcFixups.FMA_FUSED`, the runtime's `TC_FMA_FUSED` default (the build's `/DTC_FMA_FUSED` comes from target.json) |
| gaps | 104fca7 | x360-judge-gaps dcf6d29 (kept registers across calls, pointer-call arguments, float NaN words, stack paint) |
| tests | ab9708e, a129685 | two tests from master compile GameCube's TcFlow without the shared ppc folder it now needs |
| gaps fix | 3418922 | x360-judge-gaps d837bca: no r0 input from a trap with TO = 31 or an AltiVec (RA\|0) operand |
| speed | f126378 | x360-judge-gaps 13eb6a0: TcTrack and TcOracle take each instruction's facts and each call's zeroed set once |

## Merge resolutions

- `core/oracle_stamp.py` (step 1): master's `common()` hashes the scripts and facts lines once per scan; this branch's
  stamp_scopes give a function outside a scope its own scripts lines. Both kept: `stamp_of` uses the shared hash unless
  the function has "before" hashes, and then hashes its own (`common(scripts, facts, before)`).
- `tests/test_xbox360_ppc.py` (step 1): master's memoised figures, with this branch's catalog (VMX 1397, the sizes of
  the three added entries). The test ran against the real disc and passes.
- `tests/test_checkout_state.py` (step 3): the shared ppc set is TcSingle, TcPassThrough and TcNaN.
- `TcFixups.java` (step 4): `classOf` gives five entries; [3] the result kind (single, double, none), [4] the input fix
  (sign, lfs, none).
- the x360-judge-gaps merge: TcFlow, TcOracle and TcPrepare as the trial merge of that branch with oracle-fixes and
  speed; TcFixups and target.json this branch's, with `TcTrack.java` locked.

## stamp_scopes: no entry

The judge-gaps proof asks for one entry covering all three branches. No sound one exists: x360-oracle-fixes changes the
argument registers of every function with callees (pass-through), xbox360-speed rewrites TcFixups for every function,
and the judge gaps change every call's mock. A scope must name the functions a change cannot reach, and here there are
none to name. So the old `single_fp` entry is gone (as on xbox360-speed) and every stamp moves; the one re-stamp the
landing plan already has covers it. The stamp_scopes mechanism itself stays (core/oracle_stamp.py, x360_scope.py).

## Fused fma (step 8)

Too Human has 11 prepared functions with multiply-adds. Each was prepared twice from this branch's tree (104fca7):
fused, and the same tree with the three settings set back to two roundings (a scratch commit, not on the branch). On
laptop2, MSVC 14.44.

- **Harnesses:** vectors.txt and expected.txt are byte-identical between the two for all 11. `fma_sensitive` (vectors
  whose results differ when the oracle runs the other mode) is 0 in all 11.
- **Verdicts:** the same C (xenon-tx at 73b1d1c, and with the keeps patch), judged with each side's runtime: no verdict
  changes. 9 PASS and 2 FAIL in both (826a3580 11/96 and 828991b8 7/96, both an f1 argument at a call through a pointer:
  the judge-gaps pointer-call rule, not fma).
- **Why 829f6e20's 20 sensitive vectors went:** today's harness (6505588) has `fma_sensitive` 20 of 96. Its vectors equal
  the new ones; of the expected results, 65 are equal and the other 31 differ only in NaN bits (29 in stored words, 2
  in f1). Those NaN payloads and signs were where the two modes disagreed; TcNaN now gives the architecture's NaN in
  both, so the choice no longer shows on Too Human. The runtime tests still show it (84 of 2,480 fma cases).
- **Mutation:** the 9 that pass, `tools/mutation_check.py --report` on the same C, each side with its own runtime:
  87 of 134 mutants killed on both sides, and per function the same (tried, killed, survived) in all 9. 0 lost.

## Judge gaps (x360-judge-gaps 13eb6a0)

Proof on laptop2 (14.44 / laptop2), Too Human's 534 functions. Base is `xbox360-ppc` 6505588 and the branch is
`x360-judge-gaps`. Each side prepared its own harnesses, and the same translated C was judged on both: T0 is
xenon-tx 73b1d1c and T1 is the same with the keeps patch (`xenon-tx-keeps.patch`). Scripts and results are in
`2026-10-24/x360-judge-gaps/`; `verdicts_translator*.txt` list every verdict that changed (vcmp.py).

**Verdicts, the same C (T0; T1 in brackets where it differs):**

| | functions | why |
|---|---|---|
| PASS, unchanged | 466 (462) | |
| item 1, kept registers | 824c0a50, 82967760 PASS → FAIL; 82976860 NO_VECTORS → FAIL | T0's C loses a register the callee keeps (r4, r6 at 82967760; a crash at 824c0a50). T1 (keeps) passes all three |
| item 2, pointer-call arguments | 49 PASS → FAIL (and 82b1c380, whose C differs) | an argument register (f1 mostly) set before a call through a pointer that the C does not pass: user pick B, the judge stays strict and the C is fixed |
| item 2, "both r3 and f1 read" | 82436a30, 825ef068, 826209d8 PASS → FAIL | the same rule: the call's result is r3 only |
| item 2, trap | 82dbcf78 PASS → FAIL 5/96 | a conditional trap the original takes; the vector is invalid there, not the C |
| item 3, TcNaN | 82635508 FAIL 6/96 → PASS | NaN results by the PowerPC rules |
| FAIL, unchanged | 4 | |

Two refusals the branch first added are gone: Ghidra's p-code gives an unconditional trap (twi/tw TO = 31) and an
AltiVec indexed access with RA = 0 an r0 input, which a kept r0 exposed (8277d310 and 82d24cf0 were REFUSED "reads
r0 on entry"). TcFlow now ignores those (d837bca, `TcFlow.literalZero`); the inputs probe shows the change touches
only those two, and 16 more re-prepared harnesses are byte-identical apart from the stamp.

**Deadline.** The branch's oracle was slower than base's: TcTrack.step ran four regexes and a p-code walk on every
emulated step (16% of the samples in a JFR profile of 82e97a88) and TcOracle rebuilt the zeroed set per call (3%).
13eb6a0 caches both (1.2% and 0.1% after). Re-prepared with it, 257 of 260 harnesses are byte-identical apart from
the stamp (`hdiff_speed.tsv`); the other three time out under load on both versions. On this landing tree
(f126378, with xbox360-speed's TcFixups) the functions that timed out on the branch prepare and PASS with both
translators: 82e97a88 in 632 s, 82b9d648 569 s, 829ab198 364 s and 82801900 514 s (which base also timed out on),
against the 750 s deadline. 82c48438 (FAIL on the branch: r8 passed through, x360-oracle-fixes' rule) PASSes here
too.

**Mutation: 0 kills lost.** `tools/mutation_audit.py` (land-2026-10-24, with its per-mutant outcomes in
`core/mutation.py`, a report-only change) on its own seeded draw of 150 of the 466 functions that PASS on both sides
with the same C, 10 mutants each, judged by each tree on its own harnesses. Gate `audit_branch.json --baseline
audit_base.json`: PASS. 1,470 mutants compared, killed 1,054 → 1,055 (71.7%), gained 1 (82d20228 m05), lost 0, no
errors or time caps.
