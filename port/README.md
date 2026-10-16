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
| `runtime/` | Always compiled, no game names: start-up and watchdog (`main.c`), guest RAM and its P0/P2 mirrors (`guest_mem.c`), the GD-ROM image reader (`gdi.c`: `.gdi` or Redump `.cue`), calls through guest addresses (`dispatch.c`, a vectored exception handler over the generated table), logging stubs and the must-work stop (`port_stub.c`), the SH-4 instructions readable C uses (`sh4_rt.c`), the float math the 32-bit CRT only has inline (`port_libm.c`), host services and the 60 Hz vertical blank (`host.c`), a first-fit heap (`heap.c`), the log |
| `sdk/` | SDK replacements a game opts into (game.json `sdk`). Each compiles against the game's generated `game.h`, so the names it defines must be the game's registry names; the header comment of each module lists what it needs |
| `tools/gen_port.py` | Writes `<game>/build/gen`: `game.h` (globals as lvalues on their guest addresses), logging stubs, the dispatch table, and `port_config.h`, `port_entry.c`, `port_files.cmake` from game.json |
| `tools/build.py` | gen_port, CMake + Ninja under the 32-bit MSVC environment, build, and with `--run` the run and a symbolized log. Every step is bounded; a timeout kills the process tree |
| `tools/sdk_ref.py` | Ghidra exports of named SDK/engine functions into `<game>/build/ref` for reading while writing a replacement (never copied) |
| `tools/symbolize.py`, `tools/game_config.py` | `port.exe+OFFSET` in a log named from the linker map; game.json loading and the generated per-game files |

| SDK module | Replaces | Needs from the game's headers |
|------|------|------|
| `sy_heap` | `syMallocInit`, `syMalloc`, `syFree`, `syMallocFinish` | only those names |
| `sy_sys` | Shinobi system services: cable (VGA), config, RTC (host time), TMU timer | `SYE_CBL_VGA`, `SYS_RTC_DATE`; game.json `console` |
| `gdfs` | gdFs over the disc image, synchronous; CD-DA status only | `GDFS`, `GDFS_DIRREC`, `GDD_DRVSTAT_*`, `GDD_SEEK_*`, `SdkProc` |
| `am_heap` | `amHeapAlloc`, `amHeapFree` in a host-backed sound RAM | `SdkProc` |
| `shc_rt` | SHC compiler helpers: block copy, bit-field reads, unsigned mod, `div` | `ShcDivResult`, `g_errno`, `SHC_ERRNO_DIV0` |

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
| `console` | `{"country", "language", "broadcast"}`: the console `sy_sys` reports (numbers, as the game compares them) |

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

## Rules

- No game code, game strings or guest code addresses in the kit; game-range code goes in the game's `sources`.
- Platform code is new source. SDK documentation, headers and Ghidra exports are read, never copied.
- Never hand-edit matched or readable C: a change the port needs goes through names, design or the pipeline.
- 32-bit only for now (a guest pointer is a host pointer); zero warnings at `/W4 /WX`; at most 400 lines a file.
