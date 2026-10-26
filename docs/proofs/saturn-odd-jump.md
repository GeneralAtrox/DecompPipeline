# Odd jump targets on the Saturn: what changed and the proof

An odd PC is an address error on the SH-2: no run of the original reaches one. The Saturn oracle refused a function
("executes code outside its body") when an input-derived computed jump or return landed on an odd address that was
code. `TcOracle.inputJump` now treats any odd target as an invalid vector, which is repaired or dropped like a fault,
also inside the code ranges.

Proof: Dragon Force, the 11 functions judged "outside its body" plus 29 controls. The controls are drawn at random
(seed 1) from functions that prepared quickly in a first run that was stopped (200 functions was too slow). Each
function was prepared fresh on 050b097 and on the change, and the translator's C was judged on both, 14.44 /
laptop2:
- **Controls:** all 29 have byte-identical vectors and expected results, and the same verdicts (28 PASS; 06011428
  FAIL 96/96 on both sides).
- **Fixed:**
  - 06025f66: REFUSED → 48 dev and 48 hidden vectors, PASS 0/96.
  - 0602e108: REFUSED → PASS 0/96.
  - 06032140: REFUSED → FAIL 96/96. This is a correct failure of the translator's C: it copies r14 into its local
    `r4` but never puts it in the call's argument record, so both calls get r4 = 0 where the original passes
    0x060e0a24.
- **Not fixed by this rule:** 0602cc92, 0602d976, 0602df94, 060311ec, 060321d0, 06036348 and 06042758 are still
  refused, now at even targets that are code (06030100, 06040004, 06030600, 06040706, 06010602, 06020202,
  06042764). Dragon Force's code is dense, so nearly any input-derived address in 0601..0607 is an instruction, and
  the existing rule ("a target in code ... still refuses it") holds. 0603eea8 is no longer refused but has no valid
  vector: every one calls outside RAM from the inputs.
- **Byte-identical on both laptops.** The 40 new harnesses judged on 84f4f6b on each laptop give the same verdict,
  failing count, first diffs and actual-output sha256 for all 40.
- **Mutation, every candidate, after the change:** 06025f66 34/76, 0602e108 21/112. Before, these functions had no
  harness, so nothing could be audited. The survivors are mostly argument slots and stack words the judge does not
  read, plus logic survivors the vectors do not reach (weak_harness).
- The Saturn oracle scripts and runtime on this branch are byte for byte those of the proven commit 84f4f6b (the
  same change on 050b097). This branch carries only the Saturn change; the Dreamcast misaligned-access rule
  (judge-gaps-2) is separate and not part of it.
- `tests/test_sat_odd_jump.py` checks the rule is wired into `inputJump`.

## Not fixed here

The remaining 7 refusals are a separate decision: whether a computed jump from the inputs to code outside the body
that is not a function entry or a body constant should be an invalid vector rather than a refusal.

## Overlap
- No change to `core/strict_judge.py` or `tc_strict.h`, the compare or any runtime. Only Saturn harnesses can change,
  and only for functions where an input-derived jump lands on an odd address.
- Landing needs a re-prepare of the Saturn functions refused "outside its body" (Dragon Force's 11 above).
