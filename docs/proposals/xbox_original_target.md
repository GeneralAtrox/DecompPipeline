# Original Xbox target: notes (not started)

The user, 2026-10-29: "We should target Xbox Original. Make some notes, but we dont work on Xbox Original target yet."
Nothing below is built. It is the starting point for when the user says go; until then no round, branch or job
works on it (Dreamcast first, then the other consoles in spare slots).

## In short
A new target, `targets/xbox_x86/`, built like the others: unpack the disc, find the functions, make test inputs,
translate each function to C and prove it against the original, then readable C and a native port on the shared
kit. The CPU is a 733 MHz Intel Pentium III (32-bit x86 with MMX, SSE and the x87 floating-point unit); the GPU is
NVIDIA's NV2A; the program is an XBE file on an XISO disc image.

## What is new compared with the existing targets
- **x86 is a variable-length instruction set.** Function finding cannot assume fixed-width instructions (SH-4 and
  PowerPC can). Jump tables, code mixed with data and tail calls need their own care in the catalog.
- **x87 floating point.** The x87 unit computes in 80-bit precision by default, and the game's control word picks
  the precision and rounding. The judge must model what the console does (like the SH-4 fmac and X360 fma choices),
  not the PC's SSE maths; the C runtime needs the same behaviour, or results drift.
- **Calling conventions.** Xbox code mixes `__stdcall`, `__cdecl`, `__fastcall` and `__thiscall` (C++ games), so a
  function's parameters and who cleans the stack differ per function; the signature step must find them.
- **XBE loader.** Sections, the kernel import table (thunks by ordinal) and the library versions (the XDK build
  number) come from the XBE header.
- **Kernel and XDK libraries.** Games call the Xbox kernel by ordinal and link Microsoft's XDK libraries (D3D8,
  DirectSound, XAPI, the file system). Like Dreamcast's SDK functions, these are recognised (signatures per XDK
  version) and replaced by the kit, not translated function by function.
- **Shared kit pieces** (per the shared-technology rule, game-free in `port/`): XBE loading, kernel calls, FATX saves,
  controllers, the NV2A/D3D8 renderer, DirectSound/audio, movies.

## What exists already (reuse first)
Paths below are relative to the projects folder (DP's parent, config `projects_dir`).
- The user's original Xbox ports, built outside DP: `Xbox/PhantomCrash`, `Xbox/Deathrow`, `Xbox/NHL Hitz 2003`
  (their own research tools and port code). Before any DP piece is written, check whether one of them already has
  it, and move it into the kit instead of rewriting it.
- `github.com/GeneralAtrox/xemu_decomp` (`Xemu/xemu_decomp`): xemu plus `-automation` (virtual
  controller, QMP screenshots and input). The reference emulator for the original Xbox, as Flycast is for Dreamcast,
  so the divergence finder could run against it.
- The XDK copies in `Xbox/XDK-tools` and the SDK folders beside it: library names and versions for the
  signature step.
- Discs on hand: PhantomCrash, Deathrow, NHL Hitz 2003, MechAssault, Jet Set Radio Future.

## Outside work to read (reference only; never copied, vendored or linked into DP)
- **XboxLLR** (`github.com/mstan/xboxllr`, GPL-3.0) and **BlinxRecomp** (`github.com/mstan/BlinxRecomp`) by Matthew
  Stanley: an ahead-of-time x86 recompiler for original Xbox games with native console services (kernel, FATX
  storage, input, D3D11 graphics, audio) and a launcher; Blinx is its only supported game (playable through World 2,
  2026-10-29). Its output is fast generated C++, not readable C, and it checks by play, not per function. Worth
  reading for how it handles the kernel, the XBE, x87 and the hardware, not for its method.
- A JSRF recomp build on the same base is in `JSRFRecomp-windows-x64-2026.10.26.2`.
- xemu's source (NV2A, the kernel, the APU) for hardware facts.

## First steps, when the user says go
1. A pilot game: a small one first (to pick with the user), as Mega Man 10 is for Xbox 360.
2. XISO + XBE reading and the kernel import list; the function catalog on Ghidra's x86 analysis.
3. The x87 and calling-convention decisions for the judge (each one the user's yes, as for the other targets).
4. A 50-function pilot through the translator and the judge, then the usual onboarding (docs/NEW_GAME.md).
