# Check audit: planted wrong answers against the checks DP relies on

Why: Spawn's artwork matcher passed a wrong match because its author had defined success loosely. `AGENTS.md`, "Which
model does what": a score isn't proof until it rejects planted wrong answers. This fed wrong answers and near misses to
the checks, counted what each accepted, then raised the bar where a small fix could.

Numbers are "accepted before -> accepted after". Plants lived in a session scratch folder; what they fed is described
here so they can be rebuilt. Fixes are stricter only. No judge rule was loosened.

## frames.py (`port/tools/frames.py`)

A wrong answer is a frame the eye calls different: a missing button, a wrong digit, a shifted colour. The old limits
(0.5% of pixels changed, SSIM 0.97) let a small region or a slight colour shift through.

Plants were painted on all 35 real Toy Commander goldens. Accepted out of 35:

| Plant | Before | After |
|---|---:|---:|
| 8x8 object | 35 | 0 |
| 16x16 object | 35 | 0 |
| 24x24 object | 1 | 0 |
| A digit swapped (8x12 region flipped) | 35 | 4 (the region is symmetric there) |
| +8 brighter / -8 darker | 11 | 0 |
| Red +8 | 32 | 0 |
| Blue -8 | 35 | 0 |
| Contrast 97% | 30 | 0 |
| 32x32 and 64x64 object, gamma, BGR swap, shifts, bottom 5% black, all black, another golden, +-8 noise | 0 | 0 |
| Control, must pass: 20 scattered pixels | 35 | 35 |
| Control, must pass: +-1 rounding | 35 | 35 |

Real near miss: `x86-lck_regress/novmu/frame_001500` differs from its golden by a B button (288 pixels, 0.094%, SSIM
.9997). It passed; it now fails, "40 together". The clean run `x86-agent_regress` still passes 35 of 35.

The new limits are `MAX_MEAN` 1.0 and `MAX_CLUSTER` 12 (most changed pixels in any 8x8 window).

**Not proven:** a real frame from another GPU. Every non-identical real pair on hand fails anyway, so the new limits
could still fail a clean cross-machine run. A run on MAGNUS would settle it.

## regress.py (`port/tools/regress.py`)

- A frame with no golden passed. It is now a failure.
- `filled()` passed a flat white frame, which the script's own comment names as the regression. It now fails.

**Not fixed:**
- Only Toy Commander has scripts and goldens (6 scripts, 35 goldens, 6 baselines). Spawn, Power Stone 2, Robo Pit and
  Vigilante 8 have none, so none of their proofs go through this check.
- A small lit part can still meet the `filled()` band mean.
- A log class no longer reached is only a note, and a script with no frames passes on the watchdog exit.
- `--accept-log` passes by construction, as documented.

## lockstep.py (`port/tools/lockstep.py`)

Replacing emulated hardware relies on this. A wrong answer is a port that differs from the emulator while the check
says no divergence.

| Plant | Before | After |
|---|---|---|
| Port one whole frame ahead (4,999 of 5,000 frames excused as "drawn") | rc 0 | rc 2 |
| 10% of frames excused | rc 0 | rc 2 |
| 0 frames compared | rc 0 | rc 2 |
| 99 frames, all wrong, all excused as loads | rc 0 | rc 2 |
| Control: one excused sample in 200 frames | rc 0 | rc 0 |

"Drawn" excuses now count in the blind ratio (`SET_APART_MAX`, 5%).

**Not proven:** no real lockstep log with a drawn share was on hand, so a real run above 5% that now reads CHECK BLIND
has not been seen. It could mean a real divergence or a cap set too low.

## mutation_audit.py (`tools/mutation_audit.py`)

A wrong answer is a judge that lost its kills and a gate that says PASS. Plants against the real baseline:

