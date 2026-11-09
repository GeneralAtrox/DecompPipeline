# The game contract

Contract version: 1.16.0

What a game built on the port kit may rely on. Everything here keeps working, or changes only with the version, as
the kit changes; everything else in `port/` is the kit's own and may change at any time. One place: the tables
below are the only copy (port/README.md points here). `tests/test_port_contract.py` fails when the code has a
game.json key, an SDK module, an exit code or an enhancement this document does not list, or this document lists
one the code no longer has.

## Versions

A game states the version it was built against in game.json: `"contract": "1.0.0"`. `tools/game_config.py` (so
gen_port and build.py) compares it with the version above:

| Change | What it means for a game | What gen_port does |
|---|---|---|
| Major (2.0.0) | Something the game used was removed or now means something else: the game may not build or may run wrongly until it is updated | Stops, naming both versions and this file |
| Minor (1.1.0) | Something was added (a key, a module, an option, a call); nothing a game used changed | Builds. A game that asks for a newer minor than the kit has is warned: it may use what this kit lacks |
| Patch (1.0.1) | This document or a behaviour was corrected to match what the console does; nothing a game wrote against changed | Builds |
| No `contract` key | An older game.json | Builds, with one warning naming the key to add |

Whoever changes the kit raises the version in the same commit: a key, module, option, exit code or `platform.h`
call removed or changed in meaning is major; one added is minor; a wording fix is patch.

## What a game may rely on

1. **game.json**: the keys below, with these meanings.
2. **The platform layer**: every declaration in `runtime/platform.h` (and the types of `rd_rt.h` and the port base's `base/base.h` it includes: the log, the clock, the command line, the save files, the pads), which
   a game's `sources` and the kit's `sdk/` modules call. A game's sources include `game.h` and `platform.h` only.
3. **The SDK modules** below: their file names (the names game.json `sdk` lists) and what each replaces.
4. **The command-line options** below, for a game's `.bat` files and `tests/*.script` (`args` lines).
5. **The script form** of `tools/regress.py` (`seconds`, `frames`, `input`, `args`) and `--input`'s press syntax.
6. **The exit codes** below.
7. **The enhancements** below: their ids, defaults, settings names, and that each is off in a check run.

Not in the contract: the kit's other files and functions (`runtime/*.c` internals, `pvr_int.h`, the tools' Python
functions), log text other than the classes `regress.py` counts (`TODO`, `STUB #`, `ta: list`), and the generated
files' layout.

## game.json

In the game's port folder; paths are relative to it.

