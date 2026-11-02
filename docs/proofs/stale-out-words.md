# Stale words of a callee's out buffer: mask the bytes, keep the vector (tcdone round 4, 2026-10-31)

Nearest existing: `docs/proofs/tcdone/8c05ed5c.md` and `8c05ed5c_unmocked.md` in the Toy Commander repo (the stop this answers);
in this kit `readable_stack.pair_stores` (the same kind of word-for-word reading: a stack address stored by both sides) and
`readable_outptr.deliver` (the out-pointer delivery whose hidden bytes this reads). The A/B drivers in `stale-out/` follow
`rd_mut_ab2/4.py` of the earlier rounds (swap a judge side under a flag, `mutation_audit.compare`).

## What the original does
TC 8c05ed5c (RigidBody_FindContact) hands a table callee (`g_shapeContactFns[index](&candidate, ...)`) a 28-byte `candidate`
on its stack, then copies the whole buffer to `out` when the callee reports a hit and the contact is the nearer one. Before the
call the original has stored two words of its own into that buffer (sp+0x20/0x24: pointers into the shape records,
`rd32(shapes+60)+4`, e.g. 0x8cc02104). They are NOT stack addresses (the round-4 brief guessed they were). A callee that does
not overwrite them leaves them in the buffer and the copy carries them to `out`.

