# Runtime runs: measurements and proofs by game

The dated run notes of `docs/RUNTIME_TARGET.md`, moved here word for word on 2026-11-09 and grouped by game. The rules,
the "Emulated layers in the play path" table and the "Aspect-ratio proof per game" table stay there. Older notes
(2026-10-31 to 2026-11-05) are in git history; what they taught is in `docs/LESSONS.md`, "Ports and frame timing".

## Every port: the 144 cap

From "Capped at 144 by default":

- **Measured (build box A, 2026-11-05):** 144.3 shown a second where the old wait gave 128, p99 9.6 ms against 10.6 before and 7.2 uncapped, so capped is still worse than uncapped at p99 (the dated notes are in git history; lessons in `docs/LESSONS.md`).

## Several games

### What the wider view costs

What the wider view costs (headless, 2560x1600 output, thread-CPU clock, main laptop under agent load, so read the
differences, not the levels; `--aspect 4:3` against `--aspect fit`, which is 16:10 here):

| Game, replay section | Game code mean / p95 | Direct3D CPU mean | WORK mean / p95 | GPU mean / p95 | Over 16.7 ms |
|---|---|---|---|---|---|
| Power Stone 2, 20261031-122203 blanks 7582-7881, 4:3 | 4.47 / 6.14 | 0.21 | 7.40 / 9.63 | 0.32 / 0.82 | 0 of 300 |
| Power Stone 2, the same, fit | 4.72 / 7.47 | 0.22 | 7.85 / 11.29 | 0.39 / 0.75 | 0 of 300 |
| Toy Commander, 20261031-121005 blanks 19757-20057, 4:3 | 4.58 / 7.95 | 1.47 | 6.79 / 11.24 | 1.75 / 4.71 | 1 (the first blank: 217 ms, a load) |
| Toy Commander, the same, fit | 4.86 / 7.96 | 1.55 | 7.17 / 11.32 | 2.29 / 6.88 | 1 (the same) |

The wider view adds about 0.4 ms of work a blank, and on Toy Commander 0.5 ms of GPU time (2.2 ms at p95). Power Stone 2's
game-code time also rises by 0.25 ms; why was not investigated (it has no wider culling yet, so the picture differs only
in the renderer). Both stay under 16.7 ms, and both are above 6.94 ms at the 95th percentile (9.6 to 11.3 ms): the
wider view is not what keeps them from the target.

### Work per tick, `port-perf`'s final kit

Measured on the main laptop with other agents running, thread-CPU time, `port-perf`, final kit (work per tick: mean / 99th percentile / worst):

| Game | Section | Mean | p99 | Worst | Note |
|---|---|---|---|---|---|
| Power Stone 2 | vblanks 7582-7881 | 7.1 | 13.5 | 15 | was 11.4 mean; ARM7 driver 2.0 of it |
| Spawn | last 300 of replay 20261030-202531 | 8.2 | 13.4 | 280-300 | the ~0.3 s "hitch" is the proof's own PNG dump of a frame (`--dump-frames`, "frame done" 298-317 ms), not the game; see "Spikes" below |
| Toy Commander | last 300 of replay 20261031-121005 | 17.4 (game C at /O2: 15.7) | 26-30 | 78 | translated game code and `tc_call` |

All three fail 6.94 ms at the 99th percentile.

### Catch-up and the Saturn kit, corrected

`--catch-up` is the Dreamcast kit's: `pace_next_tick` has one caller, `runtime/host.c`. The Saturn kit keeps its own grid of deadlines
(`saturn/sat_sys.c`): a late blank keeps the grid, so a tick over a slot is already repaid, and a debt over one period is dropped (6 on the
Dreamcast kit). The option was parsed there and did nothing, so it is gone from the Saturn kit's list. Robo Pit, headless: 8000 blanks real
clock through the menu input, 7995 gaps under 25 ms at 50 Hz, no crash.

