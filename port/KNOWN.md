# Known differences between the kit and the console

Every difference between what the kit does and what a Dreamcast does that the kit's code, README or a game's notes
already name. Each says where it is handled and which check shows it. Nothing here is a guess: an entry cites the
file that states it. A difference found later is added here when it is found; one that is fixed is moved to the
commit that fixed it (git log) and removed. The checks against the emulator are `tools/lockstep.py` (RAM, frame by
frame), `tools/hw_check.py` (the PowerVR lists), `tools/ram_compare.py`, `tools/audio_check.py` and `tools/regress.py`
(goldens and log classes). Proposals for small hardware test programs: `docs/proposals/kit_conformance.md`.

## CPU and translated code

### Host float maths
The port's float results can differ from the SH-4's by a few ulps; `(a * b) * c` on x87 rounds once where the SH-4's
two `fmul`s round twice. Handled: `runtime/sh4_rt.c` (the rounding-mode and FSCA helpers); a game's notes report a
recorded demo going out of step from it (Toy Commander, `port/RESUME.md` "Findings"). Shown by: `tools/lockstep.py`
(first divergent word).

### Boot ROM reads
The port has no BIOS: a read of the boot ROM finishes as if the ROM held zeros, for the load forms MSVC emits; any
other instruction ends the run. Handled: `runtime/rom_read.c`. Shown by: the log (the fault and its address).

### Cache and listed hardware register operations
`ocbi`, `ocbp`, `ocbwb` and the listed hardware register writes do nothing: the port has no such hardware and its
memory is coherent. Handled: `runtime/sh4_rt.c`. Shown by: nothing (no observable effect expected).

### Copy loops of size 0
The SHC block move with a size of 0 copies nothing; the console's `dt` loop would run 2^32 times. Handled:
`sdk/shc_long.c`. Shown by: `tests/test_port_kit.py` `test_shc_long`.

## Memory and the store queues

### The store-queue model (r14 defect class c)
The console's TA gets the vertex buffer through the store queues; the port reads the buffer, so a block the game
`pref`s without writing it all is stale RAM in the port and the queue's last contents on the console. Handled: in
check runs only, `runtime/hw_check.c` models the queue as the block 0x40 back and counts it ("R frame resent"); the
port's own path has no model. What the model misses (a queue last written elsewhere) stays a difference; a list-start
dummy the console gets twice is counted apart. Shown by: `tools/hw_check.py` ("store queue resends modelled").

### QACR routing
QACR0/1 do not route the store-queue window: it stays area 3. Handled: `runtime/port.h`, `runtime/guest_mem.c` (logs
"not area 3; the store-queue window still writes area 3"). Shown by: the log.

## Graphics (PowerVR and Kamui)

### Cases the renderer does not draw yet (`pvr_todo`)
Each logs `TODO <what> (frame N)` once (`pvr/pvr_state.c`); `tools/regress.py` fails on a TODO class missing from a
script's baseline. The cases, by file:
- `pvr/pvr_draw.c`: translucent modifier volumes without autosort.
- `pvr/pvr_modvol.c`: modifier volumes with two-parameter polygons (cheap shadow off), opaque and translucent;
  exclusion modifier volumes.
- `pvr/pvr_states.c`: the secondary accumulation buffer.
- `pvr/pvr_tex.c`: a texture the host could not create.
- `pvr/pvr_twiddle.c`: VQ palettized, twiddled YUV422, an unhandled pixel format, mip-mapped and not square, a
  stride texture before the stride is set.
- `pvr/ta_batch.c`: a vertex with 1/w <= 0 or not finite.
- `pvr/ta_parse.c`: user clip outside, an unknown vertex type, an object list set, an unknown parameter (the rest of
  the list is skipped).
- `sdk/kamui1_misc.c`: colour clamp, fog vertex colour, a background plane vertex type; `sdk/kamui1_tex.c`: a
  refused `kmCreateTextureSurface`, texture memory full.
- `sdk/kamui2_misc.c`: a `Km_SetCallback` slot, the background plane; `sdk/kamui2_tex.c`: a `kmCreateTextureSurface`
  type, texture memory full, `Km_ReadSurface` of the frame buffer.

### Culling register
`kmSetCullingRegister`'s value is not applied: polygons smaller than it, which the console drops, are drawn (they
show nothing more). Handled: `sdk/kamui1_misc.c`. Shown by: nothing (no visible effect expected).