The out-pointer delivery (`readable_outptr.deliver`, D lines) gives the readable local the callee's bytes except the ones the
original stored itself before the call ("xx": the readable code's own bytes stay). The readable `candidate` is another
variable that never held the original's parked pointers, so its copy to `out` holds other bytes in those words and the vector
fails (one vector of 96 in the v4e harness).

## The rule (readable judge only)
`readable_stack.stale_out_words(exp, act, dlines, frame_lo)`: a word of the original's output (below the frame, all four
bytes written by the original) whose value equals a NON-ZERO WHOLE word that the original parked in a callee's out buffer
(from the new `S` lines, written beside the readable runner's vectors by `readable_outrun.vectors_file` through
`readable_outptr.parked_bytes`: per byte, the value both paints' raw runs hold where the original stored it, else `--`) reads
as the original's: the readable word is set to the original's. Everything else compares as before: a different word, a partly
parked word, a zero word, a frame word, every other byte, the vector itself, its return value and its calls. It is not a
vector drop (round 2's drop lost the m08 kill, `return collect_contacts(...)` -> `return 0`) and not a per-function list: it
needs the out-pointer delivery's own D lines for that call, so it is idle for every function without them.

Wiring: `readable_stack.settle_stores` runs `pair_stores` and then `stale_out_words`; `readable_check.compare` calls it in
place of `pair_stores` (net zero lines in `readable_check.py`, which is on the size ratchet at 578). `STALE_OUT = False` is the
judge as it was (the A/B's OLD side). The raw judge is untouched (not shown necessary: the raw run has the original's own
bytes).

## Proof 1: 8c05ed5c, mutation A/B, 0 lost kills
OLD = the workspace harness, no opt-ins, rule off (what is judged today: 8c05ed5c FAILS 1 vector). NEW = the round-2 v4e
harness (primed candidate, table pinned, two-box vectors) with its opt-ins (`out_targets`, `drop_vectors` 5/56/62 with
`known_callees`) and the rule on: PASSES, 0 failing. The same ten mutants (`core/mutation.generate` seed 1,
`mutation_audit.MUTANTS`) judged on both sides: OLD `KKKKKKKKKK`, NEW `KKKKKKKKKK`, 0 lost kills (`stale-out/ab5_ed5c.json`),
m08 included. (In round 2 the vector drop lost m08; masking the bytes keeps it.)

## Proof 2: where else the rule can fire, and the A/B there
- `stale-out/base_run.json` (`st_base.py`): the readable judge with the rule on, over the 327 TC functions whose readable
  judge gives a callee an out buffer (`outspec_scan.py`): 326 PASS, 8c05ed5c FAIL (no opt-in, as today). Words the rule changed
  on the unmutated C: 14 functions.
- That count is not a bound for mutants (a mutant's words differ). `stale-out/eligibility.json` (`st_elig.py`) counts instead
  the words of the ORIGINAL's output that equal a parked non-zero whole word, whether or not the C's word already agrees: both
  inputs come from the raw runs, not the C, so the count holds for every mutant. 15 functions have an eligible word; the other
  311 (plus 8c05ed5c, Proof 1) cannot be changed by the rule for any C.
- `stale-out/ab6_*.json` (`rd_mut_ab6.py`): the same A/B (rule off vs on, same harness and config) on those 15 and on 8c07de6c
  (masked in the base run, not eligible there: 16 functions): 150 mutants, killed on both sides in the same mutants for every
  function (outcome strings identical function by function), 0 lost kills, 0 baseline flips (all PASS on both sides).
  Together with Proof 1: 160 mutants on 17 functions.
Function list: `stale-out/masked14.txt` (+ 8c035bb8 from the eligibility count).

## Proof 3: the real console's paths (the coordinator's step 1)
Whether the console's hit paths really leave the contact partly unwritten decides if the original ever copies stale words.
The table callees were run in headless replays on the proven translator route (the port), the 28 `candidate` bytes
sentinel-filled (0xA5) before each table call and restored where still the sentinel, logging per callee type how many hits left
a sentinel word and whether that hit's contact was the one copied. Four scripted scenarios (the Training mission twice, h2n1, the
h6n7 boss), 53k to 72k vertical blanks each: 70,222 contact hits (57,475 whose contact was copied), ZERO hits in which any of
the seven words stayed the sentinel, for table types 0 to 8. Types 9 to 15 were not reached, nor other missions, so "never"
is not claimed for them. On these inputs the callees overwrite all 28 bytes on every hit, so the original never copies the stale
words; the unmocked harness's 0/4/16-byte hits (round 3) are inputs the game does not pass in these runs. The rule therefore
does not hide behaviour seen in real play: it removes a harness artefact (mocked callees leave the parked words) from the
judge, with the vector, return value and all other bytes kept. Step 3 (zero-initialise the readable candidate) is not
triggered: real play shows no short write.

| table type | calls | hits | hits whose contact was copied | hits with a sentinel word left |
|---|---|---|---|---|
| 0 | 5,862,673 | 44,936 | 41,182 | 0 |
| 1 | 427,775 | 97 | 94 | 0 |
| 2 | 214,609 | 555 | 533 | 0 |
| 3 | 23,656 | 12 | 12 | 0 |
| 4 | 11,487,426 | 22,858 | 14,456 | 0 |
| 5 | 1,491,567 | 8 | 5 | 0 |
| 6 | 744,271 | 1,384 | 901 | 0 |
| 7 | 24,132 | 357 | 277 | 0 |
| 8 | 16 | 15 | 15 | 0 |

(Summed over the four runs. The probe is a scratch instrumented copy of the judged translator C and is not landed.)

## What the rule gives up
A readable word equal to a parked pointer reads as the original's, so a readable C that writes a DIFFERENT value into a word
where the original has the parked value is not seen by that word alone. It is seen elsewhere if the value drives anything else
(a return value, another word, a call), and the A/B shows no mutant of 160 depends on it. The rule needs the original's word
to be a non-zero whole parked word, so a mutant that writes zero, a partial word or any other value into any other word is judged
exactly as before.

## Not in this change
The 8c05ed5c readable judge also needs the project's `readable_judge` entry (the `out_targets` form, kit commit 9bf8854f on
laptop2, not on master; `drop_vectors` 5/56/62 with `known_callees`, round 2's primed harness) and a pipeline redo of the
readable C; neither is a hand edit. Tests: `tests/test_stale_out.py` (mask vs a real difference, zero/partial/frame words, the
sidecar parse and the OLD switch, `settle_stores`, `parked_bytes`).
