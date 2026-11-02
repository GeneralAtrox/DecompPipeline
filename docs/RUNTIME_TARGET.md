# Runtime target: 144 fps at 2560x1600, no emulated hardware in the play path

The user's rule (2026-10-31), for every port on every console, now and in future: emulated hardware does not match
the target of 144 fps at 2.5k. "Ideally we prove out how it needs to work, then swap it to a modern solution."

## The target

Measured on the user's laptop (2560x1600 panel, RTX 5080 Laptop GPU), through the launcher, on the game's heaviest section.

| Number | Target |
|---|---|
| Shown frames | 144 a second, at the panel's full 2560x1600, game world filling the screen (see "Any aspect ratio"; the kit renders at output size) |
| One shown frame | 6.94 ms, all of it, 99th percentile |
| A game tick and its draw, on the thread that presents | 6.94 ms, 99th percentile; otherwise a shown frame is late on every tick. The other way to meet it is a design where the tick no longer blocks presenting |
| Below 144 fps | Only on a loading screen. Menus, movies, fights, scene changes and busy scenes are not exceptions, and sound never starves |

The user (2026-10-31): "The 99% frame should be 144fps. There is no valid reason why, except a loading screen, we
should be getting less." So the 99th percentile is measured with only loading screens left out, and a run with sustained
drops elsewhere fails even if its percentile passes.

## Which games, and what stops for it

Every native port: Spawn, Power Stone 2, Toy Commander, Robo Pit, Vigilante 8 when its port starts, and every new project.

The user accepts pausing native production while this gets fixed. For a game below the target:
- **Pauses:** new gameplay work, tweaks, new native features and playtest requests for that game.
- **Continues:** performance work, the front end's fixes, and a problem the user asks about directly. Work already in
  flight finishes.
- **Does not pause:** raw C, readable C and translators. They don't touch the play path until a port adopts them, and
  performance work needs them.

A game with no port yet starts with this budget, not with a plan to fix it later.

The game's simulation keeps its original tick (30 or 60 Hz): that is behaviour, and the judge and goldens depend on it.
The frames between ticks come from in-between frames (`runtime/frame_extra.c`, `--present interp`), which need the
game's draw function registered (`port_set_extra_draw`), or, for a game with none, the renderer's scene blending
(`--interp-scene on`, `pvr/pvr_blend.c`; see "Shown frames" at the end). A game showing only its ticks does not meet the
target.

## Any aspect ratio

The user's rule (2026-10-31), alongside the frame rate, for every port and new project: "all projects should support
widescreen and not be hardlocked to 4:3. It should support any aspect ratio."

- **The game world fills the window, at any shape.** 16:10, 16:9, 21:9 and wider, and shapes narrower than 4:3. A wider
  window shows more to the sides, and a narrower one shows more above and below. The picture is never cropped to fit, and
  there is no cap at 16:9.
- **Nothing is stretched.** 2D art, text and menus keep their proportions. Art with more picture available extends to fill
  the window. Where the game has no more art, it stays centred and the report names that screen.
- **The player chooses, in the options.** The user (2026-10-31): "We should have options for aspect ratio. 4:3, 16:9 and
  21:9." Every port's options offer 4:3, 16:9, 21:9 and Fit window. The choice takes effect at once and stays saved. A fixed
  choice that doesn't match the window's shape shows at its own shape with bars, never stretched.
- **Fit window is the default.** Play runs follow the window's shape, which is how other shapes stay supported. It is a
  deliberate switched upgrade, so checks and goldens stay at the original shape.
- **Gameplay does not change.** Wider views may only change what is drawn. Anything the game decides from what is on
  screen (enemies waking, collision, AI, sound) keeps its original result, proven by replay with the wide view on and off.
  Objects the game culled at the original screen edge appear in the new space; none pop in or out at the edges.
- **The frame rate target holds at every shape.** More of the world means more to draw, so it is measured at the panel's
  full width and at the widest shape tested, on the same section.
- **Proof per game:** frames from replays at 4:3, 16:10, 16:9, 21:9, 32:9 and one narrower than 4:3, covering the front end,
  a movie and play. Each is checked by eye for missing objects, stretched art and wrong edges.

### Aspect-ratio proof per game

