# Proposal: hardware conformance programs for the port kit (needs the user's yes: downloads)

## What it is

Today the kit is checked against Flycast through whole games: `tools/lockstep.py` (watched RAM, frame by frame),
`tools/hw_check.py` (the PowerVR lists), `tools/ram_compare.py`, `tools/audio_check.py`. A failure there names a
frame of a game, not a piece of hardware. Conformance programs are small Dreamcast programs that each exercise one
piece (a register, a texture format, a TA parameter, a sound voice) and record what they saw; run on Flycast and on
the kit, their results are compared item by item, and every remaining difference goes into `port/KNOWN.md`.

## The catch: the kit runs C, not SH-4 machine code

The kit runs a game's translated C over emulated hardware; it has no SH-4 interpreter. So a test program has to come
to the kit the way a game does: as C that calls the kit's hardware (`platform.h`, `pvr/`, `runtime/aica*.c`,
`sdk/`). Two routes:

1. **Our own tests, written in C twice-compiled.** Each test is plain C against a tiny hardware header. Compiled
   with an SH-4 toolchain (KallistiOS's or a bare `sh-elf-gcc`) it is a Dreamcast program Flycast runs; compiled with
   the kit (as a "game" whose sources are the test, game.json `sdk` naming the modules under test) it runs on the
   kit. Both print the same lines (a register's value, a digest of a rendered frame, a voice's samples).
   Needs: an SH-4 toolchain download (and its build), the user's yes.
2. **Published homebrew tests.** Existing Dreamcast hardware test programs exist in the homebrew community (PowerVR,
   AICA, timing). They are SH-4 binaries: they run on Flycast but not on the kit unless they are decompiled through
   the pipeline like a game (a workspace, prepare, judge), which costs far more than writing our own. Their value
   would be as Flycast-side references only. Needs: downloads, a licence check of each, the user's yes.

Route 1 is the recommendation.

Not needed for the CPU: single instructions (`fsca`, `fipr`, `ftrv`, store queues, FPSCR modes) are already checked
per function, without any download, by `tools/xemu_check.py` (DP's own SH-4 interpreter against the Ghidra oracle)
and the judge. Conformance programs are for the hardware around the CPU, which the kit emulates and the judge does
not see.

## Which tests first

Ordered by what `port/KNOWN.md` and the `pvr_todo` list say is open, so each test either closes or pins an entry:

| Area | Test | KNOWN.md entry it pins |
|---|---|---|
| TA / store queues | Write a list through the store queues with a `pref` of a block not fully written | The store-queue model |
| Textures | One small texture per pixel format (twiddled, VQ, palettized 4/8 bit, stride, YUV422), drawn and digested | Cases the renderer does not draw yet |
| Modifier volumes | Inclusion, exclusion, two-parameter polygons | Cases the renderer does not draw yet |
| AICA | One voice per sample form (PCM 16/8, ADPCM), envelope, the DSP on and off | The effects DSP; the AC layer without the driver |
| Timer / RTC | TMU counts across vertical blanks | System configuration |

## How they run

- **Flycast side:** `tools/lockstep.py emu` already starts Flycast with a Lua script and a spec of watched words.
  A test writes its results into a fixed RAM block; the test's `lockstep.spec` names that block, so the existing
  recording (`emu.txt`) holds the results frame by frame. Display tests add `hw_check.py emu` (list digests) and
  `emu_capture.py` (frames). Nothing new on this side but the spec files.
- **Kit side:** the test built as a game (`port/tools/build.py --game tests/conformance/<test>`), run by
  `lockstep.py port`, compared by `lockstep.py compare` and `hw_check.py`. The test folders live in DP as fixtures,
  game-free (each is a test, not a game).
- **The list:** `port/KNOWN.md` gets each remaining difference with the test that shows it; a fixed one leaves the
  list in the commit that fixes it.
- **The gate:** a script, `tools/conformance.py` (nearest existing: `regress.py`, which runs one game's scripts
  against goldens; this runs many tiny "games" against an emulator recording), runs them all and fails on a
  difference not in KNOWN.md.

## Cost

- Downloads and setup: an SH-4 toolchain (a few hundred MB; an hour to build on Windows under MSYS2, or a prebuilt).
- Writing: about 10 small tests at first (100-200 lines of C each) plus their specs: two to three working sessions.
- Running: each test is seconds on both sides; the whole set under ten minutes, fit for the coordinator's landing
  run.
- Upkeep: a kit change that fixes a difference updates KNOWN.md and the test's expected list.

Not started. Waiting on the user's yes for the toolchain download.
