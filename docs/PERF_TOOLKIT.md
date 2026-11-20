# The performance toolkit: what to swap to reach 144 fps

The user (2026-11-12): "the process on all games, is unguided, and taking too many attempts to fix performance. When we
should know exactly what to swap out". One tool, the same for every game and console kit, measures a build against the
bar in `docs/RUNTIME_TARGET.md` and names the code that keeps it under. Measure first with this, then rewrite what it
ranks; confirm each swap with its A/B benchmark.

## One command

On the machine that runs the job (build box A or build box B; the user's laptop only for the one confirming run):

```
py -3.12 port/tools/perf.py all --name Spawn --a BUILD_DIR [--b OTHER_BUILD_DIR] --replay REC.ptrec --stop-at N
    [--fast-until V] --out OUT_DIR [--log-dir GAME_LOG_DIR] -- <the game's own arguments>
```

- `BUILD_DIR` holds `port.exe` and the `port.map` its link step writes. No special build: the profiler is in every
  build, off.
- `--replay` is a recording of the heaviest section (`.ptrec`, `base/session.c`); `--stop-at` ends the run;
  `--fast-until V` runs at full speed to blank V and times from there.
- After `--` come the arguments the game always needs (its disc, save folder).
- Steps can run alone: `bench`, `profile`, `report` (the last from files kept with `--keep`).
- A whole run for one build is 4 benchmark runs, 2 at a small output size, 3 profile runs, one pass for graphics card
  time and one counting pass over the replay.

It writes `perf-<date>.md` (to read) and `perf-<date>.json` (for tools) to `OUT_DIR` and to the game's log folder.

## Scenarios: a game's standard benchmarks

The user (2026-11-12): "Can we add templates per game, that can be created from my previous reports, as they contain my
inputs, so you can quickly get into gameplay and have a consistent benchmark". A scenario is a named stretch of one of
the user's own recordings of that port: `front-end` first (boot, logos, title, main menu, ending five seconds into the
menu), then gameplay ones (first stage, 4-player fight, 8-fighter fight).

```
py -3.12 port/tools/perf.py template --name Spawn --scenario front-end --a BUILD_DIR --replay REC.ptrec
    --end-at-log "main menu" --shows "boot, logos, title, main menu" --template-out GAME/perf --out OUT_DIR -- <game arguments>
py -3.12 port/tools/perf.py all --template GAME/perf/front-end.json --a BUILD_DIR --out OUT_DIR -- <game arguments>
```

- `template` writes `GAME/perf/<scenario>.json` and a copy of the recording beside it: what it shows, the recording's
  hash, the build it was made on, the vertical blanks timing starts (`--fast-until`) and ends at, the settings that
  change timing (`--setting`), and a hash of the picture every 600 blanks. Where a scenario ends comes from the game's
  own log (`--end-at-log`), or from `--stop-at`.
- **It is checked before it is timed.** Every use replays the template once on the fixed clock with no window. A build
  that no longer follows the recording is refused, with the pad read and vertical blank where it diverged: its numbers
  would be of a different stretch of play. Cut the scenario again from a recording made on that build. A build that
  follows it but draws a different picture at a checkpoint is reported in the notes and still timed (`--strict` refuses).
  `perf.py check --template ...` does only the check.
- Templates and their recordings stay in the game's own folder, private (a recording can hold the save files its run
  read), and are listed in the game's `LOCATIONS.md`. Only recordings of the user's own play of these ports are used.

## What it measures, and how

| Level | How | Cost |
|---|---|---|
| Benchmark | Each shown frame's make time and the gap before it (`--play-frames`, from `base/play_stats.c`, the same numbers as the log's `play verdict` line). Builds run interleaved, A B B A A B (A A A A for one build) | One row in memory per shown frame |
| Layers | Each sample carries the kit's section (`runtime/prof.h`: wait, sound tick, ARM7, TA parse, Direct3D, present, ...) and the thread | None beyond the sample |
| Functions | A sampler (`base/perf.c`, `--perf-sample 500`) holds the game thread and the thread showing frames every 500 microseconds and records where each is, plus the game's call stack by original address (kept by `tc_call`). `port.map` turns that into a name, the original address and a tier: the object file says whether it is translated C, readable C, the kit, the Core or native | Stated in every report: make time with the sampler on against the benchmark runs, and the time the threads were held |
| Processor, graphics or waiting | Each sample is one of: the processor's (in the port's code, or blocked on a file read, lock or worker), the graphics driver's, or waiting (present, frame pacing). Two more runs at `--small-size` show whether make time goes with the output size; one pass with the kit's timer queries (`--prof on --prof-csv`) gives graphics card time per blank where the kit has them | Not in the timed runs: the query pass flushes each frame, so only its graphics column is read |
| Call counts | A separate fixed-clock pass (`--prof on --prof-calls FILE`), so counting never changes a timed run | Not in timed runs |

