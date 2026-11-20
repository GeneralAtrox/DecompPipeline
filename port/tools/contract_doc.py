"""The game contract's tables (port/CONTRACT.md), written here once with their meanings: the game.json keys of each
kit and the SDK modules (those in contract_sdk.py); the exit codes come from base/exit_codes.h, the one list both kits include. Run, it rewrites
those tables in CONTRACT.md between their markers ("<!-- generated: NAME ... -->" and "<!-- end generated: NAME -->");
--check only says whether they are current. game_config.py takes KNOWN and SATURN_KEYS from here, and
tests/test_port_contract.py fails while the document differs from what this writes.

    py -3.12 port/tools/contract_doc.py [--check]

Nearest existing: game_config.py - it holds the key list and loads game.json, but it is at the 400-line limit, and the
meanings are the document's prose, which the loader does not need.
"""
import pathlib
import re
import sys

KIT = pathlib.Path(__file__).resolve().parents[1]  # DecompPipeline/port
DOC = KIT / "CONTRACT.md"
EXIT_CODES_H = KIT / "base" / "exit_codes.h"
REGENERATE = "py -3.12 port/tools/contract_doc.py"


# game.json, the Dreamcast kit's (game_config.load() checks each): key -> meaning, in the document's order
GAME_KEYS = {
    "contract": "Optional: the contract version the game was built against (see Versions)",
    "name": "The game's name, for the log",
    "decomp": "The pipeline workspace (`project.json`); the readable C is its `readable/src` and `readable/include`",
    "entry": '`{"name", "addr"}`: the function the port calls first (`void name(void)`, checked against the registry)',
    "image_base": "Where the boot file loads (hex)",
    "boot_file": "Optional; `null` reads the name from IP.BIN",
    "disc": "Optional default disc image (`.cue` or `.gdi`); `--disc` overrides it",
    "fsca_table": "Optional: the workspace's fsca table, compared with the computed one at start-up",
    "must_work": "Optional: stubs that end the run at their first call (exit code 3), one name per line, `#` comments",
    "sdk": "Kit SDK modules to compile (names of `sdk/*.c`, see SDK modules)",
    "sources": "The game's own platform files: game-range hardware functions, SDK replacements bound to its own code",
    "hardware": ("`{addr: name}`: game-range functions the game's sources replace; the build stops if the registry "
                 "renames one"),
    "wrap": ("`{addr: wrapper}`: readable functions whose callers call the game's `wrapper` instead (it calls the "
             "original); the readable files that name the function compile with `Name=wrapper`, and its dispatch "
             "row points to the wrapper"),
    "console": ('`{"country", "language", "broadcast"}`: the console `sy_sys` reports (numbers, as the game '
                "compares them)"),
    "id": ("Optional: the game's id in `--version` and `installation.json` (default: the name of the folder above "
           "the port folder)"),
    "left_stick": ('Optional: `"off"` (default), not offered; `"choice"`, the game\'s key screen offers the left '
                   'stick as movement, off until a player picks it; `"on"`, on until changed. For a game that '
                   "doesn't read the analog stick: the stick's raw values still reach it."),
    "arrows": ('Optional: `"both"` (default), the arrow keys press the d-pad and move the stick; `"stick"`, they '
               "only move the stick (for a game whose d-pad does something else than steering, such as cameras). "
               "I/K/J/L press the d-pad either way"),
    "widescreen": ("Optional: `true` when the game widens its culling by `port_widescreen()` and "
                   "`port_tallscreen()` (platform.h; a wrap of its projection function: the frame's width and "
                   "height over the 640x480 screen's, each 1 or more, at most one above 1). Play runs then follow "
                   "the window's shape (`--aspect fit`, the default) at any shape, wider or narrower than 4:3: the "
                   "640x480 screen is the frame's middle (2D stays 4:3; an untextured full-screen triangle, a fade, "
                   "or one with an 8x8 texture, a fill, covers the frame). A game without the key stays 4:3 until "
                   "it chooses a shape (the menu or `--aspect`). Checks (headless or fixed-clock runs) stay 4:3 "
                   "unless `--aspect` is given; `--widescreen on` is `--aspect fit` and `off` is `--aspect 4:3`"),
    "wide_cull": ('Optional: `{"viewport": addr, "functions": [addr, ...]}`, for a game whose renderer drops what '
                  "falls outside its viewport (four floats W, H, X, Y at `viewport`), so parts past the 640x480 "
                  "screen go missing in a wider or taller frame. gen_port writes a `WideCull_ADDR` wrapper for each "
                  "function (the build folder's `wide_cull.c`, wrapped as `wrap` would) that calls it with a "
                  "viewport centred on the screen (the full screen, a wipe) grown by the frame's share "
                  "(`port_wide_cull_begin` and `_end`, platform.h) and puts the four back after. A view off centre "
                  "(a render target), a call inside a widened one, or a 4:3 frame is left alone, so checks do not "
                  "change. A function may not also be in `wrap` or `hardware`"),
    "boot_sha256": ("Optional: the SHA-256 of each boot file the port accepts (the right game and region); setup "
                    "and verify refuse any other with exit code 5. Without it, any disc whose boot file loads is "
                    "accepted"),
    "interp_scene": ("Optional boolean (default false): in-between frames come from the renderer blending the scene "
                     "with the one before (`pvr/pvr_blend.c`), not from the game's draw function, so no game code "
                     "runs and guest RAM is neither copied nor restored. `--interp-scene off` selects the draw "
                     "function again (a technical option: no menu entry, and a saved `interp-scene` line is "
                     "ignored)"),
    "part_draw": ('Optional: `{"function": addr, "stamp": "0x..", "world": "0x.."}`, the game\'s per-part 3D draw, '
                  "whose first two arguments are the object and the part (`tools/part_draw.py` wraps it with "
                  "`port_part_begin`/`_end`, `platform.h`). Each vertex the renderer parses is tied to the part "
                  "that wrote it, and with `interp_scene` an in-between frame blends each vertex of a part that "
                  "kept its object, stamp and vertex count from where it was a tick before, in perspective "
                  "(`pvr/pvr_parts.c`); 2D and changed parts are drawn as the latest tick drew them. No game code "
                  "runs. `--interp-parts off` selects screen blending again; `--interp-parts-log on` logs the parts"),
    "present_thread": ("Optional boolean (default false): with `interp_scene`, in-between frames are drawn and shown by a thread of their own on the real clock (`runtime/frame_extra.c`, "
                       "`--present-thread`), so the tick takes no time from them, and a movie's picture is shown again until the next one. Ticks and sound do not change"),
    "sq_queue_only": ("Optional boolean (default false): the translated C of the game models the SH-4 store queues "
                      "itself (`SqQueues`: the bytes stored and the `pref` that sends them to RAM), so a store into "
                      "the window `0xe0000000..0xe3ffffff` is kept by the queue only (`runtime/tx/tx_rt.c`, "
                      "`PORT_SQ_QUEUE_ONLY`) and does not also land in the RAM it mirrors. Needed when a drawer "
                      "stores into one block and sends the queue to another (Power Stone 2's shadow volumes: the "
                      "stores overwrote the blocks already sent)"),
    "sq_fast": ("Optional boolean (default false): the store-queue helpers of the translated C (`SqQueues`) run a "
                "block at a time in the play build (`tools/tx_sqfast.py`, also `build.py --sq-fast`); the judged "
                "files stay as they are and the helpers' meaning does not change (`tests/test_sq_fast.py`). A game "
                "turns it on with a whole-replay proof that its frames and audio are identical"),
    "game_o2": ("Optional boolean (default false): the game's own C (readable, translator, game sources, generated "
                "glue) builds at `/O2`, not the judge's `/O1`; also `build.py --game-o2`, and a build folder set up "
                "the other way is set up again. The judge's own builds do not change. A game turns it on with a "
                "whole-replay proof that its frames and audio are identical"),
    "vram_init": ('Optional: `{"hex byte offset in texture memory": "hex bytes"}`, set in the 8 MB texture memory '
                  "before the game's first read or upload (`pvr/pvr_state.c`, `PORT_VRAM_INIT`). For texels a game "
                  "samples that it never uploads: the console keeps the frame buffers or leftovers there, the port "
                  "keeps them elsewhere. Power Stone 2's gauge bars sample one texel at offset 0 and need it white"),
    "texture_pack": ("Optional: a folder (relative to game.json; it may be built later, e.g. into the git-ignored `artifacts\\` or by `pack_builds`) holding a texture pack the renderer uses by default "
                     "(`pvr/pvr_pack.c`). A release carries it as `packs\\<its folder name>\\` beside `port.exe`, found there first. Without it, and without `--texture-pack DIR` or optional packs in `packs\\`, there is no pack"),
    "pack_builds": ('Optional: `{NAME: {"tool": builder script, "inputs": folder it builds from}}`, both relative to game.json. After a successful compile, `build.py` runs `python TOOL --disc DISC --kit-tools <kit>/port/tools --out <build>/packs/NAME` when the stamp of inputs, tool and disc has changed. '
                    "The result is an optional pack, or the game's default pack when NAME is `texture_pack`'s folder name (found beside `port.exe` first); a failure keeps the old one, never fails the build, and leaves its reason in `packs/NAME.skipped` for the game log (`tools/pack_builds.py`). NAME is a folder name other than `on` and `off`"),
    "aica_arm": ("Optional: `true` runs the game's own sound driver on the kit's AICA ARM7 (`runtime/aica_arm.c`); "
                 "the game's sound functions in `sources` then do the original's hardware access "
                 "(`runtime/aica_arm.h`). Default `false`: no ARM"),
    "aica_arm_fast": ("Optional boolean (default false; needs `aica_arm`): the AICA ARM7 runs the sound driver's "
                      "idle scan loops a whole number of iterations at a time (`runtime/arm7_scan.c`), with the "
                      "same registers, flags, cycles and instruction count as the interpreter, so every register "
                      "write and every sample is the same. Set it only once the game has been proven side by side "
                      "(the ARM write log and the mixed audio identical on its replays). The environment variable "
                      "`DP_ARM_FAST=0` or `1` overrides it for a run"),
    "aica_arm_native": ("Optional (needs `aica_arm`): the path, relative to game.json, of the C file "
                        "`tools/arm7_translate.py` made from the game's sound driver as the replay recorder saw it "
                        "run (env `DP_ARM_PCS`). The AICA ARM7 then runs the driver as those C blocks, with the "
                        "interpreter's exact registers, flags, memory writes, cycle and instruction counts, falling "
                        "back to the interpreter for any code the file does not hold or whose words in sound RAM "
                        "differ. Set it only once the game has been proven side by side. The environment variable "
                        "`DP_ARM_NATIVE=0` or `1` overrides it for a run"),
    "kamui1": ('Optional: `{"list_word": addr, "direct_list": {"at": addr, "bytes": n}}`: the guest address of '
               "Kamui 1's list table word, and, for a game whose buffer model sends a list straight to the TA, the "
               "RAM the game leaves free for that list (without it the list shares the vertex buffer, scaled; a "
               "game that writes more than that overruns into the next list, which the kit logs), for the "
               "`kamui1_*` modules (`sdk/kamui1_scene.c`)"),
    "lockstep_sample_pcs": ("Optional: up to 8 guest addresses where the port's vertical blank comes (the SDK's "
                            "render and vsync-wait entries); `tools/lockstep_emu.lua` samples Flycast there"),
    "lockstep_rtc_pcs": ("Optional: the guest addresses where the game reads the RTC; a lockstep frame that reads "
                         "it drives the port with what it read (up to 8 with the sample PCs)"),
    "lockstep_scene_pcs": ("Optional: the guest addresses where a scene begins (Kamui's `kmBeginScene`), so "
                           "lockstep frames count as the port's do (up to 8 with the other PCs)"),
    "translator_c": ('Optional: `{"dir": the judged translator C folder, "mode": "mix" or "only"}` '
                     "(`tools/tx_port.py`); absent, the port compiles readable C and logging stubs only"),
    "tx_first": ("Optional: guest addresses of functions whose readable C yields to their judged translator C (mix "
                 "mode)"),
    "spin_words": ("Optional, needs `translator_c`: up to 4 guest addresses of 32-bit words the game busy-waits on "
                   "until an interrupt changes them (a vsync counter). A readable global at one is read through "
                   "`port_rd_spin` (`runtime/tx/tx_spin.c`), so a readable wait loop lets the next vertical blank "
                   "come, as translator C's `rd32` does; the per-function judge cannot see this (its vectors never "
                   "change the word)"),
    "not_code": ("Optional: `{addr: evidence}`, catalog entries that are data, not code: no dispatch row, so no "
                 "thunk is written over the data"),
    "coop": ('Optional: the most players of a co-op session (2..4; 0 or absent: none), `PORT_COOP` in '
            "`port_config.h`; the game opens `base/coop_join.c`'s join screen from its own screen and wires "
            'the players in. `--coop off` or the setting `game.coop` 0 turns it off for a run; the value is '
            'kept in the session recording (`port_play_option`), so a replay plays as the recorded run did'),
    "pads": ('Optional: the host controllers read, players 1..8 (1..8; absent: 4), `PORT_PADS` in '
            "`port_config.h`; `--pads N` overrides it. A game's own pad layer keeps players 1..4 and takes "
            'players 5..8 with `port_pad_player` (`base/base.h`); they are recorded and replayed with the '
            'others. At 4 a run reads, records and logs what it did before'),
    "game_menu": ('Optional: `true` when the game adds its own menu-bar entries: it defines `game_menu_extra` '
                 "(`base/console.h`, the same `PortMenuItem`s a console gives), shown after the console's in the "
                 "menu each names. A `game.` setting key is the game's to read (`port_setting_get_int`); a value "
                 'that changes play goes through `port_play_option` so replays keep it'),
    "menu_choices": ('Optional: at most eight of the game\'s own choices, each `{"menu": "Display"|"Audio"|"Input"|"Game", '
                     '"label", "setting": "game.NAME", "choices": [labels], "values": [words], "default": word}` '
                     '(2..16 choices), shown in that menu after the console\'s (`base/menu_data.c`). A choice saves its '
                     'word as `game.NAME` at once; the game reads it with `port_setting_get_int` or `port_setting_get_str` '
                     'at its own time (a scripted run, with no settings file, gets the game\'s default). Not with '
                     '`game_menu`: the base defines `game_menu_extra` from it'),
    "extra_buttons": ('Optional: `{"actions": [labels], "players": 1..8, "menu": "Input"}`, for a game with no key screen '
                      'of its own: Input > Extra buttons, a player, then LB, RB, the stick clicks, Back, Guide and the '
                      'right stick\'s four directions, each taking one of the game\'s actions (numbered from 1 in the '
                      'order given) or none (`base/menu_data.c`). Kept as `game.padex.p<N>.<row>`, as a key screen '
                      'keeps them (`base/input_ex_assign.c`); the game loads them with `port_pad_ex_assign_load` and '
                      'reads them with `port_pad_ex_assign_codes`. Not with `game_menu`'),
    "menu_commands": ("Optional: the game's own commands, each `{\"menu\": \"Display\"|\"Audio\"|\"Input\"|\"Game\", \"label\", "
                      '"tag": 1..65535, "list": "runtime" or [labels]}` (tags unique, at most 435 entries in all). A pick '
                      'goes to the game as (tag, index) at its next tick (`native_game.h` `pg_native_menu_take`) and is '
                      'recorded. A list is a submenu: `>` in a label nests, more than 16 are split into "1-16", "17-32", '
                      '...; `"runtime"` is the list the game gives with `pg_native_menu_list`. `--menu-pick` takes the '
                      'whole path (`base/menu_data.c`, `base/host_menu_tagged.cpp`). Not with `game_menu`: such a game '
                      'sets `PortMenuItem.tag` in C'),
    "keys": ("Optional: the game's whole keyboard and mouse layout (player 1), an object of key or mouse name to "
           'targets joined by `+`, e.g. `{"W": "LSUP", "MOUSELEFT": "RT"}`. Keys are SDL\'s names plus `UP DOWN LEFT '
           'RIGHT RETURN KP_ENTER SPACE ESCAPE BACKSPACE TAB LSHIFT RSHIFT LCTRL RCTRL LALT`; mouse names are '
           '`MOUSELEFT MOUSERIGHT MOUSEMIDDLE WHEELUP WHEELDOWN`. Targets are `A B X Y START UP DOWN LEFT RIGHT LB '
           'RB L3 R3 BACK GUIDE RSUP RSDOWN RSLEFT RSRIGHT LT RT LSUP LSDOWN LSLEFT LSRIGHT`, or `none`. Without it '
           'the kit\'s layout applies (README.md "Controls"). The player\'s changes (Input > Keyboard and mouse) come '
           'after it'),
    "key_ramp": ('Optional: 0 to 60 (default 0), the reads a held stick key takes to reach full deflection; 0 is at once'),
    "audio_catchup": ("Optional boolean (default false): catch-up mixing (`runtime/aica.c`). A voice's position the game reads "
                    'mid-blank first mixes the blank up to that point, so what the game sees as played has been mixed, as on '
                    'the chip; a game that refills a sound ring up to the read position then never overwrites sound before it'
                    ' plays. The positions the game reads, so its logic, do not change; only the sound does. `--audio-catchup'
                    ' off` selects the whole-blank mix again. No effect with `aica_arm`'),
    "border_edges": ('Optional boolean (default false): in a wider (taller) frame, the bars beside (above and below) the 4:3 '
                   "screen take the screen's own edge pixels, row by row (column by column), when the scene drawn last drew "
                   'nothing past the screen or a movie is over the picture (`base/present_edges.c`): a movie or a menu whose'
                   ' edge is white has white bars, not the border colour. The screen itself and a 4:3 frame are never '
                   'changed; game logic is not touched. `--border-edges off` keeps the border colour'),
    "loose_dir": "Optional: a folder name, looked for beside the disc image (`--loose-dir DIR` overrides it with any folder). Each file in it, and in its subfolders named as the disc's directories, appears in that directory of the disc as the game reads it (`runtime/gdi.c`), in place of a disc file of the same name: levels and text a player adds from their own discs. No folder, or an empty one: the disc as it is",
    "session_ahead": ('Optional: `"keep"` (default) or `"old"`, how a replay reads the notes of sound mixed ahead of '
                      'a real clock (`base/session.c`) where the game reads a save or a note (a play option): "keep" '
                      'keeps those for this blank or later, "old" drops them all, as kits before 11-07 did '
                      '(`PORT_SESSION_AHEAD`; the environment variable `DP_SESSION_AHEAD` still overrides it for a '
                      'run). For a game whose recordings must replay exactly as on such a kit'),
    "extra_ram": ('Optional boolean (default false): a second 16 MB the console does not have, mapped at 0x88000000'
                 ' with its P0 and P2 forms 0x08000000 and 0xa8000000 (area 2, unused on a Dreamcast), zeroed at '
                 'start. For data a port moves out of a full main RAM, such as tables widened for more players; a '
                 "pointer into it is a host pointer like the main RAM's. `guest_fold` and `guest_is_ram` know it. "
                 'The in-between frame copies, RAM diffs, captures and lockstep proofs see only the main 16 MB. '
                 'Off: nothing is mapped there'),
    "menu_checks": ('Optional: at most four of the game\'s own on/off settings, each `{"label", "setting": "game.NAME", '
                   '"needs": FILE, "default": true}`, shown at the end of the Game menu (`runtime/console_dc.c`). The '
                   'game reads one with `port_setting_get_int` (the menu saves `on`/`off`, read as 1/0; off when never '
                   'chosen, or on in a play run with `"default": true`; a scripted run, which has no settings file, always gets 0), so it applies from the'
                   " game's next read; a texture pack line's `if=game.NAME` word follows it at once. `needs` (optional) is a file in the data folder: without it the entry is "
                   'greyed and the log says which file is missing'),
    "move_tables": ('Optional list of `{"name": text, "from": addr, "bytes": count or hex, "to": addr, "ends": '
                   '[function addr or FUNC@INSN, ...]}`: guest data tables moved at build time '
                   '(`tools/move_tables.py`), for a table that must grow where another object follows it. Every '
                   'address literal inside `[from, from + bytes)` in the translated C the build compiles becomes '
                   '`to` plus its offset, in the same P0/P1/P2 form; the end value `from + bytes` moves only inside '
                   "the `ends` functions (a loop that stops at the table's end); an entry `FUNC@INSN` moves it only "
                   "after that instruction's comment in the translated C, for a function that also uses the same "
                   "literal as the next object's base, and the build stops if a listed end is not found. Named "
                   'globals inside a table move in game.h, so readable C follows. The judged files are not edited: '
                   'the build compiles rewritten copies (`tx_move/`). Tables may not overlap where they are or where'
                   ' they go. Usually with `extra_ram`. A moved table that still holds what it did plays the same, '
                   'proven by replay. A table of equal entries may add `"stride"`, `"max"` and `"counts"` (FUNC@INSN sites '
                   "where the original entry count is compiled in): there the count, and the table's listed ends, follow "
                   '`g_move_count[k]` (port.h; 0 = the original count)'),
    "tx_edits": ('Optional name of a .json file beside game.json: `{"prelude": [C lines], "edits": [{"func": addr, "insn": addr, '
                 '"find": text, "replace": text, "why": text}]}`, text edits of the game to build-folder copies of its translated C '
                 '(`tools/tx_edits.py`), each at one instruction and found exactly once or the build stops. Applied before `move_tables`'),
    "pack_builds": ('Optional: `{NAME: {"tool": builder script, "inputs": folder it builds from}}`, both relative to '
                   'game.json. After a successful compile, `build.py` runs `python TOOL --disc DISC --kit-tools '
                   '<kit>/port/tools --out <build>/packs/NAME` when the stamp of inputs, tool and disc has changed. '
                   'The result is an optional pack; a failure keeps the old one and never fails the build '
                   '(`tools/pack_builds.py`). NAME is a folder name other than `on` and `off`'),
    "texture_pack_choice": ("Optional: the folder name of an optional pack (`packs\\NAME\\` beside `port.exe`, e.g. a "
                            "`pack_builds` NAME) that a play run shows over the game's default until the player "
                            "chooses (`pvr/pvr_pack.c`): clearer or upscaled content is on by default. A saved "
                            "`texture-pack` setting or any `--texture-pack` replaces it, a check run (fixed clock) "
                            "ignores it as it does the default pack, and a missing folder leaves the game's default"),
    "sprite_fields": ('Optional: `[{"tsp": "8 hex digits", "size": [w, h], "period": [x, y]}]`, a tiled backdrop of '
                     'sprites that wraps just past the 640x480 screen (`pvr/ta_parse.c`, `PORT_SPRITE_FIELDS`). A '
                     'sprite with that TSP word and size is drawn again every period, as far as a wider or taller '
                     "frame needs, so nothing pops at the wrap. No copies at 4:3, and the game's lists and memory are "
                     'untouched. `port/tools/motion_check.py` finds such a pop'),
}

