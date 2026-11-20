# The game contract

Contract version: 2.17.0

2.17.0: the game's own text field, mouse and menu commands (`native_game.h`: `pg_native_text`, `pg_native_text_take`, `pg_native_mouse_mode`, `pg_native_mouse`, `pg_native_menu_take`, `pg_native_menu_list`; `console.h` `PgNativeMouse`, `PG_MOUSE_*` and `PortMenuItem.tag`), game.json `menu_commands`, `--kbm-inject TEXT=` and `FOCUSLOST`, and the recording's optional sixth section EVENTS (`base/session_events.c`, header mark `events1`); all taken at the tick's pad read and replayed from the file. Added, nothing a game used changed: a game that never asks records the five-section file it always did. Known limits in KEYBOARD_MOUSE.md.

2.16.0: game.json `menu_choices` and `extra_buttons`: a game's own menu-bar entries as data, built by the base (`base/menu_data.c`, which defines `game_menu_extra` from them), and `port_pad_ex_assign_loaded`, `port_pad_ex_row_key`, `port_settings_changes` (base.h); added, nothing a game used changed.

2.15.0: the Phangrade title bar (`--menu-bar slim|native`, `base/host_bar*.cpp`, slim by default, `native` the Windows bars); Display > Resolution (`--fullscreen-mode desktop|WxH[@Hz]`, `base/host_resolution.cpp`) and Display > Frame rate; settings.txt keeps only the player's menu keys (the base list, `bind-*`, `kbm-*`, a console's or game's menu settings) and a technical key in it is ignored with a game log line, the command line keeping every option; the game reads no pads or keys while a title bar menu is open. Added, nothing a game used changed.

