# Raw-run salvage (core/readable_outrun.py), TC 8c038c2c, 2026-10-30

Nearest existing: tcread13's rd_mut_ab (readable_optin A/B) - that toggles a compare opt-in; this toggles the vectors file.

## What changed
The out-pointer step of the readable judge takes the original's bytes (D lines) from runs of the matched raw C with
painted out buffers (B lines). `RawRuns.run` returned None when a run timed out, so one hung vector took the D lines of
all vectors away (they fell back to O lines, the vector's own bytes).

TC 8c038c2c: run one vector at a time, exactly 4 of 99 painted vectors hang (17, 25, 33, 89): the raw C walks
`link = link->next` from a grid cell the paint chose, through memory the vector never meant to be a chain, and the walk
cycles. That is the original behaving on bytes it never sees on the console; no paint value avoids it in general, so
the hang is not removed at its source. The judge now survives it: the runner flushes after each vector's E line, so a
timed-out output holds the finished vectors whole and the next vector in the file is the one it hung in. That vector
is left out, the rest run again (up to 12 times); a left-out vector has no OB lines and its out-pointer lines stay O
lines, every other vector gets D lines. Only `err == "runner timeout"` takes this path; any other failure, a build
failure and a run that finishes no vector are None as before. Switch: `readable_outrun.SALVAGE` (False = the judge as it was).

## Who can differ
Only a function whose raw run times out. Scan of TC (323 functions whose readable judge makes painted runs): runs cached
for all but 8c05034c, 8c056a0a, 8c071c18 (not cached) and 8c038c2c (partly). Every other function's vectors file is
byte for byte the old one, so its verdicts and kills are identical by construction.

## 0-lost-kills proof (docs/proofs/tcdone/rd_mut_ab3.py, ab3_salvage.json)
OLD = SALVAGE False, NEW = SALVAGE True; core/mutation.generate seed 1, 10 mutants, both judged from the same source.
| function | base OLD | base NEW | OLD outcomes | NEW outcomes | lost kills |
|---|---|---|---|---|---|
| 8c05034c | PASS | PASS | KSBSSSBSSS | KSBSSSBSSS | 0 |
| 8c056a0a | PASS | PASS | K (1 mutant drawn) | K | 0 |
| 8c071c18 | PASS | PASS | KKKKKKKKBK | KKKKKKKKBK | 0 |
| 8c038c2c | FAIL | PASS | KKKKBBBKKK (base fails: all trivially) | KKSKBBBKSS | n/a: base failed before |
Substantive lost kills: none. For 8c038c2c the base now passes 99/99 and 5 of 8 buildable mutants are killed; the
survivors are m03 (line 16 `cell[1] >= 0` const), m09 (line 44 index `+` to `-`) and m10 (line 15 `cell[0] >= 0` to `>= 1`):
the vectors rarely put the camera cell inside the grid (the mocked Grid_CellOfPoint writes nothing to `cell`, so the
cell is stack junk), so the in-grid index arithmetic and the lower bounds are barely exercised. That is a property of the
oracle's vectors, not of this change, and is listed as a coverage limit of this function.

Tests: tests/test_raw_runs_cache.py (Salvage: hung vectors left out, cached under the whole file's key; old judge and
nothing finished give None; other errors give None).