# game.json "saturn", the Saturn kit's (tools/gen_saturn.py and the gen_saturn_*.py it calls): key -> meaning
SATURN_KEYS = {
    "name": ("The game's name: the log, setup's messages and online play's check that both copies are the same game "
             "(`SAT_GAME_NAME`; default `Saturn game`)"),
    "image": ("The code image the port loads (a composite of the disc's code files until the disc loader replaces "
              "it); `--image` overrides"),
    "image_base": "Where it loads in work RAM",
    "image_files": ("Optional: the disc files of the code image, `[[\"/0\", \"0x06010000\"], ...]` (disc path, load address), as onboarding "
                    "composed `image`. With `image_sha256`, a run without `--image` makes the image from `--disc` (`sat_image.c`): a release has no "
                    "image.bin, and setup and verify refuse a disc that makes another (exit code 5). AutoOnboard writes both. Absent: the `image` file"),
    "image_sha256": "The SHA-256 of the image `image_files` makes (64 hex digits); required with it",
    "fallback": "Optional: `\"on\"` or `\"off\"`: `--fallback`'s default in this build (`sat_interp.c`), as the boot proved it: a launcher passes no flags",
    "product": ("Optional: the disc header's product number; setup and verify refuse a disc with another (exit code "
                "5, `console_sat.c`). Without it, any Saturn disc is accepted"),
    "entry": "The guest function the port calls first (on the game's thread)",
    "entry_sp": "Its initial stack",
    "entry_gbr": "Optional: its initial GBR (default: `entry_sp`)",
    "boot_dir": ("Optional: the folder of the program the image starts at when it replaces the disc's first-read "
                 "loader (`/BOMSS`); the CD block's file system and the boot ROM's words at 06000ccc..06000cce hand "
                 "that folder to the game's file system, as the loader left them (`sat_cd.c`)"),
    "slave_sp": ("Optional: the slave SH-2's stack top (the game loads it from its own memory, which the translated "
                 "slave program does not run)"),
    "frame_hz": "50 (PAL) or 60 (NTSC); the vertical blank comes on the real clock",
    "exit_regs": ("The registers a function hands on at return besides r0 (the SH-2 asm of the game's library reads "
                  "them after a call): r4..r7 and r1..r3; a callee that never writes one hands the caller's back"),
    "vblank_flags": ("Optional: offsets from GBR of the bytes only the vertical-blank handler sets: a loop that "
                     "reads one waits for the vertical blank (`sat_vblank_wait`) instead of spinning"),
    "main_loops": ("Optional: the game's main loop functions: each call is a turn with nothing to do yet, and "
                   "enough of them in one vertical blank wait for the next (`sat_idle_turn`, `sat_sys.c`)"),
    "controller": ("Optional: `\"saturn\"`: the Saturn pad on the host's controller by default (A B X Y as they are, RB C, LB Z, "
                   "LT L, RT R, Start, the d-pad; the stick clicks, Back, Guide and the right stick unassigned), by the "
                   "host button each action reads (`sat_input.c`). The keyboard is game.json `keys`. The pad is still "
                   "named dreamcast in the log: the base has no per-console name (a proposal is in the handover). Absent: "
                   "the layout games made before it keep (C on RT, Z on LT, L on LB, R on RB)"),
    "confirm": ("Optional: the game's own button that moves on in its menus (`\"A\"` `\"B\"` `\"C\"` `\"X\"` `\"Y\"` `\"Z\"` "
                "`\"L\"` `\"R\"`), found by AutoOnboard's menu probe (`onboard_sat.py`): the kit puts it on the host's A "
                "(keyboard Z), and the Saturn button that had host A takes the host button confirm had, so every button keeps "
                "one (`sat_input.c`). Absent: the layout above unchanged"),
    "cancel": ("Optional: the game's own button that backs out of a menu (same values, not confirm's), found by the same "
               "probe: the kit puts it on the host's B (keyboard X) the same way. Absent: the layout above unchanged"),
    "smpc_intback": ("Optional: `\"hardware\"`: the SMPC's INTBACK peripheral report as the hardware gives it (SR bit 7 PDE, 6 "
                     "PDL, 5 NPE, 4 RESB never; the report per port, 32 bytes a packet; a CONTINUE is IREG0 bit 7 "
                     "inverted from the last one, a BREAK wins), which SBL games need to take the pad (`sat_smpc.c`). "
                     "Absent or `false`: the older report (SR 0x90, one packet, only a CONTINUE with bit 7 set), which a "
                     "game made before it, with its recordings, keeps"),
    "sound_cpu": ("`true`: SMPC SNDON starts a 68000 on the driver the game loaded into sound RAM, and the SCSP's "
                  "timers, interrupts and 32 voices run each frame (`sat_m68k*.c`, `sat_scsp*.c`); the mixed sound "
                  "goes to the wave device and, with `--wav FILE`, to a WAV file. `sound_clear` and `sound_stream` "
                  "are then unused. Emulated hardware: for bring-up and as the answer key for a native sound "
                  "driver, not the play path to keep (RUNTIME_TARGET.md)"),
    "sound_clear": "Sound-RAM mailbox bytes the missing 68K driver would clear",
    "sound_stream": ("The driver's stream-progress report: `start_addr`, `start_cmd`, `status_addr`, "
                     "`status_count`, `status_step`, `block`, `rate`"),
    "ground_shadows": ('The game\'s floor-shadow quads, `{"pmod": "0x0128", "srca": "0xa4ec"}`: VDP1 distorted '
                       "sprites with that draw mode and texture. Play runs draw them before the rest of the list, "
                       "so nothing standing on them is darkened (Display > Shadows, `--ground-shadows "
                       "under|original`; `sat_vdp1_list.c`). Check runs keep the game's order"),
    "widescreen": ("Optional: the picture composed wider than the screen in play runs (`sat_wide.c`, "
                   'RUNTIME_TARGET.md "Any aspect ratio"): `true` every picture, `"rbg0"` only pictures that show '
                   "RBG0 (a 3D floor)"),
    "wide_cull": ('Optional: `{"sgl": SGL\'s work area, "at": [the functions that read its window]}`: the game\'s '
                  "culling widened with the picture as those are entered (`sat_wide.c`). Robo Pit leaves it out: "
                  "its replay shows it changes the game"),
    "movie": ('Optional: the native movie player (`sat_movie.c`, `--movie-native`): `{"call", "name_arg", '
              '"skip_after_arg", "skipped", "screen", "open_blanks", "close_blanks", "reset_call"}` '
              "(`tools/gen_saturn.py` `movie_h`), and `skip_logos`, the opening logos `--skip-logos` skips "
              "(`sat_logos.c`)"),
    "twin_stick": ('Twin-stick in the game\'s fights, `{"fight": "0x0605acd0", "turn": ["LEFT", "RIGHT"], "strafe": '
                   '["L", "R"]}`: while the game runs `fight` (a function it runs only in a fight), the right stick '
                   "presses the `turn` buttons and the left stick held sideways the `strafe` buttons, by the pad's "
                   "action names (base `pad_twin.c`)"),
    "auto_load": ('The game\'s own load path, driven at boot when its save file is there, `{"file": "ROBO_PIT___", '
                  '"steps": [...]}`: each step waits for the game to enter (`enter`) or leave (`leave`) a function, '
                  'pressing `press` until then (one button, or several joined by `+` pressed together: `"UP+A"`), '
                  "and may put a 16-bit `poke: [address, value]` before it runs (base `autoload.c`, `sat_bup.c`)"),
    "netplay": ('Optional: online play for two (`sat_net.c`): `{"pad", "delay", "hash_every", "port", '
                '"test_byte"}`, and `menu`, `drop_ms`, `leave` and `show` for online play started from the game\'s '
                "own menus (`tools/gen_saturn.py` `netplay_h`, `gen_saturn_menus.py` `net_menu_h`); without it the "
                "build has none"),
    "team_fight": ("Optional: a four-robot fight played two against two (the game's `sat_team.c`, `--team-fight`): the game's "
                   "addresses and sizes (`tools/gen_saturn.py` `team_fight_h`)"),
    "level_edit": ('Optional: `{"schema": the game\'s level schema, relative to game.json, "level": its level '
                   "(default: the first)}`: the edited arena and the level editor's Saturn side (`sat_level.c`, "
                   "`tools/gen_saturn_level.py`)"),
    "menus": ("Optional: the kit's entries in the game's own menus (`sat_menus.c` and the game's `sat_lobby.c`, `sat_ladder.c`): "
              "the hub, its draw, box, cursor bar, text and sound, with `fight_choice` and `online` "
              "(`tools/gen_saturn_menus.py` `menus_h`)"),
    "menu_panel": ('Optional: `{"pmod", "color"}`, the menus\' half-transparent panel drawn by the presenter '
                   "(`sat_panel.c`, `--menu-panel`)"),
    "part_choice": ('Optional: a model part the player picks (`sat_part.c`, `--part-choice`): `{"title", "object", '
                    '"pdata", "points", "polygons", "drop_points", "max_points", "original", "choices": [[value, '
                    "label, edits], ...]}` (`tools/gen_saturn_heads.py` `part_choice_h`)"),
    "heads": ("Optional, needs `part_choice`: new head types after the game's own, from part_choice's versions "
              "(the game's `sat_heads.c`, `--new-heads`; `tools/gen_saturn_heads.py` `heads_h`)"),
}

