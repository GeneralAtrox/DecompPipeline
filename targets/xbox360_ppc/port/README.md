# Xbox 360 port kit

Design and the rules it follows: `../DESIGN_PORT.md`, `docs/RUNTIME_TARGET.md` ("New console kits start with no hardware model").
The kit has no hardware model and writes nothing without a caller: every import, library function and host call exists because a
run stopped on it.

## The loop

```
python tools/gen_xbox.py bringup --ws WORKSPACE --out OUT [--rounds N] [--seconds S]
```

One round: run `xbox_run` (the game's translated functions over one 4 GB reserve of big-endian guest memory) -> it stops ->
`gen_xbox.py` reads `LOG.stop` -> translates the missing function and its direct callees -> rebuilds -> runs again.

| Stop | Meaning | Next step |
|---|---|---|
| `missing_function ADDR` | a call target that is not translated yet | the loop translates it (it is counted: judged or not yet judged) |
| `import LIB<<16 \| ORD` | an import with no host answer | write the case in `runtime/xb_imports.c`, with the arguments the log showed and the callers as its comment |
| `library_entry ADDR r3=.. r8=..` | a call into the cut Direct3D library (`boundary.json`) that `runtime/xb_d3d.c` has no case for | read the call site and the library body, write the case with its evidence; the entry is the D3D9 call whose arguments the caller passes |
| `fault ADDR` | a guest access outside the committed regions (`0x7FC8xxxx` is the GPU window, never mapped) | find why: a missing import answer, or a library that must be cut |
| `host_stack_overflow` | the translated code recursed past the host stack | the log's call stack names the loop; the cause has been a wrong import answer |
| `out_of_memory`, `timeout` | reserve used up, or `--seconds` passed | exit codes 3 and 4 |

The log (`OUT/run.log`) ends with `first calls`, `last calls`, `call stack` and `touched N of M translated functions`;
`run.log.touched` lists every translated function the run called, for the judged-or-not count against
`WORKSPACE/state/translator_verdicts.json`.

## Parts

- `runtime/xb_mem.c` guest memory, stack, PCR, time base, host objects keyed by guest address; `xb_call.c` `tc_call` and the
  stop trace; `xb_imports.c` the host answers; `xb_main.c` the entry and the watchdog. `console_x360.c` the console's side of the port
  base (`port/base/console.h`). The window, device, log, pads, pacing, menu, settings and play stats are the base's, not copied.
- `tools/gen_xbox.py` the demand loop; `tools/survey.py` the survey (`../DESIGN_PORT.md` section 1).
- `tools/boundary.py` finds the statically linked Direct3D library in an image (writes `boundary.json`: ranges that are never
  translated); `runtime/xb_d3d.c` is the host side of its entry points, one case per stop. `tools/library_share.py` compares a
  library span between two images (Mega Man 10 against Too Human: another XDK build, so the entry table is per game).
- `tools/export_names.py` writes `data/export_names.json` (ordinal to name, from the Xenia tables; `data/NOTICE-xenia.txt`).
- Port mode (`translator`'s `Facts.port`, `TC_PORT` in `tc_rt.h`): the translated code uses a real stack pointer, `mftb` reads
  the host clock, and `rd`/`wr` are inline accessors. The judge's form is unchanged when the flag is off. The kit takes each
  function's return register from the function's own body (`returns.analyse(..., meta=False)`): a harness meta that says `void`
  only means the judge compared no result (822c2cc8 is void there and returns 1 to a caller that reads it).

## Not here

`tests/test_x360_port_forbidden.py` fails when the kit's source names the Xenos register file, a PM4 parser, the eDRAM, a shader
microcode interpreter, a Xenon hardware-thread scheduler, the XMA hardware or kernel object emulation. If the game forces one,
stop and report the evidence; do not add it as bring-up.

The XMA decoder isn't here either. This repository is under the MIT licence, and the decoder is derived from LGPL code, so it stays a separate
work: `xma_decoder.dll` beside the exe, loaded by `runtime/xb_xma_dll.c`, which knows only its four functions. Give `gen_xbox.py build` its path
with `--xma-dll`. Without it the game runs, the log says so once, and XMA sounds are silent. A package that includes the DLL must also include
its licence, notices and source.

## On the port base

The kit is x64, because of the 4 GB reserve, and is the base's one x64 user; `gen_xbox.py` builds `port/base/` into `xbox_run` with vcpkg's
x64 SDL3 beside the output. Each Swap presents into the base's frame target and ends at `xb_tick_end`. Not there yet: a launcher
path (`console_check_image` refuses, `base/install.c` looks for a disc image), real sound through the base's device, and a world that
widens (Fit window and the other shapes show the game's 16:9 picture inside the frame, never stretched).

## Settings of a test run

| Variable | What it does |
|---|---|
| `XB_GAME_DIR` | the extracted package, `game:` (`tools/stfs.py` extracts it; the XEX key stays out of the repo) |
| `XB_SAVE_DIR` | where saved content goes, default `save` beside the executable. A new folder tests the path a first run takes |
| `XB_INPUT` | `gen_xbox.py` passes it as the base's `--input` script (`port/base/input.c`), by frame. Without it the pad is idle; a test run reads no real input |
| `XB_DUMP`, `XB_DUMP_EVERY`, `XB_DUMP_STAGES` | write the shown frame, and the textures each draw uses, as BMP files |
| `XB_LOG_FROM` | the frame the per-call log starts at |
| `XB_WATCH` | hex guest addresses of bytes, logged when they change |
| `XB_TRACE` | every call, for a hang or a crash in a function |

`port/boundary.json` is the library that is cut, `port/kept.json` the pure arithmetic inside it that is translated anyway.
`OUT/reached.tsv` is the list of functions the run has reached with their verdicts. The runtime's `.h` files beside `xb_d3d.c`,
`xb_gpu.c` and `xb_imports.c` are parts of those files, included once, for the 400-line cap.