Sampling, not instrumentation: timing every guest call would add two clock reads to tens of thousands of calls a tick
and measure itself; the function-entry hook (`/Gh`) is taken by `PORT_STACK_ZERO`; and sampling needs no rebuild, so
the exe that is measured is the exe that plays.

**Off by default.** Off, the cost is one test of a flag per call by guest address and one per shown frame. Proof for a
change to this code: a replay with 0 differing frames against the build before it (`port/tools/replay_compare.py`)
and an interleaved benchmark pair inside the noise (`--a OLD --b NEW`).

## Reading the report

1. **Benchmark.** Median, p99 and worst make time and late frames per 1,000, per build, with the run-to-run spread.
   A difference between builds is called real only when it is larger than the spread. On a machine that is not the
   user's laptop the numbers compare builds and rank costs; they are never a pass or fail.
0. **The frames just over the bar** (6.67 to 8 ms) come first when there are any: they decide a p99 that sits near the
   bar, and the table lists what runs longer in them than in frames within it.
   **One-off work** inside the measured window is counted and its frames marked: shader compiles, first-use texture
   or pack loads, file opens, wherever the kit logs them. The user (2026-11-12): "We should compile these before play
   so it doesnt muddy the results of our benchmark". The numbers are given with and without those frames, and a
   shader compile during play is named at the top of the report as a fault of the build. A kit reports such work with
   a game-log line `once: <kind> <ms> ms <what>` (kinds: `shader compile`, `first-use load`, `file open`, or its own);
   lines kits already write are matched by `ONCE_KINDS` in `perf_report.py`.
