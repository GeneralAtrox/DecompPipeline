# Sega Saturn: where to find which SDK versions

The Saturn has almost no development symbol sets, so this table leads with **retail discs**: for each disc on our drives it gives the Sega library stamps (SBL or SGL builds of the system, CD-file-system and stream libraries), the sound driver, the middleware and, where the disc says, the compiler. Below it are the signature sets we hold for matching library code. Made with `targets/gamecube_ppc/tools/symbol_inventory.py --console saturn` (readers in `tools/symset_sega.py`, disc reader `targets/saturn_sh2/tools/satdisc.py`, table writer in `tools/symset_consoles.py`) on 2026-11-19.

**What this is made from.** Only facts read from the discs' own files: the IP.BIN header (product number, version), the library stamp lines inside the program and system files, the sound driver's version string, middleware markers and the file list. Only code-like files up to 4 MB are read in full; every other file is read for a magic number only, so no whole disc is read. **Nothing from a disc is copied into this table**: only product numbers, version stamps and counts. Blast Chamber's disc files were read for this table and nothing else (no run, build or onboarding). A field that could not be read says "not read" or "none found". The same facts are published as `SATURN_DISCS.csv` and `SATURN_DISCS.json` (stable field names, one row per disc).

**The numbers.** 9 discs: Robo Pit (Europe), Saturn Bomberman (Europe, USA, Japan SegaNet), Dragon Force (Europe, USA, Japan Rev A), Dragon Force II (Japan Rev A) and Blast Chamber (Europe). 5 carry an `SBL` or `SGL` library stamp (Bomberman ×3 and Dragon Force II on SBL 2.10 / 2.14, Robo Pit on SGL 2.11); the other four give no SBL/SGL stamp in their program files. No disc states its compiler.

## How to use it

1. Find your disc's library stamps: search the program file (`0KRNL.BIN`, `PROGRAM.BIN`, `1STPROG.BIN` or the disc's first-read file) for ` Version ` followed by a date.
2. Match the stamp in "By SBL/SGL library stamp". `GFS_SBL 2.10 1996-02-01` means the CD file system was linked from SBL 2.10; `GFS_SGL 2.11 1996-03-21` means the SGL kit's build of it.
3. The signature sets below let a decompiler name the library functions of a game on the same kit; use the one nearest your stamp.
4. For the sound driver (`SDDRVS.TSK` and its relatives) use "By sound driver".

## Reading your own disc's build (no tool needed)

```
GFS_SBL Version 2.10 1996-02-01        STM_SBL Version 2.10 1996-02-01
SYS Version 2.21 1996-10-01            PCM Version 1.15 1995-02-21
```

- The library name's suffix says which Sega kit built it: `_SBL` (Saturn Basic Library) or `_SGL` (3D Game Library). Plain names (`SYS`, `PCM`, `CPK`, `FLD`, `BUP`, `CDC`) are shared system libraries.
- The sound driver is a separate binary with its own stamp: `Ver2.04 96/01/25`.
- Cinepak movies are `.cpk` files with a `cvid` tag; CRI's ADX audio carries `(c)CRI` near the start of its files; the tool counts both as middleware.

## Columns and legend

- **Disc**, **Product** (the IP.BIN product number and version), **Files** (files on the disc).
- **Kit:** `SBL` or `SGL` from the CD-library stamp, else "no SBL/SGL stamp". **Library stamps:** the stamps read in the program and system files, with their build dates.
- **Sound driver:** the version string; "file present, no version string" where the driver file exists but holds no stamp. **Driver files:** the driver's file names.
- **Middleware:** Cinepak (`.cpk` with `cvid`), CRI ADX, CRI Sofdec; the count is files.
- **Compiler:** a compiler string in the disc's code files, else "not stated in the disc".

## Sets

