# Runtime target: 144 fps at 2560x1600, no emulated hardware in the play path

The user's rule (2026-10-31), for every port on every console, now and in future: emulated hardware does not match
the target of 144 fps at 2.5k. "Ideally we prove out how it needs to work, then swap it to a modern solution."

## The target

Measured on the user's laptop (2560x1600 panel, RTX 5080 Laptop GPU), through the launcher, on the game's heaviest section.

| Number | Target |
|---|---|
| Shown frames | 144 a second, at the panel's full 2560x1600, game world filling the screen (see "Any aspect ratio"; the kit renders at output size) |
| Making a frame | 6.67 ms at the 99th percentile: the game tick and the draw, until the frame is ready to show (150 fps of headroom under the 144 cap) |
| Late frames | At most 1 in 1,000 shown frames misses its 144 slot (a gap between shown frames that spans n slots, to the nearest, is n - 1 late) |
| Not the test | Shown-frame spacing: under the 144 cap it can't go below 6.94 ms, so it says nothing about headroom |
| Below 144 fps | Only on a loading screen. Menus, movies, fights, scene changes and busy scenes are not exceptions, and sound never starves |

The user (2026-10-31): "The 99% frame should be 144fps. There is no valid reason why, except a loading screen, we
should be getting less." So the 99th percentile is measured with only loading screens left out, and a run with sustained drops elsewhere fails even if its percentile passes.

