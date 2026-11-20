# Nintendo 64 retail builds: SDK release, compiler family and microcode per game

A lookup table of 66 Nintendo 64 ROM images: which libultra (SDK) release each was linked against, which compiler family built the game code, and which graphics microcode it carries. Every row was produced unattended by [N64 Decomp Kit](https://github.com/TheGh0stShip/N64-Decomp-Kit) from the ROM alone, with no symbols or source. No ROM content is included: only titles, IDs, hashes and version facts.

The presentation is inspired by DecompPipeline's public build-version references for [Xbox 360](https://github.com/GeneralAtrox/DecompPipeline/blob/master/docs/XBOX360_BUILD_VERSIONS.md) and [GameCube](https://github.com/GeneralAtrox/DecompPipeline/blob/master/docs/GAMECUBE_BUILD_VERSIONS.md). The N64 evidence and conclusions below were produced independently from the surveyed ROMs using the methods documented here.

**Scope.** These are the 66 titles the kit has surveyed so far, not the whole library, and mostly USA releases. Unlike symbol-set reports for later consoles, N64 retail ROMs carry almost no debug information, so most of this is recovered from the code itself. Read the legend before relying on a column.

**The numbers.** SDK release identified in 66 of 66; microcode version strings read in 62; game code mostly GCC (KMC) in 50 and mostly SGI IDO in 16; 3 have a packed program image.

Also published, one row per title with the same fields: `N64_BUILD_VERSIONS.csv` and `N64_BUILD_VERSIONS.json` (the JSON holds every microcode string exactly as read and the full list of SDK releases that fit).

## Reading your own game's microcode (no tool needed)

Graphics and audio microcode stamp a version into the ROM as text. Search a big-endian (`.z64`) ROM for `RSP Gfx ucode` or `RSP SW Version` with a hex editor or `strings`:

```
RSP Gfx ucode F3DEX       fifo 2.08  Yoshitaka Yasumoto 1999 Nintendo.
RSP SW Version: 2.0H, 02-12-97
```

The first is an F3DEX-family microcode with its version. The second is the older Fast3D software version line, whose `2.0H` also names the SDK release it shipped with. A game often carries several. A game that compresses its data may hide them.

## How each column was obtained

| Column | Source | How far to trust it |
|---|---|---|
| Game, ID, Rev, Size, SHA-1 | ROM file and header | read directly |
| Microcode | version strings in the ROM | read directly; absent strings do not prove absence |
| SDK (libultra) | library functions located in the game by comparing its code with builds of each libultra release; the releases whose functions fit are listed | evidence from code. Releases whose code is identical for the functions found cannot be told apart, so `2.0D-I` is a real range. |
| SDK fns | number of library functions identified that way | a count; more functions means a firmer SDK answer |
| Compiler | share of the game's functions whose code shape fits each compiler family: `kmc` = GCC 2.7.2 as shipped by KMC, `ido` = SGI IDO | inferred. It is a family, not a version or flags, and code from other compilers (SN Systems GCC, GCC 2.8, C++) is counted under the nearest family. |
| Functions | functions found in the program image | a count from automatic discovery |
| Middleware | names found as text in the ROM | weak: a string only |

`libgultra` is the GCC-built SDK and `libultra` the IDO-built one; the table shows the release (`_rom`) variant where found.

## Games

| Game | ID | Rev | Size | SDK (libultra) | SDK fns | Compiler | Functions | Microcode | Middleware | SHA-1 |
|---|---|---|---|---|---|---|---|---|---|---|
| 40 Winks (USA) (Piko Interactive) | N4WE | 0 | 32 MB | libgultra_rom 2.0J | 257 | kmc 95%, ido 5% | 1552 | F3DEX.NoN 2.08 fifo | - | `420f0ed3b67f` |
| AeroGauge (USA) | NAGE | 0 | 8 MB | libultra_rom 2.0D-I | 272 | ido 99% | 1212 | F3DEX 1.23, F3DEX.NoN 1.23 | - | `77626171c35f` |
| Armorines - Project S.W.A.R.M. (USA) (packed) | NARE | 0 | 16 MB | libgultra_rom 2.0D-J | 48 | kmc 94%, ido 6% | 4270 | F3DEX.NoN 2.05 fifo, L3DEX 2.05 fifo | - | `d9f55acf77a5` |
| Asteroids Hyper 64 (USA) | NAHE | 0 | 4 MB | libgultra_rom 2.0J | 243 | kmc 98% | 1312 | F3DEX 2.06 fifo, L3DEX 2.06 fifo | - | `329f4d560b0b` |
| Automobili Lamborghini (USA) | NLCE | 0 | 4 MB | libultra_rom 2.0D-I | 246 | ido 90%, kmc 10% | 624 | F3DEX 1.21 | - | `78b36b480404` |
| Bassmasters 2000 (USA) | NB4E | 0 | 12 MB | libgultra_rom 2.0J | 146 | kmc 94%, ido 6% | 830 | F3DEX 2.08 fifo | - | `946b3e08a1a4` |
| Batman Beyond - Return of the Joker (USA) | NJQE | 0 | 4 MB | libgultra_rom 2.0K-L | 254 | kmc 92%, ido 8% | 1574 | F3DEX 2.08 fifo | - | `f73823582509` |
| Beetle Adventure Racing! (USA) (En,Fr,De) | NNSE | 0 | 16 MB | libultra_rom 2.0D-I | 236 | ido 86%, kmc 14% | 471 | F3DEX.NoN 2.07 fifo, F3DEX.NoN 2.07 xbus, F3DEX.Rej 2.07 fifo, F3DEX.Rej 2.07 xbus | - | `e5ab4d226c08` |
| Big Mountain 2000 (USA) | NMUE | 0 | 12 MB | libultra_rom 2.0J | 222 | ido 98% | 713 | F3DEX.NoN 2.06 fifo, F3DLX.Rej 2.06 fifo, S2DEX 1.07 | - | `e28f3ebfb7bc` |
| Brunswick Circuit Pro Bowling (USA) | NOWE | 0 | 8 MB | libgultra 2.0J | 303 | kmc 94%, ido 6% | 1792 | F3DLX.NoN 1.23 | - | `91f6f7843a74` |
| Bug's Life, A (USA) | NBYE | 0 | 12 MB | libgultra_rom 2.0D-I | 205 | kmc 79%, ido 21% | 888 | F3DEX 1.23, F3DLX 1.23 | - | `697c1e895fc8` |
| Carmageddon 64 (USA) | NCDE | 0 | 16 MB | libgultra_rom 2.0D-I | 233 | kmc 95% | 1145 | F3DEX.NoN 2.05 fifo, S2DEX 1.06 | - | `dc7495093fb9` |
| Charlie Blast's Territory (USA) | NCBE | 0 | 4 MB | libgultra 2.0D-I | 280 | kmc 74%, ido 26% | 899 | F3DEX 1.23 | - | `30cd3d202ae7` |
| Chopper Attack (USA) | NCHE | 0 | 8 MB | libultra_rom 2.0D-I | 231 | ido 96% | 893 | SW 2.0H | - | `1e3f1c94c806` |
| Clay Fighter - Sculptor's Cut (USA) | NC2E | 0 | 16 MB | libgultra_rom 2.0D-I | 216 | kmc 94%, ido 6% | 1025 | F3DEX.NoN 1.21, F3DLP.Rej 1.21, F3DLX.NoN 1.21, F3DLX.Rej 1.21 | - | `28697fba130d` |
| Clay Fighter 63 1-3 (USA) | NCFE | 0 | 12 MB | libultra_rom 2.0D-I | 216 | ido 92%, kmc 8% | 981 | F3DEX.NoN 1.21, F3DLP.Rej 1.21, F3DLX.NoN 1.21, F3DLX.Rej 1.21 | - | `036873ab0d31` |
| Cruis'n Exotica (USA) | NXOE | 0 | 16 MB | libgultra_rom 2.0K-L | 265 | kmc 89%, ido 11% | 936 | F3DEX 2.08 fifo | - | `428f53a06010` |
| Cruis'n USA (USA) (Rev 2) | NCUE | 2 | 8 MB | libultra_rom 2.0D-H | 127 | ido 98% | 1155 | SW 2.0D | - | `54a875ee0b48` |
| CyberTiger (USA) | NT4E | 0 | 16 MB | libgultra_rom 2.0D-I | 231 | kmc 96% | 1412 | F3DEX 2.04 fifo, F3DEX.NoN 2.04 fifo, L3DEX 2.04 fifo, S2DEX 2.04 fifo | - | `14e0635ccc2a` |
| Destruction Derby 64 (USA) | NDEE | 0 | 16 MB | libgultra_rom 2.0J | 211 | kmc 95% | 1555 | F3DEX 2.07 fifo, S2DEX 2.07 fifo | - | `a2c0799e1356` |
| Duck Dodgers Starring Daffy Duck (USA) (En,Fr,Es) | NDUE | 0 | 16 MB | libultra_rom 2.0J-K | 130 | ido 87%, kmc 13% | 474 | F3DEX.NoN 2.08 fifo | - | `2c840e2991d6` |
| Earthworm Jim 3D (USA) | NJME | 0 | 16 MB | libgultra 2.0J | 280 | kmc 93%, ido 7% | 1342 | F3DEX 1.23 | - | `eab14f23640c` |
| Forsaken 64 (USA) | NFOE | 0 | 8 MB | libgultra_rom 2.0D-I | 268 | kmc 87%, ido 13% | 439 | F3DEX 1.23, L3DEX 1.23 | - | `7c670b224acf` |
| Hercules - The Legendary Journeys (USA) | NHCE | 0 | 16 MB | libgultra 2.0D-I | 289 | kmc 86%, ido 14% | 1511 | F3DEX 1.23, F3DEX 2.06 fifo, F3DEX.Rej 2.06 fifo, F3DLP.Rej 1.23, F3DLX.Rej 2.06 fifo | - | `5c3c4345be50` |
| Jeopardy! (USA) | NJOE | 0 | 4 MB | libultra_rom 2.0D-I | 226 | kmc 89%, ido 11% | 807 | SW 2.0H | - | `c5bcf3eff6bc` |
| John Romero's Daikatana (USA) | NDWE | 0 | 16 MB | libgultra_rom 2.0K-L | 257 | kmc 90%, ido 10% | 1123 | F3DEX 2.08 fifo | - | `08709013d512` |
| Killer Instinct Gold (USA) (Rev 2) | NKIE | 2 | 12 MB | libultra_rom 2.0D-I | 56 | ido 97% | 120 | none read | - | `aaeb492b1e53` |
| Knife Edge - Nose Gunner (USA) | NKEE | 0 | 8 MB | libultra_rom 2.0D-H | 190 | ido 93%, kmc 7% | 1160 | F3DEX 2.05 fifo, F3DLX.Rej 2.05 fifo | - | `b247167e37e7` |
| Knockout Kings 2000 (USA) | NKKE | 0 | 16 MB | libgultra_rom 2.0D-I | 208 | kmc 78%, ido 22% | 843 | none read | - | `ae7229676da9` |
| Madden Football 64 (USA) (packed) |  |  | 12 MB | libultra_rom 2.0D-I | 134 | kmc 83%, ido 17% | 1377 | none read | - | `b0de34b759f1` |
| Midway's Greatest Arcade Hits - Volume 1 (USA) | NAIE | 0 | 4 MB | libgultra 2.0K-L | 40 | kmc 98% | 796 | SW 2.0H | - | `e24c61aaa5cf` |
| Milo's Astro Lanes (USA) | NBRE | 0 | 4 MB | libultra_rom 2.0D-H | 183 | kmc 78%, ido 22% | 1646 | F3DLX 1.21 | - | `7a0246e367ff` |
| Monopoly (USA) | NMOE | 0 | 8 MB | libgultra_rom 2.0J | 265 | kmc 99% | 1018 | F3DEX 2.08 fifo, F3DEX.NoN 2.08 fifo, F3DEX.Rej 2.08 fifo, F3DLX.Rej 2.08 fifo, L3DEX 2.08 fifo, S2DEX 2.08 fifo | - | `fd724bdc323a` |
| Namco Museum 64 (USA) | NNME | 0 | 4 MB | libgultra_rom 2.0J | 127 | kmc 97% | 1770 | none read | - | `a934e08f8077` |
| New Tetris, The (USA) | NRIE | 0 | 12 MB | libultra_rom 2.0D-H | 210 | ido 93%, kmc 7% | 1591 | F3DEX.NoN 2.05 fifo | - | `83fff25e8218` |
| NFL Blitz (USA) | NBZE | 0 | 16 MB | libgultra_rom 2.0J | 205 | kmc 93%, ido 7% | 1699 | F3DEX 2.05 fifo, F3DLX.Rej 2.05 fifo | - | `2853afd9e38d` |
| NFL Blitz 2000 (USA) (Rev 1) | NBIE | 1 | 16 MB | libgultra_rom 2.0J | 257 | kmc 95% | 1376 | F3DEX 2.08 fifo, F3DLX.Rej 2.08 fifo | - | `3754c8fdf5cd` |
| Penny Racers (USA) | NCRE | 0 | 8 MB | libgultra_rom 2.0D-I | 248 | kmc 93%, ido 7% | 663 | F3DLX.NoN 1.23, L3DEX 1.23 | - | `1d4fce8ad6b1` |
| Pilotwings 64 (USA) | NPWE | 0 | 8 MB | libultra_rom 2.0D-I | 169 | ido 94%, kmc 6% | 1970 | SW 2.0D | - | `ec771aedf54e` |
| Power Rangers - Lightspeed Rescue (USA) | NPUE | 0 | 12 MB | libgultra_rom 2.0K-L | 152 | kmc 97% | 822 | F3DEX 2.08 xbus | - | `9c0b970dfa34` |
| Powerpuff Girls, The - Chemical X-Traction (USA) | NPQE | 0 | 8 MB | libgultra 2.0J | 259 | kmc 86%, ido 14% | 1195 | F3DEX 1.23 | - | `88e2032997cb` |
| Quake II (USA) | NQ2E | 0 | 12 MB | libgultra_rom 2.0D-I | 191 | kmc 98% | 1465 | F3DEX.NoN 1.23, F3DLP.Rej 1.23 | - | `2edb00e602e7` |
| Quest 64 (USA) | NETE | 0 | 16 MB | libultra_rom 2.0D-I | 257 | ido 98% | 847 | F3DEX 1.23 | - | `91b96e938c6d` |
| Razor Freestyle Scooter (USA) | NFQE | 0 | 8 MB | libgultra_rom 2.0K-L | 279 | kmc 96% | 1475 | F3DEX 2.08 fifo, F3DEX.NoN 2.08 fifo, F3DEX.Rej 2.08 fifo, F3DLX.Rej 2.08 fifo, L3DEX 2.08 fifo, S2DEX 2.08 fifo | - | `d107e8d28a26` |
| Ready 2 Rumble Boxing (USA) | NRDE | 0 | 32 MB | libgultra 2.0D-I | 304 | kmc 95% | 1801 | F3DLX 1.23, L3DEX 1.23 | - | `e60fedd535a7` |
| Ready 2 Rumble Boxing - Round 2 (USA) | N22E | 0 | 32 MB | libgultra 2.0D-I | 304 | kmc 95% | 2224 | F3DLX 1.23, L3DEX 1.23 | - | `42ad558c20dd` |
| Robotron 64 (USA) | NRXE | 0 | 8 MB | libultra_rom 2.0D-H | 186 | ido 98% | 1542 | F3DEX 1.21, F3DLP.Rej 1.21 | - | `285c720f6426` |
| Scooby-Doo! - Classic Creep Capers (USA) (Rev 1) | NSYE | 1 | 16 MB | libgultra_rom 2.0K-L | 248 | kmc 96% | 934 | F3DEX 2.08 fifo, L3DEX 2.08 fifo | - | `44dc4f2a148e` |
| Sesame Street - Elmo's Letter Adventure (USA) | NELE | 0 | 8 MB | libgultra 2.0D-I | 302 | kmc 96% | 963 | F3DEX 1.23 | Factor 5 | `97777ca06f4e` |
| Sesame Street - Elmo's Number Journey (USA) | NENE | 0 | 8 MB | libgultra 2.0D-I | 302 | kmc 96% | 972 | F3DEX 1.23 | Factor 5 | `7195ea96d9fe` |
| Snowboard Kids 2 (USA) | NK2E | 0 | 16 MB | libgultra_rom 2.0J | 191 | kmc 94%, ido 6% | 2789 | F3DEX 2.05 fifo, S2DEX 2.05 fifo | - | `5ce896fd6427` |
| South Park - Chef's Luv Shack (USA) (packed) | NCYE | 0 | 16 MB | libgultra_rom 2.0D-I | 125 | kmc 92%, ido 8% | 1204 | none read | - | `0344b569e841` |
| South Park Rally (USA) | NPRE | 0 | 16 MB | libgultra_rom 2.0K-L | 132 | kmc 95%, ido 5% | 1636 | F3DEX 2.08 xbus, L3DEX 2.08 xbus | - | `e0b9e2358b09` |
| Space Invaders (USA) | NIVE | 0 | 8 MB | libgultra_rom 2.0J | 216 | kmc 93%, ido 7% | 1592 | F3DEX 2.08 fifo, F3DLX.Rej 2.08 fifo, L3DEX 2.08 fifo | - | `6850205eace5` |
| Starshot - Space Circus Fever (USA) (En,Fr,Es) | NSCE | 0 | 12 MB | libgultra_rom 2.0J | 187 | kmc 91%, ido 9% | 1570 | F3DEX.NoN 2.03 fifo, F3DLX.Rej 2.03 fifo | - | `7def46651f55` |
| Tarzan (USA) | NTAE | 0 | 16 MB | libgultra_rom 2.0K-L | 143 | kmc 94%, ido 6% | 1061 | F3DEX 2.08 fifo | - | `44c63a89e1e8` |
| Tetrisphere (USA) | NTPE | 0 | 8 MB | libultra_rom 2.0D-H | 170 | ido 94%, kmc 6% | 1050 | SW 2.0H | - | `32adaa274cee` |
| Tigger's Honey Hunt (USA) | NT9E | 0 | 16 MB | libgultra_rom 2.0K-L | 212 | kmc 83%, ido 17% | 2424 | F3DEX 2.08 fifo | - | `98db3a0f9025` |
| Tom and Jerry in Fists of Furry (USA) | NTJE | 0 | 12 MB | libgultra 2.0J | 280 | kmc 86%, ido 14% | 1185 | F3DEX 1.23 | - | `d8980a34ece4` |
| Tom Clancy's Rainbow Six (USA) | NR6E | 0 | 16 MB | libgultra_rom 2.0D-I | 245 | kmc 80%, ido 20% | 2007 | F3DEX 2.07 fifo, F3DEX.NoN 2.07 fifo, L3DEX 2.07 fifo | - | `f785674c670c` |
| Virtual Chess 64 (USA) (En,Fr,Es) | NVCE | 0 | 4 MB | libultra_rom 2.0D-I | 259 | ido 85%, kmc 15% | 970 | F3DEX 1.21 | - | `b3f84937813e` |
| Virtual Pool 64 (USA) | NVRE | 0 | 4 MB | libgultra_rom 2.0J | 199 | kmc 94%, ido 6% | 1871 | F3DEX 1.23, F3DLP.Rej 1.23, L3DEX 1.23 | - | `4fed63f7ee35` |
| WCW Nitro (USA) | NW3E | 0 | 12 MB | libgultra 2.0D-I | 283 | kmc 91%, ido 9% | 820 | F3DLX 1.23, SW 2.0H | Bink | `1f3b1a9e3485` |
| Wetrix (USA) (En,Fr,De,Es,It,Nl) | NWTE | 0 | 8 MB | libultra_rom 2.0D-H | 215 | kmc 77%, ido 23% | 901 | F3DEX.NoN 1.21 | - | `6e40312cf88a` |
| Wheel of Fortune (USA) | NWFE | 0 | 4 MB | libultra_rom 2.0D-I | 238 | kmc 91%, ido 9% | 777 | SW 2.0H | - | `b51b28660699` |
| Wipeout 64 (USA) | NWPE | 0 | 8 MB | libultra_rom 2.0D-I | 210 | kmc 85%, ido 15% | 983 | F3DLX 1.21, L3DEX 1.21, SW 2.0H | - | `54e4795aba4f` |

## Indexes

### By SDK release

- **libgultra 2.0D-I** (7): Charlie Blast's Territory (USA); Hercules - The Legendary Journeys (USA); Ready 2 Rumble Boxing (USA); Ready 2 Rumble Boxing - Round 2 (USA); Sesame Street - Elmo's Letter Adventure (USA); Sesame Street - Elmo's Number Journey (USA); WCW Nitro (USA)
- **libgultra 2.0J** (4): Brunswick Circuit Pro Bowling (USA); Earthworm Jim 3D (USA); Powerpuff Girls, The - Chemical X-Traction (USA); Tom and Jerry in Fists of Furry (USA)
- **libgultra 2.0K-L** (1): Midway's Greatest Arcade Hits - Volume 1 (USA)
- **libgultra_rom 2.0D-I** (10): Bug's Life, A (USA); Carmageddon 64 (USA); Clay Fighter - Sculptor's Cut (USA); CyberTiger (USA); Forsaken 64 (USA); Knockout Kings 2000 (USA); Penny Racers (USA); Quake II (USA); South Park - Chef's Luv Shack (USA); Tom Clancy's Rainbow Six (USA)
- **libgultra_rom 2.0D-J** (1): Armorines - Project S.W.A.R.M. (USA)
- **libgultra_rom 2.0J** (12): 40 Winks (USA) (Piko Interactive); Asteroids Hyper 64 (USA); Bassmasters 2000 (USA); Destruction Derby 64 (USA); Monopoly (USA); Namco Museum 64 (USA); NFL Blitz (USA); NFL Blitz 2000 (USA) (Rev 1); Snowboard Kids 2 (USA); Space Invaders (USA); Starshot - Space Circus Fever (USA) (En,Fr,Es); Virtual Pool 64 (USA)
- **libgultra_rom 2.0K-L** (9): Batman Beyond - Return of the Joker (USA); Cruis'n Exotica (USA); John Romero's Daikatana (USA); Power Rangers - Lightspeed Rescue (USA); Razor Freestyle Scooter (USA); Scooby-Doo! - Classic Creep Capers (USA) (Rev 1); South Park Rally (USA); Tarzan (USA); Tigger's Honey Hunt (USA)
- **libultra_rom 2.0D-H** (7): Cruis'n USA (USA) (Rev 2); Knife Edge - Nose Gunner (USA); Milo's Astro Lanes (USA); New Tetris, The (USA); Robotron 64 (USA); Tetrisphere (USA); Wetrix (USA) (En,Fr,De,Es,It,Nl)
- **libultra_rom 2.0D-I** (13): AeroGauge (USA); Automobili Lamborghini (USA); Beetle Adventure Racing! (USA) (En,Fr,De); Chopper Attack (USA); Clay Fighter 63 1-3 (USA); Jeopardy! (USA); Killer Instinct Gold (USA) (Rev 2); Madden Football 64 (USA); Pilotwings 64 (USA); Quest 64 (USA); Virtual Chess 64 (USA) (En,Fr,Es); Wheel of Fortune (USA); Wipeout 64 (USA)
- **libultra_rom 2.0J** (1): Big Mountain 2000 (USA)
- **libultra_rom 2.0J-K** (1): Duck Dodgers Starring Daffy Duck (USA) (En,Fr,Es)

### By graphics microcode

- **F3DEX 1.21** (3): Automobili Lamborghini (USA); Robotron 64 (USA); Virtual Chess 64 (USA) (En,Fr,Es)
- **F3DEX 1.23** (12): AeroGauge (USA); Bug's Life, A (USA); Charlie Blast's Territory (USA); Earthworm Jim 3D (USA); Forsaken 64 (USA); Hercules - The Legendary Journeys (USA); Powerpuff Girls, The - Chemical X-Traction (USA); Quest 64 (USA); Sesame Street - Elmo's Letter Adventure (USA); Sesame Street - Elmo's Number Journey (USA); Tom and Jerry in Fists of Furry (USA); Virtual Pool 64 (USA)
- **F3DEX 2.04 fifo** (1): CyberTiger (USA)
- **F3DEX 2.05 fifo** (3): Knife Edge - Nose Gunner (USA); NFL Blitz (USA); Snowboard Kids 2 (USA)
- **F3DEX 2.06 fifo** (2): Asteroids Hyper 64 (USA); Hercules - The Legendary Journeys (USA)
- **F3DEX 2.07 fifo** (2): Destruction Derby 64 (USA); Tom Clancy's Rainbow Six (USA)
- **F3DEX 2.08 fifo** (11): Bassmasters 2000 (USA); Batman Beyond - Return of the Joker (USA); Cruis'n Exotica (USA); John Romero's Daikatana (USA); Monopoly (USA); NFL Blitz 2000 (USA) (Rev 1); Razor Freestyle Scooter (USA); Scooby-Doo! - Classic Creep Capers (USA) (Rev 1); Space Invaders (USA); Tarzan (USA); Tigger's Honey Hunt (USA)
- **F3DEX 2.08 xbus** (2): Power Rangers - Lightspeed Rescue (USA); South Park Rally (USA)
- **F3DEX.NoN 1.21** (3): Clay Fighter - Sculptor's Cut (USA); Clay Fighter 63 1-3 (USA); Wetrix (USA) (En,Fr,De,Es,It,Nl)
- **F3DEX.NoN 1.23** (2): AeroGauge (USA); Quake II (USA)
- **F3DEX.NoN 2.03 fifo** (1): Starshot - Space Circus Fever (USA) (En,Fr,Es)
- **F3DEX.NoN 2.04 fifo** (1): CyberTiger (USA)
- **F3DEX.NoN 2.05 fifo** (3): Armorines - Project S.W.A.R.M. (USA); Carmageddon 64 (USA); New Tetris, The (USA)
- **F3DEX.NoN 2.06 fifo** (1): Big Mountain 2000 (USA)
- **F3DEX.NoN 2.07 fifo** (2): Beetle Adventure Racing! (USA) (En,Fr,De); Tom Clancy's Rainbow Six (USA)
- **F3DEX.NoN 2.07 xbus** (1): Beetle Adventure Racing! (USA) (En,Fr,De)
- **F3DEX.NoN 2.08 fifo** (4): 40 Winks (USA) (Piko Interactive); Duck Dodgers Starring Daffy Duck (USA) (En,Fr,Es); Monopoly (USA); Razor Freestyle Scooter (USA)
- **F3DEX.Rej 2.06 fifo** (1): Hercules - The Legendary Journeys (USA)
- **F3DEX.Rej 2.07 fifo** (1): Beetle Adventure Racing! (USA) (En,Fr,De)
- **F3DEX.Rej 2.07 xbus** (1): Beetle Adventure Racing! (USA) (En,Fr,De)
- **F3DEX.Rej 2.08 fifo** (2): Monopoly (USA); Razor Freestyle Scooter (USA)
- **F3DLP.Rej 1.21** (3): Clay Fighter - Sculptor's Cut (USA); Clay Fighter 63 1-3 (USA); Robotron 64 (USA)
- **F3DLP.Rej 1.23** (3): Hercules - The Legendary Journeys (USA); Quake II (USA); Virtual Pool 64 (USA)
- **F3DLX 1.21** (2): Milo's Astro Lanes (USA); Wipeout 64 (USA)
- **F3DLX 1.23** (4): Bug's Life, A (USA); Ready 2 Rumble Boxing (USA); Ready 2 Rumble Boxing - Round 2 (USA); WCW Nitro (USA)
- **F3DLX.NoN 1.21** (2): Clay Fighter - Sculptor's Cut (USA); Clay Fighter 63 1-3 (USA)
- **F3DLX.NoN 1.23** (2): Brunswick Circuit Pro Bowling (USA); Penny Racers (USA)
- **F3DLX.Rej 1.21** (2): Clay Fighter - Sculptor's Cut (USA); Clay Fighter 63 1-3 (USA)
- **F3DLX.Rej 2.03 fifo** (1): Starshot - Space Circus Fever (USA) (En,Fr,Es)
- **F3DLX.Rej 2.05 fifo** (2): Knife Edge - Nose Gunner (USA); NFL Blitz (USA)
- **F3DLX.Rej 2.06 fifo** (2): Big Mountain 2000 (USA); Hercules - The Legendary Journeys (USA)
- **F3DLX.Rej 2.08 fifo** (4): Monopoly (USA); NFL Blitz 2000 (USA) (Rev 1); Razor Freestyle Scooter (USA); Space Invaders (USA)
- **L3DEX 1.21** (1): Wipeout 64 (USA)
- **L3DEX 1.23** (5): Forsaken 64 (USA); Penny Racers (USA); Ready 2 Rumble Boxing (USA); Ready 2 Rumble Boxing - Round 2 (USA); Virtual Pool 64 (USA)
- **L3DEX 2.04 fifo** (1): CyberTiger (USA)
- **L3DEX 2.05 fifo** (1): Armorines - Project S.W.A.R.M. (USA)
- **L3DEX 2.06 fifo** (1): Asteroids Hyper 64 (USA)
- **L3DEX 2.07 fifo** (1): Tom Clancy's Rainbow Six (USA)
- **L3DEX 2.08 fifo** (4): Monopoly (USA); Razor Freestyle Scooter (USA); Scooby-Doo! - Classic Creep Capers (USA) (Rev 1); Space Invaders (USA)
- **L3DEX 2.08 xbus** (1): South Park Rally (USA)
- **S2DEX 1.06** (1): Carmageddon 64 (USA)
- **S2DEX 1.07** (1): Big Mountain 2000 (USA)
- **S2DEX 2.04 fifo** (1): CyberTiger (USA)
- **S2DEX 2.05 fifo** (1): Snowboard Kids 2 (USA)
- **S2DEX 2.07 fifo** (1): Destruction Derby 64 (USA)
- **S2DEX 2.08 fifo** (2): Monopoly (USA); Razor Freestyle Scooter (USA)
- **SW 2.0D** (2): Cruis'n USA (USA) (Rev 2); Pilotwings 64 (USA)
- **SW 2.0H** (7): Chopper Attack (USA); Jeopardy! (USA); Midway's Greatest Arcade Hits - Volume 1 (USA); Tetrisphere (USA); WCW Nitro (USA); Wheel of Fortune (USA); Wipeout 64 (USA)
- **none read** (5): Killer Instinct Gold (USA) (Rev 2); Knockout Kings 2000 (USA); Madden Football 64 (USA); Namco Museum 64 (USA); South Park - Chef's Luv Shack (USA)

### By compiler family (largest share)

- **ido** (16): AeroGauge (USA); Automobili Lamborghini (USA); Beetle Adventure Racing! (USA) (En,Fr,De); Big Mountain 2000 (USA); Chopper Attack (USA); Clay Fighter 63 1-3 (USA); Cruis'n USA (USA) (Rev 2); Duck Dodgers Starring Daffy Duck (USA) (En,Fr,Es); Killer Instinct Gold (USA) (Rev 2); Knife Edge - Nose Gunner (USA); New Tetris, The (USA); Pilotwings 64 (USA); Quest 64 (USA); Robotron 64 (USA); Tetrisphere (USA); Virtual Chess 64 (USA) (En,Fr,Es)
- **kmc** (50): 40 Winks (USA) (Piko Interactive); Armorines - Project S.W.A.R.M. (USA); Asteroids Hyper 64 (USA); Bassmasters 2000 (USA); Batman Beyond - Return of the Joker (USA); Brunswick Circuit Pro Bowling (USA); Bug's Life, A (USA); Carmageddon 64 (USA); Charlie Blast's Territory (USA); Clay Fighter - Sculptor's Cut (USA); Cruis'n Exotica (USA); CyberTiger (USA); Destruction Derby 64 (USA); Earthworm Jim 3D (USA); Forsaken 64 (USA); Hercules - The Legendary Journeys (USA); Jeopardy! (USA); John Romero's Daikatana (USA); Knockout Kings 2000 (USA); Madden Football 64 (USA); Midway's Greatest Arcade Hits - Volume 1 (USA); Milo's Astro Lanes (USA); Monopoly (USA); Namco Museum 64 (USA); NFL Blitz (USA); NFL Blitz 2000 (USA) (Rev 1); Penny Racers (USA); Power Rangers - Lightspeed Rescue (USA); Powerpuff Girls, The - Chemical X-Traction (USA); Quake II (USA); Razor Freestyle Scooter (USA); Ready 2 Rumble Boxing (USA); Ready 2 Rumble Boxing - Round 2 (USA); Scooby-Doo! - Classic Creep Capers (USA) (Rev 1); Sesame Street - Elmo's Letter Adventure (USA); Sesame Street - Elmo's Number Journey (USA); Snowboard Kids 2 (USA); South Park - Chef's Luv Shack (USA); South Park Rally (USA); Space Invaders (USA); Starshot - Space Circus Fever (USA) (En,Fr,Es); Tarzan (USA); Tigger's Honey Hunt (USA); Tom and Jerry in Fists of Furry (USA); Tom Clancy's Rainbow Six (USA); Virtual Pool 64 (USA); WCW Nitro (USA); Wetrix (USA) (En,Fr,De,Es,It,Nl); Wheel of Fortune (USA); Wipeout 64 (USA)

## Checked against full decompilations

For three titles the compiler answer has been tested by rebuilding code to identical bytes, which is much stronger than the family column above:

- **Wheel of Fortune (USA):** game code is KMC GCC 2.7.2 with optimisation off; the SDK is the IDO build. The whole cartridge rebuilds byte-identical from source.
- **Madden Football 64 (USA):** game code is KMC GCC 2.7.2 at `-O2`, in several overlays. Partly decompiled.
- **Sydney 2000 (USA prototype, not in the table):** resident code is KMC GCC; its overlays are SN Systems GCC and include C++, where GCC 2.8.1's C++ compiler reproduces constructors byte for byte. In progress.

The Sydney case shows the limit of the family column: C++ and SN-built code would be reported there as plain `kmc`.

## Known limits

- Not the whole library, and mostly one region per game.
- The compiler column is a family. Do not read a version or optimisation level into it.
- Titles marked (packed) keep their program compressed; their SDK and compiler answers cover only what could be unpacked.
- Middleware detection looks for a handful of names as text and misses anything that does not name itself.
- Audio driver versions are not in this cut.