The scorecard's source for gate 6's "any aspect ratio, on by default" check (`tools/scorecard_runtime.py`). A console's
reference game is green only with a row here that lists all six shapes (4:3, 16:10, 16:9, 21:9, 32:9, narrower), says Yes
for default on and for gameplay identical, and names evidence paths in this checkout that exist (the proof script's output
and the frames checked by eye). A row that falls short is red, no row reads unmeasured.

| Game | Shapes proven | Default on | Gameplay identical (replay, wide on and off) | Evidence |
|---|---|---|---|---|

Where the kit stands (Dreamcast renderer; the Saturn kit has none yet): the frame takes any shape. `--aspect
fit|4:3|16:9|21:9|W:H` (the Display > Aspect menu: Fit window, 4:3, 16:9, 21:9, applied at once and saved as `aspect`)
chooses it; a fixed shape that is not the window's shows at its own shape with bars, never stretched, and Fit window
(the default for a game with game.json `widescreen`) follows the window at any shape, no cap. `port_widescreen()` and
`port_tallscreen()` give the frame's width and height over the 640x480 screen's (each 1 or more, never both above 1): a
game widens its culling by them, horizontally and vertically (a narrower than 4:3 frame shows more above and below).
2D stays 4:3 in the middle; an untextured full-screen triangle (a fade) covers the frame; scissors and clears,
frame-buffer reads (the game gets the 640x480 screen) and movies sit on the screen. A game without the key stays 4:3
until its card wraps its culling and sets it (a wider frame would show the holes the game's own culling leaves). Only
Toy Commander sets it. Checks and goldens stay 4:3 unless `--aspect` is given (`tests/test_gfx_aspect.py` is the shape
maths; PS2 at 4:3 is byte-identical to before in the front end's 52 dump frames, and TC's frame 10645 of replay 20261031-121005 too). In-between frames draw into
the same target at every shape.

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

## Modern controllers: every button can be used

The user's rule (2026-10-31), for every port and new project: "we should support more buttons. Older consoles had much more
primitive game pads." On an Xbox controller that means LB, RB, left and right stick click, Back and Guide.

- **Every button and the right stick can be assigned.** The kit reads everything the controller has, not only the original
  console's buttons. Any of them can take any action the game's controls offer. Back and Guide are included; Windows or
  other software can take Guide before the game sees it, so report it if it can't be read, and don't change OS settings.
- **Where the player sets it:** a game with its own key screen (Spawn's KEY CONFIGURATION) gets that screen extended, with
  the new rows named for the connected pad, in the game's own look. A game without one gets a Controls screen in the kit's
  host menu. Every entry is reachable with keyboard and controller, and takes effect at once.
- **It stays saved, apart from the game's save.** Extra assignments live in the port's settings, per player. The game's own
  save data keeps its original format, so saves stay valid.
- **The original layout doesn't change.** New buttons start unassigned unless the user decides otherwise, so a run that
  touches none of them plays as before. Replays and checks stay byte-identical, which is the proof. Recordings capture
  the extra buttons, so a replay reproduces them.
- **Proof per game:** scripted input (test runs read no real input) assigns each new button, presses it and shows the action
  happen in the game log, then shows the assignment after a restart.

Where the kit stands: `base/input.c` reads A, B, X, Y, Start, the D-pad, the left stick and the two triggers. LB,
RB, the stick clicks, Back, Guide and the right stick are ignored.

## What the rule says

1. **Emulated hardware is a reference, not the product.** A hardware model may be used to bring a game up, and stays in
   the kit as the answer key for tests. It does not stay in a game's default play path once the game's front end works.
2. **New work does not add to the play path's emulation.** A new kit feature or console kit says how it will meet the
   budget. A slow model added for bring-up gets a row in the table below in the same change.
3. **Every swap is proven, in this order.** It is a behaviour-preserving step first and an improvement second:
   - **Record:** the model's inputs and outputs at its boundary, on replays that cover the section's real use.
   - **Replace:** prefer translating the original code or generating faster code with the same behaviour. A hand-written
     replacement is the fallback, written from the recordings.
   - **Shadow:** a test build runs both on the same inputs and stops at the first difference. Zero differences across
     the reference sessions; planted faults are caught, which shows the comparison works.
   - **Switch per game** (`game.json`), with the model still selectable.
   - **Then modernise:** each deliberate change (threads, deeper buffers, resampling, SIMD) lands alone, behind its own
     switch, with what changed stated.
4. **The judge is not part of this.** Checks keep their flags and their fixed clock. Performance work never weakens or
   skips one. A shipped build whose flags differ from the judge's is itself a swap, and proves itself the same way.
5. **Every port's log records the numbers.** Each play session's game log ends with shown fps, output size, and tick,
   draw and shown-frame times (median, 99th percentile, worst), so the user's own launcher runs are the measurement.
   The line is `play summary: shown_fps F output WxH frames N tick_ms M/P/X draw_ms M/P/X frame_ms M/P/X` (milliseconds);
   the scorecard's gate 6 reads it (`tools/scorecard_runtime.py`).
6. **Report against the target.** A performance report names the game, the section and these numbers, and says how much
   of the number comes from each row below. Agent load changes wall time by up to 2x: use per-thread CPU time or a quiet
   machine for comparisons, and only the user's laptop for the final number.

The front end still comes first (global `CLAUDE.md`): boot, logos, title, main menu, and they must also run at this target.
A front end that passes while below 144 fps is reported as failing this rule, by name.

## New console kits start with no hardware model

The user (2026-10-31), on the Xbox 360 kit: "How do we ensure we dont implement unneccessary parts? Like emulation." Dreamcast
and Saturn kits began with hardware models that now have to be swapped out, one proof at a time. A new kit skips that:

1. **Cut at the highest boundary the game's code allows.** System imports become real host calls. A graphics or sound library
   linked into the game is replaced at its function calls and never translated, so nothing reaches a chip model. Offline
   conversion of data the game ships (shaders, audio formats) is allowed: it is a translator, and runs before play.
2. **No caller, no code.** A kit part is written only when a run's log shows the game reaching it. Everything else is a stub that
   logs its name and stops, so the next part to write comes from the game, in front-end order.
3. **The kit's table names every part:** what calls it, the first log or function that needed it, what it uses on the host and its
   share of the budget. A part without a caller is deleted.
4. **Each kit lists what it must not contain.** Needing one of those parts stops the work and goes to the user with the evidence;
   it does not go in as bring-up. A test fails if the kit's source contains the listed names.
5. **The answer key is outside the kit:** the judged functions and captures from the original, or from an existing emulator run
   apart from the port. The emulated-layers table starts empty for this kit and gate 6 reads it.

Xbox 360's list: Xenos register file, PM4 command stream parser, eDRAM model, shader microcode interpreter at play time, Xenon
hardware-thread scheduler, XMA hardware model, kernel object emulation where a host object does the job.

## What native code must match

The user's rule (2026-10-31): going from raw C to readable C to native code, native code matches the behaviour while
keeping maximum performance. It matches and proves it; "close enough" is not a result. Maximum performance means the
target above is the minimum, and work on a game continues until it meets it.

| Must match, and is proven | Free to change for speed |
|---|---|
| Game logic results: physics, AI, damage, random numbers, game state | How memory is read and written |
| Game tick rate and timing | Sound and graphics chip models, and register writes |
| What the player sees and hears, apart from deliberate switched upgrades | How work is split across threads, compiler settings, 32-bit or 64-bit |
| The original's quirks and bugs that affect play | Data layout, once the game's code no longer depends on it |

- **Raw C and readable C do not change.** Each function is judged exactly against the original; that proof is what makes
  the native stage safe.
- **Native is proven on the whole game.** Replays and side-by-side runs give the same game state, picture and sound.
  Any difference is deliberate and listed, or it is a bug.
- **Data layout comes last.** Games treat memory as fixed console addresses, so moving data needs proof that nothing
  still relies on the old address.
- **Floating-point settings are a swap too.** Faster settings can change results slightly, and small differences build
  up into different gameplay. They need the same proof, and some may have to stay strict.
- **A replay only proves what it plays.** Reports name what the replays cover, and any part they miss is reported as
  unproven.

## Emulated layers in the play path

Update a row in the change that alters it. "Not measured" stays until someone measures it.

| Layer | Where | Cost measured | Status |
|---|---|---|---|
| Sound CPU: ARM7 interpreter running the game's own driver | `runtime/arm7.c`, `arm7_ls.c`, `aica_arm.c` | Power Stone 2: median 2.25 ms per tick in the heavy section (211k ARM instructions per tick, 99% in idle flag scans; Spawn the same: the two games run one byte-identical driver, AICAsoundDrv 1.76). The 40–79 ms spikes are not extra ARM work (same instruction count in those ticks; not reproduced in traced replays), likely host contention | Being replaced (card `arm7-native`). Step one done: `runtime/arm7_scan.c` runs the driver's three idle scan loops a whole number of iterations at a time (switch `DP_ARM_FAST=1`, interpreter stays the default until the per-game switch): Power Stone 2 heavy section median 2.26 → 1.02 ms per tick, p99 3.27 → 2.32 (whole session median 2.37 → 1.11), proven byte-identical on the ARM register-write log, a per-tick digest of all ARM state and 2 MB sound RAM, and the mixed audio, on 4 replays (PS2 ×3, Spawn); planted faults are caught. Step two done: `tools/arm7_translate.py` turns the driver's executed code (from the recorder, `DP_ARM_PCS`) into one C function per basic block (`runtime/arm7_native.h`, chained, the interpreter's own cycle and instruction counts, bus accesses and self-modifying stores handled by returning to the interpreter; blocks validate their words on every entry). Switch `DP_ARM_NATIVE=1` (game.json `aica_arm_native`); the interpreter runs whatever the table does not cover. Power Stone 2 heavy section (vblanks 7582–7881, no log, no audio dump): interpreter median 2.28 / p99 4.92 ms, scan skip 0.92 / 2.16, native 0.68 / 1.84 (whole session 2.22 → 1.03 → 0.76). Byte-identical ARM write log, per-tick state and RAM digest, and mixed audio against the interpreter on all 4 replays (PS2 ×3, Spawn with its own table); planted faults in the scan (1, 2) and the native blocks (3, 4) are caught, the no-fault control is identical. The 40–79 ms spikes: seen again as 10–24 ms stalls in a few runs, at different ticks in two runs of the same work, with ordinary instruction and block counts, so host stalls (other jobs on the machine), not ARM work. Step three proven but not live (2026-10-31): the per-game switch (game.json `aica_arm_fast`, `aica_arm_native`, tables in `port/src/aica_native.c`; `DP_ARM_FAST=0 DP_ARM_NATIVE=0` selects the interpreter) is only on local `arm7-native` branches of clones of the game repos (`arm7native` in the games folder: Power Stone 2 `bc2f5e9`, Spawn `2845bb3`), not on the commits the launcher builds, so both games still play on the interpreter. Re-run 2026-11-01 on master `a88a90e` with the launcher's `--engine` build: zero differences (ARM write log, state digest, mixed audio) between the interpreter and those branches on all 5 reference replays: Power Stone 2 ×3 (5,988, 7,881 and 18,471 vblanks), Spawn ×2 (1,174 and 4,658). Planted faults 1–4 caught on all 5 (fault 2 in the log only; the audio stays the same). The launcher's arguments and settings, `--no-window`, real clock, 2560x1600, replays of 7,881 and 4,658 vblanks, interpreter → native, median / p99 ms per tick, with other jobs running on the laptop: Power Stone 2 ARM 2.65 / 6.53 → 0.90 / 2.76, whole tick 4.63 / 19.90 → 3.11 / 13.52; Spawn ARM 2.80 / 11.77 → 0.92 / 2.08, whole tick 5.19 / 16.89 → 2.94 / 12.75. Both games reach the end with exit 0; the whole tick still fails the 6.94 ms target. Build box A on the same build, real window at 1280x960 on a 1920x1080 144 Hz desktop, real sound device, the same replays, quiet (other jobs held; load mean 9%, at most 16%, in 25 samples), two rounds in alternating order, all exit 0 at the end of the replay. Interpreter → native, round one then round two: Power Stone 2 ARM median / p99 3.29 / 8.36 and 3.35 / 10.21 → 1.48 / 2.81 and 1.50 / 3.13, whole tick 5.74 / 19.02 and 5.84 / 20.04 → 4.70 / 12.19 and 4.62 / 13.09, frame p99 29.13 and 33.60 → 17.66 and 18.45, underruns 7 (93 ms silent) and 1 (0 ms) → 2 (1 ms) and 1 (2 ms); Spawn ARM 3.39 / 10.22 and 3.46 / 9.74 → 1.63 / 2.69 and 1.63 / 2.78, whole tick 5.84 / 20.26 and 5.63 / 19.02 → 4.70 / 13.21 and 4.80 / 13.98, frame p99 34.17 and 33.25 → 21.80 and 21.84, underruns 2 (1,682 ms) and 3 (169 ms) → 2 (200 ms) and 3 (154 ms). Native's silence in Spawn is mainly one early 150–180 ms underrun at a tick gap of 216–232 ms, and interpreter round two shows the same (166 ms at 217 ms). Both models show about 59 and 57 shown fps and a 2.2–2.8 s longest frame in Spawn; the 144 fps target is not met with either, and native does not change that on its own. What is not covered: driver paths no replay reaches (the tables were built from these same replays, so no replay tested them independently; code outside the tables runs in the interpreter, slower), differences that appear and undo themselves between the per-vblank digests without changing a logged write or the audio, `launch.py` itself (it would build its own mirrors, so the clones' builds ran with its arguments), a real window at 2560x1600 and on the user's laptop, and anything by ear. Next: go live on the user's yes (the live kit branches need master's kit first, and the game commits land with them), then stage 5 (modern output, one switched change at a time). End state: no interpreter in the default play path, kept selectable as the reference |
| Guest memory accessors | `runtime/tx/tx_rt.c`, `tc_rt_inline.h` | Power Stone 2: ~30% of game-thread samples; now inline | Done (`port-perf`, frames and audio identical on PS2, Toy Commander, Spawn replays). Game code 6.8 -> 4.9 ms on PS2 |
| SH-4 FPU and register helpers | `runtime/sh4_rt.c` | Power Stone 2: ~13% of game-thread samples | `sh4_fr`/`sh4_r` family inline; `sh4_ftrv`, `fipr`, `fsca` still calls |
| Store-queue model | `runtime/tx/`, `tools/tx_sqfast.py` | Power Stone 2: 21.5% of active samples, 1.7 ms of game code | Faster helpers behind `sq_fast`, proven; on for no game yet. Later: the queue itself out of the vertex path |
| `tc_call` by guest address (glue lookup, argument copy, guest frame) | `runtime/tx/tx_rt.c`, generated glue | Toy Commander: ~35% of game-thread samples (call_unhooked, glue_of, port_tx_enter, guest_fold) | Lookups cached; the call itself remains. Needs direct calls between translated functions |
| PowerVR TA lists parsed then drawn | `pvr/ta_parse.c`, `pvr/pvr_render` | Power Stone 2: ~0.84 ms per tick | Open |
| Saturn VDP1 and VDP2 software renderer | `saturn/sat_vdp*.c` | Robo Pit (build box A, real clock, `/O2`): VDP2 compose 5–8.4 ms per frame; VDP1 draw 0.8–2 ms in the movies and title, 20.8 ms in the ROBOT PLANT menu (full-screen polygon layers drawn per pixel with a division per step). 3597 of 3599 frames over 6.94 ms | Open |
| Saturn's two SH-2s as fibers, SCU, SMPC, CD block | `saturn/sat_*.c` | Robo Pit (build box A, real clock, `/O2`): the 10–14 ms game tick was waiting, not work: loops on the vertical-blank flag, the drive's status and the main loop. With those named (`--vblank-wait`, on by default) the tick is 0.4–2 ms, and a whole frame is 6.6 ms median, 15.2 ms p99 headless on the main laptop through logos, menu and a fight (5499 frames, 2488 over 6.94 ms; the rest is VDP2 about 3.8 ms and VDP1 up to 4 ms) | Open |
| GameCube hardware | No port kit yet | None | Design to the budget from the start |
| Xbox 360 hardware | `targets/xbox360_ppc/port/` (being built) | None | No model allowed, see "New console kits start with no hardware model" |

Not emulation, but on the same bill: ports are 32-bit, and the kit compiles at the judge's `/O1`
(`port/CMakeLists.txt`). The kit's own C now builds at `/O2` (`PORT_KIT_O2`, on). The game's C at `/O2` (`PORT_GAME_O2`, `build.py --game-o2`) is
identical on the Toy Commander replay (24 frames, audio) and ~10% faster; it is off until the other games' replays are proven. On Power Stone 2 it is identical and about 2% faster, so not worth turning on there.

Measured on the main laptop with other agents running, thread-CPU time, `port-perf`, final kit (work per tick: mean / 99th percentile / worst):

| Game | Section | Mean | p99 | Worst | Note |
|---|---|---|---|---|---|
| Power Stone 2 | vblanks 7582-7881 | 7.1 | 13.5 | 15 | was 11.4 mean; ARM7 driver 2.0 of it |
| Spawn | last 300 of replay 20261030-202531 | 8.2 | 13.4 | 280-300 | the ~0.3 s "hitch" is the proof's own PNG dump of a frame (`--dump-frames`, "frame done" 298-317 ms), not the game; see "Spikes" below |
| Toy Commander | last 300 of replay 20261031-121005 | 17.4 (game C at /O2: 15.7) | 26-30 | 78 | translated game code and `tc_call` |

All three fail 6.94 ms at the 99th percentile.

## Power Stone 2 against 6.94 ms: what still separates it

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
  88.6 MB sound to blank 30100, identical; game code 4.24 to 3.95 ms, p99 6.36 to 5.71 (8 functions use them there). Toy Commander has none.

## Catch-up and the Saturn kit, corrected

`--catch-up` is the Dreamcast kit's: `pace_next_tick` has one caller, `runtime/host.c`. The Saturn kit keeps its own grid of deadlines
(`saturn/sat_sys.c`): a late blank keeps the grid, so a tick over a slot is already repaid, and a debt over one period is dropped (6 on the
Dreamcast kit). The option was parsed there and did nothing, so it is gone from the Saturn kit's list. Robo Pit, headless: 8000 blanks real
clock through the menu input, 7995 gaps under 25 ms at 50 Hz, no crash.

Dreamcast, headless, real clock, 2560x1464, audio off, on then off: Spawn 60.0 and 60.0 blanks a second, frame p99 19.13 and 19.15.
Toy Commander (mirror's game, its old `powf`, mission route) 57.5 and 42.9, frame p99 27.40 and 33.51. Fixed clock, on against off:
Spawn 28 frames and Toy Commander 16 frames identical.

## The look-ahead gate, corrected

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

## Toy Commander against 6.94 ms: in-between frames

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

## Shown frames: in-between frames per game

The tick keeps its original rate; the frames between ticks come from `runtime/frame_extra.c` (`--present interp`).
A game gets them from its own draw function (`port_set_extra_draw`: Toy Commander, `frame_interp.c`) or, with
`--interp-scene on`, from the renderer: `pvr/pvr_blend.c` keeps the scene it parsed before this one and an in-between
frame draws this scene's batches with each vertex part of the way back to where it was a tick ago. No game code runs
(no copy of guest RAM, nothing to undo); a batch that did not match, or a triangle that moved over 80 pixels, changed
depth by more than half or moved its texture coordinates by more than half a texture, is drawn as it is. The tick's own
frame is never touched. An in-between frame shows the scene one tick late (the same latency as the draw-function route).
A flip that does not wait (Kamui 1, Spawn: the game waits for the blank itself) holds its frame; the game's next wait
for the blank draws the in-between frames, with the textures and registers the held scene was drawn with kept, and
shows the frame itself only if none was drawn (`port_extra_flip`, `port_extra_land`).

| Game | In-between frames | Switch | Proof so far | Not proven |
|---|---|---|---|---|
| Power Stone 2 | Scene blending (Kamui 2, `kmRender`). Cost per frame about 0.5 ms Direct3D CPU at 1920x1440, headless | `--interp-scene on` (off by default; `game.json` key not added: the live line's default is the starting session's call) | Replay 20261031-122203 to vblank 7881, `--interp-frames 2`: all 160 tick frames (every 100th, with their 640x480 refs) and the 23.2 MB WAV byte-identical to `--present tick`; 15,626 in-between frames drawn. Denser run, `--interp-frames 3`, every 7th frame: 2,240 files, 0 differing; 23,439 in-between frames | Pictures of the in-between frames were looked at (castle camera move, a menu): they sit between the two ticks and move monotonically with alpha, but are an approximation, not the game's own intermediate state. Real-clock shown rate (needs a window on build box A or build box B: not run), how the cut guards behave on other scenes |
| Spawn | The same, through Kamui 1's flip (held frame) | the same | Replays 20261030-202531 (to 4658) and 20261030-192634 (to 1173): every 100th tick frame identical, and sp1 every 3rd frame (273 frames) identical; WAVs identical; 4,760 and 1,478 in-between frames | Real-clock rate; a split-screen or multi-render frame (only the last render of a tick is redrawn) |
| Toy Commander | Scene blending, game.json `interp_scene` (`--interp-scene off` selects its own draw function) | `--present interp` | Flying route to blank 14000, 2560x1600, `--interp-frames 2`: 60 tick frames (every 300th) and the 41 MB WAV byte-identical to `--present tick`, and to the draw function route; pictures of the in-between frames compared with the draw function's in menus and in flight, and at 16:9 | Only the user can judge motion: positions blend in screen space, so a fast turn is a little off where the draw function is right. Real clock at 2560x1600, and the user's laptop |
| Toy Commander, before | Its own draw function | `--interp-scene off` | Replay 20261031-121005 to 20057 with `--interp-scene on --present interp`: 46 files (every 500th frame and refs) and the WAV identical to `--present tick`; scene mode only draws before the game registers its function, in the movie windows nothing is drawn (`gfx_movie_on`) | Scene blending there is not meant to carry the movie windows (still ~31 vblank a second, see the tick design) |

Planted-fault check: `tests/test_pvr_blend.py` works small scenes by hand (blend values, guards, matching, copying) and
catches four planted mistakes in `pvr_blend.c`.

### The pause-screen playtest and the first real-window run (2026-10-31, `port-perf`)

The user's playtest (Power Stone 2, launcher, `--present interp`, vsync off, 1920x1440, capture 20261031-200527) found
the frame rate low, on a paused scene too. What the logs and replays say:

- **Why the paused scene shows 60.** `--present interp` gives Power Stone 2 no in-between frames unless `--interp-scene on`
  is also given (it has no draw function), and that is off by default and not in the live line. Only ticks are shown: 60 a
  second. It is the missing feature, not a regression.
- **The 141 ms and 215 ms gaps** end at the windows holding the user's two capture-button dumps (vblanks 65341 and 64173): the
  dump writes an 8.3 MB bitmap, the JSON and the recording on the game thread. The other gaps of 217 to 249 ms (eight in the
  log) are each at a fight start, where `gdfs` reads eight character files and a stage file in one go: loads.
- **The game's own cost in the fight** (replay 20261031-200548, vblanks 62900-65600, 1920x1440, headless, thread-CPU clock,
  main laptop under agent load): work per tick mean 10.76 ms, p50 10.21, p95 15.03, p99 18.39, worst 22.47; game code 6.64
  (p95 9.76), ARM7 sound driver 2.74, TA parse 0.92, Direct3D CPU 0.32. 51 of 2,701 ticks (1.9%) are over 16.7 ms, in
  clusters at the fight start (63283-63382) and at 65218-65388. The paused part (63500-64700) is still 10.2 ms a tick
  (game 6.1, ARM7 2.8). So the game fails the 6.94 ms target by itself, even paused, and the extra 38-53 fps windows in
  the log need the real-window path and host load on top of that.
- **First real-window run** (build box A, i7-8750H, RTX 2080 Max-Q with the Intel panel on 143 Hz, window 1729x973 not
  focused, vsync off, same replay, real clock from vblank 62900 to 65600; six unrelated java processes held about half
  the CPU, so the level is pessimistic): `--present tick`: shown 29.4 a second, tick p50 20.4 ms, p99 41.6, worst 70.3;
  frame p50 33.4, p99 50.3. `--present interp --interp-scene on`: shown 58.2 a second (5,320 in-between frames after 2,701
  ticks), tick p50 22.2, p99 43.2, worst 177; frame p50 10.0, p99 51.2, worst 214. Scene blending works in a real window and
  doubles the shown rate, but the shown frame p99 stays at the tick's, because the in-between frames are drawn after the
  tick (the host skipped 414,780 times for too little time left) so a slow tick leaves a hole. Neither run is anywhere near
  6.94 ms; build box A is not the user's laptop.
- **What follows:** the tick needs to come off the presenting thread (design in the port-perf notes, Round 3), the game's
  work (ARM7 sound driver and TA parse included) has to shrink, and in-between frames need a count that follows the
  display's rate, not a fixed number a tick.
- **Wide shape:** at `--aspect fit`, 2560x1600, replay 20261031-122203 to 7881, frame 7832 is byte-identical between
  `--present tick` and `--present interp --interp-scene on` (15,626 in-between frames drawn). One frame only.

### "It sounds fine, then it slows down; a pause clears it" (2026-11-01, `port-perf`)

The user's playtest (Power Stone 2, live kit 301dd56, launcher, 2560x1464, frame 1952x1464, captures 20261031-232451 and
-200548): "audio sounds crunchy, FPS is hovering around 30, but it feels slowed down", then "It sounds fine, then it slows
down a lot, and becomes laggy. If i pause the game, wait a few seconds, then unpause, the game is back to normal".

