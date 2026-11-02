# Junk-RAM pass: the memory a vector starts from is the junk image (tcdone round 3, 2026-10-31)

Nearest existing: `docs/proofs/junk-judge.md` (the pass itself; this note is a correction to it, with its own proof).

## The defect
`core/junk_ram.judge` runs the C and xemu's run of the original on the junk image, then compares them with
`verdicts()` -> `compare.compare_vector(meta, image, vec, ...)`. `compare_vector` takes a byte nobody wrote from
`Memory(image, vec).initial(a)` (the vector's patches, then the image's bytes, else 0). `verdicts` was handed the PLAIN
image, so every byte past it started as 0 in the comparison, although both runs had started from the junk there.

That makes a harmless thing a failure: xemu's store-queue send (`pref`) writes the 32 bytes of the block back to RAM,
including the bytes the queue got from RAM at entry (`sq_fill`). When those bytes are junk the original "writes" the junk
that was already there; the C (its translated SQ model, source block == destination block) writes nothing. The judge
expected 0 there, got the junk from the original and nothing from the C, and reported "your code did not write here".
It is no difference: the memory ends as it began on both sides.

TC 8c054830, 8c054adc, 8c0721d8, 8c071130 (raw, `sq_window`) failed the pass exactly this way: tcdone round 2 reported
2/96, 6/96, 1/101, 1/99 vectors and could not say which side was wrong. The expected words are the junk image's own bytes at those addresses (`l/junk_at.py`); round 2's search for six words in
the image had missed this and concluded they were computed: that search was wrong, not re-investigated here. With the junk image as the initial memory all four give 0 junk-verdict
failures (`tools/junk_ram.py --original -v`).

## The fix
`verdicts()` passes `_padded(image)` (the junk image, cached) to `compare_vector`. 10 changed lines in `core/junk_ram.py`
and four tests (`tests/test_junk_ram.py`, class `Initial`): the original writing the junk a byte already holds is no
difference; the C writing zero over junk IS caught; a different value is caught; the same write on both sides passes. The
first two fail with the old behaviour (checked by replacing `_padded` with the identity) — they discriminate.
The fix also catches more than before: a C that writes 0 over a junk byte while the original leaves it, which the old
comparison read as equal to the initial 0.

## Proof: no kill lost
Method: the official `tools/mutation_check.py --report` (junk pass on: `junk_ram` is on in the Dreamcast target), the same
seeded mutants (`core/mutation.generate`, seed 1, up to 16 per function) judged by the OLD judge (kit as landed, 9bf8854f)
and the NEW judge (this branch), compared mutant by mutant with `mutation_audit.compare` (`jr_cmp.py`).

| set | functions | mutants compared | killed old -> new | lost | gained | flips |
|---|---|---|---|---|---|---|
| TC, every function with `sq_window` meta, minus the four (`jrsq.txt`) | 46 | 658 | 550 -> 550 | 0 | 0 | 0 |
| TC, 48 other matched functions, seeded sample in 4 chunks (`jrs0..3.txt`) | 48 | 628 | 541 -> 541 | 0 | 0 | 0 |
| PS2 and Spawn main, the junk round's planted wrong reads (`land/junk/out/planted_*.json`), TC planted | 30 | 67 | 56 -> 56 | 0 | 0 | 0 |
| the four, NEW only (OLD fails their baseline, so it kills nothing to lose) | 4 | 59 | 45 killed, 14 survived | - | - | 4 baselines now pass |
| the six known wrong reads: PS2 8c03eee4 m1 m2, 8c171288 m1 m6, Spawn 8c04e620 m1 m9, generated mutants (`jr_six.sh`) | 3 | 39 | 36 -> 37 | 0 | 1 (8c171288 m10) | 0 |

Totals on the first three rows: 1353 mutants, 1147 killed on both sides, 0 lost kills, 0 gained, 0 flips (no matched C that
passed OLD fails NEW) across 94 + 30 functions. The only baseline that fails on both sides is Spawn 8c031786 in the planted
file (96/96, an old planted C against a newer harness; identical on both). The six known wrong reads still FAIL (outcome `K`)
on both judges.
The survivors of the four on NEW (their 14) are the mutants no vector distinguishes; they are the functions' harness
weakness as in any other row, not a consequence of this change (the zero-RAM pass alone decides them; the junk pass only adds).

Why a lost kill cannot come from the fix: a mutant is killed by the pass if its junk-run differs from the original's. The
change makes `compare_vector` read the initial value of an unwritten byte as the junk both runs started from instead of 0.
It makes a difference vanish only where the original's written value equals the junk the byte already held and the mutant
wrote nothing there, i.e. where memory ends equal on both sides. A mutant that differs in any other way (other value, other
address, return, calls) is judged exactly as before.

## What it does not do
Does not touch the zero-RAM pass, the oracle, any harness or the stamp inputs other than `core/junk_ram.py` (judge code:
the judge stamp moves, as for the flag's own landing; no re-prepare). The audit baseline (`tools/mutation_audit/baseline.json`)
needs no re-draw for this change: the figures above are the old-vs-new comparison on its kind of sample, not the frozen sample;
the frozen-sample audit should be run once on the landed head as for any judge change (`mutation_audit.py needed` says so).

## Files (here, `docs/proofs/junk-initial/`)
`jr_ab.sh` (mutation_check on a list, old or new kit), `jr_drv.sh` (loop), `jr_pl.sh` (planted wrong reads), `jr_six.sh`/`six_drv.sh`
(the six), `jr_cmp.py` (comparison with `mutation_audit.compare`), `jr_cmp.out` (its output for the rows above), the function lists.
They are build box B-path scripts (jobreg wrapper `j.sh` of the tcdone round) kept as the record of the run.