### Kamui 1 direct lists
A list that goes straight to the TA on the console is buffered by the port. Handled: `sdk/kamui1_scene.c` (logged). It shares the vertex buffer with the other lists unless game.json `kamui1.direct_list` gives it RAM of its own; a list that writes past its room is logged ("runs into the next list"). Spawn's stage 1 needs 392 KB of opaque list; the shared 177 KB overran into the translucent and modifier lists and drew black triangles.
Shown by: the log.

### Texture addresses
A texture's place in texture memory is the port's allocator's, not Kamui's. Handled: `sdk/kamui1_tex.c`,
`sdk/kamui2_tex.c`. Shown by: `tools/hw_check.py` pairs textures by content and does not compare addresses.

### What the list comparison cannot see
Two-volume strips: only x, y and z are digested; modifier volumes compare by header and triangle count only.
Handled: `tools/ta_digest.py`, `runtime/ta_digest.c`. Shown by: nothing finer than those digests.

## Sound

### The effects DSP
The AICA's effects DSP is not run: a game whose driver uses it audibly needs it first. Handled: `runtime/aica_arm.h`.
Shown by: `tools/audio_check.py` (the DSP's share of the emulator's output).

### The AC layer without the driver
With the `ac_digi` and `am_sys` modules, each AC call sets the voice directly instead of queueing a command for the
ARM's driver, so it never fails for a busy queue; the driver image is loaded but nothing runs it. Voices change at
the vertical blank. Handled: `sdk/ac_digi.c`, `sdk/am_sys.c`. Shown by: `tools/audio_check.py`. A game that needs
the driver itself sets game.json `aica_arm` instead.

## Disc, system and timing

### Synchronous disc reads
Every gdFs and ADXF read is done when the call returns; reads never fail asynchronously; CD-DA reports status only.
Handled: `sdk/gdfs.c`, `sdk/adxf.c`. Shown by: nothing (a game's loading screens run shorter).

### System configuration
Hardware set-up is a no-op, the cable is VGA, and the RTC is the host's local time. Handled: `sdk/sy_sys.c`;
`tools/lockstep.py` sets Flycast's cable to VGA so the two agree. A game's notes record the video cable, screen
margins and display mode words differing from an emulator run that used another cable (Toy Commander,
`port/RESUME.md` "Findings"). Shown by: `tools/lockstep.py`.

### Interrupts other than the vertical blank
Only the vertical blank (event 0x900) happens; handlers for the horizontal blank, GD-ROM DMA end and the GD-ROM line
are kept and never run, each logged. Handled: `sdk/sy_chain.c`. Shown by: the log.

### Movies a port does not play
A movie the port does not play has no scene where the emulator has one. Handled: `runtime/hw_check.c` (`--hw-tex`
writes nothing for such a frame). Shown by: `tools/hw_check.py`, `tools/lockstep.py` (frame pairing).

## Input and the session recording

### What a recording leaves out
In-between frames are not recorded (drawn and undone); only player 1 is recorded and replayed; a scripted `--input`
holds 4096 presses and drops later ones (logged once); a `--fixed-clock` run keeps no saves without `--save-dir`.
Handled: `base/session.c`, `base/input.c`, `sdk/bu_vmu.c`. Shown by: `session: replay out of step` in
the log.

### Real-time recordings at boot
One real-time recording, made on a heavily loaded machine, replayed out of step: the boot splash's fade loop ran 33
frames instead of 9, so something the game reads at boot is not recorded yet. Cause open. Handled: nowhere yet.
Shown by: `session: replay out of step` (port/README.md "Copy agent prompt").

### What the lockstep check leaves out
The port runs with no memory card; time words are reported apart; pointer words compare only as null or not;
`tools/ram_compare.py` leaves out the stack and SDK state. Handled: `tools/lockstep.py`, `tools/ram_compare.py`.

## Overlays

### OVERLAY MISS
A call into overlay RAM where no loaded unit has a function logs `OVERLAY MISS` once per group and returns 0; a
checked build stops with exit code 3. The console would run whatever code is there. Handled: `runtime/overlay.c`.
Shown by: the log; `regress.py` baselines; a checked build.

## Exit codes

### Exit code 4
Code 4 means a missing or unreadable disc image, but the kit also ends with 4 when the display device is lost
(`runtime/gfx/gfx_present.c`), when the renderer has no memory for a scene (`pvr/ta_batch.c`) and when
`--verify-assets` is given no `--assets` (`base/host_main.cpp`). A launcher reading 4 as "disc problem" is misled in
those cases. Handled: CONTRACT.md "Exit codes" lists them. Shown by: `tests/test_port_contract.py` `test_exit_codes`.
`tools/lockstep.py`'s own exit 4 (divergent lists while RAM agrees) is the tool's, not port.exe's.