Dreamcast, headless, real clock, 2560x1464, audio off, on then off: Spawn 60.0 and 60.0 blanks a second, frame p99 19.13 and 19.15.
Toy Commander (mirror's game, its old `powf`, mission route) 57.5 and 42.9, frame p99 27.40 and 33.51. Fixed clock, on against off:
Spawn 28 frames and Toy Commander 16 frames identical.

### The look-ahead gate, corrected

The gate by mean gap alone failed Toy Commander's movies on a real window: catch-up lifts them over 40 blanks a second, the mean gap falls
under 25 ms, the look-ahead turns off and gaps of 50-130 ms underrun. It now also counts long gaps (`LONG_GAP`, over 2 slots): two inside
about a second are a slow scene too; one is a spike. Build box A, window 1280x960, `--present interp`, opening movies, 100 s, two runs each:

| Build | Blanks/s in the movies | Underruns | Silent |
|---|---|---|---|
| Before catch-up and gate (kit 7fcd815, game before `powf`) | 30-42 | 19, 5 | 679, 112 ms |
| Gate by mean alone, catch-up | 31-53 | 51, 27 | 826, 417 ms |
| Gate with long gaps, catch-up | 42-57 | 9, 5 | 132, 27 ms |

Power Stone 2's fight is unchanged by it: same build box A, window 1729x973, sound on, new gate with `--sq-fast` 58.7 and 60.0 blanks a second,
frame p99 28.69 and 21.91, against the old gate without it 59.6 and 56.3, frame p99 22.36 and 36.88. No underruns in the fight's windows;
look-ahead at its 33 ms floor. Build box A's level moved with other load (sound CPU 4.4-5.7 ms). Fixed clock: 234 frames and the sound dump identical.

## Power Stone 2

### Power Stone 2 against 6.94 ms: what still separates it

Main laptop, headless, thread-CPU time, 2560x1464, the fight (blanks 3000-5855 of replay 20261031-232451), three alternating runs each.

| ms per tick | Before | `--sq-fast` | p99 before | p99 `--sq-fast` |
|---|---|---|---|---|
| Game code | 5.91-6.16 | 4.24-4.42 | 9.92-10.26 | 6.92-7.10 |
| Sound CPU (ARM7) | 2.72-2.82 | 2.75-2.80 | 3.97-4.34 | 4.02-4.37 |
| TA parse | 0.71-0.74 | 0.71-0.73 | 1.26-1.30 | 1.25-1.28 |
| Direct3D calls | 0.30-0.31 | 0.29-0.30 | 0.42-0.46 | 0.42-0.44 |
| Whole tick | 9.82-10.20 | 8.15-8.42 | 15.32-15.63 | 12.36-12.62 |
| Ticks over 16.7 ms (of 2856) | 11-48 | 0 | | |

- **It still fails.** The whole tick is 12.4 ms at p99 against a 6.94 ms budget for a shown frame. Real clock on the laptop, headless,
  no Present: 60.0 blanks a second, work 7.53 and 8.72 mean, 12.79 and 13.52 p99, shown-frame p99 19.2 ms (the ticks' own 60 Hz).
- **What is left, by samples** (share of active samples): sound CPU 31.5%, scattered translated bodies (`_body`) 15.3%,
  `sh4_ftrv` and `fipr` 6%, the helpers' function 6.1% (was 21.5%), `tc_call` and its glue about 5%.
- **The next pieces.** The sound driver is the largest and has its own session. Even with it gone (about -2.5 ms) the tick stays
  near 10 ms at p99, so the tick must also leave the presenting thread (a tick on its own thread hands the parsed scene to a
  presenter): only that can show frames faster than the tick costs. Translated bodies and `sh4_ftrv` are next inside the game.
- **`--game-o2` on this game is not the answer.** 234 frames and the 17.2 MB sound dump identical, game code 6.01-6.05 against 5.98-6.33
  in the same rounds, about 2-3% and inside the spread. Left off here.
- **The store-queue change (`game.json sq_fast`, `build.py --sq-fast`, `tools/tx_sqfast.py`).** Off until a game turns it on; the
  judged C stays on disk and the judged helpers stay the answer key. Proofs: `tests/test_sq_fast.py` runs 400,000 random stores and
  sends through both versions (queues compared after each, RAM every 1024) and catches 5 planted faults. Power Stone 2: 234 frames +
  17.2 MB sound to blank 5855, and 132 frames + 193 MB sound through a whole session to blank 65600, all identical. Spawn: 120 frames +
  88.6 MB sound to blank 30100, identical; game code 4.24 to 3.95 ms, p99 6.36 to 5.71 (8 functions use them there). ~~Toy Commander has
  none.~~ Wrong, and the Spawn proof missed a case: see the next point.
- **Corrected 2026-11-03: a function whose mocked callees store into the queues.** Its judged C carries `src[2][32]`
  (`sqmodel.sq_callee_helpers`): the RAM byte each queue byte came from. The fast helpers dropped it, so a send wrote wrong bytes: Spawn
  with translator C for every function lost its main menu's highlight bar (replay 231339, 26 frames). Spawn has 3 such functions
  (8c057ec0, 8c058030, 8c058150), and the copy proven above had none of them. Toy Commander has 50 store-queue functions, 8 of them
  such, and its runs had `sq_fast` on both sides. Power Stone 2 has 3, none such, so its helpers are unchanged. The fix: those functions
  get fast helpers that keep `src`, and a block holding a callee byte is sent byte by byte as the judged helper does. `test_sq_fast`
  runs them too, and catches the old helpers and 3 planted faults. Whole replays, fixed clock, without `sq_fast` against fixed `sq_fast`:
  Spawn 231339 (2,866 frames), 225236 (29,771) and the intro (89,429 frames, 265 MB of sound): 0 differ, sound the same; the old
  helpers make 26 frames differ on 231339. Toy Commander `tc_user` (12,034 blanks, 9,307 frames): 0 differ, sound the same, but the
  old helpers match too, so this recording never reaches the case, and Toy Commander's 8 are covered only by `test_sq_fast` until a
  recording does. Build box A window (1729x973), median of 3 runs, game code and frame p99 in ms, without against fixed `sq_fast`:
  Spawn 231339 2.70/5.88 to 2.46/5.74, 225236 11.18/6.47 to 10.40/6.50, Toy Commander `tc_user` 1.71/14.32 to 1.48/14.08.

## Toy Commander

### Toy Commander against 6.94 ms: in-between frames

Build box A, real window, real clock, `--present interp`, scripted route of logos, menus, loading and flying in the training mission, 195 s, candidate
game 3e1b209, kit 9f2f72ce. Draw function against scene blending, two rounds each (round one / round two):

| 1280x960 | Draw function | Scene blending |
|---|---|---|
| Shown frames a second | 130.8 / 96.4 | 469.4 / 444.8 |
| Frame p50, ms | 4.64 / 9.83 | 1.96 / 2.09 |
| Frame p99, ms | 33.38 / 34.02 | 7.09 / 7.70 |
| One in-between frame, ms | 4.8-6.1 | 1.0 |

- **Where the time went.** One in-between frame on the draw function in the mission: copy 0.8, game draw 3.7, copy back 1.4 ms, and it runs the game's C. Copying guest
  RAM was over a third of it. The tick itself is cheap in flight: game code p99 3.2-3.7, TA 0.8-2.5, Direct3D 1.5 ms.
- **It still fails at larger windows.** 1729x973 (the largest window build box A gives): 340.8 / 344.9 shown a second, frame p99 10.37 / 10.48 ms. The longest gap per 5 s stays 7-18 ms.
- **What is left.** Tick frames: they are about 7% of shown frames, so the top of them sets p99. In-between frames can't fill a gap while the tick runs on the presenting thread, so the tick must leave it
  (see "Power Stone 2 against 6.94 ms"). Movies count too: they show 30 frames a second by design, and they are about 0.5% of shown frames here.
- **`--fps` does not help.** 180 and 240 at 1729x973: gaps stay 11-13 ms, shown 140-177 a second, and with fewer frames the movies alone push p99 to 33-36 ms.
- **The figure is not the target's.** This route flies only part of the training mission, and a window at 2560x1600 and the user's laptop are not measured.

#### In-between frames on their own thread (2026-11-02, `tc-present-thread`)

`--present-thread on` (game.json `present_thread`, `runtime/frame_thread.c`) draws and shows a blended scene's in-between frames on their own thread, so a long tick no longer takes time from them. It is off by default.

Live Toy Commander on the user's laptop, launcher path, no window, 2560x1600, 195 s flying route, `--present tick`: mission work p99 3.79 ms, game 2.65, one blank over 6.94 ms. Before the mission work p99 21.24 ms: 896 of 4799 blanks are over, nearly all game code at 16-18 ms in the opening movie ticks. **The shown-frame p99 on that laptop is not measured:** a run with no window has no `play summary`.

Build box A, real window, flying route, frame p99 from `play summary`:

| Window | Thread on (clean runs) | Thread off (clean runs) |
|---|---|---|
| 1729x973 | 5.22, 4.67, 5.20, 5.23, 4.84, 5.24, 5.46 | 10.98, 9.63, 9.96, 11.02 |
| 1280x960 | 4.28 | 27.54 (one run; the opening at about 50 fps) |

Proof: flying route to blank 14000 on a fixed clock, `--present-thread fixed`, twice on each of three builds, 29240 to 56475 in-between frames a run: 300 frames at 2560x1600 and the 41162944-byte WAV byte-identical to `--present tick`.

Not settled: 3 of 11 thread-on runs and 2 of 7 thread-off runs stalled for seconds, one of them for about 100 s, on a build box A where other jobs were using about 6 cores. The rates are alike, which points away from the thread but does not clear it, and the cause was not found. Stalled runs are left out of the table. The four thread-on runs with lock waits logged did not stall, so they say nothing about it; a stall now leaves `gfx: the game's thread waited` or `frame_extra: an in-between frame took` in the log if the lock or the draw is the cause. Movies stayed shown by the tick at 30 fps with this mode, now `--present-thread hold`; these figures are that mode's.

## Robo Pit

### Widescreen: game.json `wide_cull` stays off

Robo Pit's proof (2026-11-07, `.work/wide` in its project): 4:3 checks byte-identical to master in 445 --det shots, the save and the peeked words; with the chips widened and its culling not, --det at 16:9 gives the same save and words; a fight recorded on master in a real-clock headless run and replayed on master and on sat-wide at 21:9 and 16:10 gives, without `wide_cull`, the same save, energy words and SGL random word every blank and all 2000 shots' 4:3 middles the same; with it, the save and energy are the same but the random word parts from blank 6295 (21:9) or 6279 (16:10) and 321 or 388 middles differ (not a frame's lag): the game's own logic reads the culling, so it stays off.