- **Cause.** The real-clock wait waited for the boundary after now, so a tick over 16.67 ms cost a whole slot, lost for
  good: the guest clock slows, and the sound, mixed per blank, starves. A mean tick well under a slot still ran at 45 blanks
  a second. Gaps in the log sit flat at 34 ms. The fight's cost is near the threshold and a pause sits under it, so there
  is no backlog: the same rise and fall shows on the fixed clock, and work and look-ahead do not climb with time.
- **Reproduced without a window or sound.** Laptop, headless, real clock, 1920x1440, replay -200548 through its pause,
  per 300 blanks. Live: 50, 45.5, 51.6 in the fight, 59-60 paused, 54.7, 48.5 after. The fix: 60.0 in every row (work
  8.66-10.73 ms). Under load the fix dips too (45.6-59.0 in another run), so it is not immune.
- **Not doubling.** Scenes equal blanks and 15 consecutive frames each differ in about 80% of their pixels, so the game was
  not drawing every second blank. The game ran slow; "feels like 15" has no separate cause found.
- **The fixes.** `--catch-up on|off` (default on, `pace.h`): each blank is due one tick after the last, a debt over 6 ticks is
  dropped. The inline `rd32` filter (`tx_spin.c`): naming a spin word sent every read out of line, about 14% of samples; alternating
  runs took game code from 8.53, 9.08, 6.19 ms to 6.88, 6.56, 5.05, the ARM7 control flat. Replay to 5855 with 118 frames and
  the 17.2 MB sound dump: 0 differences. The audio look-ahead now serves only a slow scene (mean gap over 25 ms, or repeated long
  gaps; see "The look-ahead gate, corrected"): a spike in a fight used to raise it, and the extra mixing held ticks over a slot.
