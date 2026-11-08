# Xbox 360 port kit: survey and design (Mega Man 10 is the reference)

Rules: `docs/RUNTIME_TARGET.md`, "New console kits start with no hardware model". The kit lives in
`targets/xbox360_ppc/port/`. Survey of 2026-10-31, from the loaded image of `mm10ws` on build box A (`image.bin`, 90 MB, imports
resolved), by `port/tools/survey.py` (reads only the image and the catalog; the XEX retail key is never needed or stored here).

## 1. What the game links (the boundary)

Mega Man 10 is a Capcom XBLA title built with the XDK (`d:\rockman10\prog_360\rm10\Release\rm10.pdb`). The code is one
statically linked image: the game, the CRT, the XDK libraries.

| Part | Evidence | Boundary |
|---|---|---|
| Kernel and XAM | 216 imports by ordinal: 139 `xboxkrnl.exe`, 77 `xam.xex`, plus 12 kernel variables. 210 are called (3,000 call sites). The imports are ordinals only: the XEX holds no names | Each import becomes a host call. Names come from the Xenia export tables (`port/data/export_names.json`, see "Where the names come from") |
| Direct3D 9 (XDK `d3d9`) | Library strings: `ERR[D3D]: The GPU is hung!`, `D3D version %i.%i`, `D3DCREATECB_DEBUG_ALLOCATOR`. The GPU window is touched in **4 instructions in 3 functions**, all inside the library: 821cdb50 (writes `0x7FC86110`, then `eieio; sync`: the ring write-pointer kick), 821d3740 (reads `0x7FC86544`), 821d3fb8. No game function outside that block reaches the GPU window. The game never writes command packets itself | The library is replaced at its entry points. Block `0x821c64f0..0x821e55d0`: **545 functions, 75 called from outside, leaving for 33 callees**, found by `port/tools/boundary.py` and recorded in `port/boundary.json` (the first lead, `0x821b8000..0x821e6000`, was too wide). Seeds: 18 functions that call `Vd*` kernel imports and 4 that build GPU-window addresses. `tc_call` checks the ranges first, so no function in them is translated; a call into one reaches `xb_lib_entry` or stops as `library_entry` |
| D3DX | Library string `File created by Microsoft (R) D3DX library` | Only the calls the front end makes |
| Shaders | 24 compiled Xenos shader containers in the image (magic `0x102A1100` ×16, `0x102A1101` ×8). Two game functions, 822525b0 and 82252758 (2,380 bytes), also write HLSL text at run time (`vs_2_0`, `ps_2_0`, `PixelShader pass %d`, `sampler2D TextureSampler[BB] : register(s0)`; blur, difference and weighted-sum passes) and hand it to the library | Offline: convert the 24 containers to HLSL/DXBC before play (translator, no interpreter). The run-time text is valid HLSL: the host compiler takes it as it is |
| Audio | XACT: `game:\media\sounds\rm10.xgs`, `SE.xsb/.xwb`, `BGM00..45.xsb/.xwb` (46 music banks, one sound-effects bank). The XACT engine is in the image | The XACT engine entry points go to the kit's mixer; wave banks are decoded once to PCM (XMA, native decode, offline or at load) |
| Files | `game:\media\bin\...`, `bin_sp00..04`, `textures\`, `sprite\`, `x360\*.bin`; save slots `rm9_sav0..7.dat`, `savedata.bin` | `game:` is the user's own package folder; saves are host files |
| Video | A `.XBMOVIE` section exists (0x200 bytes). No movie file names in the image | Not reached yet |
| Live, achievements, sign-in | Strings `Achievement Test`, `SIGN-IN & ACHIEVEMENT TEST`, XAM imports | Stubs that answer "not signed in" until the log shows the game waiting on them |

Mega Man 10's own code is plain: `LZ.bin` containers (game data decompressed by the game), `rm9_sys.dat`. No direct hardware use
was found outside the D3D block.

### Shared with Too Human?
Checked 2026-10-31 on `th_p` (88,187 functions) with `port/tools/library_share.py`: every function of the Mega Man 10 block is
fingerprinted by its instruction shape (operations only; immediates, registers and branch targets masked) and looked up in the
Too Human image. Of the 356 functions of 12 words or more, **81 are found** (55 exactly once), 52 of them within one 0x30000
window around `82740ff8..82d34d18`; of the 189 shorter ones 177 match, which means nothing (small getters look alike). Of the 23
entries written so far, 8 have any match (two of those are functions of 1 and 5 words), and the matches that are not tiny are one function each. So Too Human links **another build of
the XDK Direct3D library**: the same Direct3D 9 calls with the same arguments, but not the same code or addresses.
Consequence: the D3D11 backend (what each call does) is shared between games; the table from an entry's address to its call is
per game (found by the stops, as here). The entry list in `xb_d3d.c` is keyed by address for Mega Man 10 only and moves to a
per-game table before a second game uses the kit. The kernel and XAM host calls are shared (same ordinals, same Xenia names).

## 2. Cut

1. Imports to Win32/host: threads, events, critical sections, TLS, files, time, memory. Memory is the one place the kit
   keeps a layout: the guest sees big-endian memory at its own addresses (one 4 GB reserve, `base + addr`, one byte swap per
   access; the swap is the one cost that cannot be designed out, row in the emulated-layers table).
2. D3D block: entry points replaced by a D3D11 backend. Nothing in the block is translated. The device is a host object; the
   guest's `D3DDevice` structure is only touched where the game reads fields of it (found by the stops).
3. XACT block: same, to the kit's mixer.
4. Everything else is the game's own translated C (judged functions). A function that is not judged yet runs as the
   translator made it (bring-up) and is counted in every report.

## 3. No caller, no code: the table

Rows are added by a stop, not by plan. Columns: part, what calls it, first log or function that needed it, host call,
share of the frame budget (6.67 ms p99 to make a frame at 2560x1600).

| Part | Caller | First needed by | Host call | Budget share |
|---|---|---|---|---|
| Big-endian guest memory | every translated load and store | the first function (821b9b58) | `rd8`..`wr32r`, one `bswap` + add, inline | Not measured yet (row in the emulated-layers table) |
| Guest stack and PCR | the entry's frame, `r13` reads | 821b9b58 | committed regions `0x70000000` (1 MB), `0x7FFF0000` | none (set-up) |
| Time base | `mftb` in the CRT seed | first stop of the CRT | `QueryPerformanceCounter` scaled to 49.875 MHz | none |
| Allocation | `NtAllocateVirtualMemory` (#204), `MmAllocatePhysicalMemoryEx` (#186) | the CRT heap (822bb160), the D3D allocator (821b92a0) | bump allocator over the reserve, 64 KB aligned | none |
| Critical sections | RtlEnter/Leave/Initialize(AndSpinCount) (#293, #304, #302, #303) | the CRT lock table (822c2cc8) | Win32 `CRITICAL_SECTION` keyed by guest address (`xb_host_object`) | none |
| Thread-local storage | KeTlsAlloc, KeTlsSetValue (#338, #341) | the CRT thread data (822bcd00) | `TlsAlloc`, `TlsSetValue` | none |
| Module load and lookup | XexLoadImage (#409), XexGetProcedureAddress (#407) | 821b9ef0, 822babe0 (six XAM functions by ordinal) | an opaque token; each looked-up address is a dynamic import (`xb_dyn_import`) that stops by name when called | none |
| Video mode record | xam#977, the display set-up in 82093cf0 | 82093cf0 | 1280x720 wide HD record; the game draws at the port's resolution | none |
| Media-player state, one yes/no query | xam#500 (app 0xfa), xam#614 | 82206048, 8209ba08 | answered 'nothing playing' and 'false', logged | none |
| Names of the imports | every host log line that names an import | the first import stop | `port/data/export_names.json` (from the Xenia tables), emitted as `xb_name_table` | none |
| D3D device: create, reset, ring parameters | `821c6c50`, `821c6780`, `821d4cc0` (the display set-up 82093cf0) | the first `library_entry` stop | the game's mode record is kept; the host swap chain follows the window; no command ring (the parameters are logged) | to measure |
| D3D resources: textures, render targets, shaders, vertex declarations | `821cc0c8`, `821cc1e8`, `821cd9a0` (vertex shader), `821cd7b8` (pixel shader), `821cd520` (declaration) | 82093cf0 and the functions it calls (820937b8, 82093938, 820937f0) | a guest header block per object (the game keeps the pointer) and a host record of what was asked; the host texture, target or shader is made when a draw uses it | to measure |
| D3D binds and device modes | `821ca348` (render target), `821ca070` (depth), `821cc448` (texture), `821cd418` (declaration), `821cd248`, `821cd088` (shaders), `821c9bf0` (viewport), `821c88b8`, `821c8a60` (sampler states), `821d5110`, `821d0418`, `821d0bc0` (state setters whose names the code does not show) | 82093970 and 82093a98 | the bound objects are recorded; the state setters are answered 0 and logged once with their arguments | to measure |
| D3D clears | `821d00a8`, `821d0708` (flags in r4: colour `0xF0`, depth `0x100`, stencil `0x200`; colour block at r7, z in f1, stencil in r8) | 82093970, 82093a98 | `xb_gpu_clear` on the bound host target and depth view (the argument meaning is read from two call sites and the one colour block seen, all zero; check it when a non-zero clear colour shows) | to measure |
| D3D draw | `821d12f0` (DrawVerticesUP: primitive type, vertex count, pointer, stride; the first is type 8 = rect list, 3 vertices, stride 8) | 82093a98 | each declaration element k feeds the shader's vertex fetch k (the library patches the fetch constants from the declaration), converted to a float4 per input (3 float formats and the packed 8888 colour; any other stops); a rect list is expanded by a geometry shader; `xb_gpu_draw` in `xb_gpu.c` | to measure |
| D3D resolve | `821d24b0` (flags, source rect, destination texture, destination point, level, slice, clear colour, z, stencil) | 82093a98 | one `CopyResource` from the bound target to the texture's host texture; flag `0x100` is read as the clear of that target after the copy; depth resolves and other flags stop | to measure |
| D3D calls with no host meaning | `821ce2e8`, `821ce2f0`, `821c65e8` | 82093a98, 82256f08 | the first two only append packets to the library's ring (answered 0, logged); the third is a four-instruction device reference count (`device+60 += 1`), written as such | none |
| Texture layout arithmetic | `821ca528` (block size of a format), `821cb900` (layout calculator) and the helpers they call | 822fd090 (the game's texture header code) | not host calls: pure arithmetic inside the library range. `port/kept.json` carves them out of `boundary.json` so they translate like game code | none |
| Shaders: offline conversion | the 24 containers in the image, found by magic | 820937b8, 820937f0 and the shader creates | `tools/shader_convert.py` reads each container (header, constant table, embedded constants, microcode) and writes HLSL per shader plus `gen/shaders.c`; `xb_gpu.c` compiles it with `D3DCompile` on first use. 24 of 24 convert and compile (`tools/hlsl_check.py`). No microcode runs on the host | to measure |
| Host D3D11 device, window, swap chain | the first draw | 82093a98 | The port base's (`port/base/present.h`), opened at the first draw; `runtime/xb_gpu.c` draws on them. Headless runs draw the same frame target with no window |
| Funclets and non-volatile registers | the `__finally` blocks the compiler outlines (first: 821be588, called from 821bdd20) | the CRT lock paths | the translator's port form passes `r12` on every call (`TcArgs.r12`) and, for a call to a funclet (first instructions `addi rN, r12, ...`), `r14..r31` (`TcArgs.nv`); a funclet starts with them. Normal functions are unchanged | none |
| Direct3D unpack | `vupkd3d128` types 0..5 | 822490b8 (a vertex unpack) | `xv_vupkd3d`, written from the oracle's executor; `vpkd3d128` stays refused. A function with it is translated but not judged (the judge refuses it) | none |
| Return kinds and kept registers | every call in the port form | a null write at 0x2694, the leaf 820a0130 | the optimised build keeps volatile registers across calls by its own rules: `keeps.py` works out per function what it and its callees never write; a callee that writes neither r3 nor f1 leaves them as they were (call kind `v`). A leaf also ends at a backward tail jump | none |
| Threads, r13 | ExCreateThread and the thread's processor block | 821bb250, the sound thread 8209db88 | a guest thread is a host thread with its own guest stack and r13; ExTerminateThread is ExitThread. No scheduler | none |
| Files | NtCreateFile, NtReadFile, NtWriteFile, NtQuery/SetInformationFile | 82096fe0 | host files over the extracted package (`XB_GAME_DIR`, `tools/stfs.py`). Writing happens only inside saved content | none |
| Saves | XamContentCreateEx and its family (`runtime/xb_content.c`) | 82090c00 | a folder per package under `XB_SAVE_DIR`, the drive name mapped while it is open. Fresh and existing saves both run | none |
| Profile, licence, system screens | xam#522, #526, #614, #551, #715, #714, #709, #711 | the title's sign-in and the menu | one local profile "Player" signed in; the game answered as **licensed** (decision: no TRIAL VERSION title); device 1 chosen at once; a message box presses its focused button; achievements and marketplace open and close with nothing drawn, logged | none |
| Notifications | XNotifyGetNext (#651), overlapped blocks | the reader 82096848 | a small queue the system-screen answers post to (id 9 open, then closed). A status or HRESULT is sign-extended (`XB_STATUS`): the game tests them with a 64-bit compare | none |
| Render and sampler states | the device tables the library gives the game (`82093680`, `820936c0`, rows at 824b0900 and 824b0dc0) | the first frame | `runtime/xb_state.c`: each state is a method on `XB_CLASS_RS`/`XB_CLASS_SS` that records the value; blend, sampler and depth objects are made and cached at the draw | to measure |
| Tiling, swap | `821d00a8` BeginTiling, `821d0708` EndTiling, `821ce2f0` Swap | each frame | the game draws twice into 2x MSAA tiles of 1280x384; the host has one whole-frame target, no tiles and no MSAA yet. EndTiling copies, Swap presents | to measure |
| Textures | LockRect, the format word (layout in bits 0-5, tiled in bit 8, swizzle in 18-29) | the opening | uploaded to the host when a draw uses a dirty texture, detiled and swizzled once | to measure |
| Sound | XACT3CreateEngine `821e5990`, XAudio2Create `82264068` | 8209cde8 | the classes are methods at `XB_XACT_BASE`, played by `xb_sound.c` from the game's own banks: a cue lasts as long as its wave, by the tick. 46 BGM banks are PCM; SE.xwb is 186 XMA, decoded as it plays by a separately loaded library, `xb_xma_dll.c` | to measure |
| Pad | XInputGetState | the title | The base's pads, sampled once a frame (`console_x360.c`), every button and both sticks assignable; a test run scripts them with `--input` and reads no real input |

Stops and how they were answered are in `port/README.md` (the loop) and in each import's comment in `port/runtime/xb_imports.c`.

### Where the run reaches (build box A, 2026-11-01)

Boot, the Capcom and Inti Creates logos, the history screens, the intro, the title, PUSH A BUTTON, device select, save create and
load, the main menu and every item it opens (Game Start, Time Attack, Leaderboards, Challenges, Greeting Card, Help & Options,
each screen back out), all in a hidden scripted run read back as BMPs. Achievements and Downloadable Content open the console's
own screens, which the port answers with nothing drawn. Return to Xbox LIVE Arcade runs the game's tear-down part way and is not
finished. A New Game has not been started.

Not passed against the front-end list: no sound; no real-clock measurement; no 144 fps measurement; the aspect options and the
controller screen are not there, because this kit is not on the shared base yet. So no playtest is asked for.

`port/tools/reached.py` keeps `OUT/reached.tsv`, one row per function the run has reached, with its verdict. Functions not yet
judged run in bring-up. The campaign's priority step takes the ones with no verdict ahead of its chunk order.

### Where the names come from

`port/tools/export_names.py` reads the Xenia export tables as shipped in XenonRecomp (`XenonUtils/xbox/xboxkrnl_table.inc` and
`xam_table.inc`, in the XenonRecomp checkout already on the main laptop; no download) and writes
`port/data/export_names.json` (ordinal to name). Xenia is BSD-3-Clause and XenonRecomp is MIT; both notices are in
`port/data/NOTICE-xenia.txt`. Only the names (facts about the console's published export ordinals) are used; none of either
project's code is in the kit. A name is shown in logs and stop lines; the behaviour of each import is still written from what
the callers pass.

## 4. Must not contain

Xenos register file, PM4 command stream parser, eDRAM model, shader microcode interpreter at play time, Xenon hardware-thread
scheduler, XMA hardware model, kernel object emulation where a host object does the job.
`tests/test_x360_port_forbidden.py` fails if the kit's source holds those names (`xenos`, `pm4`, `edram`, `microcode`
interpreter, `xma` hardware names, a guest-thread scheduler, kernel object tables). The GPU window `0x7FC8xxxx` is not mapped
by the kit: the library that touches it is replaced, so a guest access there is a stop.

## 5. Budget from day one

6.67 ms p99 to make a frame and 1 late frame per 1,000 at 2560x1600; 4:3, 16:9, 21:9 and Fit window; every controller button assignable; the play summary in the game log
(reuse `port/runtime/play_stats.c`, `game_log.c`, the overlay, input and launcher path, and the aspect-ratio and
in-between-frame kit; no parallel copies).

## 6. Answer key

Judged functions and captures from the original under an emulator, run apart from the port. Xenia is **not** on build box A
(checked: no emulator folder in build box A's work folder). Nothing is downloaded without the user's yes.

## 7. Open questions

1. The window and menu belong to the shared port base. `xb_gpu.c` still has its own hidden window and device; it moves to
   `host_window_hwnd()` when the kit moves to the base.
2. The run-time HLSL goes through `D3DCompile` on the host; no D3DX is linked.
3. Three entry setters whose names the code does not show (`821d5110`, `821d0418`, `821d0bc0`) are answered 0 and logged. The
   picture is right through every screen named above with them answered so. That does not prove them on a screen that has not
   been drawn, and nothing has been compared with the original: there is no answer key on build box A.
4. The XMA hardware window `0x7FEA` is touched outside the D3D range (822668a8, 82267140, 8226725c under XAudio2, and 82302670,
   82303460, 82303474, 823034e8 under XACT). The cut above those layers means they do not run. No XMA hardware model.
5. A second game (Too Human) links another XDK build: its entry table is found the same way, from the stops.
