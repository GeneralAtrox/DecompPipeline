# Dreamcast symbol sets: where to find which versions

A lookup table of the 7 Dreamcast development symbol sets we hold (ELF files with DWARF 1, Hitachi, GNU and CodeWarrior link maps, `.sym` files, and one Windows CE executable), so you can find the sets that match your game's Sega SDK build and middleware, and that hold the data you need. Made with `targets/gamecube_ppc/tools/symbol_inventory.py --console dreamcast` (readers in `tools/symset_sega.py`, table writer in `tools/symset_consoles.py`) on 2026-11-19.

**What this is made from.** Only facts read from the contents of the files: the library build stamps in the binaries, compiler and linker strings, the link-map kind, library and object names, symbol counts and section names. Nothing is taken from file names except the set's own name (which carries its date). A field that could not be read says "not known". **It contains no symbol files, no name lists and no game code.** The same facts are published as `DREAMCAST_SYMBOL_SETS.csv` and `DREAMCAST_SYMBOL_SETS.json` (stable field names, one row per set).

**The numbers.** 7 sets (the reference folder holds 7 set folders and 2 note files; the brief said 9). 6 carry Sega SDK stamps (Shinobi in all 6, KAMUI/KAMUI 2 in 5, Ninja or Nindows in 2); the seventh is a Windows CE executable (Heroes of Might and Magic III) with no Sega library stamps. 6 sets have function names (from a map, a `.sym` file or an ELF symbol table); 4 carry DWARF 1 with line numbers. The Shinobi builds seen are 1.42 (Apr 1999), 1.43 (Jul 1999) and 1.62 / 1.62zr (Aug 1999).

## How to use it

1. Read your game's Sega library stamps from its `1ST_READ.BIN` (next section). Note the Shinobi version and KAMUI (or KAMUI 2) version.
2. Find those versions in "By SDK library and version" at the end. Sets on the same Shinobi build link the same system library; then look at the nearest builds on either side.
3. In the table check what each set can give: **Fn names**, **Sizes** (an ELF symbol table gives a size per function; the maps and `.sym` files do not), **Data**, **DWARF**, **Lines**.
4. For middleware (CRI ADX and Sofdec, Dolby, Bink, Miles) use the middleware index.
5. The set's folder name is in the **Set** column exactly as it is named.

## Reading your own game's SDK build (no tool needed)

The Sega libraries stamp their name, version and build date into the executable as text. Search `1ST_READ.BIN` (or an `.elf`) with `strings` or a hex editor for `Ver` and `Build:`:

```
Shinobi Ver 1.62 Build:Aug 30 1999 ...          (system library)
KAMUI Ver 1.11 Build:...                          (graphics library; KAMUI 2 prints its own form)
Ninja Ver ...  /  Nindows Ver ...                 (Ninja model library; Nindows is its Windows CE flavour)
```

- Each library has its own stamp: `<Name> Ver <version> Build:<Mon> <D> <YYYY> <HH:MM:SS>`. Libraries are built at different times, so the dates differ within one game; the Shinobi stamp is the best single key.
- Other Sega libraries (`sy*`, `pd*`, `kd*`) and CRI's ADX, Sofdec and LSC libraries stamp themselves the same way; the table counts them in "Other libs" and the JSON lists them.
- The compiler is not stamped in the binary. Maps and objects show it: a Hitachi `H SERIES LINKAGE EDITOR` map with `SHC\INCLUDE` paths is the Hitachi SHC toolchain; a CodeWarrior `Link Map of` file with `MSL C++` paths is Metrowerks; a `GCC: (GNU) 2.9-SYSDEV-sh4p2` string in an ELF is the SN Systems GCC for SH-4.
- A Windows CE game is an SH-4 PE `.exe` (subsystem 9): its tool is Microsoft's, not Sega's.

## Example

Your `1ST_READ.BIN` says `Shinobi Ver 1.43` and `KAMUI Ver 1.10`.
- **Same build:** "By SDK library and version" lists the three sets on Shinobi 1.43 (Dragons Blood, Ecco the Dolphin, NHL 2K); Dragons Blood also carries KAMUI 1.10.0.1.
- **Nearest builds either side:** Shinobi 1.42 (NFL 2K, a GNU-linked map set) and 1.62 (Space Channel 5 and Crazy Taxi).
- **Middleware:** if your game uses CRI ADX, the index lists the four sets that link it.

## Columns and legend

- **Set**, **Has** (file types, with a count when there is more than one).
- **SDK libraries:** the Shinobi, KAMUI, Ninja and Nindows stamps with their version and build month. **Other libs:** how many further library stamps were read (all in the JSON).
- **Compiler:** compiler strings in the ELF or executable, else the toolchain the include paths show; "not stated" with the map kind where the files hold nothing more. **Maps:** the kind of link map.
- **Fn names** `yes N`: distinct function names from a map, a `.sym` file or an ELF symbol table, the largest of them. **Sizes:** yes when an ELF symbol carries a size. **Data:** data symbols named. **DWARF:** `DWARF 1` sections in an ELF (presence only). **Lines:** a line-number section.
- **Middleware:** found by strings in the files.

## Sets

