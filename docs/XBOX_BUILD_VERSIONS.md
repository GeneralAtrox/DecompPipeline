# Original Xbox symbol sets: where to find which versions

A lookup table of the 5 original Xbox development symbol sets we hold (PDB, MAP and debug executable sets: Halo three times, Gauntlet Dark Legacy, Kelly Slater's Pro Surfer), so you can find the sets that match your game's XDK build and middleware, and that hold the data you need. Made with `targets/gamecube_ppc/tools/symbol_inventory.py --console xbox` (readers in `tools/symset_xbox.py`, table writer in `tools/symset_consoles.py`) on 2026-11-19.

**What this is made from.** Only facts read from the contents of the files: the library version records in each XBE header, the PDB and MAP contents, PE (Rich) header tool builds, library and object names, symbol counts, and middleware strings. Nothing is taken from file names except the set's own name. A field that could not be read says "not known" or "n/r". **It contains no symbol files, no name lists and no game code, and nothing from an XBE certificate except the title ID and version** (the signing and LAN keys are never read). The same facts are published as `XBOX_SYMBOL_SETS.csv` and `XBOX_SYMBOL_SETS.json` (stable field names, one row per set).

**The numbers.** 5 sets; 5 give an XDK build (read from XBE library records), 4 give function names (3 PDBs: Halo 2001-01-14, Gauntlet, Kelly Slater; and the Halo 2001-09-25 MAPs, 10,884 names without sizes), and the 3 PDBs carry sizes, types, locals and line numbers. Two PDBs (Halo 2001-01-14, Kelly Slater) match the executable in their folder by build id; Gauntlet's does not (see the notes after the table).

## How to use it

1. Read your game's XDK build from its `default.xbe` (next section): the build number of the `XAPILIB` record is the XDK build.
2. Find that number in "By XDK build" at the end. Sets on the same number link the same XDK libraries; the "Other XDK libs" column shows libraries stamped with a different number (Halo links Direct3D 3925 or 3948 against XAPILIB 3911).
3. In the table check what each set can give: **Fn names**, **Sizes** (the PDB gives a size per procedure; the MAPs do not), **Code** (the set holds the executable the symbols describe: `build id` means the PDB's id is found in it), **Lib/obj**, **SDK fns**, **CRT**, **Data**, **Debug**.
4. For middleware use the index at the end. A library name such as `binkxbox.lib` counts as a middleware hit; so does a version string in the image.

## Reading your own game's XDK build (no tool needed)

Every XBE header lists the XDK libraries the game was linked with, as 16-byte records. Read the count at `0x160` and the table address at `0x164` (addresses are virtual: subtract the base address stored at `0x104` to get a file offset). Each record is an 8-byte name, then major, minor and build as three 16-bit values, then a 16-bit flags word (the low 13 bits are the QFE number; `0x8000` marks a debug library):

```
XAPILIB  1.0.3911  QFE 1      D3D8  1.0.3925  QFE 1      DSOUND  1.0.3936  QFE 1
```

- `XAPILIB`, `XBOXKRNL`, `LIBC`/`LIBCMT` are the base libraries: their build number is the XDK build. Others (D3D8, D3DX8, DSOUND, XNET, WMVDEC, XGRAPHC, ...) can be newer when a game picked up a later fix for one library.
- The link time is at `0x114` (seconds since 1970), the debug path of the `.exe` at `0x14C`. A game with a PDB has an `NB10` record (the PDB's time stamp and age) in its PE or XBE: a PDB belongs to an executable when both match.
- The compiler is not stated in an XBE. A PE `.exe` carries a "Rich" header with the Microsoft tool builds (compilers, assembler, linker); a PDB's compile records here name only the assembler.

## Example

Your `default.xbe` lists `XAPILIB 3911` and `D3D8 3925`.
- **Same build:** "By XDK build" lists the three Halo sets at 3911 and the 2001-09-25 MAP set; the debug executables set (2001-10-12) carries no symbols but is the same XDK.
- **Nearest builds either side:** 4242 (Gauntlet Dark Legacy) and 4627 (Kelly Slater), both PDB sets with sizes, types and line numbers. Check them next: they are later, and every library in them carries the same build number.
- **Middleware:** if your game uses Bink (`binkxbox.lib`), the index lists the four sets that link it.

## Columns and legend

- **Set**, **Has** (file types, with a count when there is more than one), **Title ID** (from the XBE certificate).
- **XDK build (link date):** the `XAPILIB` build number and the XBE's link date. **Other XDK libs:** libraries whose build differs, with the number.
- **Compiler:** from a PE Rich header when the set holds an `.exe` (build number of the compiler products, number of objects, linker version), and the assembler the PDB names (MASM 6.15.8803). Where only a PDB is held the C/C++ compiler is not named in it: "not known".
- **Middleware:** library names and strings read from the images (a version where the string has one).
- **Fn names** `yes N`: N distinct function names (from the PDB's procedure records, or the MAP's function-marked entries). **Sizes:** yes when the symbols carry a size (a PDB procedure record); an MSVC link map gives only addresses, so it reads no.
- **Code:** `build id` the set holds the executable the PDB describes (its `NB10` time stamp and age are found in it); `same folder` a MAP and its XBE sit together (no build id proof); `no` nothing in the set to match against. **Lib/obj:** `lib+obj` the symbols say which library and which object each function came from.
- **SDK fns:** functions from the XDK libraries (xapilib, d3d8, d3dx8, dsound, xnet, xboxkrnl, xgraphics, xmv, wmvdec and so on). **CRT:** functions from the C/C++ runtime libraries (libc, libcmt, libcpmt, oldnames). **Data:** data symbols named. **Debug:** type records, local-variable records and the number of modules with line numbers. A MAP-only set reads `n/r` for the fields that need a PDB.

## Sets

<!-- sets -->
| Set | Has | Title ID | XDK build (link date) | Other XDK libs | Compiler | Middleware | Fn names | Sizes | Code | Lib/obj | SDK fns | CRT | Data | Debug |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Halo_2001-01-14_PDB | exe pdb xbe | 4D530004 | 3911 (2002-01-14) | D3D8 3948; DSOUND 3936; DSOUNDH 3937 | Rich header: build 9254 (706 objs), linker 7.00; MASM 6.15.8803 | bink; zlib | yes 2,854 | yes | build id | lib+obj | yes 1,947 | yes 897 | yes 105 | types 11,659, locals 7,039, lines in 284 of 833 modules |
| Halo_2001-09-25_MAP | exe mapx2 xbex2 | 4D530004 | 3911 (2001-09-25) | D3D8 3925; DSOUND 3936; DSOUNDH 3937 | Rich header: build 9254 (705 objs), linker 7.00 | bink; zlib | yes 10,884 | no | same folder | lib+obj | n/r | n/r | n/r | no |
| Halo_2276_2001-10-12_debug_executables | txt xbex3 | 4D530004 | 3911 (2001-10-12) | D3D8 3925; DSOUND 3936; DSOUNDH 3937 | not known | bink; zlib | no | no | no | no | n/r | n/r | n/r | no |
| GauntletDarkLegacy_2002-03-25_PDB | pdb xbe | 4D57000E | 4242 (2002-03-22) | none | MASM 6.15.8803; C/C++ compiler not named in the PDB | zlib | yes 6,992 | yes | no | lib+obj | yes 1,924 | yes 830 | yes 1,422 | types 21,836, locals 19,093, lines in 433 of 499 modules |
| KellySlaterProSurfer_beta_2002-07-14_PDB | pdb xbe | 027A27C2 | 4627 (2002-07-14) | none | MASM 6.15.8803; C/C++ compiler not named in the PDB | bink; ngl_xbox lib; nsl_xbox lib; nvl_xbox lib | yes 10,612 | yes | build id | lib+obj | yes 1,433 | yes 632 | yes 1,354 | types 65,166, locals 21,955, lines in 299 of 404 modules |
<!-- /sets -->

Notes on single sets, read from the files:
- **Halo 2001-01-14:** the set's folder name says 2001-01-14, but the XBE's link date and the PDB's time stamp both read 2002-01-14; the PDB's build id is found in both `cachebeta.exe` and `cachebeta.xbe`.
- **Gauntlet Dark Legacy:** `shell3D.pdb` (linked 2002-03-23) does not carry the build id of the `Default.xbe` (linked 2002-03-22) in the folder; both name `shell3D.exe`. It is a neighbouring link of the same program, not the PDB of that XBE.
- **Halo 2001-09-25 and 2001-10-12:** XDK builds are read from `cache.xbe`/`cachebeta.xbe` (and `final.xbe`); the 2001-10-12 set holds only executables (no maps), so it reads no symbols.
- **Other folders in the same reference folder** (not Xbox sets, not in the table): two GameCube NHL Hitz 2003 ELFs (USA and Europe) with function names and sizes (about 6,750), object names, no SDK build string (the older `Dolphin OS $Revision: 54 $` only) and no DWARF, which belong in the GameCube table's family; one PS2 NHL Hitz Pro set (a 25 MB ELF and an ELF.MAP) not surveyed here.

## Indexes

<!-- indexes -->

### By XDK build (XAPILIB build number)

| Key | Sets |
|---|---|
| XAPILIB 3911 | Halo_2001-01-14_PDB; Halo_2001-09-25_MAP; Halo_2276_2001-10-12_debug_executables |
| XAPILIB 4242 | GauntletDarkLegacy_2002-03-25_PDB |
| XAPILIB 4627 | KellySlaterProSurfer_beta_2002-07-14_PDB |

### By middleware

| Key | Sets |
|---|---|
| bink | Halo_2001-01-14_PDB; Halo_2001-09-25_MAP; Halo_2276_2001-10-12_debug_executables; KellySlaterProSurfer_beta_2002-07-14_PDB |
| ngl_xbox lib | KellySlaterProSurfer_beta_2002-07-14_PDB |
| nsl_xbox lib | KellySlaterProSurfer_beta_2002-07-14_PDB |
| nvl_xbox lib | KellySlaterProSurfer_beta_2002-07-14_PDB |
| zlib | GauntletDarkLegacy_2002-03-25_PDB; Halo_2001-01-14_PDB; Halo_2001-09-25_MAP; Halo_2276_2001-10-12_debug_executables |
<!-- /indexes -->