2.14.0: game.json `present_pace` and `--present-pace` removed: every game presents the same way (the thread of in-between frames takes the device whenever the game's thread does not want it, and its Present never waits for the display, 2.13.0). A game.json that still has `present_pace` is refused with a message to delete it; nothing else a game used changed.

2.13.0: `--present-nowait on|off` (`base/present.c`): with `present_thread` and vsync, a Present that would wait for the display comes back at once and the thread of in-between frames makes it again holding no lock, so the game's thread never waits for the display; added, nothing a game used changed.

2.12.0: the menu pointer: `input_kbm_menu_pointer` and `PortMenuPointer` (base.h), `--kbm-inject MOUSEPOS=X:Y@V`; the mouse picks a menu's entries while the game gives them (`base/input_kbm_pointer.c`); added, nothing a game used changed.

2.11.0: game.json `loose_dir` and `--loose-dir DIR`: loose files laid over the disc's directories (`runtime/gdi.c`, `host_loose_dir`); added, nothing a game used changed.

2.10.0: the kit lines joined into one kit (`core-kit`): this 2.x line (2.9.0) with the 1.x line's 1.19.0 to 1.22.0 below (game.json `present_pace`, `menu_checks` `"default"`, a pack line's `if=game.NAME`, `pack_builds` naming the default pack, `extra_ram`, `move_tables`, `tx_edits`; and the Toy Commander kit line, 1.21.0 (game.json `audio_catchup`, `border_edges`, `keys`, `key_ramp`, `port_render_keep_frame`, mouse steer). Every key keeps its meaning and its default when absent; added, nothing a game used changed. A game that declared a 1.x contract states `"contract": "2.10.0"`: the 2.0.0 change (call hooks removed) was undone on this line at 2.4.0, so nothing a 1.x game uses is missing.

2.9.0: game.json `texture_pack_choice`, the optional texture pack a play run starts with until the player chooses (clearer content on by default); added, nothing a game used changed.

2.8.0: seats (`base/seat.c`: `port_seats_claim`, `port_seat_claimed`, `port_seat_read` and `PortSeat` in `base/base.h`), which of a game's player slots a host player holds and that player's pad, the rest being the game's computer players; calls added, nothing a game used changed.

2.7.0: this line's 2.6.0 together with master's 1.17.0 set: co-op (game.json `coop`, `base/coop_join.c`, `--coop`), up to 8 controllers (game.json `pads`, `port_pad_player`), play options kept in the recording (`port_play_option`); game.json `game_menu` was already here.

1.22.0: game.json `pack_builds` (`tools/pack_builds.py`, from the Spawn kit line): packs a game builds from its own sources and the disc after each successful compile, into `<build>/packs/NAME`; a NAME equal to `texture_pack`'s folder name is the game's default pack, and when it could not be built the game log says why (`packs/NAME.skipped`). `build.py --disc` now also feeds them; added, nothing a game used changed.

1.21.0: game.json `menu_checks` entries take `"default": true` (on until the player chooses, in play runs only), and a texture pack's index line takes `if=game.NAME` (`pvr/pvr_pack.c`): the line applies while that setting is on, and the pack is read again when it changes. So a game's clearer art is on by default with a Game menu check to turn it off; added, nothing a game used changed.

1.20.0: game.json `present_pace` (default true), a game's default for `--present-pace`, which is now listed under the options; added, nothing a game used changed.

1.19.0: seats, the same change as 2.8.0 above.

1.18.0: this line's game.json `menu_checks` (its 1.17.0) together with master's 1.17.0 set: co-op (game.json `coop`, `base/coop_join.c`, `--coop`), up to 8 controllers (game.json `pads`, `port_pad_player`), play options kept in the recording (`port_play_option`) and a game's own menu entries (game.json `game_menu`).

The Toy Commander kit line numbered its 1.x versions on its own, so the same numbers above meant other changes there: 1.21.0 mouse steer (`input_kbm_mouse_steer`, `--kbm-mouse-*`); 1.20.0 the keyboard and mouse layout (game.json `keys` and `key_ramp`, `--kbm-*`, Input > Keyboard and mouse); 1.18.0 `port_render_keep_frame`; 1.17.0 game.json `border_edges`; 1.16.0 co-op and players 2..8 in the recording (as 1.18.0 above); 1.15.0 game.json `audio_catchup`. All of them are in 2.10.0.

What a game built on the port kit may rely on. Everything here keeps working, or changes only with the version, as
the kit changes; everything else in `port/` is the kit's own and may change at any time. One place: the tables
below are the only copy (port/README.md points here). The game.json, Saturn, SDK module and exit code tables are
written by `port/tools/contract_doc.py`, which holds their meanings (the exit codes' are in `base/exit_codes.h`):
edit them there and run it. `tests/test_port_contract.py` fails when the code has a game.json key, an SDK module,
an exit code or an enhancement this document does not list, or this document lists one the code no longer has, or
when a generated table differs from what contract_doc.py writes.

## Versions

A game states the version it was built against in game.json: `"contract": "2.0.0"`. `tools/game_config.py` (so
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

<!-- generated: game-keys (port/tools/contract_doc.py: edit the meanings there, then run it) -->
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
| `interp_scene` | Optional boolean (default false): in-between frames come from the renderer blending the scene with the one before (`pvr/pvr_blend.c`), not from the game's draw function, so no game code runs and guest RAM is neither copied nor restored. `--interp-scene off` selects the draw function again (a technical option: no menu entry, and a saved `interp-scene` line is ignored) |
| `part_draw` | Optional: `{"function": addr, "stamp": "0x..", "world": "0x.."}`, the game's per-part 3D draw, whose first two arguments are the object and the part (`tools/part_draw.py` wraps it with `port_part_begin`/`_end`, `platform.h`). Each vertex the renderer parses is tied to the part that wrote it, and with `interp_scene` an in-between frame blends each vertex of a part that kept its object, stamp and vertex count from where it was a tick before, in perspective (`pvr/pvr_parts.c`); 2D and changed parts are drawn as the latest tick drew them. No game code runs. `--interp-parts off` selects screen blending again; `--interp-parts-log on` logs the parts |
| `present_thread` | Optional boolean (default false): with `interp_scene`, in-between frames are drawn and shown by a thread of their own on the real clock (`runtime/frame_extra.c`, `--present-thread`), so the tick takes no time from them, and a movie's picture is shown again until the next one. Ticks and sound do not change |
| `sq_queue_only` | Optional boolean (default false): the translated C of the game models the SH-4 store queues itself (`SqQueues`: the bytes stored and the `pref` that sends them to RAM), so a store into the window `0xe0000000..0xe3ffffff` is kept by the queue only (`runtime/tx/tx_rt.c`, `PORT_SQ_QUEUE_ONLY`) and does not also land in the RAM it mirrors. Needed when a drawer stores into one block and sends the queue to another (Power Stone 2's shadow volumes: the stores overwrote the blocks already sent) |
| `sq_fast` | Optional boolean (default false): the store-queue helpers of the translated C (`SqQueues`) run a block at a time in the play build (`tools/tx_sqfast.py`, also `build.py --sq-fast`); the judged files stay as they are and the helpers' meaning does not change (`tests/test_sq_fast.py`). A game turns it on with a whole-replay proof that its frames and audio are identical |
| `game_o2` | Optional boolean (default false): the game's own C (readable, translator, game sources, generated glue) builds at `/O2`, not the judge's `/O1`; also `build.py --game-o2`, and a build folder set up the other way is set up again. The judge's own builds do not change. A game turns it on with a whole-replay proof that its frames and audio are identical |
| `vram_init` | Optional: `{"hex byte offset in texture memory": "hex bytes"}`, set in the 8 MB texture memory before the game's first read or upload (`pvr/pvr_state.c`, `PORT_VRAM_INIT`). For texels a game samples that it never uploads: the console keeps the frame buffers or leftovers there, the port keeps them elsewhere. Power Stone 2's gauge bars sample one texel at offset 0 and need it white |
| `texture_pack` | Optional: a folder (relative to game.json; it may be built later, e.g. into the git-ignored `artifacts\` or by `pack_builds`) holding a texture pack the renderer uses by default (`pvr/pvr_pack.c`). A release carries it as `packs\<its folder name>\` beside `port.exe`, found there first. Without it, and without `--texture-pack DIR` or optional packs in `packs\`, there is no pack |
| `pack_builds` | Optional: `{NAME: {"tool": builder script, "inputs": folder it builds from}}`, both relative to game.json. After a successful compile, `build.py` runs `python TOOL --disc DISC --kit-tools <kit>/port/tools --out <build>/packs/NAME` when the stamp of inputs, tool and disc has changed. The result is an optional pack; a failure keeps the old one and never fails the build (`tools/pack_builds.py`). NAME is a folder name other than `on` and `off` |
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
| `coop` | Optional: the most players of a co-op session (2..4; 0 or absent: none), `PORT_COOP` in `port_config.h`; the game opens `base/coop_join.c`'s join screen from its own screen and wires the players in. `--coop off` or the setting `game.coop` 0 turns it off for a run; the value is kept in the session recording (`port_play_option`), so a replay plays as the recorded run did |
| `pads` | Optional: the host controllers read, players 1..8 (1..8; absent: 4), `PORT_PADS` in `port_config.h`; `--pads N` overrides it. A game's own pad layer keeps players 1..4 and takes players 5..8 with `port_pad_player` (`base/base.h`); they are recorded and replayed with the others. At 4 a run reads, records and logs what it did before |
| `game_menu` | Optional: `true` when the game adds its own menu-bar entries: it defines `game_menu_extra` (`base/console.h`, the same `PortMenuItem`s a console gives), shown after the console's in the menu each names. A `game.` setting key is the game's to read (`port_setting_get_int`); a value that changes play goes through `port_play_option` so replays keep it |
| `menu_choices` | Optional: at most eight of the game's own choices, each `{"menu": "Display"\|"Audio"\|"Input"\|"Game", "label", "setting": "game.NAME", "choices": [labels], "values": [words], "default": word}` (2..16 choices), shown in that menu after the console's (`base/menu_data.c`). A choice saves its word as `game.NAME` at once; the game reads it with `port_setting_get_int` or `port_setting_get_str` at its own time (a scripted run, with no settings file, gets the game's default). Not with `game_menu`: the base defines `game_menu_extra` from it |
| `extra_buttons` | Optional: `{"actions": [labels], "players": 1..8, "menu": "Input"}`, for a game with no key screen of its own: Input > Extra buttons, a player, then LB, RB, the stick clicks, Back, Guide and the right stick's four directions, each taking one of the game's actions (numbered from 1 in the order given) or none (`base/menu_data.c`). Kept as `game.padex.p<N>.<row>`, as a key screen keeps them (`base/input_ex_assign.c`); the game loads them with `port_pad_ex_assign_load` and reads them with `port_pad_ex_assign_codes`. Not with `game_menu` |
| `menu_commands` | Optional: the game's own commands, each `{"menu": "Display"\|"Audio"\|"Input"\|"Game", "label", "tag": 1..65535, "list": "runtime" or [labels]}` (tags unique, at most 435 entries in all). A pick goes to the game as (tag, index) at its next tick (`native_game.h` `pg_native_menu_take`) and is recorded. A list is a submenu: `>` in a label nests, more than 16 are split into "1-16", "17-32", ...; `"runtime"` is the list the game gives with `pg_native_menu_list`. `--menu-pick` takes the whole path (`base/menu_data.c`, `base/host_menu_tagged.cpp`). Not with `game_menu`: such a game sets `PortMenuItem.tag` in C |
| `keys` | Optional: the game's whole keyboard and mouse layout (player 1), an object of key or mouse name to targets joined by `+`, e.g. `{"W": "LSUP", "MOUSELEFT": "RT"}`. Keys are SDL's names plus `UP DOWN LEFT RIGHT RETURN KP_ENTER SPACE ESCAPE BACKSPACE TAB LSHIFT RSHIFT LCTRL RCTRL LALT`; mouse names are `MOUSELEFT MOUSERIGHT MOUSEMIDDLE WHEELUP WHEELDOWN`. Targets are `A B X Y START UP DOWN LEFT RIGHT LB RB L3 R3 BACK GUIDE RSUP RSDOWN RSLEFT RSRIGHT LT RT LSUP LSDOWN LSLEFT LSRIGHT`, or `none`. Without it the kit's layout applies (README.md "Controls"). The player's changes (Input > Keyboard and mouse) come after it |
| `key_ramp` | Optional: 0 to 60 (default 0), the reads a held stick key takes to reach full deflection; 0 is at once |
| `audio_catchup` | Optional boolean (default false): catch-up mixing (`runtime/aica.c`). A voice's position the game reads mid-blank first mixes the blank up to that point, so what the game sees as played has been mixed, as on the chip; a game that refills a sound ring up to the read position then never overwrites sound before it plays. The positions the game reads, so its logic, do not change; only the sound does. `--audio-catchup off` selects the whole-blank mix again. No effect with `aica_arm` |
| `border_edges` | Optional boolean (default false): in a wider (taller) frame, the bars beside (above and below) the 4:3 screen take the screen's own edge pixels, row by row (column by column), when the scene drawn last drew nothing past the screen or a movie is over the picture (`base/present_edges.c`): a movie or a menu whose edge is white has white bars, not the border colour. The screen itself and a 4:3 frame are never changed; game logic is not touched. `--border-edges off` keeps the border colour |
| `loose_dir` | Optional: a folder name, looked for beside the disc image (`--loose-dir DIR` overrides it with any folder). Each file in it, and in its subfolders named as the disc's directories, appears in that directory of the disc as the game reads it (`runtime/gdi.c`), in place of a disc file of the same name: levels and text a player adds from their own discs. No folder, or an empty one: the disc as it is |
| `session_ahead` | Optional: `"keep"` (default) or `"old"`, how a replay reads the notes of sound mixed ahead of a real clock (`base/session.c`) where the game reads a save or a note (a play option): "keep" keeps those for this blank or later, "old" drops them all, as kits before 11-07 did (`PORT_SESSION_AHEAD`; the environment variable `DP_SESSION_AHEAD` still overrides it for a run). For a game whose recordings must replay exactly as on such a kit |
| `extra_ram` | Optional boolean (default false): a second 16 MB the console does not have, mapped at 0x88000000 with its P0 and P2 forms 0x08000000 and 0xa8000000 (area 2, unused on a Dreamcast), zeroed at start. For data a port moves out of a full main RAM, such as tables widened for more players; a pointer into it is a host pointer like the main RAM's. `guest_fold` and `guest_is_ram` know it. The in-between frame copies, RAM diffs, captures and lockstep proofs see only the main 16 MB. Off: nothing is mapped there |
| `menu_checks` | Optional: at most four of the game's own on/off settings, each `{"label", "setting": "game.NAME", "needs": FILE, "default": true}`, shown at the end of the Game menu (`runtime/console_dc.c`). The game reads one with `port_setting_get_int` (the menu saves `on`/`off`, read as 1/0; off when never chosen, or on in a play run with `"default": true`; a scripted run, which has no settings file, always gets 0), so it applies from the game's next read; a texture pack line's `if=game.NAME` word follows it at once. `needs` (optional) is a file in the data folder: without it the entry is greyed and the log says which file is missing |
| `move_tables` | Optional list of `{"name": text, "from": addr, "bytes": count or hex, "to": addr, "ends": [function addr or FUNC@INSN, ...]}`: guest data tables moved at build time (`tools/move_tables.py`), for a table that must grow where another object follows it. Every address literal inside `[from, from + bytes)` in the translated C the build compiles becomes `to` plus its offset, in the same P0/P1/P2 form; the end value `from + bytes` moves only inside the `ends` functions (a loop that stops at the table's end); an entry `FUNC@INSN` moves it only after that instruction's comment in the translated C, for a function that also uses the same literal as the next object's base, and the build stops if a listed end is not found. Named globals inside a table move in game.h, so readable C follows. The judged files are not edited: the build compiles rewritten copies (`tx_move/`). Tables may not overlap where they are or where they go. Usually with `extra_ram`. A moved table that still holds what it did plays the same, proven by replay. A table of equal entries may add `"stride"`, `"max"` and `"counts"` (FUNC@INSN sites where the original entry count is compiled in): there the count, and the table's listed ends, follow `g_move_count[k]` (port.h; 0 = the original count) |
| `tx_edits` | Optional name of a .json file beside game.json: `{"prelude": [C lines], "edits": [{"func": addr, "insn": addr, "find": text, "replace": text, "why": text}]}`, text edits of the game to build-folder copies of its translated C (`tools/tx_edits.py`), each at one instruction and found exactly once or the build stops. Applied before `move_tables` |
| `texture_pack_choice` | Optional: the folder name of an optional pack (`packs\NAME\` beside `port.exe`, e.g. a `pack_builds` NAME) that a play run shows over the game's default until the player chooses (`pvr/pvr_pack.c`): clearer or upscaled content is on by default. A saved `texture-pack` setting or any `--texture-pack` replaces it, a check run (fixed clock) ignores it as it does the default pack, and a missing folder leaves the game's default |
| `sprite_fields` | Optional: `[{"tsp": "8 hex digits", "size": [w, h], "period": [x, y]}]`, a tiled backdrop of sprites that wraps just past the 640x480 screen (`pvr/ta_parse.c`, `PORT_SPRITE_FIELDS`). A sprite with that TSP word and size is drawn again every period, as far as a wider or taller frame needs, so nothing pops at the wrap. No copies at 4:3, and the game's lists and memory are untouched. `port/tools/motion_check.py` finds such a pop |
<!-- end generated: game-keys -->

`overlays.json`: Optional, beside game.json: the code the game loads over guest RAM at run time, one unit per workspace
range (`tools/gen_overlays.py` lists its keys). A unit's `"native": {addr: name}` puts the game's own C function `name`
(in `sources`, with the function's registry prototype) in place of that overlay function: calls by name, its dispatch
row and `tc_call` all reach it, and its readable and translator C are not compiled. `"wrap"` (the same form) does that
too, and keeps the function compiled as `<name>_Original` for `name` to call. A native or wrapped function may call a
guest address with `u32 tc_call(u32 target, const PortTcArgs *args)` (`runtime/tx/tx_rt.c`; `PortTcArgs` in
`runtime/tx/tx_rt.h`: r4..r7, fr4..fr11, the stack words).

A non-static function defined at column 0 in an `sdk` module or a `sources` file replaces the stub of the
registry function with that name. The entry and every `hardware` name are checked against the registry. An unknown
key stops gen_port.

## Saturn ports

A Saturn game keeps a `game.json` with one object, `saturn`, read by `tools/gen_saturn.py` (the Dreamcast keys above do
not apply). Paths and addresses are strings (`"0x06000000"`).

<!-- generated: saturn-keys (port/tools/contract_doc.py: edit the meanings there, then run it) -->
| Key | Meaning |
|------|------|
| `name` | The game's name: the log, setup's messages and online play's check that both copies are the same game (`SAT_GAME_NAME`; default `Saturn game`) |
| `image` | The code image the port loads (a composite of the disc's code files until the disc loader replaces it); `--image` overrides |
| `image_base` | Where it loads in work RAM |
| `image_files` | Optional: the disc files of the code image, `[["/0", "0x06010000"], ...]` (disc path, load address), as onboarding composed `image`. With `image_sha256`, a run without `--image` makes the image from `--disc` (`sat_image.c`): a release has no image.bin, and setup and verify refuse a disc that makes another (exit code 5). AutoOnboard writes both. Absent: the `image` file |
| `image_sha256` | The SHA-256 of the image `image_files` makes (64 hex digits); required with it |
| `fallback` | Optional: `"on"` or `"off"`: `--fallback`'s default in this build (`sat_interp.c`), as the boot proved it: a launcher passes no flags |
| `product` | Optional: the disc header's product number; setup and verify refuse a disc with another (exit code 5, `console_sat.c`). Without it, any Saturn disc is accepted |
| `entry` | The guest function the port calls first (on the game's thread) |
| `entry_sp` | Its initial stack |
| `entry_gbr` | Optional: its initial GBR (default: `entry_sp`) |
| `boot_dir` | Optional: the folder of the program the image starts at when it replaces the disc's first-read loader (`/BOMSS`); the CD block's file system and the boot ROM's words at 06000ccc..06000cce hand that folder to the game's file system, as the loader left them (`sat_cd.c`) |
| `slave_sp` | Optional: the slave SH-2's stack top (the game loads it from its own memory, which the translated slave program does not run) |
| `frame_hz` | 50 (PAL) or 60 (NTSC); the vertical blank comes on the real clock |
| `exit_regs` | The registers a function hands on at return besides r0 (the SH-2 asm of the game's library reads them after a call): r4..r7 and r1..r3; a callee that never writes one hands the caller's back |
| `vblank_flags` | Optional: offsets from GBR of the bytes only the vertical-blank handler sets: a loop that reads one waits for the vertical blank (`sat_vblank_wait`) instead of spinning |
| `main_loops` | Optional: the game's main loop functions: each call is a turn with nothing to do yet, and enough of them in one vertical blank wait for the next (`sat_idle_turn`, `sat_sys.c`) |
| `controller` | Optional: `"saturn"`: the Saturn pad on the host's controller by default (A B X Y as they are, RB C, LB Z, LT L, RT R, Start, the d-pad; the stick clicks, Back, Guide and the right stick unassigned), by the host button each action reads (`sat_input.c`). The keyboard is game.json `keys`. The pad is still named dreamcast in the log: the base has no per-console name (a proposal is in the handover). Absent: the layout games made before it keep (C on RT, Z on LT, L on LB, R on RB) |
| `confirm` | Optional: the game's own button that moves on in its menus (`"A"` `"B"` `"C"` `"X"` `"Y"` `"Z"` `"L"` `"R"`), found by AutoOnboard's menu probe (`onboard_sat.py`): the kit puts it on the host's A (keyboard Z), and the Saturn button that had host A takes the host button confirm had, so every button keeps one (`sat_input.c`). Absent: the layout above unchanged |
| `cancel` | Optional: the game's own button that backs out of a menu (same values, not confirm's), found by the same probe: the kit puts it on the host's B (keyboard X) the same way. Absent: the layout above unchanged |
| `smpc_intback` | Optional: `"hardware"`: the SMPC's INTBACK peripheral report as the hardware gives it (SR bit 7 PDE, 6 PDL, 5 NPE, 4 RESB never; the report per port, 32 bytes a packet; a CONTINUE is IREG0 bit 7 inverted from the last one, a BREAK wins), which SBL games need to take the pad (`sat_smpc.c`). Absent or `false`: the older report (SR 0x90, one packet, only a CONTINUE with bit 7 set), which a game made before it, with its recordings, keeps |
| `sound_cpu` | `true`: SMPC SNDON starts a 68000 on the driver the game loaded into sound RAM, and the SCSP's timers, interrupts and 32 voices run each frame (`sat_m68k*.c`, `sat_scsp*.c`); the mixed sound goes to the wave device and, with `--wav FILE`, to a WAV file. `sound_clear` and `sound_stream` are then unused. Emulated hardware: for bring-up and as the answer key for a native sound driver, not the play path to keep (RUNTIME_TARGET.md) |
| `sound_clear` | Sound-RAM mailbox bytes the missing 68K driver would clear |
| `sound_stream` | The driver's stream-progress report: `start_addr`, `start_cmd`, `status_addr`, `status_count`, `status_step`, `block`, `rate` |
| `ground_shadows` | The game's floor-shadow quads, `{"pmod": "0x0128", "srca": "0xa4ec"}`: VDP1 distorted sprites with that draw mode and texture. Play runs draw them before the rest of the list, so nothing standing on them is darkened (Display > Shadows, `--ground-shadows under\|original`; `sat_vdp1_list.c`). Check runs keep the game's order |
| `widescreen` | Optional: the picture composed wider than the screen in play runs (`sat_wide.c`, RUNTIME_TARGET.md "Any aspect ratio"): `true` every picture, `"rbg0"` only pictures that show RBG0 (a 3D floor) |
| `wide_cull` | Optional: `{"sgl": SGL's work area, "at": [the functions that read its window]}`: the game's culling widened with the picture as those are entered (`sat_wide.c`). Robo Pit leaves it out: its replay shows it changes the game |
| `movie` | Optional: the native movie player (`sat_movie.c`, `--movie-native`): `{"call", "name_arg", "skip_after_arg", "skipped", "screen", "open_blanks", "close_blanks", "reset_call"}` (`tools/gen_saturn.py` `movie_h`), and `skip_logos`, the opening logos `--skip-logos` skips (`sat_logos.c`) |
| `twin_stick` | Twin-stick in the game's fights, `{"fight": "0x0605acd0", "turn": ["LEFT", "RIGHT"], "strafe": ["L", "R"]}`: while the game runs `fight` (a function it runs only in a fight), the right stick presses the `turn` buttons and the left stick held sideways the `strafe` buttons, by the pad's action names (base `pad_twin.c`) |
| `auto_load` | The game's own load path, driven at boot when its save file is there, `{"file": "ROBO_PIT___", "steps": [...]}`: each step waits for the game to enter (`enter`) or leave (`leave`) a function, pressing `press` until then (one button, or several joined by `+` pressed together: `"UP+A"`), and may put a 16-bit `poke: [address, value]` before it runs (base `autoload.c`, `sat_bup.c`) |
| `netplay` | Optional: online play for two (`sat_net.c`): `{"pad", "delay", "hash_every", "port", "test_byte"}`, and `menu`, `drop_ms`, `leave` and `show` for online play started from the game's own menus (`tools/gen_saturn.py` `netplay_h`, `gen_saturn_menus.py` `net_menu_h`); without it the build has none |
| `team_fight` | Optional: a four-robot fight played two against two (the game's `sat_team.c`, `--team-fight`): the game's addresses and sizes (`tools/gen_saturn.py` `team_fight_h`) |
| `level_edit` | Optional: `{"schema": the game's level schema, relative to game.json, "level": its level (default: the first)}`: the edited arena and the level editor's Saturn side (`sat_level.c`, `tools/gen_saturn_level.py`) |
| `menus` | Optional: the kit's entries in the game's own menus (`sat_menus.c` and the game's `sat_lobby.c`, `sat_ladder.c`): the hub, its draw, box, cursor bar, text and sound, with `fight_choice` and `online` (`tools/gen_saturn_menus.py` `menus_h`) |
| `menu_panel` | Optional: `{"pmod", "color"}`, the menus' half-transparent panel drawn by the presenter (`sat_panel.c`, `--menu-panel`) |
| `part_choice` | Optional: a model part the player picks (`sat_part.c`, `--part-choice`): `{"title", "object", "pdata", "points", "polygons", "drop_points", "max_points", "original", "choices": [[value, label, edits], ...]}` (`tools/gen_saturn_heads.py` `part_choice_h`) |
| `heads` | Optional, needs `part_choice`: new head types after the game's own, from part_choice's versions (the game's `sat_heads.c`, `--new-heads`; `tools/gen_saturn_heads.py` `heads_h`) |
<!-- end generated: saturn-keys -->

Options of the built program: `--frames N` (stop after N vertical blanks), `--fast` (no wait for the real clock: a test
aid, never a launcher's), `--headless`, `--shot VB FILE`, `--shot-every N`, `--watch SECONDS`, `--disc CUE`, `--image FILE`,
`--log FILE`, `--input`, `--bind-NAME`, `--volume N`, `--audio on|off`, `--scene-dump-at V`, `--dump-frames`, `--settings`, `--profile`, `--trace N`, `--trace-from ADDR N`, `--trace-vb VB N`, `--work-counts FILE`, `--work-from BLANK` (README, "Work counters"). Exit codes: those of every console (Exit codes, below).

## SDK modules

Each compiles against the game's generated `game.h`, so the names it defines must be the game's registry names; the
header comment of each module lists what it needs. A module whose names the game lacks is left out of game.json
`sdk` and the game writes its own.

<!-- generated: sdk-modules (port/tools/contract_doc.py: edit the meanings there, then run it) -->
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
<!-- end generated: sdk-modules -->

Registry names in gen_port.py `GUEST_CRT` (`malloc`, `free`, `calloc`, `realloc`) are the guest's own C library over
guest RAM, never the host CRT's: game.h maps each onto its kit name (`#define malloc sy_guest_malloc`), so readable
calls, dispatch rows and a logging stub (where no module defines it) all use the kit's name.

Names the SDK does not give (the `Sy_*`, `Gd_*` and `Shc_*` ones) are registry names. A new game gets them for
byte-identical library functions with `python xproject.py --project NEW --from OLD names`.

## Command-line options

The options a game's `.bat` files and `tests/*.script` `args` lines may use. A play run reads `settings.txt` (each
line `key=value` is the option `--key value`, for the keys a player's menu choice writes only: the Display, Audio, Input
and Game menus' keys, `bind-*`, `kbm-*` and `game.*`; any other key, a technical option such as presenting, threads or
pacing, is ignored and logged, so such options are the command line's only); a check run (headless or `--fixed-clock`) reads none. The launcher's
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
| `--audio-catchup on\|off` | Catch-up mixing (game.json `audio_catchup`): on mixes up to a voice position the game reads mid-blank; off mixes each blank whole at its end |
| `--border-edges on\|off` | Edge-colour bars (game.json `border_edges`): on, the bars beside the 4:3 screen in a wider or taller frame take its edge pixels after a frame that drew nothing in them; off, the border colour |
| `--log FILE` | Where the log goes |
| `--pad-style auto\|dreamcast\|xbox\|playstation\|NAME@V,...` | The style of controller a texture pack's variant lines (`KEY FILE xbox`) apply to; auto (default) follows the device last used, or before that the first connected gamepad, only in a run that reads real input; `NAME@V,...` swaps at vertical blanks, for a test |
| `--pad-art on\|off` | Controller pictures (Input menu, setting `pad-art`): on (default) shows the pictures for the pad in use, off the game's own art |
| `--texture-keys DIR` | Lists the textures the run decodes: `DIR/KEY.png` each and `DIR/keys.txt` (pack key, size, first vertical blank), to build a texture pack |
| `--input [Pn.]NAME@V[+N],...` | Scripted presses at vertical blank V, held N blanks (`L=V`/`R=V` a trigger part way; `LSUP`, `LSDOWN`, `LSLEFT`, `LSRIGHT` the left stick alone, without the d-pad) |
| `--bind-NAME SOURCE[+SOURCE...]` | Assigns host buttons to one of the console's actions, on every console (`NAME` is the action: `A`, `START`, `RT` ...; each `SOURCE` one of `A B X Y START UP DOWN LEFT RIGHT LB RB L3 R3 BACK GUIDE LT RT RSUP RSDOWN RSLEFT RSRIGHT`, or `none`). The Input > Controls menu saves the same choice as `bind-NAME`. An assigned button presses the action's default button before the pad is recorded, so the game, its log and a replay see only the default layout; a scripted run uses each action's default |
| `--kbm-SOURCE TARGETS` | Assigns the keyboard or mouse input `SOURCE` (a key name, or `MOUSELEFT`, `MOUSERIGHT`, `MOUSEMIDDLE`, `WHEELUP`, `WHEELDOWN`) the targets joined by `+` (as game.json `keys`), over the game's layout. Input > Keyboard and mouse saves the same choice as `kbm-SOURCE` (lower case); a replay or check reads none of it |
| `--kbm-inject NAME@V[+N],...` | A test's keys and mouse instead of the real ones, through the same layout: `NAME` held from vertical blank V for N blanks (default 6); `WHEELUP`/`WHEELDOWN` one notch; `MOUSEMIDDLE` a click; `MDRAG=DX:DY@V+N` the middle button held while the mouse moves; `MOUSEMOVE=DX:DY@V+N` the mouse moved DX, DY pixels (right, down) before each of N reads from V, no button held (mouse steer, e.g. `MOUSEMOVE=6:-3@6000+20`). The middle drag turns player 1's right stick and sets `HOST_PAD_MOUSE_LOOK` (base.h) in the recorded pad |
| `--kbm-mouse-steer on\|off`, `--kbm-mouse-sensitivity 1..10`, `--kbm-mouse-invert on\|off` | Mouse steer (`input_kbm_mouse_steer`, base.h): whether the mouse moving with the middle button up turns player 1's left stick while the game asks for it (default on), how far (default 5: 24 pixels between two reads is full deflection, 10 twice as far), and whether pushing the mouse forward is the stick down (default off: forward is up). Input > Keyboard and mouse saves the same choices as `kbm-mouse-steer`, `kbm-mouse-sensitivity` and `kbm-mouse-invert`; the stick is set before the pad is recorded, so a replay reads none of it |
| `--pads N`, `--rumble on\|off` | How many players' pads are read (1..8; default game.json `pads`, else 4); whether they rumble |
| `--dump-frames N,..`, `--dump-every N`, `--dump-dir DIR` | Frame PNGs |
| `--dump-hashes FILE` | With those: no PNGs, a line per frame in FILE (its name, size and a hash of its pixels) |
| `--pvr-yuv simd\|table\|check` | YUV422 textures (a movie frame): a pair at a time with SSE2 (default), one texel at a time, or both with every texel compared (`pvr: --pvr-yuv check` in the log) |
| `--scene-dump-at V`, `--stop-at V` | A scene dump at vertical blank V; end the run there (exit code 0) |
| `--watch-ram A@V1:V2` | Logs each write to the guest word A (hex), with the stack, in vertical blanks V1 to V2: the `--lockstep-watch` watch for a replay with no lockstep |
| `--poke-ram A=V@V1:V2` | Writes the byte V to guest address A (hex) on each frame of vertical blanks V1 to V2: a test's way to a late scene (a stage); both sides of a compare pass it |
| `--fault-at V` | A test fault (an integer divide by zero, which no console's memory model takes for a store) at vertical blank V, to check that the log, the crash recording and its replay work; the replay commands carry it |
| `--game-log on\|off`, `--game-log-every N`, `--game-log-save N` | The game log (README "Game log"): on in play runs, off in check runs unless given; a snapshot of the game's fields every N vertical blanks (600); the recording autosaved every N (3600; 0: never) |
| `--replay FILE`, `--replay-until V`, `--record off` | The session recording from boot: play it back (players 2..8 too, when it has them); end the replay at vertical blank V (the player takes over with `--live-input on`); turn it off. With `--replay-until V` the timer goes on from the replay's value, never back, so with `--fast-until V --live-input on` the player takes over at V: Help > Resume before the last crash does that with the newest `crash-*.ptrec` or `killed-*.ptrec`, 5 s before its end, after asking (`base/host_resume.cpp`; the new run waits for the old one to end), and a crash's log prints the same command |
| `--coop on\|off` | A co-op game's sessions (game.json `coop`): off, the join screen never opens; on (default) follows the setting `game.coop` |
| `--save-dir DIR`, `--vmu A1,B1,..\|none` | The save folder; the memory cards' sockets |
| `--present-nowait on\|off` | Real clock with `present_thread` and vsync: a Present that would wait for the display (a buffer, the next refresh) comes back at once (DXGI_PRESENT_DO_NOT_WAIT) and the thread of in-between frames makes it again holding no lock between tries; the game's thread waits at most for the copy into the back buffer (on, the default). A frame not taken in 250 ms is presented the old way. off: that thread's Present waits holding gfx_lock, as before. The 5 s log line says how many Presents would have waited. Ticks and sound do not change |
| `--window WxH`, `--vsync on\|off`, `--fps N` | Window size (Display > Resolution), vertical sync, a frame-rate cap (Display > Frame rate) |
| `--present tick\|interp\|auto`, `--interp-scene on\|only\|off`, `--aspect fit\|4:3\|16:9\|21:9\|W:H`, `--widescreen on\|off`, `--texture-pack NAME\|DIR\|on\|off` | Enhancements (below) |
| `--settings FILE\|none` | The saved options file |
| `--must-work FILE` | Stubs that end the run at their first call (exit code 3) |

## Exit codes

The same on every console: `base/exit_codes.h`, which both kits include.

<!-- generated: exit-codes (port/base/exit_codes.h, written by port/tools/contract_doc.py: edit the meanings there, then run it) -->
| Exit code | Meaning |
|------|------|
| 0 | A normal close, or setup or verify succeeded, or a run reached `--stop-at` or the end of the frames a check replays (`--lockstep-end exit`, hw_check, `--ram-frames`); also a Dreamcast game's own exit (its entry function returned), and on Saturn `--frames N` reached or an online match ended or moved to a new copy of the game |
| 1 | A crash or fault, or another failure (a copy that failed); on Saturn also the entry function returning (a Saturn game never returns, so the recording is kept and the launcher pauses), the call depth bound and no memory for online play |
| 2 | The `--seconds` watchdog (test runs only); also a check-only option it could not read (`--lockstep*`, `--hw-*`) |
| 3 | A `must_work` stub was called, a checked build's run-time check failed, or a call into overlay RAM found no loaded function; on Saturn the game halted (`tc_halt`) |
| 4 | The disc image is missing or unreadable (also for a play run); also the display device was lost, the renderer ran out of memory for a scene, or `--verify-assets` was given no `--assets` (KNOWN.md "Exit code 4") |
| 5 | The image is another game or region (also a play run's image with no boot file) |
| 6 | Online play (Saturn): the two copies went out of step |
| 7 | Online play (Saturn) could not start: the network did not start, no host answered in 60 s, or the host runs a different copy of the game |
<!-- end generated: exit-codes -->

## Enhancements

Each is off by default where marked, switched at run time (the Display menu writes `settings.txt`), and off in a
check run (headless or `--fixed-clock`: regress, lockstep, hw_check, bu_sweep), so goldens and emulator comparisons
see the original. None of these changes guest memory or timing. A game's own additions (its `sources`, through game.json
`wrap` and overlays.json `native` and `wrap`) may change both; each is the game's own switch, and the game keeps it off in a
check run as these are.

| Id | Option | Default in a play run | In a check run | Menu (Display) | settings.txt |
|---|---|---|---|---|---|
| `interp` | `--present tick\|interp\|auto` | `auto` (the host adds it): on while the frame-rate cap, or no cap, is above the rate the game is rendering at now, which is measured, since it can change from screen to screen | off: the host adds nothing and the tools pass `--present tick` | none: it follows Frame-rate cap | none (a saved `present` is ignored) |
| `aspect` | `--aspect fit\|4:3\|16:9\|21:9\|W:H` (a decimal ratio too; `--widescreen on\|off` is `fit` and `4:3`) | `fit` (the window's shape) for a game with game.json `widescreen`, else `4:3` | `4:3` unless given | Aspect: Fit window, 4:3, 16:9, 21:9 | `aspect` |
| `texture_pack` | `--texture-pack NAME\|DIR\|on\|off` | on for a game with game.json `texture_pack` | off unless `--texture-pack on`, `NAME` or `DIR` | Texture pack | `texture-pack` |
| `ssaa` | `--ssaa 2\|4` | off | off unless given | none | none (command line only) |
| `aniso` | `--aniso N` | off | off unless given | none | none (command line only) |
| `vmu_screen` | `--vmu-screen off\|side\|over` | off | off unless given | VMU screen | `vmu-screen` |
| `fps_counter` | `--fps-counter on\|off` | off | off unless given | Frame-rate counter: on the menu bar before the copy icon, the frames shown a second and the game's own rate against its console's, `144 fps   game 55/60`, the game's figure red under 98% of its target (in-between frames keep the first at the cap while the game runs slow); read once a second, it changes nothing the game sees. Apart from it, a play run's log gets a `tick gap:` line when a tick took longer than two of the console's vertical blanks (at most one a second, with a count of those held back) | `fps-counter` |
| `menu-bar` | `--menu-bar slim\|native` | slim | slim | The window's bar: `slim`, the Phangrade title bar (host_bar*.cpp), one dark bar in place of the Windows title bar and menu bar, drawing this same menu with the frame-rate reading, the copy icon and the window buttons; the window grows by the bar and the picture keeps its size and pixels. Its menus open with a click, F10, Alt+letter, and Esc or a controller's Guide while those are not given a game action (stick or d-pad moves, A chooses, B backs out); full screen shows it at the top edge. `native`: the Windows title bar and menu bar. Display > Phangrade title bar switches it at once | `menu-bar` |
| `fullscreen_mode` | `--fullscreen-mode desktop\|WxH[@Hz]` | desktop | not used (no window) | Resolution, in full screen: the display's modes, Desktop (its own) first. In a window the same group is the window's size (Fit to the desktop, 1x-4x). The game is drawn at the window's size either way (present.c gfx_resize), so this is also the size it is drawn at (host_resolution.cpp) | `fullscreen-mode` |
| `twin_stick` | `--twin-stick on\|off` | on for a game with game.json `twin_stick`: in its fights the right stick turns and the left stick's sideways direction sidesteps; the d-pad turns as before | off in a scripted run unless given; a replay plays the recorded pad, which holds the buttons twin-stick pressed | Input > Controls > Twin-stick | `twin-stick` |
| `menu_checks` | none: the game's own, which it reads from `game.NAME` | off, or on for an entry with `"default": true` | off: a check run has no settings file, and a default is never on there | Game > each entry's label | `game.NAME` |
| `auto_load` | `--auto-load on\|off` | on for a game with game.json `auto_load` when its save file is there: the game's load path is driven at boot | off unless given; a replay takes the recorded choice | Game > Auto-load save | `auto-load` |
| `fast_forward` | `--fast-forward 2x\|3x\|4x\|max` | 3x, only while Tab or its button (R3 until the player picks another: Input > Controls > Fast forward) is held: the console runs its machine's clock that many times as fast (base/speed.c), for a console with PORT_CONSOLE_SPEED; the title bar says so | never: not in a check run, a replay, online play or on the fixed clock (`speed: fast forward not offered`) | Game > Fast forward speed | `fast-forward` |
| `fast_forward_toggle` | `--fast-forward-toggle on\|off` | off: held, not toggled | never | Game > Fast forward: toggle | `fast-forward-toggle` |
| `game_options` | `--ID WORD` (each option's: on/off, or one of a choice's values) | each option's `default`: an improvement is on (a game's own options: a `PortOption` table, `base/game_options.c`, docs/NEW_GAME.md "Adding a per-game option"; Dragon Force's `quick-dialogue`) | its `original` unless given; a replay takes the recording's note | the menu its table names | the option's id |

Not enhancements: the player's preferences the menu also saves, which change neither what the game draws nor its
timing: `vsync`, `fps`, `volume`, `mute-unfocused`, `rumble`, `pause-unfocused`, `bind-fast-forward` (Fast forward's button). `pad-art` (the Input menu's Controller pictures) picks among a texture pack's controller images and does nothing without one.

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