<!-- sets -->
| Disc | Product | Files | Kit | Library stamps | Sound driver | Driver files | Middleware | Compiler |
|---|---|---|---|---|---|---|---|---|
| Dragon Force II - Kami Sarishi Daichi ni (Japan) (Rev A) | GS-9184 V1.007 | 374 | SBL | BUP 1.25 (1997-06-20); CDC 1.22 (1997-02-27); CPK 1.20 (1995-10-05); GFS_SBL 2.14 (1997-04-11); SYS 2.50 (1997-06-10) | file present, no version string | SDDRVSA.204; SDDRVSA.209 | cri adx (48 files) | not stated in the disc |
| Saturn Bomberman (Europe) | MK-81070 V1.000 | 27 | SBL | GFS_SBL 2.10 (1996-02-01); PCM 1.15 (1995-02-21); STM_SBL 2.10 (1996-02-01); SYS 2.21 (1996-10-01) | none found | none | cinepak (3 files) | not stated in the disc |
| Saturn Bomberman (Japan) (SegaNet) | T-14305G V1.001 | 34 | SBL | FLD 1.21 (1996-02-28); GFS_SBL 2.10 (1996-02-01); PCM 1.15 (1995-02-21); STM_SBL 2.10 (1996-02-01); SYS 2.10 (1996-01-31) | Ver2.04 96/01/25 | none | cinepak (3 files) | not stated in the disc |
| Saturn Bomberman (USA) (1S) | MK-81070 V1.003 | 430 | SBL | FLD 1.21 (1996-02-28); GFS_SBL 2.10 (1996-02-01); HFLD 1.21 (1996-02-28); PCM 1.15 (1995-02-21); STM_SBL 2.10 (1996-02-01); SYS 2.21 (1996-10-01) | Ver1.31 95/06/20; Ver2.04 96/01/25 | none | cinepak (3 files) | not stated in the disc |
| Robo Pit (Europe) | T-10002H50 V1.001 | 34 | SGL | GFS_SGL 2.11 (1996-03-21); STM_SGL 2.11 (1996-03-21) | Ver2.04 96/01/25 | SDDRVS.133 | cinepak (4 files) | not stated in the disc |
| Blast Chamber (Europe) (En,Fr,De,Es,It) | T-13003H50 V1.000 | 363 | no SBL/SGL stamp | none read | Ver1.31 95/06/20 | SDDRVS.TSK | none found | not stated in the disc |
| Dragon Force (Europe) | MK-8138250 V1.100 | 734 | no SBL/SGL stamp | CPK 1.10 (1995-03-31); PCM 1.16 (1995-03-31) | file present, no version string | SDDRVS.TSK | cinepak (16 files) | not stated in the disc |
| Dragon Force (Japan) (Rev A) (10M) | GS-9028 V1.011 | 745 | no SBL/SGL stamp | CPK 1.10 (1995-03-31); PCM 1.16 (1995-03-31) | file present, no version string | SDDRVS.TSK | cinepak (12 files) | not stated in the disc |
| Dragon Force (USA) | T-12703H V1.000 | 734 | no SBL/SGL stamp | CPK 1.10 (1995-03-31); PCM 1.16 (1995-03-31) | file present, no version string | SDDRVS.TSK | cinepak (16 files) | not stated in the disc |
<!-- /sets -->

Notes on single discs:
- **Dragon Force (all three regions)** carry only the `CPK 1.10` and `PCM 1.16` system-library stamps (1995-03-31) in the main program, and no `SBL`/`SGL` file-system stamp: the program was not linked from the stamped GFS library, or the stamp is not stored in it.
- **Blast Chamber** has no library stamp in `1STPROG.BIN` or `2NDPROG.BIN`; its sound driver is `Ver1.31 95/06/20`. (Disc files read only.)
- **Saturn Bomberman (Europe)** has a very small disc (27 files); the USA disc, with the same `GFS_SBL 2.10` and `SYS 2.21`, carries two sound-driver stamps (`Ver1.31` and `Ver2.04`).

## Signature sets we hold

IDA FLIRT signatures for the Sega libraries, in `Integrations\SegaSaturn\reference\symbols` (library name and function count read from each file's own header; no matching was run for this table):

| File | Library | Functions |
|---|---|---|
| `sbl60.sig` | Sega Basic Library 6.0 | 197 |
| `sbl601.sig` | Sega Basic Library 6.01 | 198 |
| `sgl20a.sig` | Sega 3D Game Library 2.0a | 311 |
| `sgl21.sig` | Sega 3D Game Library 2.1 | 38 |
| `sgl300.sig` | Sega 3D Game Library 3.00 | 349 |
| `sgl302j.sig` | Sega 3D Game Library 3.02j | 346 |

Note the version scheme: the stamps inside games (`GFS_SBL 2.10`) are the stamps of individual libraries and differ from the kit versions in these files' names (SBL 6.0x, SGL 2.0a to 3.0x). A disc's `GFS_SBL 2.10` is a library inside a kit; which kit release shipped it is not read from any file here.

## Indexes

<!-- indexes -->

### By SBL/SGL library stamp

| Key | Sets |
|---|---|
| GFS_SBL 2.10 (1996-02-01) | Saturn Bomberman (Europe); Saturn Bomberman (Japan) (SegaNet); Saturn Bomberman (USA) (1S) |
| GFS_SBL 2.14 (1997-04-11) | Dragon Force II - Kami Sarishi Daichi ni (Japan) (Rev A) |
| GFS_SGL 2.11 (1996-03-21) | Robo Pit (Europe) |
| STM_SBL 2.10 (1996-02-01) | Saturn Bomberman (Europe); Saturn Bomberman (Japan) (SegaNet); Saturn Bomberman (USA) (1S) |
| STM_SGL 2.11 (1996-03-21) | Robo Pit (Europe) |

### By sound driver

| Key | Sets |
|---|---|
| Ver1.31 95/06/20 | Blast Chamber (Europe) (En,Fr,De,Es,It) |
| Ver1.31 95/06/20; Ver2.04 96/01/25 | Saturn Bomberman (USA) (1S) |
| Ver2.04 96/01/25 | Robo Pit (Europe); Saturn Bomberman (Japan) (SegaNet) |
| not read | Dragon Force (Europe); Dragon Force (Japan) (Rev A) (10M); Dragon Force (USA); Dragon Force II - Kami Sarishi Daichi ni (Japan) (Rev A); Saturn Bomberman (Europe) |
<!-- /indexes -->