| Plant | Before | After |
|---|---|---|
| Control (4965 compared) | PASS | PASS |
| All kills lost (3883 compared) | FAIL | FAIL |
| Every mutant unbuilt | FAIL | FAIL |
| Every function errors (a broken judge), 0 compared | PASS | FAIL |
| Every sha differs and all kills lost, 0 compared | PASS | FAIL |
| Outcomes 1 short | PASS | FAIL |
| Empty outcomes | PASS | FAIL |
| Half flips, half stale | PASS | FAIL |
| Empty baseline | PASS | FAIL |

A run that sets aside over `MAX_UNCOMPARED` (25%) as flipped or stale is now a failure. The real rate is lower: 2% flips
in judge-gaps (14 of 652 functions), 6% stale in gccatch and in Toy Commander now (9 of 150). `baseline` used to be able
to wipe kills through flips or stale, despite "only moves up"; a thin run now needs `--reason`, and the reason is
printed. `JUDGE_PATHS` missed `x360_compare.py`, `x360_check.py`, `core/strict_judge.py`, `core/call_coverage.py`,
`core/junk_ram.py`, `core/ret_reg.py`, `core/run_flag_set.py`, `core/target_hooks.py` and `tools/mutation_check.py`; a
change to them did not call for the gate. They are in now.

**Not fixed:** the baseline is stale. It last moved 2026-10-24 (789f901), and 20 judge or runtime files changed since.
`prewave-judge.md` already records that master fails against it. Refreshing is a heavy audit of about 6 hours and was
not run. Kill rates stand at grandia 79.5, ps2 74.8, skies 85.1, spawn-main 74.9, tc 79.7, against the scorecard's 85.
`spec.mutation_keys` has no Xbox 360 or Saturn sample.

## Scorecard (`tools/scorecard.py`, `tools/scorecard_runtime.py`)

- `gate_status` read info-only or empty as green. It is now amber.
- Aspect evidence accepted a folder. It now needs a file.

**Not fixed:**
- Gate 1 and the pre-wave items pass on file existence: an empty planted file passes.
- `div_check` passes on 1 clean of 1, and ignores unknown counts such as "skipped".
- The runtime gate passes an idle 600-frame title session and a worst stall of 500 ms, doesn't check which part of the
  game was played, and counts 4200x1000 as big enough by area.
- The aspect row is hand-written: "Yes, not really" passes.

Checked and fine: the runtime gate's headless guard is real (`play_stats.c:43` excludes fixed clock, replay and
headless); the final_audit filename sort is safe (timestamps are padded).

## Empty comparisons

"Nothing compared" passed in `port/tools/ram_compare.py`, `port/tools/hw_check.py` (no paired render) and
`tools/land_smoke.py`. Each now fails or returns 2. `hw_check.py` with every texture missing still passes (not fixed).
`tools/regress_targets.py` classed `xemu` as no judge dependency, but `core/junk_ram.py` makes xemu the junk oracle, so
it is now "judge". That one comes from reading the code: the plant for it crashed in setup.

## Spawn's artwork matcher (`play/Spawn_art/_tools`, not in this repository)

A wrong answer is a comic page that isn't the source of the game's tile. Inlier counts are from SIFT with a 4-DOF
RANSAC fit.

- The real strict sweep (133 issues, 3273 pages): wrong pages reach 6; the true `02_top_F` scores 37, `00_BR` 9, `01_bot` 8.
- The looser `missing` sweep (154 issues): wrong pages reach 10.
- The bar moved from "22 or more" to "above 6" after the results were seen.