<!-- sets -->
| Set | Has | SDK libraries (version, build date) | Other libs | Compiler | Maps | Middleware | Fn names | Sizes | Data | DWARF | Lines |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Crazy_Taxi_2000_04_28_MAP_SYM | abs binx2 gomi map mot none sym | KAMUI 1.11.0.1 (Jul 1999); Shinobi 1.62zr (Aug 1999) | +30 libs | not stated; map is Hitachi linkage editor 6.0 | Hitachi linkage editor 6.0 | cri adx; cri middleware | yes 4,229 | no | yes 1,885 | no | no |
| Dragons_Blood_2000_04_03_DWARF | elf | KAMUI 1.10.0.1 (Jul 1999); Shinobi 1.43 (Jul 1999) | +12 libs | CodeWarrior; Hitachi SH includes | none | none found | yes 7,431 | yes | yes 10,061 | DWARF 1 | yes |
| Ecco_the_Dolphin_Defender_of_the_Future_2000_06_01_DWARF_MAP | bin elf map txt | Shinobi 1.43 (Jul 1999); kamui2 16,3,2,4 (Jul 1999) | +17 libs | CodeWarrior | CodeWarrior link map | cri adx; cri middleware; dolby | yes 12,091 | yes | yes 8,765 | DWARF 1 | yes |
| NFL_2K_1999_12_27_MAP | binx4 map | KAMUI 1.06 (Feb 1999); Ninja 01000086 (Apr 1999); Shinobi 1.42 (Apr 1999) | +30 libs | not stated; map is GNU ld map | GNU ld map | cri adx; cri middleware; cri sofdec | yes 8,257 | no | no | no | no |
| NHL_2K_2000_01_04_DWARF | elf | Shinobi 1.43 (Jul 1999) | +17 libs | GCC: (GNU) 2.9-SYSDEV-sh4p2_final-991006a; GNU C++ 2.9-SYSDEV-sh4p2_final-991006a | none | none found | yes 4,404 | yes | yes 1,751 | DWARF 1 | yes |
| Space_Channel_5_1999_08_11_DWARF | elfx5 mapx5 | KAMUI 1.11.0.0 (Jul 1999); Nindows 01000078 (Jul 1999); Ninja 01100103 (Aug 1999); Shinobi 1.62 (Aug 1999) | +42 libs | Hitachi SHC (include path) | Hitachi linkage editor 6.0A | cri adx; cri middleware; cri sofdec | yes 3,275 | no | yes 1,555 | DWARF 1 | yes |
| Heroes_of_Might_and_Magic_III_The_Restoration_of_Erathia_2000_08_11 | exe | Windows CE (SH4 Windows CE link 6.01) | none | Microsoft (R) 32-bit C/C++ Optimizing Compiler Version 12.17.8370 | none | bink; miles | no | no | no | no | no |
<!-- /sets -->

Notes on single sets, read from the files:
- **Crazy Taxi** and **NFL 2K** are map-and-binary sets: the maps give function names without sizes; Crazy Taxi's Hitachi list names about 1,900 data symbols as well.
- **Space Channel 5** holds five ELFs and five Hitachi maps; its ELFs have DWARF 1 and line numbers but no symbol table, so its names come from the maps.
- **Heroes of Might and Magic III** is a Windows CE SH-4 executable linked with Microsoft's tools (compiler 12.17.8370), not a Katana/Shinobi title.

## Indexes

<!-- indexes -->

### By SDK library and version

| Key | Sets |
|---|---|
| KAMUI 1.06 (Feb 1999) | NFL_2K_1999_12_27_MAP |
| KAMUI 1.10.0.1 (Jul 1999) | Dragons_Blood_2000_04_03_DWARF |
| KAMUI 1.11.0.0 (Jul 1999) | Space_Channel_5_1999_08_11_DWARF |
| KAMUI 1.11.0.1 (Jul 1999) | Crazy_Taxi_2000_04_28_MAP_SYM |
| Nindows 01000078 (Jul 1999) | Space_Channel_5_1999_08_11_DWARF |
| Ninja 01000086 (Apr 1999) | NFL_2K_1999_12_27_MAP |
| Ninja 01100103 (Aug 1999) | Space_Channel_5_1999_08_11_DWARF |
| Shinobi 1.42 (Apr 1999) | NFL_2K_1999_12_27_MAP |
| Shinobi 1.43 (Jul 1999) | Dragons_Blood_2000_04_03_DWARF; Ecco_the_Dolphin_Defender_of_the_Future_2000_06_01_DWARF_MAP; NHL_2K_2000_01_04_DWARF |
| Shinobi 1.62 (Aug 1999) | Space_Channel_5_1999_08_11_DWARF |
| Shinobi 1.62zr (Aug 1999) | Crazy_Taxi_2000_04_28_MAP_SYM |
| kamui2 16,3,2,4 (Jul 1999) | Ecco_the_Dolphin_Defender_of_the_Future_2000_06_01_DWARF_MAP |
| not known | Heroes_of_Might_and_Magic_III_The_Restoration_of_Erathia_2000_08_11 |

### By middleware

| Key | Sets |
|---|---|
| bink | Heroes_of_Might_and_Magic_III_The_Restoration_of_Erathia_2000_08_11 |
| cri adx | Crazy_Taxi_2000_04_28_MAP_SYM; Ecco_the_Dolphin_Defender_of_the_Future_2000_06_01_DWARF_MAP; NFL_2K_1999_12_27_MAP; Space_Channel_5_1999_08_11_DWARF |
| cri middleware | Crazy_Taxi_2000_04_28_MAP_SYM; Ecco_the_Dolphin_Defender_of_the_Future_2000_06_01_DWARF_MAP; NFL_2K_1999_12_27_MAP; Space_Channel_5_1999_08_11_DWARF |
| cri sofdec | NFL_2K_1999_12_27_MAP; Space_Channel_5_1999_08_11_DWARF |
| dolby | Ecco_the_Dolphin_Defender_of_the_Future_2000_06_01_DWARF_MAP |
| miles | Heroes_of_Might_and_Magic_III_The_Restoration_of_Erathia_2000_08_11 |
<!-- /indexes -->
