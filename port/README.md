# Dreamcast port kit

A game-free kit that turns a pipeline workspace's readable C into a native 32-bit x86 Windows program. It boots
from the user's own disc image: it maps guest RAM at its Dreamcast addresses, loads the boot file, compiles the
readable C at `/W4 /WX`, and runs the game's own start-up. SDK and engine functions with no readable C stay logging
stubs until a platform-layer replacement takes over. Toy Commander's port is built from it.

The kit holds no game code. Each game keeps a `game.json`, its own sources and its notes in its own repository
(next to the decomp workspace). `tests/test_port_kit.py` fails if a guest code address, a game name or a
`game.h` include in the runtime enters `port/`.

## The port base

Every console kit stands on one console-free base (`base/`, built into every kit's executable) and supplies only its
machine. The user's rules (2026-10-31): "We should ensure all projects have a consistent titlebar menu" and "We should
have a consistent project base for all projects to use, so we dont have to reset this up". A new console or game gets
every common part below with no setup; `tools/game_setup.py` scaffolds the rest.

**Inventory of the common parts** (state of `master` f379edb before the base; "Where it goes" is `base/` unless said):

| Common part | Dreamcast kit (`base/`, `runtime/`) | Saturn kit (`saturn/`) | Xbox 360 / GameCube / PS2 | Where it goes |
|---|---|---|---|---|
| Window and placement (work area, chrome, fit, full screen) | `base/host_window.cpp`, `host_place.h` (SDL3) | `sat_video.c`: own Win32 window on its own thread, fixed 960x720 | Xbox 360 kit starting; GameCube none; PS2 runs on the Dreamcast kit | `base/host_window.cpp`, `host_place.h`; the native picture size comes from the console |
| Presenting (device, swap chain, drop policy, frame-rate readout) | `runtime/gfx/gfx_device.c`, `gfx_present.c` (D3D11 flip model, waitable) | `sat_video.c`: StretchDIBits into the window | none yet | `base/` presenter: a console hands over a CPU frame or draws into the frame target the base owns |
| Titlebar menu | `base/host_menu.cpp` (Display, Audio, Input, Help) | none | none | `base/host_menu.cpp`; console-only entries through `console_menu()`, left out when they cannot apply |
| Settings file | `base/host_settings.cpp`, `host_data.cpp` | none (`--save` only) | none | `base/` as is |
| Copy agent prompt (copy icon, Help entry, F9, scene dump, clipboard prompt) | `base/host_copy_button.cpp`, `base/scene_dump.c`, `scene_json.c`, `scene_host.c`, F9 in `host_window.cpp` | none (`sat_shot.c` writes a PNG on `--shot`) | none | `base/`; a console adds fields through `console_scene_fields()` |
| Session recording and replay | `base/session.c` (pad, SDK timer, RTC, saves) | none | none | `base/session.c`; the console's own channels (timer, RTC, saves) are registered, not built in |
| Game log and the `play summary:` line | `base/game_log.c`, `game_log_end.c`, `play_stats.c`, `host_data.cpp` (`rotate_logs`) | `sat_main.c` writes `saturn_port.log` with `port_log`; `sat_perf.c` has its own percentile report | none | `base/` |
| Crash handling | `base/host_crash.cpp` (dump, log line), `game_log_end.c` (crash recording, replay commands) | `watchdog` and `port_exit` only | none | `base/` |
| Controllers | `base/input.c`, `live.c` (SDL3 gamepads and joysticks, 4 players, script) | `sat_pad.c`: window key messages + the first XInput pad, phantom filter (`ca39b77`) | none | `base/input.c`: every button readable, the assignment table, the phantom-pad filter; the console lists its actions |
| Aspect options | `gfx_aspect.c` (987345d): fit, 4:3, 16:9, 21:9 | none (always 4:3 in the window) | none | `base/` |
| In-between frame presenting, real-clock pacing and timing stats | `runtime/frame_extra.c` (in-between frames with undo), `host.c` (60 Hz clock), `play_stats.c` | `sat_sys.c` (vertical blank on the real clock), `sat_perf.c`, pacing report in `sat_video.c` | none | policy, fps cap, clock and statistics in `base/`; undoing a console's state stays with the console |
| Audio output device | `runtime/gfx/gfx_audio.c` (SDL3 stream, volume, Phangrade volume) | `sat_audio.c` (wave device) | none | `base/` device and volume; the console fills the samples |
| Launcher build and run path | `tools/build.py` (CMake + vcpkg), `play/launch.py` | `tools/build_saturn.py` (plain `cl`, no SDL) | none | one build path: `tools/build.py` builds `base/` into every kit; `build_saturn.py` uses the same file list |
| Phangrade integration | `base/host_phangrade.cpp`, `install.c` (`--setup-only`, `--verify-assets`, `--version`) | none | none | `base/` |
| Common test harness | `tests/test_port_kit.py`, `test_port_contract.py`, `tools/regress.py` | `tests/test_saturn_hw.py` | none | `tests/test_port_base.py` (drift) and the shared replay compare |

**State of the parts now** (the Dreamcast and Saturn kits both stand on `base/`; `tests/test_port_base.py` fails when a kit grows its own copy of one):

| Part | Dreamcast | Saturn |
|---|---|---|
| Window, menu bar, settings, Copy agent prompt (copy icon, Help entry, F9), game log, crash handling, Phangrade, `--setup-only` / `--verify-assets` | the base | the base: `sat_video.c` no longer makes a window (the old Win32 thread is gone) |
| Controllers | the base reads every button and `Input > Controls` assigns them to the Dreamcast's buttons (`console_actions` in `console_dc.c`); the game reads the `HostPad` | the base reads every button (LB, RB, stick clicks, Back, Guide, right stick) and `Input > Controls` assigns them to the Saturn's 13 buttons (`console_actions` in `sat_input.c`, defaults A B C X Y Z L R Start and the D-pad; a choice is saved as `bind-<name>`) |
| Aspect options (4:3, 16:9, 21:9, Fit window) | the world fills the shape | accepted, saved and shown in the menu, but the composed VDP2 picture is a fixed width, so it stays 4:3 with bars at every shape: **not widened yet** |
| Session recording and replay | the base | the base (pad changes). The Saturn has no timer or RTC channel (its RTC is a fixed date). `--fixed-clock 1` runs it on a virtual clock (`sat_sys.c`, `sat_clock_ticks`): time moves only when the guest waits and by 1 ms at each interrupt poll, so a run repeats exactly (a headless run twice gives the same picture and WAV) |
| Scene dump | `scene_dump.json` fields by global name | `scene_dump.json` fields by guest address (`"addr"` with `"type"`, `tools/scene_map.py`, written by `gen_saturn.py` into `scene_map.c`) plus `console_scene_extra`: VDP1 and VDP2 registers and the frame size. Big-endian numbers are handed over byte-reversed (2, 4 and 8 byte fields) |
| `play summary:` line | `play_stats.c` hooks | `sat_perf.c` feeds the work of each vertical blank to `play_stats_tick`; the draw is `sat_compose` |
| Audio output | `base/audio_out.c` (SDL3 streams, mixed by the system, the base's volume); `gfx_audio.c` fills it | `base/audio_out.c`; `sat_audio.c` and `sat_cdda.c` fill it |
| Presenting | `base/present.c`, `present_device.c`, `present_aspect.c`; `gfx_present.c` is the console's hooks | the same, through `base/present_cpu.c`: the frame is drawn as the 4:3 screen with bars until the kit composes it wider |
| Waiting for the real clock (a late tick is repaid, `--catch-up`) | `base/pace.c`, `base/pace.h`; drawing and undoing in-between frames and the `--fps` cap stay in `frame_extra.c` | `base/pace.c` |
| Build | `tools/build.py` (CMake + vcpkg) | `tools/build_saturn.py` compiles the kit and `base/` with `cl` (C++20 for `base/*.cpp`) on the pinned toolset (`vcvars_args`), SDL3 from vcpkg beside the exe |

### Adding a console

1. `python tools/game_setup.py console <id> "<Name>" "<Game>"` writes `port/<id>/console_<id>.c` from `base/template/console_template.c`:
   every function of `base/console.h` stubbed (run state, scene dump, view, menu hook, actions, image check, presenter hooks,
   volume, `port_main`). It compiles at `/W4 /WX` as it is (`tests/test_port_base.py`).
2. Fill in the TODOs, which are the machine only: tick, frame, guest memory for the scene dump, audio samples, the pad's
   buttons (`console_actions`) and the disc check. Present each tick through `cpu_present(frame, w, h)` (a CPU-composed
   frame) or supply the `gfx_*` functions for a renderer of your own, then `game_log_poll`, `scene_dump_poll`, `play_stats_shown`.
3. Build `base/*.c` and `base/*.cpp` (C++20) with the kit and link SDL3, `bcrypt`, `dbghelp`, `comctl32`: `tools/build_saturn.py` is the
   short example. Do not make a window, read a pad, open a log, open an audio device, make a Direct3D device, wait for the
   real clock or build a menu: the drift test fails the build. A wait that is not the game's tick (a sampler thread, a
   watchdog) carries a `/* not the tick: ... */` comment on its line.
4. A game on the console starts with `tools/game_setup.py setup` as before (`port_starter/scene_dump.json` is written for the game log's map).

### Not on the base yet

GameCube has no runtime kit; PS2 runs on the Dreamcast kit. The Xbox 360 kit (`targets/xbox360_ppc/port`) stands on the base with its own
start-up and `console_x360.c`, built x64; it has no launcher path yet.
`tests/test_port_base.py` also scans `targets/*/port/`, so a kit that appears there is held to the same rules.

## When a game gets a port

After the readable stage: every game-range function has readable C in `<workspace>/readable/src`. The engine's
extras (`project.json` `extra_functions`) are compiled with `--engine`; without it they stay stubs.

## Layout

| Path | Role |
|------|------|
| `runtime/` | Always compiled, no game names: start-up and watchdog (`main.c`), guest RAM and its P0/P2 mirrors (`guest_mem.c`), the GD-ROM image reader (`gdi.c`: `.gdi` or Redump `.cue`), calls through guest addresses (`dispatch.c`: jump thunks on the boot image's function starts, `--thunks on|off`, and a vectored exception handler over the generated table for the rest), logging stubs and the must-work stop (`port_stub.c`), the SH-4 instructions readable C uses (`sh4_rt.c`), the float math the 32-bit CRT only has inline, and rand (`port_libm.c`), host services and the 60 Hz vertical blank (`host.c`), in-between frames drawn and undone while a frame waits for its vertical blank (`frame_extra.c`: `--present tick|interp`, `--interp-frames N`, `--interp-verify 1`, `--fps N`, `--interp-copy-threads N`, `--interp-scene on|only|off`, game.json `interp_scene` for only: a game with no draw function gets in-between frames anyway, and only uses them instead of the game's draw function, `pvr/pvr_blend.c`), accidental boot-ROM accesses through a NULL guest pointer, finished as the console does: reads as zeros, stores skipped (`rom_read.c`, from the exception handler), a first-fit heap (`heap.c`), the log |
| `runtime/gfx/` | The host graphics layer: an SDL3 window (borderless full screen at the desktop mode, `--window WxH`, `--headless`), a Direct3D 11 device and a waitable flip-model swap chain (a frame the display would not take is dropped, never waited for), the frame target in the chosen shape (`gfx_aspect.c`: `--aspect fit|4:3|16:9|21:9|W:H`, default the window's shape for a game with game.json `widescreen`, else 4:3) presented with bars where the shapes differ, and PNG frame dumps (`--dump-frames`, `--dump-every`; `--dump-extra N` adds the in-between frames after each) |
| `runtime/media/`, `runtime/gfx/gfx_movie.c` | Media decoders, no game names, written from the published formats (`media/CLEANROOM.md`): CRI ADX for streamed music and movie sound, and Sofdec movies (the MPEG-1 system stream and MPEG-1 video). Their sound is mixed beside the AICA's voices at each vertical blank (`gfx_audio.c`); a movie's picture is drawn over the frame (`gfx_movie.c`). A game reaches them through the `adx_mw` SDK module |
| `runtime/arm7.c`, `runtime/arm7_ls.c`, `runtime/arm7_scan.c`, `runtime/arm7_native.c`, `runtime/aica_arm.c`, `runtime/aica_arm_probe.c`, `runtime/aica_chan.c` | The sound processor as the console has it: an ARMv4 core runs the game's own sound driver from the sound RAM, and the AICA's registers, timers and interrupts drive the kit's voices (`aica.c`). `arm7_scan.c` runs the driver's idle byte-scan loops (three exact shapes) a whole number of iterations at a time, with the interpreter's registers, flags, cycles and instruction count (game.json `aica_arm_fast`; env `DP_ARM_FAST=0` or `1` overrides it; proof log `DP_ARM_LOG`, per-blank trace `DP_ARM_TRACE`). `aica_arm_probe.c` holds the measures and proof tools (all off unless the environment asks). `arm7_native.h` and `arm7_native.c` are the table the driver's executed code becomes when `tools/arm7_translate.py` turns the recorder's output (`DP_ARM_PCS`) into one C function per basic block, run chained with the interpreter's own cycle and instruction counts and the interpreter for whatever the table does not cover (game.json `aica_arm_native`; env `DP_ARM_NATIVE=0` or `1`; `DP_ARM_FAULT=1..4` plants a fault to show the proof comparison catches it). Off unless game.json sets `aica_arm`; the game's sound hardware functions then do the original's register and sound-RAM access (`aica_arm.h`, `docs/CLEANROOM_AICA_ARM7.md`) |
| `pvr/` | The PowerVR side the SDK modules hand work to: texture memory, fog, background plane, clip, and the TA-list renderer (`pvr_render`); `pvr_todo` logs each unhandled case once. No game names |
| `pvr/pvr_read.c` | The frame buffer read back: `pvr_read_frame` (all of it as RGB565, what `Km_ReadSurface` of the frame buffer surface returns) and `gfx_read_pixel` (platform.h: one pixel of the displayed 640x480 buffer as a video memory read would give it, RGB565). Replaces a game's own frame-buffer or VRAM pixel-read function, which is then a few lines calling `gfx_read_pixel`; a game sets nothing but lists that function under game.json `hardware` |
| `pvr/pvr_twiddle.c` | Texture decoding to RGBA8 (twiddled, VQ, rectangle, stride, YUV422, palettized, mip chains), the layouts in the file's head comment. The twiddle index is a bit-spread and a shift (no loop or division), a palettized level decodes its palette once, the YUV products come from tables and a pair is read once, and each 16-bit format has its own loop; about 1 ms a tick off Toy Commander's movie scene. `tests/test_pvr_decode_same.py` decodes thousands of random textures here and in `tests/fixtures/pvr_twiddle_reference.c` (the decoder as it was) and compares every texel |
| `pvr/pvr_screen.c` | Where the 640x480 screen is in the draw target when the frame is wider or taller than 4:3: scissor and clear rectangles map into its middle, and a clip spanning the screen spans the target. `tests/test_gfx_aspect.py` checks it with `gfx_aspect.c` |
| `pvr/pvr_blend.c` | In-between frames for a game with no draw function (`--interp-scene on`, with `--present interp`): the renderer keeps the scene it parsed before this one, and an in-between frame draws this scene's batches with each vertex moved part of the way back to where the same vertex was a tick ago. A batch is matched with the old one of the same list, render state, scissor and vertex count; a triangle that moved over 80 pixels, changed depth by more than half or moved its texture coordinates by more than half a texture is drawn as it is. No game code runs and the tick's frame is untouched. `tests/test_pvr_blend.py` works small scenes by hand |
| `pvr/pvr_blend_hold.c` | What an in-between frame keeps of the scene drawn last: its textures (a reference each) and registers, since the game goes on after a flip that does not wait, and the render target outside the clip rectangle, put back after each in-between frame |
| `pvr/pvr_pal.c` | Palette RAM (1024 entries) and PAL_RAM_CTRL (ARGB1555, RGB565, ARGB4444, ARGB8888), and with `pvr_twiddle.c` the palettized textures that index it (pixel formats 5 and 6: twiddled 4- and 8-bit indices, the TCW's palette selector, mip levels); the texture cache decodes one again when the entries it reads change. Kamui 1's and Kamui 2's `kmSetPaletteMode` / `kmSetPaletteData` (`kamui1_misc`, `kamui2_misc`) feed it; a game turns it on with either module. Texture packs never replace a palettized texture |
| `pvr/pvr_pack.c`, `pvr/pvr_pack_png.c`, `tools/texture_pack.py` | Texture packs: replacement images (PNG, any size) for guest textures, host-side only, in place of what the decoder makes from texture memory (a VQ texture's blocks, say). Keyed by the texture's content, so it does not depend on where a texture lands; mip-mapped guests get a box-filtered chain. Off unless a game sets game.json `texture_pack` (or `--texture-pack DIR`), and always off in a check run (fixed clock) unless asked with `--texture-pack on|NAME|DIR`; `--texture-pack on|off|NAME` and the Display menu's Texture pack entry choose it (settings.txt `texture-pack`). A release carries its packs in `packs\` beside `port.exe` (`tools/package_release.py`). Guest memory and timing are untouched. A game's pack builder uses `tools/texture_pack.py` (key, Kamui placement, PNG, index); see Texture packs below |
| `pvr/ta_parse.c`, `pvr/pvr_state.c` (debug options) | Reading a scene by hand, diagnostics only: `port/tools/ta_volumes.py DUMP` checks a dump's modifier volumes are parameters and closed; `--ta-dump SCENE` (`odd`: the first scene with a stray parameter) writes that scene's TA lists as words to `ta_scene_SCENE.txt`; `--dump-vram SCENE` writes the 8 MB texture memory as the decoder reads it at that scene to `ta_vram_SCENE.bin` (decode a texture word from it with `tests/test_pvr_pack.py`'s driver); `--pvr-debug nocull,nofog,nodepth` switches a stage off. Both files go to the working directory. Replaces nothing; no game.json setting, a run passes the option (or a `bu_sweep` script's `args`). Test: `tests/test_pvr_vram_dump.py` |
| `sdk/` | SDK replacements a game opts into (game.json `sdk`). Each compiles against the game's generated `game.h`, so the names it defines must be the game's registry names; the header comment of each module lists what it needs |
| `tools/gen_port.py` | Writes `<game>/build/gen`: `game.h` (globals as lvalues on their guest addresses), logging stubs, the dispatch table, and `port_config.h`, `port_entry.c`, `port_files.cmake` from game.json, and `scene_map.c` from the game's `scene_dump.json` (`tools/scene_map.py`). Each file is written only when it changed, so an unchanged build is seconds. With translator C it warns (stderr and gen_report.txt) when the workspace has `readable/src` files but no `state/readable.json`: no readable C then counts as passing and a mix build quietly takes translator C everywhere |
| `tools/gen_overlays.py` (`overlay_slots.py`, `overlay_cmake.py`, `runtime/overlay.c`) | Overlays, for a game with `overlays.json` (units: `id`, `decomp` workspace, optional `ranges`): writes `<game>/build/gen/overlays`, one static library per workspace with its functions renamed `<workspace>__<name>` (a forced-include `prefix.h`), stubs, and trampolines for calls out of its range, plus each unit's identity windows (16-byte code windows where it differs from every unit loaded over the same RAM). A call into overlay RAM runs the unit whose windows match guest RAM now; with none, `OVERLAY MISS` is logged once per group, the call returns 0, and a checked build stops. A game without overlays links an empty table. With translator C (`overlays.json` `translator_c`, or build.py `--tx-ovl`), a workspace's judged functions compile too, with its prefix and its own tc_call glue (`tools/overlay_tx.py`), as gen_port's mix does for main. A **slot unit** (`overlays.json` `slot`: `base` it is written at, `region` `[lo, hi]`, `align`) is code the game loads at any `align` boundary of the region (one file, several places, as a fighter's per-character code): a call at A finds the slot base `A & ~(align-1)`, the units of the region compete there by their windows, and each base keeps its own; `overlay_delta()` is the distance from `base`, and translator C reads the addresses it folded in as `addr + tx_ovl_delta` (`overlay_slots.py`, set by tc_call around each call into the unit). build.py runs it after gen_port.py |
| `base/scene_*.c`, `base/session.c`, `base/host_copy_button.cpp` | Copy agent prompt: the scene dump's JSON, BMP and clipboard prompt, the session recording and replay, and the menu bar's copy icon (see below) |
| `base/game_log.c`, `game_log_end.c`, `base/host_data.cpp` (`rotate_logs`) | The game log: the player's pad, keyboard and menu actions, window events, the scene map's fields as they change, snapshots, the run's last line, the crash recording and its replay commands, and the last eight logs kept (see "Game log") |
| `tools/build.py` | gen_port, CMake + Ninja under the 32-bit MSVC environment, build, and with `--run` the run and a symbolized log. Every step is bounded; a timeout kills the process tree |
| `tools/build_gc.py` | Old agent builds: when `build.py` exits it removes the `build/NAME` folders of this kit (a configured build whose `PORT_GEN` is `NAME_gen`) with their `NAME_gen`, `NAME_run`, `NAME_regress`, `NAME_lockstep` and `NAME.log` once nothing in them was written for 5 days (newest file, or the `.used` stamp `build.py` touches) and no process uses them. `x86`, `x86chk`, `gen`, `run`, `regress`, `reference`, the running build and anything it cannot confirm as a build stay; one line says what went. At most every 6 hours, never in the launcher's `--progress` view; off with `build.py --no-build-gc` or `DP_BUILD_GC=0`; a game needs nothing in game.json |
| `tools/provenance.py` | Where a golden came from: a `provenance.json` beside the goldens and log baselines. Regress fails a file with no record; a refresh from the port's output is labelled a native baseline, which supports no fidelity claim, and fails over original evidence (AGENTS.md "Test rules"). |
| `tools/regress.py`, `tools/frames.py` | The regression run (`build.py --regress`): each `<game>/tests/*.script` bounded, headless, fixed clock, tick presenting; fails on an exit other than the watchdog's, a log class (TODO, STUB, `ta: list`) not in `tests/baseline/`, a frame unlike its golden in `tests/goldens/` or with no golden (per-pixel error, SSIM, mean error and the largest changed cluster, with a side-by-side sheet), or a frame under the script's `filled` key with an empty edge band (a full-screen picture that drew only part of itself; needs no golden). `--accept-log` / `--accept-frames` update them |
| `tools/rate_compare.py` | The frame-rate comparison: one regression script's run shown at the game's tick (`--present tick`), then with in-between frames at the display's rate (`--present interp`); both go through the script's start on the fixed clock and turn to real time at a chosen vertical blank (`port.exe --fast-until N`) |
| `tools/lockstep.py` (`lockstep_emu.lua`, `lockstep_frames.py`, `lockstep_watch.py`; `runtime/lockstep*.c`) | The lockstep check: Flycast plays from a scripted input and records the game's watched state (the game's `lockstep.spec`) each frame; the port replays those frames (timer, pad, RTC) and traces the same state; the compare names the first divergent frame and its words. `--watch NAME[J]@N` names the functions that wrote a word in a frame (an x86 debug register); `--baseline TRACE` reports what a reference build does not share |
| `tools/bu_sweep.py` (`runtime/diag.c`) | Bring-up sweeps, diagnostics only (never a play build): `sweep` runs the port on a scripted input and at each blocker (fault, must-work stub, exit, hang, wrong mode) records the guest function, its chain, the vertical blank and the last frame, then steps past it with `port.exe --diag-skip FILE` (the host function becomes a logging stub returning 0 from one blank before the blocker, no rebuild) and runs again; `weak` lists the functions a run reached (`--diag-cover FILE`, one-shot breakpoints) with their port C kind and ledger verdict, and A/B-tests weak readable functions against their PASS translator C (`tx_first` in a copy's game.json). Both switches are off by default; a game sets nothing |
| `tools/gen_sy_font.py` | Writes `sdk/sy_font_table.h`, the open 12x24 glyphs `sdk/sy_font.c` hands a game in place of the Dreamcast BIOS font; the table is committed, so a build needs neither the font nor Pillow |
| `tools/sdk_ref.py` | Ghidra exports of named SDK/engine functions into `<game>/build/ref` for reading while writing a replacement (never copied) |
| `tools/aica_bank.py` | Sound data read from files, no game names: the Dreamcast/NAOMI sound driver's sample banks (DTPK: the sample table, each sample's rate from its program records' transpose), NAOMI SPSD streams (mono, or stereo in 0x2000-byte blocks), and the AICA's sample forms (16/8-bit PCM, Yamaha ADPCM decoded as `runtime/aica.c` plays it) written as WAV (`dump FILE OUTDIR [--offset N --size N]`, `list`). An asset reader for listening and comparing, not part of a build; a game turns nothing on (tests: `tests/test_aica_bank.py`) |
| `saturn/` (`tools/gen_saturn.py`, `tools/build_saturn.py`) | The Saturn kit, for the SH-2 translator's judged C (`targets/saturn_sh2`): a different machine from the Dreamcast kit, so its own folder; a game opts in with a `saturn` object in game.json (CONTRACT.md, Saturn ports). No game names or guest code addresses (the entry, the stack, the slave's stack (`slave_sp`: the game loads it from its own memory, which the translated slave program does not run, so a missing value leaves the slave on a default stack that can overlap the master's frames and zero its saved registers) and the sound hooks come from game.json). `gen_saturn.py` turns a folder of `ADDR.c` into chunked units, the sorted function table (`sat_table.c`) and `sat_game.h`; `build_saturn.py` compiles kit and chunks at `/W4 /WX`, 32-bit |
| `saturn/sat_call.c`, `sat_cpu.h`, `sat_where.c` | Calls between translated functions (the translator's TcArgs block, the shared r8..r14, tail jumps, the pr-popped return), the two SH-2s as Windows fibers (master on the game's thread, slave started by MINIT, taking turns at polls), logging stubs for an address with no translated function, and the reports (`--profile`, the call chain a halt or stub prints; `--trace-from ADDR N` lists calls with r8; `--watch-saved` names callees that hand back a changed r8..r14, the sign of a clobbered stack frame) |
| `saturn/sat_mem.c`, `sat_bios.c` | The SH-2 bus (both work RAMs, the cache-through views, the on-chip divider and free-running timer, the regions the devices claim) and the BIOS services the game reaches through the pointers in work RAM, as native functions (no BIOS image runs) The free-running timer honours TCR's divider (the movie player's /128). |
| `saturn/sat_bup.c` | The BIOS backup-RAM library (BUP): Init, SelPart, Format, Stat, Write, Read, Delete, Dir, Verify and the date calls on the console's internal 32 KB memory, reached through the pointers at work RAM 0x06000354/0x06000358. The saves live in one host file (`--save FILE`, default `saturn_backup.bin`), rewritten after each change |
| `saturn/sat_perf.c` | Frame cost for the runtime target (docs/RUNTIME_TARGET.md): per vertical blank the thread's work apart from waits, split into game tick, VDP2 compose, VDP1 software draw and showing; the game log ends with median, p99 and worst of each, per-300-frame stretches and process CPU time. `--sample` adds a 1 ms sampler of the running guest function |
| `saturn/sat_sys.c`, `sat_scu.c`, `sat_smpc.c` | Waits the game burns are named so the thread sleeps to the next vertical blank (`--vblank-wait off` keeps burning): a loop on a flag only the vertical-blank handler sets (game.json `vblank_flags`, offsets from GBR), 32 drive status questions in a frame (`sat_idle_ask`), and a main loop's eighth turn in a frame (game.json `main_loops`). The clock and interrupts (vertical blank on the real clock, delivered at safe points through the game's vector table); `--det` (a test aid, never the launcher's) drives the vertical blank, the raster and the free-running timer from the machine's own progress instead of the real clock, so a run with the same `--input` repeats exactly and a replay can be compared run to run, the SCU (mask, status, the three DMA levels, direct and indirect) and the SMPC (commands, INTBACK clock and peripheral data, SSH on/off, MINIT/SINIT; the game log shows each change of the pad data the game reads, as `smpc: vb N the game read pad data XXXX`) On the real clock the game keeps at least 5 ms after a vertical blank's work, so a frame whose picture took too long runs late instead of leaving the game no time. Port 2 is reported when a second controller is connected or a script names `P2.` (Robo Pit's VS MODE needs it). |
| `saturn/sat_disc.c`, `sat_cd.c`, `sat_cdbuf.c` | The game disc (a `.cue` of per-track bins, ISO 9660 on track 1) and the CD block: HIRQ and command registers, sectors flowing through the 24 selectors and 200-block buffers, the file-system commands and the data port. Data plays deliver 150 sectors a second (a 2x drive; `--cdrate N` to change it): the game's movies pace their frames on the arrival, and 1200 a second made them play in bursts. CD-DA plays through the default wave device in a window run. `--cdrate`, and the picture-pacing lines in the log (`pacing:` gap between presents, how many presents each picture was held) are how stutter is measured on the real clock CD audio is delivered by the time passed on the real clock; the status command answers without the periodic flag (Robo Pit's music waits for exactly "paused"). |
| `saturn/sat_vdp.c`, `sat_vdp1.c`, `sat_vdp2.c` | The video chips' memories and registers, and a software renderer: VDP1's command list (sprites, scaled and distorted, polygons, lines, clips, Gouraud, mesh, shadow, half-luminance) into the draw framebuffer, and VDP2's scroll planes, the sprite layer and the back screen composed by priority (no rotation plane, windows, line scroll or mosaic yet). Colour RAM in mode 0 (RGB555, 1024 colours) ignores address bit 11, so the CPU's upper 2 KB mirrors the lower; a 256-colour 1-word scroll plane takes its bank from pattern-name bits 14-12. Test aids: `--v1dump N` (VDP1 commands every N frames), `--layers HEX` (which layers draw), `--peek A,B` (guest words with each shot's report), `--vwatch LO,HI[,VB]` (which guest function changes VDP2 VRAM in a range, one line per frame with the call chain; `--vwatch 25f80000,25f80200,VB` instead logs every non-zero VDP2 register at that vertical blank, for the priorities and plane enables), the CRAM and NBG1-index summaries in the shot report VDP1 walks a quad without a divide per pixel and skips what cannot land (off the screen or out of the user clip); byte-identical pictures on replays. |
| `saturn/sat_m68k*.c`, `sat_scsp.c`, `sat_scsp_mix.c`, `sat_audio.c` | The Saturn's sound, off unless game.json sets `saturn.sound_cpu`: a 68000 core (`sat_m68k.c` state, operands, flags and the run loop; `_g0`, `_g4`, `_g8` the instruction groups, `tests/sat_m68k_test.c` on hand-assembled programs) runs the game's own sound driver from sound RAM, the SCSP's registers, timers (A/B/C), interrupt levels to the 68000 and the SCU's sound request, and 32 voices (`sat_scsp_mix.c`: 16/8-bit PCM, loops, envelope, level and pan; no DSP, LFO or FM yet) mixed at 44100 Hz one video frame at a time (on the real clock, as many samples as the time since the last frame held). `sat_audio.c` plays them on the default wave device, and `--wav FILE` writes them (how a headless run is checked). A run logs each voice key-on (the first 60) and the 68000's state every 250 frames, and the perf report has an `audio` column. Robo Pit: ~0.7 ms median, 2.8 ms p99 a frame in the interpreter |
| `saturn/sat_video.c`, `sat_shot.c`, `sat_input.c`, `console_sat.c`, `sat_scsp.c` | The Saturn's picture and pad on the base: `sat_video.c` composes the VDP2 picture once per vertical blank and hands it to the base's presenter (`base/present_cpu.c`; `--headless` opens no window; `--shot VB FILE` and `--dump-frames` write PNGs, `sat_shot.c`), `sat_input.c` maps the base's pad to the Saturn's 13 buttons (`console_actions`, assignable in Input > Controls; a scripted `--input` run uses the defaults), `console_sat.c` is the Saturn's side of `base/console.h`, and `sat_scsp.c` holds the sound side's memory: sound RAM and the SCSP registers (with no `sound_cpu` in game.json, storage with the game's sound-driver replies `sound_clear` and `sound_stream`, and no sound) |
| `tools/symbolize.py`, `tools/game_config.py` | `port.exe+OFFSET` in a log named from the linker map; game.json loading and the generated per-game files |
| `CONTRACT.md` (`tools/game_config.py`, `tests/test_port_contract.py`) | The game contract, version 1.6.0: what a game may rely on (game.json keys, `platform.h`, SDK modules, options, exit codes, enhancements) and what a major, minor or patch change means. Replaces nothing; a game sets game.json `contract` to the version it was built against (a different major stops gen_port, a missing key warns once) |
| `runtime/tx/tx_rt.c` (`port_hook_call` in `platform.h`) | Call hooks: a game registers `port_hook_call(addr, before, after, arg)` and the runtime runs the hooks around each call to that guest address that goes through translator C's `tc_call` (any function, up to 32 hooks). For a game that changes what a function reads for the length of one call and puts it back, or notes that a call happened. Replaces nothing; a game sets nothing in game.json, it only calls it (Spawn's character select uses it) |
| `runtime/prof.c` (`prof.h`) | The frame-time profiler: `--prof on` times each vertical blank's host work by section (the ARM7 sound driver, sound tick, media decoders, TA parse, Direct3D calls, in-between copies and draw, present, read-back, and the game's own translated C as the remainder) and the scene's GPU time (D3D11 timestamps, with a flush after each: unflushed they measured the driver's submit delay, 15-30 ms for a 1.5 ms scene), and logs mean / 95th percentile / worst per row every 600 blanks and at exit, with the worst frames. `--prof-from V`, `--prof-csv FILE`, `--prof-clock cycles` (thread CPU time: use it for A/B on a loaded machine), `--prof-sample FILE` (an Eip sampler for `tools/symbolize.py`). Off by default: a call is one flag test (`--prof-sample` now also names who called a system wait: a sample in a system DLL is the port.exe code that called it, bit 31 set) |
| `runtime/gfx/gfx_audio.c` | The sound output: the AICA mixed at each vertical blank into an SDL3 stream and `--audio-dump`. In real-clock play the mix is kept ahead of the clock by the longest recent gap between ticks (a slow scene ticks at half the blanks and used to leave the device silent a third of the time), underruns counted and logged every 10 s ("audio: ... underruns"); `--audio-ahead off` is the old fixed queue, for comparing. Fixed-clock runs and the WAV dump are unchanged. Replaces nothing; nothing to set |
| `runtime/prof_calls.c` (`prof.h`) | The profiler's count of translator C's `tc_call` by guest address (`--prof`, from `--prof-from`): calls a blank, the 24 most called targets, and the order of 400 calls (the shape of a loop). It found Toy Commander's movie scene polling `acDigiGetCurrentReadPosition` about 13,000 times a blank. Part of `prof.c`'s family, split off for the file-size cap; off with `--prof` |
| `runtime/tx/tc_rt_inline.h`, `runtime/tx/tx_spin.c` | The judge's `tc_rt.h` for the translator C, wrapped (`tx_spin.c`: the spin words `rd32` waits on): guest memory accesses (`rd8..wrf32`) and `sh4_fr`, `sh4_set_fr`, `sh4_r`, `sh4_set_r` are inline here, with the same results as `tx_rt.c`'s and `sh4_rt.c`'s (areas absent on a console read 0, the store-queue window skipped under `sq_queue_only`, a named spin word takes the out-of-line read). `txc` forces it in first (`/FI`), and it includes the judge's header (`PORT_TC_RT_H`); the translated C is unchanged. `PORT_KIT_O2` (default ON) builds the kit's own C (runtime, SDK, `tx_rt.c`) at `/O2`; the generated and game C stays at the judge's `/O1` |
| `base/play_stats.c` (`play_stats.h`) | The play session's summary: three hooks (the wait for the blank, a draw, a shown frame) keep a 10 us histogram each, and the exit writes `play summary: shown_fps F output WxH frames N tick_ms M/P/X draw_ms M/P/X frame_ms M/P/X` (median, 99th percentile, worst, in ms) before the log's `END:` line. It is what `tools/scorecard.py` gate 6 reads, so the 144 fps target is measured on a session the user played. On in real-time play runs, off in check runs (`--fixed-clock`, `--replay`, `--headless`: goldens and baselines do not move); `--play-summary on\|off\|auto`. Not `prof.c`: no sections, no GPU timestamps, always on at about 3 clock reads a shown frame |
| `KNOWN.md` | Every known difference between the kit and the console, with where it is handled and which check shows it. Replaces nothing; a game sets nothing |

SDK modules (each one's name and what it replaces), game.json's keys, the options a game's scripts use, the exit
codes and the enhancements are the game contract: [CONTRACT.md](CONTRACT.md), the only copy. The console's behaviours
the kit does not reproduce yet are listed in [KNOWN.md](KNOWN.md).

## Build and run

```bash
python <DecompPipeline>/port/tools/build.py --game <game port folder> --engine --run --seconds 60
```

Needs Visual Studio's 32-bit tools (`VCVARS32`, or `vcvars32.bat` beside `config.local.json` `"vcvars"`), CMake and
Ninja (`NINJA` or on PATH). `--checked` builds unoptimised with `/RTC1` in `build/x86chk`. Always run with
`--seconds`: the watchdog ends the run and logs where the game thread was. The log is `build/run/port.log`,
symbolized in `build/run/port_sym.log`; a fault logs the registers and the return addresses on the stack.
`--jobs N` sets the compile jobs (default: every logical processor). A game's `Start Game.bat` builds with
`--progress`: one line counting the files as they compile, rewritten in place, diagnostics in full as they come, and
"Game is up to date." when nothing changed; the full output goes to `build/build.log`. Its `Play.bat` starts the last
build with no build step. Any change to the registry rewrites the generated `game.h`, which every readable file
includes, so a new name or readable function rebuilds them all.

## Texture packs

A pack is a folder: `index.txt`, one `KEY FILE` per line (`#` starts a comment), and the PNG images it names. KEY
(16 hex digits) is the FNV-1a 64 hash of the texture control word's format bits (`tcw & 0xfe000000`), the width,
height and stride (little-endian u32s), then the texture memory bytes the decoder reads from the TCW address (a
16-bit twiddled mip chain from its 1x1 level, 6 bytes in). The VQ codebook is part of it. An image may be any size:
higher-resolution packs work the same way. `tools/texture_pack.py` computes the key from a texture's bytes as the
game hands them to kmLoadTexture (`kamui_vram`, `kamui_tcw`), writes the PNGs and the index (`Pack`), and checks a
pack (`texture_pack.py check DIR`). The renderer hashes a texture only when it decodes it, which is after the guest
wrote its memory, so an unchanged texture is never hashed again; the log names the pack and its first hit.
An index line may end in a variant word, `KEY FILE xbox` or `KEY FILE playstation`: it applies only while that style
of controller is in use (`--pad-style auto|dreamcast|xbox|playstation`; auto follows the type SDL reports for the first
connected gamepad, once a second, and only in a run that reads real input, so a check run, replay or headless run
never has one), a variant line wins over the line without one for its key, and with no variant in use nothing changes.
A line may also end in `rim=RRGGBB`, for a model that draws its sides untextured right after the textured face: those
vertices take that colour, scaled by their strongest channel so the game's lighting stays.
`tools/pad_icons.py` draws an Xbox-style and a PlayStation-style pad (own simple drawings, no manufacturer art) into the
boxes of a game's controller texture and writes both variant lines, e.g. Spawn's player-count screen: key
`342a3e1bf2e7794b`, `--size 256 --top 0,128,122,128 --front 135,187,121,69 --flip-v`. All the draws of a texture show
the same variant, so a screen that draws four slots from one texture shows one style on all four.
`--texture-keys DIR` lists the textures a run decodes (a PNG each, named by its key, and `keys.txt` with the size,
format and first vertical blank), so a pack for a disc game can start from a replayed scene. Don't commit a pack made
from a game's textures or other people's art. `tests/test_pvr_pack.py` builds the renderer's lookup with its decoder;
a game can run that driver over its own textures (before/after sheets).

**Packs in a release.** game.json's `texture_pack` path exists only on the machine that built the game, so a release
carries its packs beside the program, in `packs\<folder>\`: the game's default under its own folder name, found there
first, and any number of optional packs. The Display menu's Texture pack entry offers Off, Game's default and each
optional pack by its folder name (15 at most, or 14 for a game with a default). Off is off for every pack. An optional
pack shows over the game's default, which still gives the keys the optional pack lacks; it stays off until the player
chooses it, and the choice is saved as `texture-pack=NAME`. `--texture-pack NAME` is the same choice on a command line,
and a saved name whose folder has gone leaves the game's default. `--texture-pack DIR`, a path, replaces the game's
default as before, for a pack in the making.
`tools/package_release.py --exe PORT.EXE --out ZIP [--game-pack DIR] [--pack NAME=DIR]...` makes the release ZIP: the
program, the DLLs beside it and, from each pack, only `index.txt` and the images it names. An optional pack must also
have a `NOTICE.txt` crediting the owners of what it holds, which ships in its folder and goes in the release's
description (`publish-release.ps1 -NotesFile`).

## Controls

The port has a controller for each of players 1 to 4 on Maple ports A to D (`base/input.c`,
`sdk/maple_pad.c`), each with a vibration pack. A gamepad or joystick becomes the next free player the first time it
is used; the keyboard is always player 1, and player 1's controller is always connected. `--pads N` caps the players
(`--pads 1`: every device together is player 1). Gamepads are read even while the window is not focused.

| Dreamcast | Gamepad (Xbox layout) | Keyboard |
|---|---|---|
| A / B / X / Y | A / B / X / Y | Z / X / A / S |
| Start | Start (Menu) | Enter |
| D-pad | D-pad | Arrow keys (not with game.json `"arrows": "stick"`), and I / K / J / L (up / down / left / right) |
| Analog stick | Left stick | Arrow keys (full deflection) |
| R trigger (analog) | RT | W or Space (fully pressed) |
| L trigger (analog) | LT | Q or Left Shift (fully pressed) |

The triggers are analog, 0 to 255; past half travel they also set the pad's digital trigger bits, which some games
bind actions to. Driving games usually accelerate on R and brake on L; each game's README gives its own controls.
Scripted runs press buttons with `--input [Pn.]NAME@VBLANK[+N],...` (`L=V`/`R=V` press a trigger part of the way, V 1
to 255; `P2.` to `P4.` press another player's, and connect that player from the start); it holds up to 4096 presses
and logs once when it drops later ones. Only player 1 is recorded and replayed.

**Live input.** The real keyboard and gamepads are read only by a run a person plays. A run that plays by itself
(`--input`, `--replay`, `--lockstep*`, `--headless`, `--no-window`, `--fixed-clock` other than 0, `--fast-until`)
reads neither, not even a pad SDL would deliver while another window has the focus, so what the user types in
another window can never reach it, and the window such a run opens cannot take the focus (it is created
unfocusable and not activated). `--live-input on` turns the real input back on, for watching a replay or a scripted
run and playing on; `--live-input off` turns it off for any run. Players 2 to 4 are not read during a replay either.
`base/live.c` decides it.

**No window.** `--no-window` runs the real-clock play run without opening anything: the settings, `--present interp`,
the game log and log rotation of a play run stay, at the real 60 Hz pace, with no window, menu bar, swap chain or
SDL video, no real input and no sound device (`--audio on` opens it). It is `--headless` for a launcher's play run
(`python play/launch.py GAME DISC --no-window ...`, the games' `Play.bat --no-window`). Not covered by such a run:
window creation and its placement, the swap chain and its present (vsync, tearing, occlusion), the menu bar, SDL's
keyboard and gamepads, the sound device, and the final pixels on the display; the frame is still drawn on the GPU
(or WARP with `--warp`) at `--size` (default 640x480).

## Copy agent prompt

The menu bar's copy icon (two sheets, tooltip "Copy agent prompt (F9)"), the Help menu's "Copy agent prompt" entry and
F9, which also works in full screen, take a scene dump after the next frame. Nothing pops up. The dump goes to
`scene_dumps/` in the data folder (`--scene-dir DIR` puts it elsewhere), as `<YYYYMMDD-HHMMSS>.json`, `.bmp` (the frame
as drawn) and `.ptrec` (the session recording), with an empty `_replay/` folder for the replay's output. The clipboard
gets a prompt an agent can act on: one instruction line (naming the game, as Phangrade's crash prompt leads), the JSON,
then Phangrade's agent context (`PHANGRADE_AGENT_CONTEXT`) when Phangrade launched the game. The user pastes it and says
what is wrong. It is a kit feature (`base/`): every game built on the kit (Toy Commander, Spawn,
Power Stone 2) has it with no game code; a game's map only adds the scene fields it names. A failure is logged and the
game carries on. `--scene-dump-at V` takes a dump at vertical blank V (scripted runs). `--watch-ram A@V1:V2` (A a hex guest
address) logs each write to that word, with the stack, in vertical blanks V1 to V2: the `--lockstep-watch` debug-register
watch for a replay with no lockstep (a bad value found at the frame a game faults: who wrote it). The `tx: call ... no host
function` log line carries r4..r7 and the caller's r0..r3, which name a function table's handler and its object.

The JSON (`dcport_scene_dump_v1`, `base/scene_dump.c`) is what an agent needs to pick the problem up:

| Field | What it holds |
|---|---|
| `task` | One line: the game and the scene fields the game's map names for it |
| `problem` | Empty: the user says what is wrong |
| `how_to_reach` | The `port.exe` command that replays this session to this frame, headless on the fixed clock, and writes it as `frame_NNNNNN.png` in `_replay/` (`--stop-at` ends it there, exit code 0); then how to play live |
| `scene` | Vertical blank, frame, pad reads, and the game's fields read from guest RAM (with names from its headers) |
| `files` | What to read and what may be edited in this scene, relative to `files.root` (the game's map rules) |
| `capture` | The paths of the JSON, the BMP, the recording, the replay folder and the log |
| `build` | Version (the game's and the kit's commits), the executable, the command line, the full argument list (defaults, saved settings, command line) and the launcher (Phangrade) |
| `host` | Input devices by name and VID:PID, output and frame size, window state, presenting mode, frames a second, the log's last warnings and last lines |

**The session recording** (`base/session.c`) runs from boot in memory, up to 256 MB, so a dump at any moment can
replay it. It keeps everything a real-time run reads that a fixed-clock run would not: player 1's pad (`host_pads_read`,
kept on change), the timer counts (`Sy_TimerGetCount`, as deltas), the clock offset for the RTC and the saves read.
`--replay FILE` plays it back: the pad reads come from the file until it ends, then the live pad takes over only with `--live-input on` (see Live input); otherwise no pad is read. A pad
read at a different vertical blank than recorded is logged once as `session: replay out of step`. `--record off`
turns the recording off. In-between frames (`--present interp`) are not recorded; they are drawn and undone.

**The game's map** is `<game>/scene_dump.json`, turned into `build/gen/scene_map.c` by `tools/scene_map.py`
(the format is in its header). Fields name globals from `game.h`, so the compiler checks them. Their types come from
game.h, and their value names come from `names` or from an `enum_prefix` of `#define`s in the readable headers.
`deref` follows pointers. Rules list read and edit files by a field's values. A game without the file gets the
kit's fields only. Never name a value that the readable C does not.

Checked on Toy Commander (2026-10-18): mission 1 was played on the real-time clock, scripted, and dumped mid-drive at
vertical blank 10440. The `how_to_reach` replay wrote frame 6272 at the same vertical blank, identical pixel for
pixel to the dump's BMP, with no `out of step`. On the attract run, a fixed-clock dump at vertical blank 3000 and a
real-time one replayed identically too. One real-time recording, made while the machine was heavily loaded, did not:
the boot splash's fade loop ran 33 frames instead of 9, so the first pad read came at vertical blank 36 instead of
11, and the replay was `out of step` from there. The game's timer values replayed the same, so something else the
game reads at boot is not recorded yet. That cause is open.

## Game log

Every play run writes what happened, in words, into `port.log` (`data\logs\port.log`; the last eight runs stay as
`port.1.log` to `port.8.log`). It is a kit feature (`base/game_log.c`) with no game code, on in play runs
(`--game-log on`) and off in check runs (headless or `--fixed-clock`), which do not read guest RAM for it, rotate
nothing and write no extra line. A game turns on its own fields in its map (`scene_dump.json`, `"log": true`). Each
line starts `[clock +seconds-since-start vVERTICAL-BLANK]`:

| Line | Meaning |
|---|---|
| `P1 press A`, `P2 stick up-left`, `P1 trigger L release`, `P3 controller connected` | The pads of all four players, as the game reads them (from a keyboard or a controller) |
| `keyboard: press Z (P1 A)`, `keyboard: F11 (full screen on)`, `menu: Display > Full screen` | The keys that act as a pad, the host's keys, the menu bar's choices (the menu's own words) |
| `window: lost the focus`, `window: resized, output 1920x1440`, `window: close requested (the user closed the window)` | The window |
| `game_mode: GAME_MODE_DEMO (1) -> GAME_MODE_BATTLE (2)` | A map field marked `"log": true` changed, with the value's name where the readable headers have one. A field that changes more than 6 times in 2 s is summarised (`N more changes in 120 vertical blanks, now V`) |
| `start state: ...`, `snapshot: ...` | Every field of the map and its counts (`fighters_active=2`), at the start and every 600 vertical blanks |
| `play summary: shown_fps 143.9 output 2560x1600 frames 8640 tick_ms 1.10/2.30/9.10 draw_ms ...` | Just before `END:`: how the session ran, from the first shown frame to the last (`base/play_stats.c`). The times are median / 99th percentile / worst in ms; tick is the host's work between two vertical blanks without the wait, draw the renderer's work behind a shown frame, frame the time between shown frames. 144 fps at 2560x1600 means a 99th percentile frame of 6.94 ms. Not written by a check run |
| `END: ...` | The last line. Who ended the run: the window closed by the user, the game's own exit, the build's stop message (a `must_work` stub, a run-time check), `FAULT` with its registers, an unhandled exception (`CRASH`), a CRT abort or invalid parameter, a console close or Ctrl+C |

A log with no `END:` line ended abruptly: the process was killed from outside (Task Manager, a launcher, a power cut) or
died where nothing can catch it (a stack overflow, a fast-fail). The next launch says so: it appends `END: missing` to
that log (now `port.1.log`), keeps the autosaved recording as `killed-<time>.ptrec` and repeats the note and a replay
command in the new log's first lines. Windows' Application log (event 1000) holds the exception code of a hard crash.

**After a fault, or any exit with a non-zero code,** the session recording is saved as `crash-<time>.ptrec` beside the
log (the last five are kept) and the log prints two replay commands: to the last frame presented before the end
(`--replay ... --stop-at V`, writes that frame's PNG into the `_replay` folder), and on through it (the same without
`--stop-at`, `--seconds` raised): it ends on the same `FAULT` one frame later. The recording is also autosaved to
`autosave.ptrec` every 3600 vertical blanks (`--game-log-save`), by a temporary name, so a killed run leaves one.
Replay depends on a recording that stays in step (see "Copy agent prompt").

`--fault-at V` raises a test fault (a write to address 0x40) at vertical blank V, with the replay commands carrying it: the way
to check that a game's log, crash recording and replay work end to end (the replay ends on the same `FAULT` and registers).
The END line is preceded by the log's own cost per presented frame (`gamelog: cost of the log ...`).

The log reads guest RAM through the scene map only and writes nothing into the game, the frames or the recording; the
state lines stop at 8 MB and everything but the end at 32 MB.

**When someone reports a problem in a playtest, read the log first** (`data\logs\port.log`, or the dump's
`capture.log`): what they pressed, the state of the game and how the run ended are in it, so do not ask the player what
they did. If the log cannot answer, fix the log: name the missing field in the game's map (from the readable C, never
a guess) or add the missing event here, and say so in the report.

**A new game's map** starts from `<workspace>/port_starter/scene_dump.json` (tools/game_setup.py writes it): the game
mode, the stage or level, the round, how many characters or players are in play, each player's health or lives, the
timer, and the animation or action state of the characters, each `"log": true`, `"index": N` for per-player fields,
`"counts"` for totals. The readable C names some of these; say in the port's report which it does not yet.

## Launcher contract

The same command lines as Lodoss's launcher, so one launcher (Phangrade) installs and starts every port.
`base/host_main.cpp` handles them before the kit starts; setup and verify are `runtime/install.c`. Neither opens a
window or the sound device.

| Command | What it does |
|------|------|
| `port.exe --version` | Prints one line, the build's identity: the game id, the game's and the kit's commits (`-dirty` with uncommitted changes) and the build date |
| `port.exe --setup-only --no-dialog --data-directory WORK --disc IMAGE` | Checks that IMAGE (`.cue` or `.gdi`) is this game (`boot_sha256`), copies it and its track files into `WORK\assets\disc`, checks the copy, then writes `WORK\installation.json` `{"version": <--version line>, "assets": "assets/disc"}` last. The log is `WORK\logs\setup.log` |
| `port.exe --assets DIR --verify-assets` | Checks the image in DIR (the first `.cue`, else `.gdi`, in DIR or a folder below it) |
| `port.exe --assets DIR --data-directory USERDATA [options]` | Plays the image in DIR, with no watchdog unless `--seconds N` is given. USERDATA holds `settings.txt`, `saves\`, `logs\` (the log and crash dumps) and `dumps\` (frame dumps), created if missing |

`--disc IMAGE` still works in place of `--assets`, and without `--data-directory` the data folder is `data\` beside
`port.exe` (or `PORT_DATA_DIR`), so development runs and the games' `.bat` files are unchanged. `--no-dialog` shows no
message box, even for a crash; a failed setup or verify prints one plain line, starting `error: `, to stderr and the log.

When `PHANGRADE_SETTINGS` names an absolute INI file (Phangrade sets it), its `[audio] volume` (0..100, default 25)
sets the process's Windows audio session before the sound device opens, and is polled four times a second; a change
in the Windows Volume Mixer is written back (`base/host_phangrade.cpp`, with `phangrade_audio.hpp` copied unchanged
from Phangrade's `integration` folder). Phangrade's window placement and window menu are not used: the kit keeps its
own menu and full-screen handling.

Exit codes: CONTRACT.md "Exit codes".

`tools/launcher_test.py --exe PORT.EXE --disc GOOD.cue [--wrong-disc OTHER.cue]` runs every case above, bounded.

## Releasing for Phangrade

A port without the contract does not reject `--setup-only`: it ignores the options it does not know and starts the
game, so Phangrade's Install opens the game and then reports a failed setup (Toy Commander `v0.1.0-preview`,
17 October 2026). A game is only as current as the kit it was last released with, so in this order:

1. Rebuild the game's release with a kit that has the launcher contract (`caabe02` or later), and make its ZIP with
   `tools/package_release.py` (Texture packs, "Packs in a release").
2. Run Phangrade's `scripts/check-port.ps1 -Game <id> -Executable <port.exe> -Disc <image>` on the build.
   It must print `PASS`.
3. Publish the release, download the published ZIP, extract it and run the same check on that copy.
4. Only then may Phangrade's `data/catalogue.json` declare the game's `port` object.

The full model (what Phangrade runs, in what order, and how to read a failed install) is Phangrade's
[docs/port-contract.md](https://github.com/GeneralAtrox/Phangrade/blob/main/docs/port-contract.md).

## Rules

- No game code, game strings or guest code addresses in the kit; game-range code goes in the game's `sources`.
- Platform code is new source. SDK documentation, headers and Ghidra exports are read, never copied.
- Never hand-edit matched or readable C: a change the port needs goes through names, design or the pipeline.
- 32-bit only for now (a guest pointer is a host pointer); zero warnings at `/W4 /WX`; at most 400 lines a file.