The bar is "150 fps headroom" (the user's choice, 2026-11-05): 99% of frames are made within 6.67 ms, and at most 1 in
1,000 shown frames misses its 144 slot, at 2560x1600 through the launcher. The verdict is for the user's laptop (`target_laptop`) only: only its launcher runs pass or fail a game. The user (2026-11-05): "Build box A might not be able to
do it and that is OK." Build box A and build box B figures compare builds side by side and never count as a fail. Every game log
ends with both figures and the verdict, which names the machine it ran on (`base/play_stats.c`, rule point 5 below). It leaves a loading screen out of both and counts it
apart: a gap that starts within half a second after 256 KB came from the disc within half a second (a music stream reads
far less). The Saturn kit has no disc hook yet, so its verdict leaves nothing out.

### Capped at 144 by default

The user's rule (2026-11-04), for every port: "By default, our projects should cap at 144fps. Then have an option for uncapped."

- **Play runs pass `--fps 144`** (`base/host_main.cpp`), before the saved settings. Display > Frame-rate cap offers None
  (as many as the display takes), 60, 120, 144 and 240. The choice is saved (`fps`, 0 for None) and wins over the
  default. Scripted runs (a fixed clock, `--headless`) get no defaults, so checks and replays don't change.
- **The cap shows frames in slots 1/N s apart** (`runtime/frame_thread.c`), not 1/N s after the last one. A late frame
  is followed by one that much sooner, so the rate holds without a wake-up's delay added to every gap. The old way made
  the cap cost frame time (`docs/LESSONS.md`, "Ports and frame timing").
- **A missed slot is a late frame.** Under the cap no gap is shorter than a slot, so their spacing is not the test. The
  game log's `play verdict:` line counts the slots no new frame filled (a gap that spans n slots, to the nearest, is n - 1 late) against 1 in 1,000 shown frames.
- **Measured (build box A, 2026-11-05):** 144.3 shown a second where the old wait gave 128, p99 9.6 ms against 10.6 before and 7.2 uncapped, so capped is still worse than uncapped at p99 (the dated notes are in git history; lessons in `docs/LESSONS.md`).
- **Every console takes it once it has in-between frames.** The menu entry is greyed on a kit without
  `PORT_CONSOLE_FPSCAP`: today the Saturn and Xbox 360 kits, whose `extra_set_fps` does nothing. They get the cap with
  their in-between frames.

## Which games, and what stops for it

Every native port: Spawn, Power Stone 2, Toy Commander, Robo Pit, Vigilante 8 when its port starts, and every new project.

The user accepts pausing native production while this gets fixed. For a game below the target:
- **Pauses:** new gameplay work, tweaks, new native features and playtest requests for that game.
- **Continues:** performance work, the front end's fixes, and a problem the user asks about directly. Work already in flight finishes.
- **Does not pause:** raw C, readable C and translators. They don't touch the play path until a port adopts them, and
  performance work needs them.

A game with no port yet starts with this budget, not with a plan to fix it later.

The game's simulation keeps its original tick (30 or 60 Hz): that is behaviour, and the judge and goldens depend on it.
The frames between ticks come from in-between frames (`runtime/frame_extra.c`, `--present interp`), which need the
game's draw function registered (`port_set_extra_draw`), or, for a game with none, the renderer's scene blending
(`--interp-scene on`, `pvr/pvr_blend.c`; see "Shown frames" at the end). A game showing only its ticks does not meet the target.

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

Where the kit stands (Dreamcast renderer; the Saturn kit below): the frame takes any shape. `--aspect
fit|4:3|16:9|21:9|W:H` (the Display > Aspect menu: Fit window, 4:3, 16:9, 21:9, applied at once and saved as `aspect`)
chooses it; a fixed shape that is not the window's shows at its own shape with bars, never stretched, and Fit window
(the default for a game with game.json `widescreen`) follows the window at any shape, no cap. `port_widescreen()` and
`port_tallscreen()` give the frame's width and height over the 640x480 screen's (each 1 or more, never both above 1): a
game widens its culling by them, horizontally and vertically (a narrower than 4:3 frame shows more above and below).
2D stays 4:3 in the middle, and a scene that draws nothing past the screen (a menu; 8 pixels of overscan allowed)
has the border colour around it rather than the background plane's, which its art hid on the console; an untextured full-screen triangle (a fade), or one textured with an 8x8 texture (a fill), covers the frame; scissors and clears,
frame-buffer reads (the game gets the 640x480 screen) and movies sit on the screen. A game without the key stays 4:3
until its card wraps its culling and sets it (a wider frame would show the holes the game's own culling leaves). Only
Toy Commander sets it. Checks and goldens stay 4:3 unless `--aspect` is given (`tests/test_gfx_aspect.py` is the shape
maths; PS2 at 4:3 is byte-identical to before in the front end's 52 dump frames, and TC's frame 10645 of replay 20261031-121005 too). In-between frames draw into
the same target at every shape. `port_shape_chosen()` is 0 only in a check at its original 4:3 (no
`--aspect`): a picture fix that should also change a picked 4:3, such as a background drawn larger, turns on with it.

The Saturn kit (`port/saturn/sat_wide.c`) composes the picture wider than the screen in play runs of a game with game.json `widescreen`: `true` for every picture, `"rbg0"` only for pictures with RBG0 on (BGON), a 3D floor; the log's `widescreen: composed` lines are that decision and its `widescreen: shown` lines the last lit picture's width, since Robo Pit keeps RBG0 on over an empty map through its black logos and movies (a 2D screen's planes hold the map's unused fill past the screen, so menus keep their width and bars). VDP1 draws into host framebuffers 1024 dots wide, twins of the guest's, with clip edges and erases that reach the screen's edges taken to the wide ones and the middle copied back for the game; VDP2 composes each plane, RBG0, the windows that reach an edge and the sprite layer at screen x minus the added width; the base's CPU presenter draws the screen at its 4:3 size with the sides beside it. game.json `wide_cull` widens SGL's window words as its readers are entered (SGL 3.0x: GBR 0x060ffc00); Robo Pit leaves it out, because a replay proves it changes the game (below). High-resolution modes and frames narrower than 4:3 are not widened (bars). Robo Pit's proof (2026-11-07, `.work/wide` in its project): 4:3 checks byte-identical to master in 445 --det shots, the save and the peeked words; with the chips widened and its culling not, --det at 16:9 gives the same save and words; a fight recorded on master in a real-clock headless run and replayed on master and on sat-wide at 21:9 and 16:10 gives, without `wide_cull`, the same save, energy words and SGL random word every blank and all 2000 shots' 4:3 middles the same; with it, the save and energy are the same but the random word parts from blank 6295 (21:9) or 6279 (16:10) and 321 or 388 middles differ (not a frame's lag): the game's own logic reads the culling, so it stays off. The composer works a line at a time (RBG0's coefficient once a line unless dKAx moves it, X and Y stepped as exact integers, the layers a window hides along the whole line dropped, the top two layers kept as they come): 4:3 byte-identical, about 2.4 times faster than master; the wide picture costs in proportion to its dots (1.20 at 16:10, 1.71 at 21:9 in one thread's cycles).
`port_read_whole_frame(1)` makes frame-buffer reads squeeze the whole frame into 640x480 instead of taking the screen
in its middle, for a game that draws what it read back over the whole frame (Toy Commander's pause picture).

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

Where the kit stands: `base/input_ex.c` reads LB, RB, the stick clicks, Back, Guide and the right stick, and names them for
the connected pad. `base/input_ex_assign.c` keeps what each player gave them, in the settings and in the recording, for a game
whose key screen offers the rows (Spawn's is the first; `port_hook_skip_call` lets a game's screen draw them in place of its
own). `base/pad_ex_page.c` is that screen's page, all but the drawing and the presses: the rows, their names, keep, cancel
and reset. `base/pad_stick.c` and game.json `"left_stick"` offer the left stick as movement, for a game that never reads the
analog stick. `base/pad_twin.c` and game.json `"twin_stick"` turn on the right stick in a game's fights, with the left
stick's sideways direction as the sidestep (Input > Controls > Twin-stick, on by default; Robo Pit is the first).
`base/pad_style.c` follows the pad in hand for a texture pack's controller pictures, with the Input menu's
Controller pictures to turn them off. A game supplies its choice words, its presses, where it draws and its own addresses.
A game without a key screen still needs the kit's Controls screen, which doesn't exist yet.

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
   draw, shown-frame and make times (median, 99th percentile, worst), then the verdict against the bar, so the user's own
   launcher runs are the measurement. The lines are
   `play summary: shown_fps F output WxH frames N tick_ms M/P/X draw_ms M/P/X frame_ms M/P/X make_ms M/P/X` (milliseconds)
   and `play verdict: pass|fail|none make_ms p99 P (bar 6.67) late L of N shown (R per 1000, bar 1) loading S shown K late machine M`;
   the scorecard's gate 6 reads both (`tools/scorecard_runtime.py`).
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
- **A replay only proves what it plays.** Reports name what the replays cover and report any part they miss as unproven.

## Emulated layers in the play path

Update a row in the change that alters it. "Not measured" stays until someone measures it.

| Layer | Where | Cost measured | Status |
|---|---|---|---|
| Sound CPU: ARM7 interpreter running the game's own driver | `runtime/arm7.c`, `arm7_ls.c`, `aica_arm.c` | Power Stone 2: median 2.25 ms per tick in the heavy section (211k ARM instructions per tick, 99% in idle flag scans; Spawn the same: the two games run one byte-identical driver, AICAsoundDrv 1.76). The 40–79 ms spikes are not extra ARM work (same instruction count in those ticks; not reproduced in traced replays), likely host contention | Being replaced (card `arm7-native`). Step one done: `runtime/arm7_scan.c` runs the driver's three idle scan loops a whole number of iterations at a time (switch `DP_ARM_FAST=1`, interpreter stays the default until the per-game switch): Power Stone 2 heavy section median 2.26 → 1.02 ms per tick, p99 3.27 → 2.32 (whole session median 2.37 → 1.11), proven byte-identical on the ARM register-write log, a per-tick digest of all ARM state and 2 MB sound RAM, and the mixed audio, on 4 replays (PS2 ×3, Spawn); planted faults are caught. Step two done: `tools/arm7_translate.py` turns the driver's executed code (from the recorder, `DP_ARM_PCS`) into one C function per basic block (`runtime/arm7_native.h`, chained, the interpreter's own cycle and instruction counts, bus accesses and self-modifying stores handled by returning to the interpreter; blocks validate their words on every entry). Switch `DP_ARM_NATIVE=1` (game.json `aica_arm_native`); the interpreter runs whatever the table does not cover. Power Stone 2 heavy section (vblanks 7582–7881, no log, no audio dump): interpreter median 2.28 / p99 4.92 ms, scan skip 0.92 / 2.16, native 0.68 / 1.84 (whole session 2.22 → 1.03 → 0.76). Byte-identical ARM write log, per-tick state and RAM digest, and mixed audio against the interpreter on all 4 replays (PS2 ×3, Spawn with its own table); planted faults in the scan (1, 2) and the native blocks (3, 4) are caught, the no-fault control is identical. The 40–79 ms spikes: seen again as 10–24 ms stalls in a few runs, at different ticks in two runs of the same work, with ordinary instruction and block counts, so host stalls (other jobs on the machine), not ARM work. Step three proven but not live (2026-10-31): the per-game switch (game.json `aica_arm_fast`, `aica_arm_native`, tables in `port/src/aica_native.c`; `DP_ARM_FAST=0 DP_ARM_NATIVE=0` selects the interpreter) is only on local `arm7-native` branches of clones of the game repos (`arm7native` in the games folder: Power Stone 2 `bc2f5e9`, Spawn `2845bb3`), not on the commits the launcher builds, so both games still play on the interpreter. Re-run 2026-11-01 on master `a88a90e` with the launcher's `--engine` build: zero differences (ARM write log, state digest, mixed audio) between the interpreter and those branches on all 5 reference replays: Power Stone 2 ×3 (5,988, 7,881 and 18,471 vblanks), Spawn ×2 (1,174 and 4,658). Planted faults 1–4 caught on all 5 (fault 2 in the log only; the audio stays the same). The launcher's arguments and settings, `--no-window`, real clock, 2560x1600, replays of 7,881 and 4,658 vblanks, interpreter → native, median / p99 ms per tick, with other jobs running on the laptop: Power Stone 2 ARM 2.65 / 6.53 → 0.90 / 2.76, whole tick 4.63 / 19.90 → 3.11 / 13.52; Spawn ARM 2.80 / 11.77 → 0.92 / 2.08, whole tick 5.19 / 16.89 → 2.94 / 12.75. Both games reach the end with exit 0; the whole tick still fails the 6.94 ms target. Build box A on the same build, real window at 1280x960 on a 1920x1080 144 Hz desktop, real sound device, the same replays, quiet (other jobs held; load mean 9%, at most 16%, in 25 samples), two rounds in alternating order, all exit 0 at the end of the replay. Interpreter → native, round one then round two: Power Stone 2 ARM median / p99 3.29 / 8.36 and 3.35 / 10.21 → 1.48 / 2.81 and 1.50 / 3.13, whole tick 5.74 / 19.02 and 5.84 / 20.04 → 4.70 / 12.19 and 4.62 / 13.09, frame p99 29.13 and 33.60 → 17.66 and 18.45, underruns 7 (93 ms silent) and 1 (0 ms) → 2 (1 ms) and 1 (2 ms); Spawn ARM 3.39 / 10.22 and 3.46 / 9.74 → 1.63 / 2.69 and 1.63 / 2.78, whole tick 5.84 / 20.26 and 5.63 / 19.02 → 4.70 / 13.21 and 4.80 / 13.98, frame p99 34.17 and 33.25 → 21.80 and 21.84, underruns 2 (1,682 ms) and 3 (169 ms) → 2 (200 ms) and 3 (154 ms). Native's silence in Spawn is mainly one early 150–180 ms underrun at a tick gap of 216–232 ms, and interpreter round two shows the same (166 ms at 217 ms). Both models show about 59 and 57 shown fps and a 2.2–2.8 s longest frame in Spawn; the 144 fps target is not met with either, and native does not change that on its own. What is not covered: driver paths no replay reaches (the tables were built from these same replays, so no replay tested them independently; code outside the tables runs in the interpreter, slower), differences that appear and undo themselves between the per-vblank digests without changing a logged write or the audio, `launch.py` itself (it would build its own mirrors, so the clones' builds ran with its arguments), a real window at 2560x1600 and on the user's laptop, and anything by ear. Next: go live on the user's yes (the live kit branches need master's kit first, and the game commits land with them), then stage 5 (modern output, one switched change at a time). Whole-replay proof 2026-11-02: the dated notes are in git history (lessons in `docs/LESSONS.md`); the game keys are for the game cards to turn on, with the user's yes for the live move. End state: no interpreter in the default play path, kept selectable as the reference Profiled again 2026-11-05 on build box A (live game, kit a7dede82, the fight, vblanks 7000-12000): the translated driver is on, and a blank's ARM work is about 185k instructions, 82k of them skipped by the scan fast paths and 103k run in about 35k translated block entries, almost none interpreted, at 34 FIQs a blank. An exact idle skip was tried the same day (kit branch `arm-idle` in the fps144 card's clone, not landed: jump whole passes of the idle loop that come back with every register the same, no sound-RAM byte changed, no device access and no FIQ). No pass ever repeated: in each minute of play about 4 to 6 million checks found a sound-RAM byte changed since the last arrival, against about 1 million on registers and almost none on devices or the FIQ. The driver's idle pass keeps time in the sound RAM, so the passes cannot be skipped exactly. Cutting the ARM further needs it off the game thread, or the translated blocks made cheaper. |
| Guest memory accessors | `runtime/tx/tx_rt.c`, `tc_rt_inline.h` | Power Stone 2: ~30% of game-thread samples; now inline | Done (`port-perf`, frames and audio identical on PS2, Toy Commander, Spawn replays). Game code 6.8 -> 4.9 ms on PS2 |
| SH-4 FPU and register helpers | `runtime/sh4_rt.c` | Power Stone 2: ~13% of game-thread samples | `sh4_fr`/`sh4_r` family inline; `sh4_ftrv`, `fipr`, `fsca` still calls |
| Store-queue model | `runtime/tx/`, `tools/tx_sqfast.py` | Power Stone 2: 21.5% of active samples, 1.7 ms of game code | Faster helpers behind `sq_fast`, proven over whole replays on Spawn, Power Stone 2 and Toy Commander (corrected 2026-11-03 for functions whose callees store into the queues, "The store-queue change"); a game's own card turns it on. Later: the queue itself out of the vertex path |
| `tc_call` by guest address (glue lookup, argument copy, guest frame) | `runtime/tx/tx_rt.c`, generated glue | Toy Commander: ~35% of game-thread samples (call_unhooked, glue_of, port_tx_enter, guest_fold) | Lookups cached; the call itself remains. Needs direct calls between translated functions |
| PowerVR TA lists parsed then drawn | `pvr/ta_parse.c`, `pvr/pvr_render` | Power Stone 2: ~0.84 ms per tick | Open |
| Saturn VDP1 and VDP2 software renderer | `saturn/sat_vdp*.c` | Robo Pit (build box A, real clock, `/O2`): VDP2 compose 5–8.4 ms per frame; VDP1 draw 0.8–2 ms in the movies and title, 20.8 ms in the ROBOT PLANT menu (full-screen polygon layers drawn per pixel with a division per step). 3597 of 3599 frames over 6.94 ms | VDP2 compose in scanline bands across cores (`base/bands.c`, `--bands`, 4 by default) and frames shown by a thread of their own (`base/present_cpu.c`, `--present-thread`), ee06456a: identical shots, planted faults caught. The user's laptop, launcher path, no window, real clock, fight route: work p99 4.12 ms (VDP2 0.90 median, VDP1 2.08 p99, tick 1.46 p99), 3 of 5599 frames over; before, 12.89 ms. The shown-frame p99 there is not measured (no window, no `play summary`); build box A in a window shows 144.0, shown-frame p99 7.3 ms. Still the software renderer: GPU VDP1/VDP2 and in-between frames are later steps |
| Saturn's two SH-2s as fibers, SCU, SMPC, CD block | `saturn/sat_*.c` | Robo Pit (build box A, real clock, `/O2`): the 10–14 ms game tick was waiting, not work: loops on the vertical-blank flag, the drive's status and the main loop. With those named (`--vblank-wait`, on by default) the tick is 0.4–2 ms, and a whole frame is 6.6 ms median, 15.2 ms p99 headless on the main laptop through logos, menu and a fight (5499 frames, 2488 over 6.94 ms; the rest is VDP2 about 3.8 ms and VDP1 up to 4 ms) | Open |
| Saturn movies: the game's own Cinepak decode on the SH-2s, SCU DMA and VDP1 | `saturn/sat_movie.c`, `base/cinepak.c` | Robo Pit, build box A, real clock, no window: the host player makes a picture in under 1 ms (0 over 6.67 ms) against the emulated movie stretch's 11–14 ms ticks; play verdict pass, make_ms p99 4.96–5.84. Picture after sound 3.3 ms (KOKO, OPENINGM) and 5.2 ms (LOGO) median, against Mednafen's 21.6, 33.0 and 82→9 ms (measured before presenting): **a deliberate improvement** — the host shows each picture as its sound plays instead of after the SH-2 decode and VDP1 copy, so LOGO's 5 ms against Mednafen's 33 is kept, not matched | Replaced per game behind game.json `movie` and `--movie-native` (on in play runs; checks and `--det` keep the emulated player). Proven 2026-11-08 on DP master af3820cf (widescreen): switch off, the long det route is identical (445 shots, same save). Switch on, det, against the emulated player blank for blank: the original's open wait (`open_blanks` 6), sound start, pictures and end give returns at 441, 739 and 2597 as emulated; START at 201 and 344 skips by the original's rule (201 not taken, KOKO returns at 348 as emulated, LOGO 646 against 647); A+B+C+START calls the game's check 06004094 at 253 and returns −1 at 255 as emulated, LOGO 553 as emulated. **Not matched: the title is read 2 blanks early in det (2611 against 2613); real clock 2580–2590 against the emulated 2591–2605** (on the kit's real clock each emulated movie runs about 6 blanks longer than in det). The drive state is identical from blank 2602; the game's file loop before the title runs 174 times against 243. Suspected cause, not proven: the original movie runs its own SGL copy, which shares the game's GBR work area and writes the state word read by 0607f678 after OPENINGM (0xb2ef emulated against 0xaf5b native); the native player does not touch it. Accepted by the user ("Yes, merge it") as it stands |
| GameCube hardware | No port kit yet | None | Design to the budget from the start |
| Xbox 360 hardware | `targets/xbox360_ppc/port/` (being built) | None | No model allowed, see "New console kits start with no hardware model" |
| Xbox 360 Xenos colour rules from COGBox (MIT, with Xenia BSD-3 parts): resolve exponent bias, 7e3 encode, decode and range clamp, 16-bit fixed point decode, a render target's own exponent bias | `targets/xbox360_ppc/port/runtime/xb_xenos_colour.h` on kit branch `x360-th` (Too Human bring-up) | None: one clamp in the scaled resolve's pixel shader, and one multiply per pixel in every converted pixel shader (a factor of exactly 1 when off) | Bring-up and answer-key layer only, off by default behind `XB_COGBOX_7E3=1` (the scaled resolve clamps its source to what a console 7e3 target holds before the exponent bias) and `XB_COGBOX_RT_BIAS=1` (the pixel shader's colour times 2^RB_COLOR_INFO's exponent bias, read from the surface header's +0x1c). Added 2026-11-07. Not in any play path: Too Human has no launcher entry or live line past the opening yet. To be replaced by native drawing that keeps the game's colours in its own terms |

Not emulation, but on the same bill: ports are 32-bit, and the kit compiles at the judge's `/O1`
(`port/CMakeLists.txt`). The kit's own C now builds at `/O2` (`PORT_KIT_O2`, on). The game's C at `/O2` (game.json `game_o2`, `PORT_GAME_O2`, `build.py --game-o2`) is
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

## Work counters

Exact counts of a fixed-clock replay's work catch a slowdown on every commit; timing runs stay the judge of the target. Rows, baselines, proof and limits: `docs/proofs/work_counts/README.md`, `port/README.md`.

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

### In-between frames on their own thread (2026-11-02, `tc-present-thread`)

`--present-thread on` (game.json `present_thread`, `runtime/frame_thread.c`) draws and shows a blended scene's in-between frames on their own thread, so a long tick no longer takes time from them. It is off by default.

Live Toy Commander on the user's laptop, launcher path, no window, 2560x1600, 195 s flying route, `--present tick`: mission work p99 3.79 ms, game 2.65, one blank over 6.94 ms. Before the mission work p99 21.24 ms: 896 of 4799 blanks are over, nearly all game code at 16-18 ms in the opening movie ticks. **The shown-frame p99 on that laptop is not measured:** a run with no window has no `play summary`.

Build box A, real window, flying route, frame p99 from `play summary`:

| Window | Thread on (clean runs) | Thread off (clean runs) |
|---|---|---|
| 1729x973 | 5.22, 4.67, 5.20, 5.23, 4.84, 5.24, 5.46 | 10.98, 9.63, 9.96, 11.02 |
| 1280x960 | 4.28 | 27.54 (one run; the opening at about 50 fps) |

Proof: flying route to blank 14000 on a fixed clock, `--present-thread fixed`, twice on each of three builds, 29240 to 56475 in-between frames a run: 300 frames at 2560x1600 and the 41162944-byte WAV byte-identical to `--present tick`.

Not settled: 3 of 11 thread-on runs and 2 of 7 thread-off runs stalled for seconds, one of them for about 100 s, on a build box A where other jobs were using about 6 cores. The rates are alike, which points away from the thread but does not clear it, and the cause was not found. Stalled runs are left out of the table. The four thread-on runs with lock waits logged did not stall, so they say nothing about it; a stall now leaves `gfx: the game's thread waited` or `frame_extra: an in-between frame took` in the log if the lock or the draw is the cause. Movies stayed shown by the tick at 30 fps with this mode, now `--present-thread hold`; these figures are that mode's.

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

A game that names its per-part 3D draw (game.json `part_draw`) gets in-between frames from game state instead: each vertex is tied at parse time to the object part that wrote it, and a part that kept its object, stamp and vertex count is blended from its own vertices a tick before as (x w, y w, w), so it moves as the part and the camera moved, in perspective. Nothing is paired by screen position, so nothing ghosts (`pvr/pvr_parts.c`).

| Game | In-between frames | Switch | Proof so far | Not proven |
|---|---|---|---|---|
| Power Stone 2 | From object parts (`pvr/pvr_parts.c`, game.json `part_draw`): each vertex of a part blended from its own vertex a tick before, in perspective; 2D and changed parts as the latest tick drew them. What a blend can't show is held: a scene where half the parts' vertices are new or gone (a cut, one logo for another) gets no in-between frames, and a part that turned back the way it came (a pulse at its peak) is drawn where it was a tick before. About 0.15 ms an in-between frame | On by default with `part_draw` and `interp_scene` (`--interp-parts off`: scene blending) | Kit 2026-11-05: replays 20261030-215535, 20261031-233517, 20261031-200548 and 20261104-193034 (160,836 frames) and their sound byte-identical to master's `--present tick`; planted `--fault-at` caught. Truth pictures against the real frame between (fight median 2.69, scene blending 3.18, holding 12.85). Build box B, real clock, 2560x1600, fights: shown_fps 143.8, frame_ms p99 7.76, tick_ms worst 14.92 (14.10 ticks only). The user's laptop through the launcher, live game 94956802 on this kit (2026-11-05, 12000 blanks): shown 144.2 fps, frame_ms 6.95/7.25/192.85 (the worst a load), 216 of 28,800 gaps 7.29 ms or more (0.75%), tick_ms 1.12/4.09/17.00. Holds (kit 2026-11-05): the title logo's truth pictures 0 of 127 frames worse than holding (3 held, median 0.65; before holds 3 were worse: 420, 426 and 574, the CAPCOM logo gone, a white flash and the pulse's turn), fight frames 31000-31255 median 2.74 with none worse; every local PS2 recording (6 distinct, 172,784 frames) and its sound identical to `--present tick`; planted `--fault-at` caught. The user's laptop while they were working on it (indicative only), headless, real clock, 2560x1600, fights: shown_fps 145.3, frame_ms 6.95/8.17/65.11, tick_ms 6.30/11.12/30.51 | One fight frame, 35168 of 20261104-193034, is worse than holding: the camera cuts within one tick, and the truth test blends across two. Timing with holds on an idle machine |
| Spawn | The same, through Kamui 1's flip (held frame) | the same | Replays 20261030-202531 (to 4658) and 20261030-192634 (to 1173): every 100th tick frame identical, and sp1 every 3rd frame (273 frames) identical; WAVs identical; 4,760 and 1,478 in-between frames | Real-clock rate; a split-screen or multi-render frame (only the last render of a tick is redrawn) |
| Toy Commander | Scene blending, game.json `interp_scene` (`--interp-scene off` selects its own draw function) | `--present interp` | Flying route to blank 14000, 2560x1600, `--interp-frames 2`: 60 tick frames (every 300th) and the 41 MB WAV byte-identical to `--present tick`, and to the draw function route; pictures of the in-between frames compared with the draw function's in menus and in flight, and at 16:9 | Only the user can judge motion: positions blend in screen space, so a fast turn is a little off where the draw function is right. Real clock at 2560x1600, and the user's laptop |
| Toy Commander, before | Its own draw function | `--interp-scene off` | Replay 20261031-121005 to 20057 with `--interp-scene on --present interp`: 46 files (every 500th frame and refs) and the WAV identical to `--present tick`; scene mode only draws before the game registers its function, in the movie windows nothing is drawn (`gfx_movie_on`) | Scene blending there is not meant to carry the movie windows (still ~31 vblank a second, see the tick design) |

Planted-fault check: `tests/test_pvr_blend.py` works small scenes by hand (blend values, guards, matching, copying) and
catches four planted mistakes in `pvr_blend.c`.

Dated notes that were here (playtests, spikes and timings, 2026-10-31 to 2026-11-05) are in git history; what they taught is in `docs/LESSONS.md`, "Ports and frame timing".
