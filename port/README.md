# Dreamcast port kit

A game-free kit that turns a pipeline workspace's readable C into a native 32-bit x86 Windows program. It boots
from the user's own disc image: it maps guest RAM at its Dreamcast addresses, loads the boot file, compiles the
readable C at `/W4 /WX`, and runs the game's own start-up. SDK and engine functions with no readable C stay logging
stubs until a platform-layer replacement takes over. Toy Commander's port is built from it.

The kit holds no game code. Each game keeps a `game.json`, its own sources and its notes in its own repository
(next to the decomp workspace). `tests/test_port_kit.py` fails if a guest code address, a game name or a
`game.h` include in the runtime enters `port/`.

## When a game gets a port

After the readable stage: every game-range function has readable C in `<workspace>/readable/src`. The engine's
extras (`project.json` `extra_functions`) are compiled with `--engine`; without it they stay stubs.

## Layout

| Path | Role |
|------|------|
| `runtime/` | Always compiled, no game names: start-up and watchdog (`main.c`), guest RAM and its P0/P2 mirrors (`guest_mem.c`), the GD-ROM image reader (`gdi.c`: `.gdi` or Redump `.cue`), calls through guest addresses (`dispatch.c`: jump thunks on the boot image's function starts, `--thunks on|off`, and a vectored exception handler over the generated table for the rest), logging stubs and the must-work stop (`port_stub.c`), the SH-4 instructions readable C uses (`sh4_rt.c`), the float math the 32-bit CRT only has inline, and rand (`port_libm.c`), host services and the 60 Hz vertical blank (`host.c`), in-between frames drawn and undone while a frame waits for its vertical blank (`frame_extra.c`: `--present tick|interp`, `--interp-frames N`, `--interp-verify 1`, `--fps N`, `--interp-copy-threads N`), a first-fit heap (`heap.c`), the log |
| `runtime/gfx/` | The host graphics layer: an SDL3 window (borderless full screen at the desktop mode, `--window WxH`, `--headless`), a Direct3D 11 device and a waitable flip-model swap chain (a frame the display would not take is dropped, never waited for), the 4:3 frame target presented pillarboxed, and PNG frame dumps (`--dump-frames`, `--dump-every`; `--dump-extra N` adds the in-between frames after each) |
| `runtime/media/`, `runtime/gfx/gfx_movie.c` | Media decoders, no game names, written from the published formats (`media/CLEANROOM.md`): CRI ADX for streamed music and movie sound, and Sofdec movies (the MPEG-1 system stream and MPEG-1 video). Their sound is mixed beside the AICA's voices at each vertical blank (`gfx_audio.c`); a movie's picture is drawn over the frame (`gfx_movie.c`). A game reaches them through the `adx_mw` SDK module |
| `runtime/arm7.c`, `runtime/arm7_ls.c`, `runtime/aica_arm.c`, `runtime/aica_chan.c` | The sound processor as the console has it: an ARMv4 core runs the game's own sound driver from the sound RAM, and the AICA's registers, timers and interrupts drive the kit's voices (`aica.c`). Off unless game.json sets `aica_arm`; the game's sound hardware functions then do the original's register and sound-RAM access (`aica_arm.h`, `docs/CLEANROOM_AICA_ARM7.md`) |
| `pvr/` | The PowerVR side the SDK modules hand work to: texture memory, fog, background plane, clip, and the TA-list renderer (`pvr_render`); `pvr_todo` logs each unhandled case once. No game names |
| `pvr/pvr_pal.c` | Palette RAM (1024 entries) and PAL_RAM_CTRL (ARGB1555, RGB565, ARGB4444, ARGB8888), and with `pvr_twiddle.c` the palettized textures that index it (pixel formats 5 and 6: twiddled 4- and 8-bit indices, the TCW's palette selector, mip levels); the texture cache decodes one again when the entries it reads change. Kamui 1's and Kamui 2's `kmSetPaletteMode` / `kmSetPaletteData` (`kamui1_misc`, `kamui2_misc`) feed it; a game turns it on with either module. Texture packs never replace a palettized texture |
| `pvr/pvr_pack.c`, `tools/texture_pack.py` | Texture packs: replacement images (PNG, any size) for guest textures, host-side only, in place of what the decoder makes from texture memory (a VQ texture's blocks, say). Keyed by the texture's content, so it does not depend on where a texture lands; mip-mapped guests get a box-filtered chain. Off unless a game sets game.json `texture_pack` (or `--texture-pack DIR`); `--texture-pack on|off` and the Display menu's Texture pack switch it (settings.txt `texture-pack`). Guest memory and timing are untouched. A game's pack builder uses `tools/texture_pack.py` (key, Kamui placement, PNG, index); see Texture packs below |
| `pvr/ta_parse.c`, `pvr/pvr_state.c` (debug options) | Reading a scene by hand, diagnostics only: `--ta-dump SCENE` (`odd`: the first scene with a stray parameter) writes that scene's TA lists as words to `ta_scene_SCENE.txt`; `--dump-vram SCENE` writes the 8 MB texture memory as the decoder reads it at that scene to `ta_vram_SCENE.bin` (decode a texture word from it with `tests/test_pvr_pack.py`'s driver); `--pvr-debug nocull,nofog,nodepth` switches a stage off. Both files go to the working directory. Replaces nothing; no game.json setting, a run passes the option (or a `bu_sweep` script's `args`). Test: `tests/test_pvr_vram_dump.py` |
| `sdk/` | SDK replacements a game opts into (game.json `sdk`). Each compiles against the game's generated `game.h`, so the names it defines must be the game's registry names; the header comment of each module lists what it needs |
| `tools/gen_port.py` | Writes `<game>/build/gen`: `game.h` (globals as lvalues on their guest addresses), logging stubs, the dispatch table, and `port_config.h`, `port_entry.c`, `port_files.cmake` from game.json, and `scene_map.c` from the game's `scene_dump.json` (`tools/scene_map.py`). Each file is written only when it changed, so an unchanged build is seconds |
| `tools/gen_overlays.py` (`runtime/overlay.c`) | Overlays, for a game with `overlays.json` (units: `id`, `decomp` workspace, optional `ranges`): writes `<game>/build/gen/overlays`, one static library per workspace with its functions renamed `<workspace>__<name>` (a forced-include `prefix.h`), stubs, and trampolines for calls out of its range, plus each unit's identity windows (16-byte code windows where it differs from every unit loaded over the same RAM). A call into overlay RAM runs the unit whose windows match guest RAM now; with none, `OVERLAY MISS` is logged once per group, the call returns 0, and a checked build stops. A game without overlays links an empty table. With translator C (`overlays.json` `translator_c`, or build.py `--tx-ovl`), a workspace's judged functions compile too, with its prefix and its own tc_call glue (`tools/overlay_tx.py`), as gen_port's mix does for main. build.py runs it after gen_port.py |
| `runtime/scene_*.c`, `runtime/session.c`, `host/host_copy_button.cpp` | Copy agent prompt: the scene dump's JSON, BMP and clipboard prompt, the session recording and replay, and the menu bar's copy icon (see below) |
| `tools/build.py` | gen_port, CMake + Ninja under the 32-bit MSVC environment, build, and with `--run` the run and a symbolized log. Every step is bounded; a timeout kills the process tree |
| `tools/regress.py`, `tools/frames.py` | The regression run (`build.py --regress`): each `<game>/tests/*.script` bounded, headless, fixed clock, tick presenting; fails on an exit other than the watchdog's, a log class (TODO, STUB, `ta: list`) not in `tests/baseline/`, or a frame unlike its golden in `tests/goldens/` (per-pixel error and SSIM, with a side-by-side sheet). `--accept-log` / `--accept-frames` update them |
| `tools/rate_compare.py` | The frame-rate comparison: one regression script's run shown at the game's tick (`--present tick`), then with in-between frames at the display's rate (`--present interp`); both go through the script's start on the fixed clock and turn to real time at a chosen vertical blank (`port.exe --fast-until N`) |
| `tools/lockstep.py` (`lockstep_emu.lua`, `lockstep_frames.py`, `lockstep_watch.py`; `runtime/lockstep*.c`) | The lockstep check: Flycast plays from a scripted input and records the game's watched state (the game's `lockstep.spec`) each frame; the port replays those frames (timer, pad, RTC) and traces the same state; the compare names the first divergent frame and its words. `--watch NAME[J]@N` names the functions that wrote a word in a frame (an x86 debug register); `--baseline TRACE` reports what a reference build does not share |
| `tools/bu_sweep.py` (`runtime/diag.c`) | Bring-up sweeps, diagnostics only (never a play build): `sweep` runs the port on a scripted input and at each blocker (fault, must-work stub, exit, hang, wrong mode) records the guest function, its chain, the vertical blank and the last frame, then steps past it with `port.exe --diag-skip FILE` (the host function becomes a logging stub returning 0 from one blank before the blocker, no rebuild) and runs again; `weak` lists the functions a run reached (`--diag-cover FILE`, one-shot breakpoints) with their port C kind and ledger verdict, and A/B-tests weak readable functions against their PASS translator C (`tx_first` in a copy's game.json). Both switches are off by default; a game sets nothing |
| `tools/sdk_ref.py` | Ghidra exports of named SDK/engine functions into `<game>/build/ref` for reading while writing a replacement (never copied) |
| `tools/aica_bank.py` | Sound data read from files, no game names: the Dreamcast/NAOMI sound driver's sample banks (DTPK: the sample table, each sample's rate from its program records' transpose), NAOMI SPSD streams (mono, or stereo in 0x2000-byte blocks), and the AICA's sample forms (16/8-bit PCM, Yamaha ADPCM decoded as `runtime/aica.c` plays it) written as WAV (`dump FILE OUTDIR [--offset N --size N]`, `list`). An asset reader for listening and comparing, not part of a build; a game turns nothing on (tests: `tests/test_aica_bank.py`) |
| `tools/symbolize.py`, `tools/game_config.py` | `port.exe+OFFSET` in a log named from the linker map; game.json loading and the generated per-game files |

| SDK module | Replaces | Needs from the game's headers |
|------|------|------|
| `sy_heap` | `syMallocInit`, `syMalloc`, `syFree`, `syMallocFinish`; the guest C library's `malloc`, `free` (Shinobi's, on the same allocator) as `sy_guest_malloc`, `sy_guest_free` | only those names |
| `sy_sys` | Shinobi system services: cable (VGA), config, RTC (host time), TMU timer | `SYE_CBL_VGA`, `SYS_RTC_DATE`; game.json `console` |
| `gdfs` | gdFs over the disc image, synchronous; CD-DA status only | `GDFS`, `GDFS_DIRREC`, `GDD_DRVSTAT_*`, `GDD_SEEK_*`, `SdkProc` |
| `am_heap` | `amHeapAlloc`, `amHeapFree` in a host-backed sound RAM | `SdkProc` |
| `shc_rt` | SHC compiler helpers: block copy, bit-field reads, unsigned mod, `div` | `ShcDivResult`, `g_errno`, `SHC_ERRNO_DIV0` |
| `kamui2_scene` | Kamui 2 (game-written vertex buffer): `kmSetSystemConfiguration`, scenes and passes, `kmRender` (renders `pvr/`, flips at the configured vsync interval), queue/texture status, VSync, clip; a vertex buffer configured past main RAM's end is cut to end there (logged) instead of wrapping onto the game's code; `kmBeginScene` never sets the system up again (as Kamui's does not), so the same config through another address mirror keeps the textures made after set-up | `KMSYSTEMCONFIGSTRUCT`, `KMVERTEXBUFFDESC`; layouts in `sdk/kamui2.h` |
| `kamui2_tex` | Texture surfaces in texture memory: create (all Kamui 2 types), load, free, frame-buffer read-back | `KMSURFACEDESC` |
| `kamui2_misc` | Device, display mode and its callbacks, border, version, fog, background plane, palette (`kmSetPaletteMode`, `kmSetPaletteData` to `pvr/pvr_pal.c`) | `KmCallbackFunc`, `KmVersionInfo` |
| `adx_mw` | CRI ADX (`ADXT_*`: streamed music read through gdFs, looping, output volume and fades, state, pause and error code) and Sofdec (`mwPlyCreateSofdec`: a movie player whose methods the game calls through the handle's table), on `runtime/media` | `ADXT`, `AdxtErrFunc`, `MWS_PLY_INIT_SFD`; the `gdfs` module in `sdk` |
| `maple_pad` | The Maple bus (`pd*`): standard controllers on ports A..D from `runtime/gfx/gfx_input.c`, sampled once per `pdExecPeripheralServer`; a vibration pack in each socket 2; the VMU LCD (`pdVmsLcd*`) for the cards `--vmu` places. A game that starts the bus through an entry the port does not bind (a newer library's `pdInitPeripheral` variant) gets its controllers at the first server pass or read, logged once | `LibPadDevice` (the record type `pdGetPeripheral` returns), `PDS_VIBPARAM` |

Registry names in gen_port.py `GUEST_CRT` (`malloc`, `free`, `calloc`, `realloc`) are the guest's own C library over
guest RAM, never the host CRT's: game.h maps each onto its kit name (`#define malloc sy_guest_malloc`), so readable
calls, dispatch rows and a logging stub (where no module defines it) all use the kit's name.

Names the SDK does not give (the `Sy_*`, `Gd_*` and `Shc_*` ones) are registry names. A new game gets them for
byte-identical library functions with `python xproject.py --project NEW --from OLD names`. A module whose names
the game lacks is left out of game.json `sdk` and the game writes its own.

## game.json

In the game's port folder; paths are relative to it.

| Key | Meaning |
|------|------|
| `name` | The game's name, for the log |
| `decomp` | The pipeline workspace (`project.json`); the readable C is its `readable/src` and `readable/include` |
| `entry` | `{"name", "addr"}`: the function the port calls first (`void name(void)`, checked against the registry) |
| `image_base` | Where the boot file loads (hex) |
| `boot_file` | Optional; `null` reads the name from IP.BIN |
| `disc` | Optional default disc image (`.cue` or `.gdi`); `--disc` overrides it |
| `fsca_table` | Optional: the workspace's fsca table, compared with the computed one at start-up |
| `must_work` | Optional: stubs that end the run at their first call (exit code 3), one name per line, `#` comments |
| `sdk` | Kit SDK modules to compile (names of `sdk/*.c`) |
| `sources` | The game's own platform files: game-range hardware functions, SDK replacements bound to its own code |
| `hardware` | `{addr: name}`: game-range functions the game's sources replace; the build stops if the registry renames one |
| `wrap` | `{addr: wrapper}`: readable functions whose callers call the game's `wrapper` instead (it calls the original); the readable files that name the function compile with `Name=wrapper`, and its dispatch row points to the wrapper |
| `console` | `{"country", "language", "broadcast"}`: the console `sy_sys` reports (numbers, as the game compares them) |
| `id` | Optional: the game's id in `--version` and `installation.json` (default: the name of the folder above the port folder) |
| `arrows` | Optional: `"both"` (default), the arrow keys press the d-pad and move the stick; `"stick"`, they only move the stick (for a game whose d-pad does something else than steering, such as cameras). I/K/J/L press the d-pad either way |
| `widescreen` | Optional: `true` when the game widens its horizontal culling by `port_widescreen()` (platform.h; a wrap of its projection function). Play runs then draw Hor+: the frame is as wide as the output up to 16:9, the 640x480 screen is its middle (2D stays 4:3; an untextured full-screen triangle, a fade, covers the frame). Off in checks (headless or fixed-clock runs) and with `--widescreen off`; `--widescreen on` forces it |
| `boot_sha256` | Optional: the SHA-256 of each boot file the port accepts (the right game and region); setup and verify refuse any other with exit code 5. Without it, any disc whose boot file loads is accepted |
| `texture_pack` | Optional: a folder (relative to game.json; it may be built later, e.g. into the git-ignored `artifacts\`) holding a texture pack the renderer uses by default (`pvr/pvr_pack.c`). Without it, and without `--texture-pack DIR`, there is no pack |
| `aica_arm` | Optional: `true` runs the game's own sound driver on the kit's AICA ARM7 (`runtime/aica_arm.c`); the game's sound functions in `sources` then do the original's hardware access (`runtime/aica_arm.h`). Default `false`: no ARM |

A non-static function defined at column 0 in an `sdk` module or a `sources` file replaces the stub of the
registry function with that name. The entry and every `hardware` name are checked against the registry.

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
wrote its memory, so an unchanged texture is never hashed again; the log names the pack and its first hit. A pack
is built on the user's machine from their own files: never commit one. `tests/test_pvr_pack.py` builds the
renderer's lookup with its decoder; a game can run that driver over its own textures (before/after sheets).

## Controls

The port has a controller for each of players 1 to 4 on Maple ports A to D (`runtime/gfx/gfx_input.c`,
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

## Copy agent prompt

The menu bar's copy icon (two sheets, tooltip "Copy agent prompt (F9)"), the Help menu's "Copy agent prompt" entry and
F9, which also works in full screen, take a scene dump after the next frame. Nothing pops up. The dump goes to
`scene_dumps/` in the data folder (`--scene-dir DIR` puts it elsewhere), as `<YYYYMMDD-HHMMSS>.json`, `.bmp` (the frame
as drawn) and `.ptrec` (the session recording), with an empty `_replay/` folder for the replay's output. The clipboard
gets a prompt an agent can act on: one instruction line (naming the game, as Phangrade's crash prompt leads), the JSON,
then Phangrade's agent context (`PHANGRADE_AGENT_CONTEXT`) when Phangrade launched the game. The user pastes it and says
what is wrong. It is a kit feature (`host/`, `runtime/scene_*.c`): every game built on the kit (Toy Commander, Spawn,
Power Stone 2) has it with no game code; a game's map only adds the scene fields it names. A failure is logged and the
game carries on. `--scene-dump-at V` takes a dump at vertical blank V (scripted runs).

The JSON (`dcport_scene_dump_v1`, `runtime/scene_dump.c`) is what an agent needs to pick the problem up:

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

**The session recording** (`runtime/session.c`) runs from boot in memory, up to 256 MB, so a dump at any moment can
replay it. It keeps everything a real-time run reads that a fixed-clock run would not: player 1's pad (`host_pads_read`,
kept on change), the timer counts (`Sy_TimerGetCount`, as deltas), the clock offset for the RTC and the saves read.
`--replay FILE` plays it back: the pad reads come from the file until it ends, then the live pad takes over. A pad
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

## Launcher contract

The same command lines as Lodoss's launcher, so one launcher (Phangrade) installs and starts every port.
`host/host_main.cpp` handles them before the kit starts; setup and verify are `runtime/install.c`. Neither opens a
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
in the Windows Volume Mixer is written back (`host/host_phangrade.cpp`, with `phangrade_audio.hpp` copied unchanged
from Phangrade's `integration` folder). Phangrade's window placement and window menu are not used: the kit keeps its
own menu and full-screen handling.

| Exit code | Meaning |
|------|------|
| 0 | A normal close, or setup or verify succeeded |
| 1 | A crash or fault, or another failure (a copy that failed) |
| 2 | The `--seconds` watchdog (test runs only) |
| 3 | A `must_work` stub was called, or a checked build's run-time check failed |
| 4 | The disc image is missing or unreadable (also for a play run) |
| 5 | The image is another game or region (also a play run's image with no boot file) |

`tools/launcher_test.py --exe PORT.EXE --disc GOOD.cue [--wrong-disc OTHER.cue]` runs every case above, bounded.

## Releasing for Phangrade

A port without the contract does not reject `--setup-only`: it ignores the options it does not know and starts the
game, so Phangrade's Install opens the game and then reports a failed setup (Toy Commander `v0.1.0-preview`,
17 October 2026). A game is only as current as the kit it was last released with, so in this order:

1. Rebuild the game's release with a kit that has the launcher contract (`caabe02` or later).
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