2. **Where the time goes.** Frames are split into those made within 6.67 ms, those just over (to 8 ms) and those far
   over, and every cost is given for each: the right-hand column is what was extra in the frames that missed.
   - Processor, graphics or waiting, for the whole run and for each of the worst frames.
   - What the waiting waits on: present or the display, frame pacing, a file read, a lock, a worker thread, by the
     function that made the call.
   - By thread and layer: game logic (translated and readable functions and the game's native code), kit (emulated
     hardware and the translated code's runtime), Core (present, input, audio out, libraries), waiting.
   - By kit section, with the extra each section costs in frames over the bar.
   - Functions: self time (its own code), inclusive time (with what it calls by original address), exact calls a
     tick, and the sample counts behind the numbers.
   - The worst frames: what was running in each, and the game log's lines just before it (screen, menu, loads).
   - Where in the replay the frames over the bar cluster.
3. **What to swap, in order.** For each cost: what it is, its cost per frame in the frames over the bar, its share of
   the frame's work, the cost against the cut the p99 frame needs, an estimated gain and how sure the number is
   (from its sample count). "Bar reached by here" marks where the mid estimates add up to the cut needed.
   "Which rule applies" says how: move it off the frame, cache it, or rewrite it natively with the translated route
   kept selectable as the answer key (`docs/RUNTIME_TARGET.md`).

## Why a frame was late: one segment, every late frame named

The user (2026-11-19): "a benchmarking tool that will highlight what exactly is going wrong". `perf.py all` ranks what
to swap over a replay; `perf.py segment` takes one timed run of a stretch (a save state, or a replay from a blank) and
puts every late frame and every long tick down to its cause.

```
py -3.12 port/tools/perf.py segment --name "Dragon Force map" --a BUILD_DIR --exe game.exe --out OUT_DIR
    --state map9450.state [--state-at 120] [--slot 9] [--warmup 600] [--sample-us 500] [--symbols SYMS.txt]
    [--signed-in] -- <the game's own arguments: --image, --disc, --fallback, --frames N, --input FILE>
py -3.12 port/tools/perf.py segment --report OUT_DIR [--a BUILD_DIR --exe game.exe --symbols SYMS.txt]
```

- The run loads the state at `--state-at` and measures from `--warmup` blanks after the load (`--perf-from load+N`, a
  base option: the game log's play summary and verdict restart there too, so the load's own frames are left out). With
  `--replay REC --from V` it measures a replay from blank V.
- `--signed-in` starts the run as a task in the machine's signed-in session: a window over ssh needs it (build box A).
- On the user's laptop it runs only as `MAIN_LAPTOP_OK=final-timing`, once per candidate build.
- It keeps the frame rows (`run.frames.csv`: gap, make, tick, draw, present, wait to show), the tick rows
  (`run.ticks.csv`: the kit's parts; Saturn: work, game, the pacing wait, the game polling a flag, VDP2 and compose,
  VDP1, audio, show) and the samples (deleted after the report unless `--keep`).
- A late frame's cause is what took the game's thread most time in its gap, the pacing wait left out (the kit's
  in-between frames are on time); with nothing there, the thread that shows frames; with no samples, the tick's largest
  part. Causes: the game's code (translated or interpreted, the function from `--symbols` or the guest stack), a wait
  (the game polling a flag), a kit part (VDP1, VDP2, audio, the disc), or the system (a blocked call by its caller, busy
  in the kernel, or a thread Windows did not run).
- The report (`late-<time>.md`, `.json`, and `.html` with the frame-time chart, late frames coloured by cause) gives the
  verdict, the causes worst first with counts, the functions in the late windows, and the share of the game thread that
  was work, waiting or the system's. Off the user's laptop the verdict is for comparing builds only.
- The trace costs nothing without `--play-frames`; with it, a row per frame and per tick in a ring (30 minutes).

## Limits, stated

- **Gains are estimates.** The ranges come from past swaps (in `perf_report.py`, `GAINS`). The cost is measured; what a
  rewrite gives back is known only after its A/B benchmark.
- **Inclusive time follows calls by original address.** Readable functions calling each other directly show as each
  one's self time; the kit's own functions have self time only.
- **Functions the linker folded together** (identical code) are one row, marked with how many share it.
- **A sample in a system DLL** (a driver call, a file read, a wait) is given to the function in the exe that called
  it: the nearest stack word that is a return address after a real call in the exe's code. On 32-bit builds without
  frame pointers that is a scan, not an unwind; the planted function is the check that it names the right caller.
- **Time in the kernel on a 32-bit build has no caller.** While a 32-bit thread is busy inside a system call, Windows
  only has the context of its last blocking call, so its stack would name the wrong function. Such samples are rows
  "(in the kernel, caller not known; under <game function>)", placed by section and the game's call stack. A blocked
  thread (a sleep, a wait on present or a lock) is named correctly. 64-bit builds are not affected.
- **The machine's state is on the report's second line** (processor, power plan, mains or battery, clock speed after
  each run). A laptop on battery or a saving plan clocks down; numbers from two days compare only where these match.
- **Rows marked [caller uncertain] are never in the swap list.** They are samples of a thread busy inside the system (not
  blocked, not waiting), where the caller's name cannot be trusted on a 32-bit build. The Core's own system calls are
  measured by named timers instead (`perf_timer_begin` / `perf_timer_end` in `base/perf.h`: pacing sleep, present, the
  wait to show, save file read, audio put, and both plants), written to the log as `perf: timer ...` lines and shown in
  the report per tick. A kit adds a timer where it makes a system call of its own in the frame: its ids are
  `PERF_T_KIT + n`, named once by the kit with `perf_kit_timers` (the Dreamcast renderer's `PVR_T_*` in `pvr/pvr_int.h`:
  textures, batch and constant upload, vertex upload, clears, pipeline bind, batch states, draw calls, modifier volume and
  autosort passes, the graphics lock wait, `pvr_seen`). Each timer is also split by the shown frame it fell in, within
  the bar or over it, and the report gives both per frame.
- **Sampled sizes can read high on a busy machine.** On build box A a planted 3.0 ms function read 3.04 ms against 3.01 ms by
  its own named timer (2026-11-13, f5e69e3d), but 3.83 ms in an earlier run with a p99 twice as long; that run is not
  explained. The plant has a named timer: compare the two in a `--plant` run.
- **Present and the wait to show are timed only in a window.** Timed runs use `--no-window` (`base/present_device.c`:
  no swap chain then), so `show_inner` presents nothing and the show thread never waits for the display: Spawn's log
  had no `present` or `wait to show` line for that reason (2026-11-13). `perf.py --window WxH` times them in a real
  window; over ssh a window cannot show, so on build box A that run is started from the signed-in session (scheduled task).
- **Timed runs run above normal priority, as the launcher's play run does** (`base/host_main.cpp`; a run with
  `--fixed-clock` is scripted and would not set it). Below normal, a shared machine's other jobs took the game's
  threads: Spawn's front end on build box A read make p99 5.84 ms in one run and 14-90 ms in others (2026-11-13). Each run
  also records the other processes' share of the machine while it ran; the report lists it.