from contract_sdk import SDK_MODULES  # noqa: E402 (sdk/NAME.c: what each replaces, what it needs)

def exit_codes(header=EXIT_CODES_H):
    """[(code, name, meaning)] of base/exit_codes.h: each `#define PORT_EXIT_NAME N` and the comment just above it."""
    text = header.read_text(encoding="utf-8")
    found = re.findall(r"/\*((?:(?!\*/).)*)\*/\s*#define (PORT_EXIT_\w+) (\d+)\b", text, re.S)
    out = [(int(code), name, " ".join(re.sub(r"(?m)^\s*\*", " ", text).split())) for text, name, code in found]
    if not out or len({c for c, _, _ in out}) != len(out):
        raise SystemExit(f"{header}: no exit codes, or one code under two names")
    return out


def tables():
    """{name: the table's lines}, what CONTRACT.md holds between each pair of markers."""
    import game_config  # noqa: E402  (here: it imports this module)
    have = set(game_config.sdk_modules())
    if have != set(SDK_MODULES):
        raise SystemExit(f"contract_doc.py SDK_MODULES: no row for {sorted(have - set(SDK_MODULES))}, a row for no "
                         f"module {sorted(set(SDK_MODULES) - have)} (port/sdk/*.c)")

    def cell(text):
        return text.replace("|", "\\|")  # a literal | in a table cell
    keys = ["| Key | Meaning |", "|------|------|"]
    return {
        "game-keys": keys + [f"| `{k}` | {cell(m)} |" for k, m in GAME_KEYS.items()],
        "saturn-keys": keys + [f"| `{k}` | {cell(m)} |" for k, m in SATURN_KEYS.items()],
        "sdk-modules": ["| SDK module | Replaces | Needs from the game's headers |", "|------|------|------|"]
        + [f"| `{n}` | {cell(r)} | {cell(need)} |" for n, (r, need) in SDK_MODULES.items()],
        "exit-codes": ["| Exit code | Meaning |", "|------|------|"]
        + [f"| {c} | {cell(m)} |" for c, _, m in exit_codes()],
    }


def render(doc):
    """doc (newlines as \\n) with every generated table written again; a missing pair of markers stops."""
    for name, lines in tables().items():
        pair = re.compile(rf"(<!-- generated: {name} [^\n]*-->\n)(.*?)(<!-- end generated: {name} -->)", re.S)
        if not pair.search(doc):
            raise SystemExit(f"{DOC}: no <!-- generated: {name} ... --> and <!-- end generated: {name} --> pair")
        doc = pair.sub(lambda m: m.group(1) + "\n".join(lines) + "\n" + m.group(3), doc, count=1)
    return doc


def main(argv=None):
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    raw = DOC.read_bytes().decode("utf-8")
    text = raw.replace("\r\n", "\n")
    new = render(text)
    if new == text:
        print(f"{DOC}: the generated tables are current")
        return 0
    if check:
        print(f"{DOC}: the generated tables are out of date; run {REGENERATE}")
        return 1
    DOC.write_text(new, encoding="utf-8", newline="\r\n" if "\r\n" in raw else "\n")
    print(f"{DOC}: the generated tables written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