Planted run through the real `process_strict` with the `run_strict.sh` settings: 56 tiles, 10 issues, 241 pages (10 game
textures that aren't comic art, 16 from issues never searched, 20 true tiles mild and heavy, 10 mirrors).

A true tile on another page is a wrong fit too, so all 56 tiles can be wrongly accepted.

| Bar | Tiles with a wrong page accepted, of 56 | Wrong fits | True tiles found on their source, of 20 |
|---|---:|---:|---:|
| above 6 | 2 (both 8 inliers, on Spawn 052) | 3 | 20 |
| 9 or more | 0 | 0 | 20 |
| 22 or more | 0 | 0 | 20, but rejects the real recoloured 9 and 8 |

Three wrong fits above 6 in about 13,500 tile-pages means about 6 expected over a real sweep of 8 queries on 3273 pages,
so no inlier count can prove an 8 or a 9.

A second bar was stated before it ran: edge-strength correlation between the tile and the region the fit places it on,
inside the part's mask, must beat every wrong candidate.

| Fit | Edge correlation |
|---|---:|
| 51 wrong fits with 6+ inliers (31 planted, 20 real parts' other pages) | 0.197 at most |
| Planted true, heavy recolour | 0.281 to 0.731 |
| `00_BR`, Spawn 076 p.02, 9 inliers | 0.717 |
| `02_top_F`, Spawn 047 p.03, 37 inliers | 0.509 |
| `01_bot`, Spawn 070 p.17, 8 inliers | 0.499 |
| `01_top`, Spawn 091 p.12, 5 inliers (a candidate, never accepted) | -0.013 |

`strict_view.py` now prints the score and `SPAWN_CUT` refuses under 0.25. The Spawn artwork session then ran its 24
Malebolgia candidates from a hand-cut outline through it: all score 0.146 or less. The bar comes from selected tiles, and
no true match from an outline query has been scored, so it isn't proven for those.

This audit's own error: two of its commands ran the old viewer with `SPAWN_CUT=1` after a patch failed, which wrote
`01_top` into the real output folder, with a row in `sources.tsv`. The audit then took that row for the card's and reported
it as a live wrong match. It wasn't: the artwork session never accepted it and the user never approved it. That session
withdrew the row and files. The audit found no wrong match among the artwork accepted so far, though only `00_BR`,
`01_bot` and `02_top_F` were scored. Don't run `SPAWN_CUT` to test the tool: it writes to `comic_source/final`.

## Proofs and the rest: found, not fixed

Ranked by what depends on them.

1. **Machine-adopted readable C, 1,496 functions.** `docs/proofs/readable_tx/regate.py:8-13` re-runs the gates that chose
   them, so it can't fail. No mutation audit or eye sample. The 701-caller `Shc_QuickMoveWordsR3` blind spot is
   unresolved (`READABLE_TX.md:166-168`).
2. **Stale-out rule, `core/readable_stack.py:259-264`.** Accepts a wrong value, zero, or a store the C never makes (3 of
   3 synthetic plants). No mutant targets masked words. `stale-out-words.md` says 16 where `ab6_*.json` has 15, and
   8c07de6c has 28 eligible words.
3. **`core/proof.py:106,154-156`.** `verified` for no summary, 0/0, 2/3 and 99/10 blocks. `proof_backlog.py:94-100` says
   CLEAR with 1,000 no_data and 1,000 weak harnesses. Left alone: its effect reaches the whole ledger.
4. **`core/call_coverage.py:38-43,85`.** One left-out run with 0 reach runs "explains" 200 of 200 sites; zero sites read
   100%.
5. **Salvage proof.** `tcdone/raw_run_salvage.md:32` says 5 of 8 where the JSON gives 4 of 7; "0 lost" is vacuous on the
   only function it changes.
6. **Smaller:** `decode_check.py:50-54` accepts 66 of 66 conditional-branch swaps. `completeness.py:202-209` accepts 1 MB
   of code called data. `gc_truth_dp.py:162` scores a holed body as exact. `audio_check.py:238` and `rate_compare.py:84`
   always exit 0. `ram_compare`, `rate_compare`, `readable_parity`, `ram_delta` and `emu_capture` have no tests.
   judge-gaps has `MIN_VALID=0`. x360-other has no mutation audit.

These items were found by a subagent with synthetic plants; it found no real wrong answer passing. Some proof scripts and
`lockstep_watch.py` were not read, and `verdicts_translator*.txt` were not tallied.

Recomputed and held: pre-wave 3802 to 3874 with 0 lost, gccatch, x360 1054 to 1055, Magnus (only timings differ). No proof
has been sampled by anyone but its author, which the rule asks for.
