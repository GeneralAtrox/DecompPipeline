# Target: saturn_sh2

Sega Saturn programs on the master SH-2 (Hitachi SH7604), big-endian. The Ghidra language is stock
`SuperH:BE:32:SH-2` and needs no install step. It is the Dreamcast target with the FPU removed and the byte order
and memory map changed. The record formats, the judge and the pipeline are shared.

## Getting code off a disc (read-only on the image)

```
python tools/satdisc.py info    <image>              # IP.BIN: title, product, first-read file and its load address
python tools/satdisc.py list    <image> [out.tsv]    # every file: size, LBA, path
python tools/satdisc.py scan    <image> [out.tsv]    # scores each file as SH-2 code (prologues, rts, 0x06... pointers)
python tools/satdisc.py extract <image> <new_dir> [--code | NAME ...]
```

It reads `.iso` (2048-byte sectors), raw Mode 1 `.bin` (2352) and `.cue`. `--code` extracts the first-read file and
every file the scan scores as code.

## Memory map

| range | what | in the oracle / runner |
|---|---|---|
| 0x06000000-0x060fffff | work RAM high (1 MB): the program, overlays, heap, stack | RAM; the image is loaded here |
| 0x00200000-0x002fffff | work RAM low (1 MB) | RAM, uninitialized (an input) |
| 0x26..., 0x202... | the cache-through view of the two RAM banks | folds to 0x06... / 0x002... |
| 0x05..., 0x25..., 0x00100000-0x001fffff, 0xfffffe00+ | VDP1/VDP2/SCU/SCSP/SMPC, on-chip registers | hardware: listed by the catalog |
| 0x060e0000-0x060e0fff | the scratch area the agents' C may use | |
| 0x060f0000-0x060fffff | the stack; r15 = 0x060fe000 at entry; return sentinel 0x060ff000 | |

The cache-through view folds only for the two RAM banks. A hardware address keeps its own view.

## Workspace convention: one image from 0x06000000

A Saturn game loads its code files (overlays) at fixed RAM addresses at run time. A workspace's binary is a work RAM
high image that starts at 0x06000000 and holds the files that are in memory together:

```
python tools/overlay_image.py image.bin --context <loader>@0x0600A000 FIELD.BIN@0x0602C000
```

It prints the project.json fields:
- `load_address` (always 0x06000000)
- `binary_sha256`
- `image_gaps`: the padding up to the first file. TcSetup makes it uninitialized, as it does the RAM past the image.
- `game_ranges`: the files to decompile. `--context` files are only there for their callees and globals.

Overlays that share a load address each need their own image and their own project. The judge (`core/compare.py`
via `judge_memory` in target.json) and the C runner both assume the image starts at 0x06000000.

## The SH-2 against the Dreamcast target

- **No FPU.** TcEmu (TcOracle's emulator) reads the missing FPU registers as 0 and drops writes to them, so the
  record formats are unchanged. `float_args` is empty. `tc_rt.h` has no float helpers. `fsca_table` stays true only
  because the shared runner and TcPrepare read the table file; the SH-2 never uses it.
- **Big-endian.** Guest memory accessors (`rd16/rd32/wr16/wr32`, `TcBase.getBE/putBE`, `TcInputs.genPut`) use the
  SH-2's byte order. Instruction words are read big-endian. The judge reads vector words with `>I`.
- **SR** is the SH-2's (M, Q, I3-0, S, T: mask 0x3f3). There is no MD/RB/BL/FD.
- **No store queues, no P4.** `TcCatalog.isSq` is false, and `tcrt_p4_logged` returns 0.
- **The SH-4 mirror rule is off** (`judge_memory.word_mirror` false): the judge treats a word as 0x0c... ≠ 0x8c...
  This judges more strictly than the Dreamcast does.
- **Agent prompt.** `agent_cpu` ("SH-2") replaces "SH-4" in TASK.md. `agent_reminders.md` replaces the SH-4
  reminders and covers MACH/MACL, addc/subc chains, div0/div1 steps, big-endian memory and the absence of an FPU.

## Ghidra's SH-2 language (stock 12.1.2, `superh.sinc`)

- `mac.l` and `mac.w` are marked `FIXME: review this instruction`. `mac.l` is a stub, and `mac.w` does not follow
  the manual's multiply-accumulate. Both are refused through `TcCatalog.SYS`, so a function
  that uses them falls into the **excluded tier** and is never served.
- `bsr` sets PR here. The Dreamcast language's `bsr` bug is not present.
- Like the Dreamcast, `ldc`/`stc`/`trapa`/`sleep`/`rte` are system instructions and are excluded.

## Not supported

- **Low work RAM in the readable stage.** The readable stage runs (runtime_rd, readable_task.md,
  docs/READABLE_SATURN_GAMECUBE.md), but a function whose raw C or vectors touch the low bank (0x00200000) is
  refused with BE3: Windows keeps that range, so the readable build cannot map it at its own address.
- **Slave SH-2, SCU DSP, 68000 sound code.** Only the master SH-2's code is decompiled. Calls into the others are
  hardware accesses.
