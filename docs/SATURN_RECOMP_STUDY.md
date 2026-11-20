# Saturn recompilers compared with the DP Saturn kit

Study of 2026-11-16. Read: headers, docs and the files named below, not every line. Clones are in
`.work/recomp-study/` (git-ignored), read as data only; nothing was built or run.

| Project | What it is | Licence |
|---|---|---|
| ours | `port/saturn/*.c` kit, `targets/saturn_sh2/` (sh2translate, TcOracle judge), `tools/onboard*.py` | the user's own |
| callumjenkins/saturn-recomp | shared base for Saturn recomps (Python recompiler + C++20 runtime); forked from vs-sr-dev/saturnkit. Games: Hydlide, Deep Fear, X JAPAN, Saturn Bomberman (own repo, callumjenkins/pc-saturnbomberman) | MIT (the LICENSE text names Samuele Voltan; Callum's repos are MIT too). Reusable with the copyright notice and credit. One optional file (VDP1 by Mednafen's rules) is GPL: leave it out |
| tmcleroy/satrecomp | toolkit with its own machine and Ymir as the reference build | GPL-3.0. **Ideas only: no code is quoted or copied here or later** |

Shape of each. Ours translates each function to C (sh2translate), judged function by function against Ghidra's
p-code emulator, and has no fallback: a call with no translation is a logging stub that returns 0. callumjenkins
recompiles each program image to C++ by a recursive-descent discovery, with the instruction semantics in a shared
header; a miss at run time stops the run and a loop adds a seed. satrecomp translates basic blocks recorded from
runs, and runs any block it has no translation for in an interpreter, counting it.

## Block by block

