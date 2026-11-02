# Proposal: Original pacing for the port kit (design only; needs the user's yes)

## What it is

On the console a game slows down where a frame took longer than its vertical blank: the frame missed the blank and
was shown one (or more) blanks late, so the game's tick ran slower for that stretch. The port runs on a PC that never
misses, so those stretches play at full speed. "Original pacing" would wait the extra vertical blanks the console
waited, at the same moments, so a slow stretch plays as slowly as it did. "Enhanced pacing" is today's behaviour.

The user's frame-rate rule holds either way: the simulation runs at the original tick and the tick is never raised.
Original pacing only adds the console's missed blanks back; it never removes or shortens one. In-between frames
(`--present interp`) still draw while the tick waits, so the picture stays smooth while the game slows.

## Where the switch sits

- `--pacing original|enhanced`, parsed beside `--present` in `runtime/frame_extra.c`'s option handling (or a small new
  `runtime/pacing.c` if frame_extra.c has no room under its 400 lines); the menu's Display entry "Original slowdown"
  and settings.txt `pacing`, as the other enhancements are wired (`base/host_menu.cpp`, `base/host_settings.cpp`).
- Default `enhanced` (no change for anyone). Check runs (headless or `--fixed-clock`) always run `enhanced`: lockstep
  already takes each frame's length from the emulator, and goldens must not move.
- One row in `port/CONTRACT.md`'s enhancement table and a contract minor version (1.1.0).
- The wait itself goes where the port waits for its vertical blank (`port_wait_vblank` in `runtime/host.c`): for a
  tick the profile marks as slow, wait the extra blanks the profile gives before the tick goes on.

## The cost profile: measured from Flycast, never typed in

The kit already records what is needed. `tools/lockstep.py emu` runs Flycast with `lockstep_emu.lua`; with game.json
`lockstep_sample_pcs` it writes a steps file (`LS_STEPS`) with one line per game frame: `label frames ms msrem step
gametime paused`. `frames` there is the number of vertical blanks that game frame took on the console. That is the
cost profile, read off the emulator, frame by frame.

1. **Record.** For each scripted run a game already has (its `tests/*.script` inputs, or a play recording replayed
   in Flycast), run `lockstep.py emu` once. Flycast is deterministic for the same input, and lockstep already keeps
   one recording per input, so nothing new is run for a script lockstep has already recorded.
2. **Reduce.** A new small tool, `port/tools/pacing_profile.py` (nearest existing: `lockstep_frames.py`, whose
   `build_frames` already reads the steps file; it pairs frames and does not keep their lengths), turns the steps
   into a profile: for each frame whose `frames` is more than the game's normal interval (1 at 60 Hz, 2 at 30 Hz, as
   Kamui's vsync interval sets it), the extra blanks, keyed by something the port can see at run time.
3. **The key.** Two options, the cheaper first:
   - *Per-scene cost model.* hw_check already digests each render's lists (vertex and polygon counts per list,
     `tools/ta_digest.py`). The tool fits, per game, the smallest list size above which the console missed its blank
     (a threshold, or a few thresholds for 2 or 3 blanks), from the recorded pairs (list size, blanks taken). The
     port counts the same lists it renders (`pvr/ta_parse.c` already walks them) and adds the blanks the model gives.
     It works for live play, not only for replays. Its error is measured, not assumed: the tool replays the
     recording's lists through the model and reports how many slow frames it gets right and wrong.
   - *Exact replay table.* For a recorded input only: frame label -> extra blanks. Exact, but useless once the player
     does something the recording did not. Kept as the check of the model above.
4. **Store.** The profile is a small text file in the game's folder (`pacing.txt`, written by the tool, with the
   recording's commit and input in its header). It holds counts and thresholds only, no game data. A game without
   one has no Original pacing (the menu entry is greyed), as the texture pack's is without a pack.

Costs other than the GPU's (the CPU's game logic, disc loads) also miss blanks on the console. The steps file sees
those too (their `frames` is above the interval); the model gives them no list size, so they show up as the model's
misses and are reported. Loads are already timed by the port's own disc reads and are left alone.

## Checks

- A unit test of `pacing_profile.py` on a synthetic steps file (known slow frames in, the right profile out).
- A runtime test that `--pacing original` on the fixed clock adds exactly the profile's blanks (port_vblank_count).
- `tests/test_port_contract.py`: the new enhancement is in the table and off in check runs.
- One named cross-check per game: `tools/rate_compare.py` style, the port's slow stretch next to Flycast's capture
  (`emu_capture.py`), the user watching both.

## Cost

- Recording: one `lockstep.py emu` run per script not yet recorded (minutes each; none for scripts already recorded).
- Code: the tool (~200 lines), the runtime wait and option (~80 lines), menu and settings wiring (~20), tests (~150).
  About one working session.
- Per game: one profile run and a look at its report; a game turns it on by having `pacing.txt`.
- Risk: the threshold model can slow a frame the console did not, or miss one it did; the report gives the rate, and
  the default stays `enhanced`.

## What nativeshvc does differently (added 2026-10-30)

It charges time per bus access, by kind, with weights fitted from the original's access counts; for the Dreamcast
that is the wrong unit (GPU, list size and disc loads dominate, and the kit has no per-access hook), so this design
keeps the frame as its unit. What carries over is how the profile is kept honest: a versioned `pacing.txt` header
(reference build, recording commit and input hash) with a stale check, a fit report (bias, mean error, 95th
percentile, the slowest 5% of frames) with a limit above which the profile is refused, fitting only on frames whose
cost was real (disc-load frames left out), and the frame rate used only to check the result. See
`docs/proposals/kit_nativeshvc_ideas.md` idea 1; fold the four rules into "The cost profile" above when this is built.

Not built. Waiting on the user's yes.