- **Real window with sound** (build box A, i7-8750H, RTX 2080 Max-Q, window 1729x973, `--present interp`, vsync off, fight 3000-5855,
  sound driver and all; alternating, level moved by other load):

| Build | Blanks/s | Frame p50 / p99 ms | Work mean / p99 ms | Silent |
|---|---|---|---|---|
| Fix, quiet round | 60.0 | 16.66 / 18.80 | 13.78 / 18.32 | 0.0%, 0 underruns |
| Master's look-ahead, with catch-up and filter | 26.8 | 33.99 / 124.81 | 35.06 / 114.60 | 27.8% |
| The same, 2 quieter runs | 46.6, 46.7 | 17.21, 17.87 / 59.49, 56.59 | 20.90, 19.93 / 53.70, 51.29 | 5.8%, 3.0% |
| The same, `--audio-ahead off` | 59.5, 59.5 | 16.64 / 24.39 | 15.43 / 24.18 | 0.9%, 1.0% |
| Live kit | 47.0 | 16.75 / 35.37 | 15.76 / 24.65 | not logged |
| Fix, overloaded round (game code 28 ms) | 12.8 | 57.38 / 529.21 | 72.08 / 472.02 | 60.6% |

- **Against 6.94 ms: it fails.** Work per tick on the laptop is 9.39 mean, 18.24 p99 (headless, quiet, no Present), and the
  frame p99 is 21.84. 60 steady blanks is the game's own speed restored, not the target: the tick has to come down by more
  than half at p99. What is left: game code 5-6 ms (the store-queue helpers about 20% late in a fight), ARM7 about 2.9, TA 0.8.
- **Not confirmed:** a real window on the user's laptop (the only proof that counts; a replay there can't have a window while
  the user plays); what made the live log's wall time 1.4 times its thread-CPU time (other sessions' jobs ran on the laptop
  in that playtest, Present not separated); Toy Commander's movies with the gate, reasoned only (half rate is over the gate's 40
  blanks a second); an overloaded machine still collapses, look-ahead or not.

The spikes checked on whole replays are in `docs/RUNTIME_SPIKES.md`.