| Block | Ours | callumjenkins | satrecomp | Take |
|---|---|---|---|---|
| Master SH-2 | translated C per function, state in `SatCpu`/TcArgs; judged per function. Setjmp/longjmp kernel on host fibers (`sat_task.c`) | generated C++ `void f(SH2Context&)`, registers in one struct, call chain on the host stack; programs swapped at one address are separate modules identified by crc | blocks keyed by address and exact bytes, so overwritten code never runs stale; native, interpreter or lifted routine per block | ours is judged better per function; theirs is whole-game complete |
| Slave SH-2 | Windows fiber, started on MINIT, yields at polls and every 4096 calls; the decompiler judges master only | second context as a coroutine on its own thread, deterministic slice | second context, both CPUs interleaved in about 32-cycle slices (needed by a game with a master/slave race) | same idea; theirs has a stated slice rule |
| SCU: DMA, DSP | interrupts, 3 DMA levels, timers; **DSP port is a stub** (idle) | interrupts, timers, 3 DMA levels; **DSP disassembler and interpreter** (`scudsp.cpp`, 273 lines; Deep Fear) | interrupt controller, 2 timers, DMA (not timed) | borrow the DSP interpreter (MIT) |
| SMPC | COMREG, INTBACK (status, pads), SNDON/OFF | the same, plus the pad read directly through PDR1/DDR1, clock (CKCHG), scripted and recorded pads | the same, saves in console format | borrow direct port read and CKCHG handling |
| VDP1 | software, quad walker, gouraud, mesh, shadow | software, every command and colour calc, chip's draw-end timing; Mednafen-rule file is GPL (skip) | CPU quads for software, or GPU quads at window resolution | ours enough for now; theirs a checklist of edge cases |
| VDP2 | NBG0-3, RBG0 (A only), windows, line scroll, wide; no RBG1/B, vertical cell scroll, sprite window, mosaic | NBG0-3 cell and bitmap, zoom, rotation A, sprite types, colour calc, hi-res, interlace, windows (still partial) | layers in software or GPU, widescreen by game opt-in | our gaps are the list to close; theirs shows which games hit them |
| CD block + ISO | commands run at once; 200 buffers, 24 selectors, filters; ISO 9660 and cue in `sat_disc.c`; fast loads | commands at once, `--accurate-drive` times seeks as Mednafen does; ISO lookups for boot; NetLink modem UART modelled | commands, one partition, no filters (games' file reads replaced by the port) | ours is the fuller block model; take their drive timing as a switch |
| 68000 + SCSP | own 68000 core, 32 voices, mixer | Musashi 68000, full SCSP (slots, LFO, FM, DSP, DMA, CD-DA input) in virtual time | own 68000 and SCSP, each checked against Ymir's | theirs is more complete (DSP); satrecomp's checking idea is the best |
| BIOS services | native functions at made-up ROM addresses, pointers at 0x06000300+; added as the game is seen to use them | HLE: every vector and service slot has its own ROM address and is dispatched by slot; SBL `SYS_*` pointers; BUP in a file | native BIOS reproducing tables, stack frames and effects "as seen on a real BIOS" | take slot-per-vector dispatch, so a pointer saved and restored still works |
| Boot with no BIOS | the tool starts at the main program (0KRNL) with hand-over values inferred (06000ccc..cce) | boot does what the BIOS does: IP.BIN to 0x06002000, the 1st-read file to its address, header to 0x06000C00, vectors, VBR, stack. It runs the real chain, programs load each other | no BIOS image needed; same approach | run from the IP's first-read file, not from a mid-chain program |
| Function discovery | census, `overlay.py --pointers`, TcPrune; seeded from pointers; unknown calls are stubs | recursive descent, many jump-table forms (GCC switch, SHC absolute, byte tables, record tables), seeds file (`seeds.json`, learned) | record blocks that actually ran, then follow fixed branches and literal-pool calls from them; plausibility check keeps data out | descent + run-time recording together |
| Fallback | none (stub returns 0) | none in the runtime: stop with "call to X, not an entry" and a learn loop adds the seed (one run per miss) | interpreter runs the block; counted; fed back (see below) | the fallback is what ours lacks |
| Self-check | TcOracle: per function, same vectors, p-code vs C, memory, registers; whole game: Mednafen frame (owed, needs build box B's BIOS) | `sh2emu.py` Python interpreter records vectors, the C++ replays them; Beetle Saturn (Mednafen) played headless by `reference.py`, frames aligned on the game's own frame count, audio correlated; `executed.py` checks every instruction a reference run executed has a translation | Ymir as the reference build: translated against its interpreter after every frame (hash of RAM, registers, picture); interpreter against translated; chips run side by side (`SATRECOMP_SOUND=check`, `M68K=check`) | theirs add whole-game checks; ours is stronger per function |

## Saturn Bomberman: our eight rounds against their designs

callumjenkins' column is for the shared base plus pc-saturnbomberman, which is the same game and has a working port.

| Round | Stop | callumjenkins | satrecomp |
|---|---|---|---|
| 1 | NetLink modem window taken for the CD block | Yes: the modem UART at 0x25895001 is its own device with a Hayes command set | Partly: no NetLink noted; the CD block is a separate map entry so the clash would show at once |
| 2 | unchecked table jump into the function's own body | Partly: discovery knows many table forms, but a function-shaped output can still miss one; a miss is a seed | Yes: the jump lands in a block; no block means the interpreter runs it |
| 3 | boot hand-over 06000ccc (inferred) | Yes: runs the real boot chain, so the previous program leaves the value | Yes: same approach as the BIOS |
| 4 | table-reached code not found | Partly: seeds.json (367 lines for this game) and the learn loop, but each miss costs a run | Yes: unseen blocks interpreted, then recorded |
| 5 | sound command never acknowledged (`sound_cpu`) | Yes: the real driver runs on Musashi and the SCSP all the time | Yes: 68000 runs its own driver |
| 6 | call to 26006020 (task yield) | Yes: `tasks.cpp`, `--tasks SETJMP:LONGJMP`, host fibers, the same design as our `sat_task.c` (theirs names the pair in game.toml; ours finds them by code) | Partly: no setjmp support noted; real guest stack and registers make a longjmp a register load, but nothing proves it |
| 7 | byte-offset copy sled | Partly: the byte-table form is recognised; sleds are not stated | Yes: runs by block, any address |
| 8 | stubs from record tables and `bound()` | Partly: tables of records were added for Deep Fear; the rest is seeds | Yes: blocks, not static bounds |

Their designs would have avoided rounds 3, 5 and 6 outright (callumjenkins) and most of 2, 4, 7, 8 (satrecomp). Rounds 1 to 4, 7 and 8 were
all coverage misses of static discovery, which a runtime fallback turns from a stop into a counter.

## Step 5 answers

- **Does callumjenkins/saturn-recomp contain an SH-2 interpreter?** Only offline: `src/saturnrecomp/sh2emu.py`, 556 lines of
  Python, for isolated functions and as the recompiler's oracle. The C++ runtime has no interpreter. It shares no state with recompiled
  code; `runtime/include/saturn/sh2.h` says the semantics are "sh2emu.py's, instruction for instruction", held together by
  `recomp/selftest.py` and `runtime/src/app/selftest.cpp` (vectors recorded by the interpreter, replayed on the C++).
  Recompiled code uses `SH2Context` (r0-r15, T/S/Q/M, imask, GBR, VBR, MACH/L, PR, pc, budget, cpu). Calls: `sh2_call` looks the address
  up in the active modules and calls the C++ function on the host stack; `rts` leaves the return target in `c.pc` and the caller checks it;
  a mismatch goes to `sh2_bad_return`, which routes to the task layer if a task switch is pending, else stops. An unknown target goes to
  `sh2_call_unknown`: BIOS slot, else activate the module that contains it, else the fatal "not an entry of an active module" that `learn.py` reads.
- **How does satrecomp count misses and feed them back?** Mechanism only. The dispatcher finds a block by address and exact code bytes; with no
  translation it runs the interpreter and counts that block's runs. With `--record-blocks` every block that ran without a translation is added to a
  text block database (address, hex bytes), merged with earlier runs, and static discovery follows branches and literal-pool calls from them. The generator
  reads that database to produce the next build. `--stats` prints interpreted against recompiled block-run percentages, the eight most-run interpreted blocks,
  and blocks found this run. The goal is zero interpreted.

## What to borrow, ranked by what it saves us

1. **A runtime interpreter fallback with miss counting and a record file** (idea from satrecomp; write our own). Saves most of our rounds: 1 to 4, 7 and 8 were
   misses. Perhaps 2 to 4 days to build, then no later game stops at a stub; a miss becomes a count and a seed.
   **Done 2026-11-16** on branch `saturn-fallback`: `port/saturn/sat_interp.c` (our own SH-2 decode, `--fallback on`), counted
   `FALLBACK` lines, the onboard boot's count and `decomp/state/fallback_seeds.txt` fed to the next translation and census.
   It also covers part of item 3: a guarded overlay translation whose bytes are not in RAM (`sat_absent`) runs the code that is there.
2. **Run the real boot chain from the IP's first-read file** (callumjenkins `bios.cpp`, MIT). Removes the inferred 06000ccc values and the
   0KRNL special case. About 1 to 2 days, and one round fewer per game.
3. **Overlay modules identified by crc, activated by an address call** (callumjenkins `sh2_identify`, `sh2_activate`, MIT). Our Round 4 note says the kit cannot
   switch overlays by memory content. About 2 days.
4. **A learned seeds file plus a learn loop** (callumjenkins `learn.py`, `seeds.json`, MIT): a stub or miss adds a seed, rebuilds and re-runs, with no
   hand work. Half a day to a day, on top of item 1.
5. **SCU DSP interpreter** (callumjenkins `scudsp.cpp`, MIT) and **the instruction-coverage check against a reference run** (`executed.py`, MIT). The DSP
   removes a stub for Deep Fear-style games (1 to 2 days); `executed.py` finds untranslated code that no stub reports (1 day).
6. Whole-game reference play and frame/audio alignment on the game's own frame counter (`reference.py`, MIT); about 1 day, and it is the owed Mednafen check.
7. Slot-per-vector BIOS dispatch (about half a day) and the drive timing switch (`--accurate-drive`), as Mednafen times it (about half a day).

Not borrowed: the VDP1 Mednafen-rules file (GPL), anything from satrecomp's source.

Redundancy check: (1) DP has no runtime fallback, only the judge and `sat_call.c`'s stub log, so it is new. (2) `onboard_sat.py` derives an entry and a boot_dir
now, which this replaces. (3) `overlay.py --pointers` and the image builder place overlays statically, but not by crc at run time: new. (4) `onboard_gaps.py` and `onboard.py`
resume already list the missing functions, which cover the "what is missing" half of the loop, not the "add and re-run". (5) the DSP is a stub in `sat_scu.c`, and
nothing in DP checks reference-run coverage against the translator's map (TcOracle judges function by function; `docs/CALL_COVERAGE.md` is for calls). (6) the Mednafen
compare is owed in the Bomberman handover. (7) `sat_bios.c` covers the services by address, not by slot.