- **`--fast-until` makes a timed run a scripted one.** `--fixed-clock 1` gets none of the play run's defaults
  (`base/host_main.cpp`), and a Game menu entry's default is never on with a fixed clock (`runtime/console_dc.c`): Power
  Stone 2's clear HUD (its texture pack's `if=game.clear_hud` lines) was off, so the pack loaded 0 images (2026-11-13).
  `--from-boot` plays the whole replay on the real clock as a play run, as the launcher does, and times from
  `--fast-until` (`--perf-from`). A replay that goes out of step on the real clock before that start needs the old way.
- **Frames the kit marks as a loading screen are left out** (over 256 KB read from the disc in half a second,
  `base/play_stats.h`). Toy Commander's front end is marked so throughout (5,362 of 5,380 frames, 2026-11-13): time
  such a stretch with `--count-loading`, which the report states.
- **The sound device is opened in timed runs** (`--audio on`): a no-window run left it closed (`gfx_audio.c`
  `audio_start`), so `audio put` had no line. Mixing ahead of the clock is off (`--audio-ahead off`): the replay already
  mixes what the recorded run mixed ahead, and more would change what the game sees.
- **Timed runs use the launcher's presenting** (`--present auto`, added unless the game arguments set `--present`): a
  scripted run gets no play defaults, and without it a game with in-between frames is timed at its tick rate.
- **One sample stands for about half a millisecond**, so a single frame's list is coarse; the profile runs are pooled
  by tick, which a replay makes line up. Rows with few samples say "low".
- **Sections exist only where a kit names them** (`perf_sections`): the Dreamcast kit does. A kit without them still
  gets the function, tier and layer split from the map.
- **Graphics card time is indirect.** A frame held up by the card shows as waiting on present, not as a function. The
  output size check and the timer-query pass say whether the card is in the frame; neither is read from a timed run.
- **What a wait waits on is read from the caller's name** (`perf_report.py`, `WAIT_KINDS`); a call that fits no
  pattern is "a system call" under its caller.
- **A replay only measures what it plays.** Record the heaviest section; a cost outside it is not in the report.
- Saturn builds write `game.map` beside `game.exe` (`port/tools/build_saturn.py`, `/MAP`, from 2026-11-19); older
  builds name kit and system functions by offset only.

## Checking the tool itself

- `--plant US` puts a planted slow function (`perf_planted_slow_function`, US microseconds a tick) into build A's
  runs. The report must show it at the top with about that cost. `tests/test_perf_report.py` does the same on made-up
  `--plant-sys US` plants the same time inside system calls (`perf_planted_system_call`): the report must show it
  under that name or as kernel time with no caller, never under another function. On Spawn the first plant was a spin
  on the clock read and three reports in a row gave it to the game's wait stub; that is how the kernel limit above
  was found.
  runs, and checks that a cost that is the same in every frame is not blamed and that a difference inside the noise
  is not called real.
- A prediction is checked by doing it: bypass or stub the top item in a test build and benchmark `--a BEFORE --b AFTER`.

## Files

| File | Role |
|---|---|
| `port/base/perf.c`, `perf.h` | Sampler, frame rows, the plant; options through `play_stats_option`, so every console has them |
| `port/runtime/prof.c` | Hands its section to the sampler; `prof_calls.c` writes `--prof-calls` |
| `port/runtime/tx/tx_rt.c` | The call stack by original address (`perf_push`, `perf_pop` in `call`) |
| `port/tools/perf.py` | The driver: bench, profile, report, template, check, segment |
| `port/tools/perf_late.py` | `perf.py segment`: one segment's late frames and long ticks by cause, the chart |
| `port/base/perf_trace.c` | The frame and tick rows (rings), `--perf-from load+N` |
| `port/saturn/sat_perf.c` | The Saturn kit's tick parts, its sections and guest stack for the sampler |
| `port/tools/perf_template.py` | Scenario files: cut from a recording, checked in step before timing |
| `port/tools/perf_report.py`, `perf_map.py`, `perf_text.py` | The analysis, the map reader (name, address, tier, layer), the page |

It replaces `runtime/prof_sample.c` (`--prof-sample`, `--prof-samples` still work and now write the new file) and the
per-game timing drivers in the games' `.work` folders.