| Key | Meaning |
|------|------|
| `contract` | Optional: the contract version the game was built against (see Versions) |
| `name` | The game's name, for the log |
| `decomp` | The pipeline workspace (`project.json`); the readable C is its `readable/src` and `readable/include` |
| `entry` | `{"name", "addr"}`: the function the port calls first (`void name(void)`, checked against the registry) |
| `image_base` | Where the boot file loads (hex) |
| `boot_file` | Optional; `null` reads the name from IP.BIN |
| `disc` | Optional default disc image (`.cue` or `.gdi`); `--disc` overrides it |
| `fsca_table` | Optional: the workspace's fsca table, compared with the computed one at start-up |
| `must_work` | Optional: stubs that end the run at their first call (exit code 3), one name per line, `#` comments |
| `sdk` | Kit SDK modules to compile (names of `sdk/*.c`, see SDK modules) |
| `sources` | The game's own platform files: game-range hardware functions, SDK replacements bound to its own code |
| `hardware` | `{addr: name}`: game-range functions the game's sources replace; the build stops if the registry renames one |
| `wrap` | `{addr: wrapper}`: readable functions whose callers call the game's `wrapper` instead (it calls the original); the readable files that name the function compile with `Name=wrapper`, and its dispatch row points to the wrapper |
| `console` | `{"country", "language", "broadcast"}`: the console `sy_sys` reports (numbers, as the game compares them) |
| `id` | Optional: the game's id in `--version` and `installation.json` (default: the name of the folder above the port folder) |
| `left_stick` | Optional: `"off"` (default), not offered; `"choice"`, the game's key screen offers the left stick as movement, off until a player picks it; `"on"`, on until changed. For a game that doesn't read the analog stick: the stick's raw values still reach it. |
| `arrows` | Optional: `"both"` (default), the arrow keys press the d-pad and move the stick; `"stick"`, they only move the stick (for a game whose d-pad does something else than steering, such as cameras). I/K/J/L press the d-pad either way |
| `widescreen` | Optional: `true` when the game widens its culling by `port_widescreen()` and `port_tallscreen()` (platform.h; a wrap of its projection function: the frame's width and height over the 640x480 screen's, each 1 or more, at most one above 1). Play runs then follow the window's shape (`--aspect fit`, the default) at any shape, wider or narrower than 4:3: the 640x480 screen is the frame's middle (2D stays 4:3; an untextured full-screen triangle, a fade, or one with an 8x8 texture, a fill, covers the frame). A game without the key stays 4:3 until it chooses a shape (the menu or `--aspect`). Checks (headless or fixed-clock runs) stay 4:3 unless `--aspect` is given; `--widescreen on` is `--aspect fit` and `off` is `--aspect 4:3` |
| `wide_cull` | Optional: `{"viewport": addr, "functions": [addr, ...]}`, for a game whose renderer drops what falls outside its viewport (four floats W, H, X, Y at `viewport`), so parts past the 640x480 screen go missing in a wider or taller frame. gen_port writes a `WideCull_ADDR` wrapper for each function (the build folder's `wide_cull.c`, wrapped as `wrap` would) that calls it with a viewport centred on the screen (the full screen, a wipe) grown by the frame's share (`port_wide_cull_begin` and `_end`, platform.h) and puts the four back after. A view off centre (a render target), a call inside a widened one, or a 4:3 frame is left alone, so checks do not change. A function may not also be in `wrap` or `hardware` |
| `boot_sha256` | Optional: the SHA-256 of each boot file the port accepts (the right game and region); setup and verify refuse any other with exit code 5. Without it, any disc whose boot file loads is accepted |
| `interp_scene` | Optional boolean (default false): in-between frames come from the renderer blending the scene with the one before (`pvr/pvr_blend.c`), not from the game's draw function, so no game code runs and guest RAM is neither copied nor restored. `--interp-scene off` selects the draw function again. Display > In-between frames (from the next start) chooses Off or Screen blending and keeps it in settings.txt as `interp-scene` |
| `part_draw` | Optional: `{"function": addr, "stamp": "0x..", "world": "0x.."}`, the game's per-part 3D draw, whose first two arguments are the object and the part (`tools/part_draw.py` wraps it with `port_part_begin`/`_end`, `platform.h`). Each vertex the renderer parses is tied to the part that wrote it, and with `interp_scene` an in-between frame blends each vertex of a part that kept its object, stamp and vertex count from where it was a tick before, in perspective (`pvr/pvr_parts.c`); 2D and changed parts are drawn as the latest tick drew them. No game code runs. `--interp-parts off` selects screen blending again; `--interp-parts-log on` logs the parts |
| `present_thread` | Optional boolean (default false): with `interp_scene`, in-between frames are drawn and shown by a thread of their own on the real clock (`runtime/frame_extra.c`, `--present-thread`), so the tick takes no time from them, and a movie's picture is shown again until the next one. Ticks and sound do not change |
| `sq_queue_only` | Optional boolean (default false): the translated C of the game models the SH-4 store queues itself (`SqQueues`: the bytes stored and the `pref` that sends them to RAM), so a store into the window `0xe0000000..0xe3ffffff` is kept by the queue only (`runtime/tx/tx_rt.c`, `PORT_SQ_QUEUE_ONLY`) and does not also land in the RAM it mirrors. Needed when a drawer stores into one block and sends the queue to another (Power Stone 2's shadow volumes: the stores overwrote the blocks already sent) |
| `sq_fast` | Optional boolean (default false): the store-queue helpers of the translated C (`SqQueues`) run a block at a time in the play build (`tools/tx_sqfast.py`, also `build.py --sq-fast`); the judged files stay as they are and the helpers' meaning does not change (`tests/test_sq_fast.py`). A game turns it on with a whole-replay proof that its frames and audio are identical |
| `game_o2` | Optional boolean (default false): the game's own C (readable, translator, game sources, generated glue) builds at `/O2`, not the judge's `/O1`; also `build.py --game-o2`, and a build folder set up the other way is set up again. The judge's own builds do not change. A game turns it on with a whole-replay proof that its frames and audio are identical |
| `vram_init` | Optional: `{"hex byte offset in texture memory": "hex bytes"}`, set in the 8 MB texture memory before the game's first read or upload (`pvr/pvr_state.c`, `PORT_VRAM_INIT`). For texels a game samples that it never uploads: the console keeps the frame buffers or leftovers there, the port keeps them elsewhere. Power Stone 2's gauge bars sample one texel at offset 0 and need it white |
| `texture_pack` | Optional: a folder (relative to game.json; it may be built later, e.g. into the git-ignored `artifacts\`) holding a texture pack the renderer uses by default (`pvr/pvr_pack.c`). A release carries it as `packs\<its folder name>\` beside `port.exe`, found there first. Without it, and without `--texture-pack DIR` or optional packs in `packs\`, there is no pack |
| `aica_arm` | Optional: `true` runs the game's own sound driver on the kit's AICA ARM7 (`runtime/aica_arm.c`); the game's sound functions in `sources` then do the original's hardware access (`runtime/aica_arm.h`). Default `false`: no ARM |
| `aica_arm_fast` | Optional boolean (default false; needs `aica_arm`): the AICA ARM7 runs the sound driver's idle scan loops a whole number of iterations at a time (`runtime/arm7_scan.c`), with the same registers, flags, cycles and instruction count as the interpreter, so every register write and every sample is the same. Set it only once the game has been proven side by side (the ARM write log and the mixed audio identical on its replays). The environment variable `DP_ARM_FAST=0` or `1` overrides it for a run |
| `aica_arm_native` | Optional (needs `aica_arm`): the path, relative to game.json, of the C file `tools/arm7_translate.py` made from the game's sound driver as the replay recorder saw it run (env `DP_ARM_PCS`). The AICA ARM7 then runs the driver as those C blocks, with the interpreter's exact registers, flags, memory writes, cycle and instruction counts, falling back to the interpreter for any code the file does not hold or whose words in sound RAM differ. Set it only once the game has been proven side by side. The environment variable `DP_ARM_NATIVE=0` or `1` overrides it for a run |
| `kamui1` | Optional: `{"list_word": addr, "direct_list": {"at": addr, "bytes": n}}`: the guest address of Kamui 1's list table word, and, for a game whose buffer model sends a list straight to the TA, the RAM the game leaves free for that list (without it the list shares the vertex buffer, scaled; a game that writes more than that overruns into the next list, which the kit logs), for the `kamui1_*` modules (`sdk/kamui1_scene.c`) |
| `lockstep_sample_pcs` | Optional: up to 8 guest addresses where the port's vertical blank comes (the SDK's render and vsync-wait entries); `tools/lockstep_emu.lua` samples Flycast there |
| `lockstep_rtc_pcs` | Optional: the guest addresses where the game reads the RTC; a lockstep frame that reads it drives the port with what it read (up to 8 with the sample PCs) |
| `lockstep_scene_pcs` | Optional: the guest addresses where a scene begins (Kamui's `kmBeginScene`), so lockstep frames count as the port's do (up to 8 with the other PCs) |
| `translator_c` | Optional: `{"dir": the judged translator C folder, "mode": "mix" or "only"}` (`tools/tx_port.py`); absent, the port compiles readable C and logging stubs only |
| `tx_first` | Optional: guest addresses of functions whose readable C yields to their judged translator C (mix mode) |
| `spin_words` | Optional, needs `translator_c`: up to 4 guest addresses of 32-bit words the game busy-waits on until an interrupt changes them (a vsync counter). A readable global at one is read through `port_rd_spin` (`runtime/tx/tx_spin.c`), so a readable wait loop lets the next vertical blank come, as translator C's `rd32` does; the per-function judge cannot see this (its vectors never change the word) |
| `not_code` | Optional: `{addr: evidence}`, catalog entries that are data, not code: no dispatch row, so no thunk is written over the data |

A non-static function defined at column 0 in an `sdk` module or a `sources` file replaces the stub of the
registry function with that name. The entry and every `hardware` name are checked against the registry. An unknown
key stops gen_port.

## Saturn ports

A Saturn game keeps a `game.json` with one object, `saturn`, read by `tools/gen_saturn.py` (the Dreamcast keys above do
not apply). Paths and addresses are strings (`"0x06000000"`).

| Key | Meaning |
|------|------|
| `image` | The code image the port loads (a composite of the disc's code files until the disc loader replaces it); `--image` overrides |
| `image_base` | Where it loads in work RAM |
| `entry` | The guest function the port calls first (on the game's thread) |
| `entry_sp` | Its initial stack; `entry_gbr` the GBR (default: the same) |
| `frame_hz` | 50 (PAL) or 60 (NTSC); the vertical blank comes on the real clock |
| `exit_regs` | The registers a function hands on at return besides r0 (the SH-2 asm of the game's library reads them after a call): r4..r7 and r1..r3; a callee that never writes one hands the caller's back |
| `sound_cpu` | `true`: SMPC SNDON starts a 68000 on the driver the game loaded into sound RAM, and the SCSP's timers, interrupts and 32 voices run each frame (`sat_m68k*.c`, `sat_scsp*.c`); the mixed sound goes to the wave device and, with `--wav FILE`, to a WAV file. `sound_clear` and `sound_stream` are then unused. Emulated hardware: for bring-up and as the answer key for a native sound driver, not the play path to keep (RUNTIME_TARGET.md) |
| `sound_clear` | Sound-RAM mailbox bytes the missing 68K driver would clear |
| `ground_shadows` | The game's floor-shadow quads, `{"pmod": "0x0128", "srca": "0xa4ec"}`: VDP1 distorted sprites with that draw mode and texture. Play runs draw them before the rest of the list, so nothing standing on them is darkened (Display > Shadows, `--ground-shadows under\|original`; `sat_vdp1_list.c`). Check runs keep the game's order |
| `twin_stick` | Twin-stick in the game's fights, `{"fight": "0x0605acd0", "turn": ["LEFT", "RIGHT"], "strafe": ["L", "R"]}`: while the game runs `fight` (a function it runs only in a fight), the right stick presses the `turn` buttons and the left stick held sideways the `strafe` buttons, by the pad's action names (base `pad_twin.c`) |
| `auto_load` | The game's own load path, driven at boot when its save file is there, `{"file": "ROBO_PIT___", "steps": [...]}`: each step waits for the game to enter (`enter`) or leave (`leave`) a function, pressing `press` until then (one button, or several joined by `+` pressed together: `"UP+A"`), and may put a 16-bit `poke: [address, value]` before it runs (base `autoload.c`, `sat_bup.c`) |
| `sound_stream` | The driver's stream-progress report: `start_addr`, `start_cmd`, `status_addr`, `status_count`, `status_step`, `block`, `rate` |

Options of the built program: `--frames N` (stop after N vertical blanks), `--fast` (no wait for the real clock: a test
aid, never a launcher's), `--headless`, `--shot VB FILE`, `--shot-every N`, `--watch SECONDS`, `--disc CUE`, `--image FILE`,
`--log FILE`, `--input`, `--bind-NAME`, `--volume N`, `--audio on|off`, `--scene-dump-at V`, `--dump-frames`, `--settings`, `--profile`, `--trace N`, `--trace-from ADDR N`, `--trace-vb VB N`, `--work-counts FILE`, `--work-from BLANK` (README, "Work counters"). Exit codes: 0 normal end, 1 the call
depth bound, 2 the entry function returned, 3 the game halted (`tc_halt`), 4 the watchdog ended the run.

## SDK modules

Each compiles against the game's generated `game.h`, so the names it defines must be the game's registry names; the
header comment of each module lists what it needs. A module whose names the game lacks is left out of game.json
`sdk` and the game writes its own.

| SDK module | Replaces | Needs from the game's headers |
|------|------|------|
| `sy_heap` | `syMallocInit`, `syMalloc`, `syFree`, `syMallocFinish`; the guest C library's `malloc`, `free` (Shinobi's, on the same allocator) as `sy_guest_malloc`, `sy_guest_free` | only those names |
| `sy_sys` | Shinobi system services: cable (VGA), config, RTC (host time), TMU timer | `SYE_CBL_VGA`, `SYS_RTC_DATE`; game.json `console` |
| `sy_chain` | Shinobi's interrupt chain (`syChainAddHandler`, `syChainDeleteHandler`): the vertical blank event runs; the others are kept, logged, never run | `SYCHAIN`, `SyChainFunc` |
| `gdfs` | gdFs over the disc image, synchronous; CD-DA status only | `GDFS`, `GDFS_DIRREC`, `GDD_DRVSTAT_*`, `GDD_SEEK_*`, `SdkProc` |
| `adxf` | CRI's ADX file system (ADXF) over the `gdfs` module, synchronous reads | `ADXF`, `GDFS`; the `gdfs` module in `sdk` |
| `am_heap` | `amHeapAlloc`, `amHeapFree` in a host-backed sound RAM | `SdkProc` |
| `am_sys` | Audio64's AM start-up (1.x): driver choice, file and memory callbacks, `amInit` loading the driver image into sound RAM (nothing runs it) | the registry names in its header |
| `ac_digi` | Audio64's AC layer (1.x): digital ports and CD input on the kit's AICA voices, set directly instead of queued | the registry names in its header |
| `shc_rt` | SHC compiler helpers: block copy, bit-field reads, unsigned mod, `div` | `ShcDivResult`, `g_errno`, `SHC_ERRNO_DIV0` |
| `shc_long` | SHC's register-ABI division and block-move helpers, bit-exact with the original | `g_errno`, `SHC_ERRNO_DIV0` |
| `kamui1_scene` | Kamui 1's vertex buffer, render, flip and frame buffer calls over `pvr/` | game.json `kamui1`; layouts in `sdk/kamui1.h` |
| `kamui1_state` | Kamui 1's render state and strip calls | `sdk/kamui1.h` |
| `kamui1_tex` | Kamui 1's texture surfaces in the emulated texture memory | `sdk/kamui1.h` |
| `kamui1_misc` | Kamui 1's callbacks (end of render, vertical sync), scanline, fog, palette, border, background, clipping and register calls | `sdk/kamui1.h` |
| `kamui2_scene` | Kamui 2 (game-written vertex buffer): `kmSetSystemConfiguration`, scenes and passes, `kmRender` (renders `pvr/`, flips at the configured vsync interval), queue/texture status, VSync, clip; a vertex buffer configured past main RAM's end is cut to end there (logged) instead of wrapping onto the game's code; `kmBeginScene` never sets the system up again (as Kamui's does not), so the same config through another address mirror keeps the textures made after set-up; a pass whose direct-transfer list has a 0% share (the game feeds that list itself) takes the other bank (half the bank when there is only one), so the other lists keep the shares the game sized; a list that still runs past its room is logged (`kamui2: list N ran M bytes past its room`) | `KMSYSTEMCONFIGSTRUCT`, `KMVERTEXBUFFDESC`; layouts in `sdk/kamui2.h` |
| `kamui2_tex` | Texture surfaces in texture memory: create (all Kamui 2 types), load, free, frame-buffer read-back | `KMSURFACEDESC` |
| `kamui2_misc` | Device, display mode and its callbacks, border, version, fog, background plane, palette (`kmSetPaletteMode`, `kmSetPaletteData` to `pvr/pvr_pal.c`) | `KmCallbackFunc`, `KmVersionInfo` |
| `adx_mw` | CRI ADX (`ADXT_*`: streamed music read through gdFs, looping, output volume and fades, state, pause and error code) and Sofdec (`mwPlyCreateSofdec`: a movie player whose methods the game calls through the handle's table), on `runtime/media` | `ADXT`, `AdxtErrFunc`, `MWS_PLY_INIT_SFD`; the `gdfs` module in `sdk` |
| `maple_pad` | The Maple bus (`pd*`): standard controllers on ports A..D from `base/input.c`, sampled once per `pdExecPeripheralServer`; a vibration pack in each socket 2; the VMU LCD (`pdVmsLcd*`) for the cards `--vmu` places. A game that starts the bus through an entry the port does not bind (a newer library's `pdInitPeripheral` variant) gets its controllers at the first server pass or read, logged once | `LibPadDevice` (the record type `pdGetPeripheral` returns), `PDS_VIBPARAM` |
| `bu_vmu` | Shinobi's memory card library (`bu*`) on host files, one 128 KB VMU image per card in the save directory (an emulator's file), and the one game file a card holds (`buSaveExecFile`, `buRewriteExecFile`, `buLoadFileEx`) | the registry names in its header; `BUS_BACKUPFILEHEADER` for file images |
| `sy_font` | The BIOS font in the boot ROM (`syBtFntGetAddr`, `syBtFntGetInfo`, `Sdk_BiosFontGetAddress`, `Sdk_BiosFontLock`, `Sdk_BiosFontUnlock`): an open 12x24 face, written by `tools/gen_sy_font.py`; Shift-JIS with no glyph is blank and logged | the registry names in its header |
| `vmu_flash` | The VMU flash file system on a card image (used by `bu_vmu`) | none (`sdk/vmu_flash.h`) |
| `fm_mng` | The flash manager (`fmMng*`) over the console's 128 KB flash ROM image (`--flash FILE`, else a stock console's) | the registry names in its header |
| `lib_ctx` | The library's execution contexts (`Lib_InitContext`, `Lib_SwapContext`) as host coroutines | the registry names in its header |
| `net_none` | The network library with no modem: every request fails, so the game takes its no-network path | the registry names in its header |

Registry names in gen_port.py `GUEST_CRT` (`malloc`, `free`, `calloc`, `realloc`) are the guest's own C library over
guest RAM, never the host CRT's: game.h maps each onto its kit name (`#define malloc sy_guest_malloc`), so readable
calls, dispatch rows and a logging stub (where no module defines it) all use the kit's name.

Names the SDK does not give (the `Sy_*`, `Gd_*` and `Shc_*` ones) are registry names. A new game gets them for
byte-identical library functions with `python xproject.py --project NEW --from OLD names`.

## Command-line options

The options a game's `.bat` files and `tests/*.script` `args` lines may use. A play run reads `settings.txt` (each
line `key=value` is the option `--key value`); a check run (headless or `--fixed-clock`) reads none. The launcher's
options (`--version`, `--setup-only`, `--no-dialog`, `--data-directory`, `--assets`, `--verify-assets`) are in
README.md "Launcher contract".

| Option | What it does |
|---|---|
| `--disc IMAGE` | The disc image (`.cue` or `.gdi`) |
| `--seconds N` | The watchdog: the run ends after N seconds with exit code 2 (0: none) |
| `--headless` | No window and no sound device: a check run |
| `--no-window` | A play run that opens nothing: no window, no crash dialog, no real input, no sound device; the play run's settings, game log and real-clock pacing stay (a launcher proves its path with it) |
| `--live-input on\|off` | Whether the real keyboard and gamepads are read; off by default for `--input`, `--replay`, `--lockstep*`, `--headless`, `--no-window`, `--fixed-clock` and `--fast-until` runs, whose windows also cannot take the focus |
| `--fixed-clock 1` | Virtual time: the timer, the vertical blank and the RTC repeat exactly: a check run |
| `--catch-up on\|off` | Real clock: a late vertical blank is repaid by not waiting, up to 6 ticks behind (on, the default); off: the wait for the boundary after now, where any tick over 1/60 s costs a whole slot. No effect under `--fixed-clock`. Dreamcast kit only: the Saturn kit keeps its own grid (`saturn/sat_sys.c`) |
| `--log FILE` | Where the log goes |
| `--pad-style auto\|dreamcast\|xbox\|playstation\|NAME@V,...` | The style of controller a texture pack's variant lines (`KEY FILE xbox`) apply to; auto (default) follows the device last used, or before that the first connected gamepad, only in a run that reads real input; `NAME@V,...` swaps at vertical blanks, for a test |
| `--pad-art on\|off` | Controller pictures (Input menu, setting `pad-art`): on (default) shows the pictures for the pad in use, off the game's own art |
| `--texture-keys DIR` | Lists the textures the run decodes: `DIR/KEY.png` each and `DIR/keys.txt` (pack key, size, first vertical blank), to build a texture pack |
| `--input [Pn.]NAME@V[+N],...` | Scripted presses at vertical blank V, held N blanks (`L=V`/`R=V` a trigger part way; `LSUP`, `LSDOWN`, `LSLEFT`, `LSRIGHT` the left stick alone, without the d-pad) |
| `--bind-NAME SOURCE[+SOURCE...]` | Assigns host buttons to one of the console's actions, on every console (`NAME` is the action: `A`, `START`, `RT` ...; each `SOURCE` one of `A B X Y START UP DOWN LEFT RIGHT LB RB L3 R3 BACK GUIDE LT RT RSUP RSDOWN RSLEFT RSRIGHT`, or `none`). The Input > Controls menu saves the same choice as `bind-NAME`. An assigned button presses the action's default button before the pad is recorded, so the game, its log and a replay see only the default layout; a scripted run uses each action's default |
| `--pads N`, `--rumble on\|off` | How many pads are read (1..4); whether they rumble |
| `--dump-frames N,..`, `--dump-every N`, `--dump-dir DIR` | Frame PNGs |
| `--pvr-yuv simd\|table\|check` | YUV422 textures (a movie frame): a pair at a time with SSE2 (default), one texel at a time, or both with every texel compared (`pvr: --pvr-yuv check` in the log) |
| `--scene-dump-at V`, `--stop-at V` | A scene dump at vertical blank V; end the run there (exit code 0) |
| `--watch-ram A@V1:V2` | Logs each write to the guest word A (hex), with the stack, in vertical blanks V1 to V2: the `--lockstep-watch` watch for a replay with no lockstep |
| `--fault-at V` | A test fault (an integer divide by zero, which no console's memory model takes for a store) at vertical blank V, to check that the log, the crash recording and its replay work; the replay commands carry it |
| `--game-log on\|off`, `--game-log-every N`, `--game-log-save N` | The game log (README "Game log"): on in play runs, off in check runs unless given; a snapshot of the game's fields every N vertical blanks (600); the recording autosaved every N (3600; 0: never) |
| `--replay FILE`, `--replay-until V`, `--record off` | The session recording from boot: play it back; end the replay at vertical blank V (the player takes over with `--live-input on`); turn it off. With `--replay-until V` the timer goes on from the replay's value, never back, so with `--fast-until V --live-input on` the player takes over at V: Help > Resume before the last crash does that with the newest `crash-*.ptrec` or `killed-*.ptrec`, 5 s before its end, after asking (`base/host_resume.cpp`; the new run waits for the old one to end), and a crash's log prints the same command |
| `--save-dir DIR`, `--vmu A1,B1,..\|none` | The save folder; the memory cards' sockets |
| `--window WxH`, `--vsync on\|off`, `--fps N` | Window size, vertical sync, a frame-rate cap |
| `--present tick\|interp\|auto`, `--interp-scene on\|only\|off`, `--aspect fit\|4:3\|16:9\|21:9\|W:H`, `--widescreen on\|off`, `--texture-pack NAME\|DIR\|on\|off` | Enhancements (below) |
| `--settings FILE\|none` | The saved options file |
| `--must-work FILE` | Stubs that end the run at their first call (exit code 3) |

## Exit codes

| Exit code | Meaning |
|------|------|
| 0 | A normal close, or setup or verify succeeded, or a run reached `--stop-at` or the end of the frames a check replays (`--lockstep-end exit`, hw_check, `--ram-frames`) |
| 1 | A crash or fault, or another failure (a copy that failed) |
| 2 | The `--seconds` watchdog (test runs only); also a check-only option it could not read (`--lockstep*`, `--hw-*`) |
| 3 | A `must_work` stub was called, a checked build's run-time check failed, or a call into overlay RAM found no loaded function |
| 4 | The disc image is missing or unreadable (also for a play run); also the display device was lost, the renderer ran out of memory for a scene, or `--verify-assets` was given no `--assets` (KNOWN.md "Exit code 4") |
| 5 | The image is another game or region (also a play run's image with no boot file) |

## Enhancements

Each is off by default where marked, switched at run time (the Display menu writes `settings.txt`), and off in a
check run (headless or `--fixed-clock`: regress, lockstep, hw_check, bu_sweep), so goldens and emulator comparisons
see the original. None changes guest memory or timing.

| Id | Option | Default in a play run | In a check run | Menu (Display) | settings.txt |
|---|---|---|---|---|---|
| `interp` | `--present tick\|interp\|auto` | `auto` (the host adds it): on while the frame-rate cap, or no cap, is above the rate the game is rendering at now, which is measured, since it can change from screen to screen | off: the host adds nothing and the tools pass `--present tick` | none: it follows Frame-rate cap | none (a saved `present` is ignored) |
| `aspect` | `--aspect fit\|4:3\|16:9\|21:9\|W:H` (a decimal ratio too; `--widescreen on\|off` is `fit` and `4:3`) | `fit` (the window's shape) for a game with game.json `widescreen`, else `4:3` | `4:3` unless given | Aspect: Fit window, 4:3, 16:9, 21:9 | `aspect` |
| `texture_pack` | `--texture-pack NAME\|DIR\|on\|off` | on for a game with game.json `texture_pack` | off unless `--texture-pack on`, `NAME` or `DIR` | Texture pack | `texture-pack` |
| `ssaa` | `--ssaa 2\|4` | off | off unless given | none | `ssaa` |
| `aniso` | `--aniso N` | off | off unless given | none | `aniso` |
| `vmu_screen` | `--vmu-screen off\|side\|over` | off | off unless given | VMU screen | `vmu-screen` |
| `fps_counter` | `--fps-counter on\|off` | off | off unless given | Frame-rate counter: on the menu bar before the copy icon, the frames shown a second and the game's own rate against its console's, `144 fps   game 55/60`, the game's figure red under 98% of its target (in-between frames keep the first at the cap while the game runs slow); read once a second, it changes nothing the game sees. Apart from it, a play run's log gets a `tick gap:` line when a tick took longer than two of the console's vertical blanks (at most one a second, with a count of those held back) | `fps-counter` |
| `twin_stick` | `--twin-stick on\|off` | on for a game with game.json `twin_stick`: in its fights the right stick turns and the left stick's sideways direction sidesteps; the d-pad turns as before | off in a scripted run unless given; a replay plays the recorded pad, which holds the buttons twin-stick pressed | Input > Controls > Twin-stick | `twin-stick` |
| `auto_load` | `--auto-load on\|off` | on for a game with game.json `auto_load` when its save file is there: the game's load path is driven at boot | off unless given; a replay takes the recorded choice | Game > Auto-load save | `auto-load` |

Not enhancements: the player's preferences the menu also saves, which change neither what the game draws nor its
timing: `vsync`, `fps`, `volume`, `mute-unfocused`, `rumble`, `pause-unfocused`. `pad-art` (the Input menu's Controller pictures) picks among a texture pack's controller images and does nothing without one.

## Releasing for Phangrade

A port without the contract does not reject `--setup-only`: it ignores the options it does not know and starts the
game, so Phangrade's Install opens the game and then reports a failed setup (Toy Commander `v0.1.0-preview`,
17 October 2026). A game is only as current as the kit it was last released with, so in this order:

1. Rebuild the game's release with a kit that has the launcher contract (`caabe02` or later), and make its ZIP with
   `tools/package_release.py` (Texture packs, "Packs in a release").
2. Run Phangrade's `scripts/check-port.ps1 -Game <id> -Executable <port.exe> -Disc <image>` on the build. It must print `PASS`.
3. Publish the release, download the published ZIP, extract it and run the same check on that copy.
4. Only then may Phangrade's `data/catalogue.json` declare the game's `port` object.

The full model (what Phangrade runs, in what order, and how to read a failed install) is Phangrade's
[docs/port-contract.md](https://github.com/GeneralAtrox/Phangrade/blob/main/docs/port-contract.md).
