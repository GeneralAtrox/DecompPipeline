# Xbox 360 symbol sets: where to find which versions

A lookup table of Xbox 360 symbol sets (PDB, MAP and XEX files from development builds), so a project porting an Xbox 360 game can go straight to the set that matches its SDK build instead of unpacking and reading them all. Made on 2026-11-08 from 445 archives.

This file holds set names, version numbers and library names only. No symbols, code or game data are included.

## How to use it

1. Read your game's XDK build from its XEX: the XEX2 optional header `0x000103FF` lists each static library with a version `2.0.NNNN.0`, and the build is `NNNN`. A bare `.pe` (no XEX header) has no such records; search it for `2.0.NNNN.0` version strings instead.
2. Find that build, or the nearest on either side, in the table below. It is sorted by XDK build.
3. Check the **Has** column: a set needs a `pdb` (or at least a `map`) to give names. Prefer a shipping build (`XeReleaseLTCG`, `Opt`, `Final`, `f_mt`) over a debug one.
4. For middleware (Havok, Wwise and so on), use the index at the end and match the library versions.
5. Open the archive whose name is in the **Set** column. Copies of the same set (`_2` in the archive) are counted in **Copies**.

## Example

You are porting game X, and its XEX says XDK 6995 (Too Human's build). In the table:
- **6995:** `Need for Speed Undercover [2008-08-19] (MAP+PDB)`, an `Opt` build with a PDB. Open it first. For Too Human it matched 4,337 XDK functions byte for byte.
- **Nearest on either side with a PDB:** 6534 (Bourne, Highlander, Silent Hill Homecoming) and 7645 or 7776 (Gears of War 2, Skate 2, Godfather 2). Builds on either side name the same XDK functions, so use these next.
- **Middleware:** if X uses the old Wwise (`akaudioengine`), the index points to Mass Effect 2 and The Saboteur.

## Columns

- **Set:** the archive's name. **Copies:** how many archives hold the identical set.
- **Has:** the files in it: `pdb`, `map`, `xex`, `pe`, `exe`, `dll`, with a count when there is more than one.
- **Title ID** and **Version:** from the XEX header.
- **XDK:** the XDK build, `NNNN` of `2.0.NNNN.0`. **From:** `xex` (the header's static-library records, exact) or `version strings` (no XEX; can list more than one build).
- **Compiler:** `Optimizing Compiler` versions in the PDB (14.0 is Visual Studio 2005, 15.0 is 2008, 16.0 is 2010), or the XEX's `C2` record without a PDB.
- **Config:** build configuration read from the file names.
- **Other XDK libs:** static libraries linked from a different XDK build than the rest.
- **Middleware:** detected from library names, with version strings where present. A family can be listed without a version. Havok is listed as one family: check the library names (`hkcollide`/`hkdynamics` old, `hkp*` newer) in the set itself.

## Sets

| Set | Copies | Has | Title ID | Version | XDK | From | Compiler | Config | Other XDK libs | Middleware |
|---|---|---|---|---|---|---|---|---|---|---|
| Call of Duty 2 [2005-09-26] (MAP) | 1 | exe map | — | — | — | — | — | — | — | bink |
| Call of Duty 2 [2005-09-26] (PE+MAP) | 1 | exe map | — | — | — | — | — | — | — | bink |
| Call of Duty 2 [2005-10-05] (MAP) | 1 | exe map | — | — | — | — | — | — | — | bink |
| Call of Duty 2 [2005-10-05] (PE+MAP) | 1 | exe map | — | — | — | — | — | — | — | bink |
| Call of Duty 2 [2005-10-09] (MAP) | 1 | exe map | — | — | — | — | — | — | — | bink |
| Call of Duty 2 [2005-10-09] (PE+MAP) | 1 | exe map | — | — | — | — | — | — | — | bink |
| Call of Duty 4 [2007-06-11] (PE+PDB) | 1 | exe pdb | — | — | — | — | 14.0.1802, 14.0.2909, 14.0.4404 | — | — | bink, scaleform |
| Call of Duty Black Ops [2011-09-26] (PE+MAP) | 1 | exe×4 map×4 | — | — | — | — | — | Profile | — | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-01-31] (MAP) | 1 | exe×3 map×3 | — | — | — | — | — | Profile, profile | — | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-01-31] (PE+MAP) | 1 | exe×3 map×3 | — | — | — | — | — | Profile, profile | — | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-10-17] (PE+MAP) | 1 | exe map | — | — | — | — | — | — | — | bink, demonware, scaleform |
| Call of Duty Black Ops II [2013-03-07] (PE+MAP) | 1 | exe×3 map×3 | — | — | — | — | — | Profile, profile | — | bink, demonware, scaleform |
| Call of Duty Modern Warfare 3 [2011-10-11] (MAP+PDB) | 1 | exe×9 map×9 pdb×9 | — | — | — | — | 14.0.2909, 14.0.6130, 15.0.8153, 16.0.11886 | — | — | bink, demonware |
| connectx.dll [2013-09-06] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| connectx.dll [2015-04-22] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| Crazy Taxi [2010-08-18] (PDB) | 2 | exe pdb | — | — | — | — | 14.0.2909, 16.0.10224 | — | — | — |
| CreateProfile.dll [2013-09-06] (PDB) | 1 | pdb | — | — | — | — | — | profile | — | — |
| CreateProfile.dll [2015-04-22] (PDB) | 1 | pdb | — | — | — | — | — | profile | — | — |
| Def Jam Icon 2 (Unreleased) [2007-10-25] (MAP) | 2 | exe map | — | — | — | — | — | — | — | renderware |
| Devil's Third [2012-01-17] (MAP+PDB) | 2 | exe map pdb | — | — | — | — | 14.0.2909, 16.0.11886 | — | — | lua, naturalmotion, physx, wwise (physx PhysX_2.8.4) |
| Disney Universe (EngineXT Tools) [2011-04-01] (PDB) | 2 | exe×26 pdb×48 | — | — | — | — | 13.10.3077, 13.10.4035, 14.0.50727, 15.0.21022, 15.0.30729, 16.0.30319, 16.0.40219 | — | — | bink, demonware (lua Lua 5.1.1, Lua 5.1; zlib deflate 1.2.5, inflate 1.2.5, deflate 1.2.3, inflate 1.2.3) |
| hud.dll [2013-09-06] (PDB) | 1 | pdb | — | — | — | — | — | — | — | — |
| hud.dll [2015-04-22] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| Loadout [2012-05-23] (Unreleased) (MAP+PDB) | 1 | exe×3 map×4 pdb×4 | — | — | — | — | 14.0.2909, 15.0.8153, 16.0.11886 | — | — | miles (scaleform GFx_4.0.15, gfx_4.0.15; zlib deflate 1.2.6, inflate 1.2.6) |
| Marble Blast Ultra XNA Tech Demo [2007-02-20] (PDB) | 2 | exe pdb×2 | — | — | — | — | — | — | — | — |
| Project Sylpheed [2007-04-04] (EXE+PDB+MAP) | 1 | exe map pdb | — | — | — | — | 14.0.2909 | — | — | — |
| Share Play (Unreleased) [2010-06-16] (PDB) | 1 | — | — | — | — | — | — | — | — | — |
| signin.dll [2013-09-06] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| signin.dll [2015-04-22] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| Sonic Adventure [2010-08-01] (PDB+XDB) | 2 | exe pdb | — | — | — | — | 14.0.2909, 15.0.8153, 16.0.10224 | — | — | gamebryo |
| Tomb Raider Underworld [2007-08-18] (MAP) | 2 | exe map | — | — | — | — | — | — | — | fmod |
| Viva Piñata [2007-09-27] (PDB) | 2 | — | — | — | — | — | — | — | — | — |
| vk.dll [2013-09-06] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| vk.dll [2015-04-22] (PDB) | 1 | pdb | — | — | — | — | — | — | — | — |
| Wolfenstein The New Order (Early Prototype) [2012-08-29] (PE+PDB) | 1 | exe pdb | — | — | — | — | 14.0.2909, 16.0.11886 | — | — | bink |
| X-Men The Official Game [2005-11-02] (MAP) | 1 | exe map | — | — | — | — | — | — | — | havok, lua |
| xamd.dll [2013-09-06] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| xamd.dll [2015-04-22] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| xbdm.dll [2013-09-06] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| xbdm.dll [2015-04-22] (PDB) | 2 | pdb | — | — | — | — | — | — | — | — |
| xboxkrnlc.exe [2013-09-06] (PDB) | 2 | exe pdb | — | — | — | — | — | — | — | — |
| xboxkrnlc.exe [2015-04-22] (PDB) | 2 | exe pdb | — | — | — | — | — | — | — | — |
| xboxkrnlct.exe [2013-09-06] (PDB) | 2 | exe pdb | — | — | — | — | — | — | — | — |
| xboxkrnlct.exe [2015-04-22] (PDB) | 2 | exe pdb | — | — | — | — | — | — | — | — |
| xboxkrnld.exe [2013-09-06] (PDB) | 2 | exe pdb | — | — | — | — | — | — | — | — |
| xboxkrnld.exe [2015-04-22] (PDB) | 2 | exe pdb | — | — | — | — | — | — | — | — |
| xboxkrnldt.exe [2013-09-06] (PDB) | 2 | exe pdb | — | — | — | — | — | — | — | — |
| xboxkrnldt.exe [2015-04-22] (PDB) | 1 | exe pdb | — | — | — | — | — | — | — | — |
| AMPED 3 [2005-08-09 - 2005-01-18] (PDB+MAP+PE) | 1 | exe×6 map×7 pdb×4 pe×6 xex | 545407D4 | 0.0.0.0 | 2099 | xex | 14.0.1835, 14.0.1863 | Debug, Release | — | bink, havok, lua (lua Lua 5.0.2; sdk 2.0.2099.3, 2.0.2099.6, 2.0.1873.0, 2.0.1838.0, 2.0.1746.0; zlib inflate 1.2.3, inflate 1.2.1) |
| Amped 3 [2005-10-15] (PDB+MAP) | 2 | map×6 pdb×3 pe×5 xex | 545407D4 | 0.0.0.0 | 2099 | xex | 14.0.1835, 14.0.1863 | Debug, Release | — | bink, havok, lua (lua Lua 5.0.2; sdk 2.0.2099.3, 2.0.2099.6, 2.0.1838.0, 2.0.1746.0; zlib inflate 1.2.3, inflate 1.2.1) |
| Project Gotham Racing 3 (Network Test) [2005-10-16] (PDB) | 2 | pdb×2 xex×2 | 4D5307D1 | 0.0.0.0 | 2099 | xex | 14.0.1530, 14.0.1835, 14.0.1863, 14.0.2110 | — | D3D9 2135 | bink |
| NBA Live 2006 (Demo) [2005-10-28] (PDB+MAP) | 2 | map pdb xex×2 | 454187E0 | 0.0.1.2 | 2099, 2406 | xex | 14.0.1802, 14.0.1863 | — | D3D9LTCG 2099, D3DX9 2099, LIBCMT 2099, XAPILIB 2099, XAUD 2099, XBOXKRNL 2099, XGRAPHC 2099, XMEDIA 2099, XMP 2099, XRTLLIB 2099 | havok, renderware (renderware renderware/RW4.c.0.235131, renderware/rw4.c.0.235131, renderware/rw4.c.0.233841; sdk 2.0.2099.11, 2.0.2099.6, 2.0.2099.3, 2.0.2099.4) |
| Burnout Revenge [2006-02-07] (PDB) | 2 | pdb pe | — | — | 2417, 2500 | version strings | 14.0.1120, 14.0.1530, 14.0.2110 | — | — |  (sdk 2.0.2417.0, 2.0.2500.0; zlib deflate 1.1.3, inflate 1.1.3) |
| Test Drive Unlimited [2006-05-06] (PDB+MAP) | 2 | exe map×2 pdb×3 pe×3 xex×2 | 494707D4 | 1.0.306.0 | 2920 | xex | 14.0.1835, 14.0.2110, 14.0.2558, 14.0.2909 | — | — | havok (sdk 2.0.2920.0; zlib deflate 1.2.3, inflate 1.2.3) |
| Just Cause [2006-06-09] (MAP+PDB) | 1 | map pdb xex | 534307D5 | 0.0.0.0 | 3215 | xex | 14.0.2110, 14.0.2558, 14.0.2712, 14.0.2909 | — | — | bink, havok |
| NBA 2K7 [2006-08-17] (PDB) | 2 | pdb xex | 545407EF | 0.0.0.0 | 3215 | xex | — | opt | — | — |
| Gears of War [2006-09-21] (PDB) | 2 | pdb×2 pe×2 | — | — | 3529, 3802 | version strings | 14.0.2909 | XeRelease, XeReleaseLTCG | — | bink, physx (sdk 2.0.3802.0, 2.0.3529.0; zlib deflate 1.2.3, inflate 1.2.3) |
| Call of Duty World at War [2007-03-27] (MAP) | 1 | exe×3 map×6 xex×2 | 415607FE | 0.0.0.0 | 4314 | xex | — | — | — | bink, scaleform |
| Call of Duty World at War [2007-03-27] (PE+MAP) | 1 | exe×3 map×6 xex×2 | 415607FE | 0.0.0.0 | 4314 | xex | — | — | — | bink, scaleform |
| Call of Duty World at War [2007-05-11] (MAP) | 1 | exe×4 map×7 xex×2 | 415607FE | 0.0.0.0 | 4314 | xex | — | — | — | bink, scaleform (miles mSs57) |
| Call of Duty World at War [2007-05-11] (PE+MAP) | 1 | exe×4 map×7 xex×2 | 415607FE | 0.0.0.0 | 4314 | xex | — | — | — | bink, scaleform (miles mSs57) |
| Dead Space Vertical Slice [2007-04-20] (PDB) | 1 | pdb xex | 454107E9 | 0.0.0.0 | 4314 | xex | 14.0.2909, 14.0.4404 | — | — | havok, lua, renderware (renderware renderware/3.7, renderware/3.7) |
| Halo 3 [2007-03-07] (MAP) | 2 | map×5 xex×5 | 4D5307E6 | 0.0.0.0 | 5426 | xex | — | debug, profile, release | RTDMP 4902 | bink, havok |
| Halo 3 [2007-03-08] (MAP) | 2 | map×5 xex×5 | 4D5307E6 | 0.0.0.0 | 5426 | xex | — | debug, profile, release | RTDMP 4902 | bink, havok |
| Halo 3 [2007-03-09] (MAP) | 1 | map xex×2 | 4D5307E6 | 0.0.0.0 | 5426 | xex | — | profile | RTDMP 4902 | bink, havok |
| Boom Boom Rocket [2007-05-16] (PDB) | 2 | pdb xex | 5841086A | 0.0.0.0 | 5632 | xex | 14.0.2909, 14.0.5603 | Release | — | — |
| Skate. (Debug)  [2007-10-11] (PDB + MAP) | 1 | map pdb xex | 45410813 | 0.0.0.1 | 5632 | xex | 14.0.2909, 14.0.5603 | — | — | gamebryo, lua, renderware (sdk 2.0.5632.2, 2.0.5632.1) |
| Skate. (Release) [2007-10-11] (PDB + MAP) | 1 | map pdb xex | 45410813 | 0.0.0.1 | 5632 | xex | 14.0.2909, 14.0.5603 | — | — | gamebryo, lua, renderware (sdk 2.0.5632.2, 2.0.5632.1) |
| Too Human [2007-07-20] (PDB) | 2 | pdb×2 xex×2 | 4D5307DE | 0.0.0.0 | 5632 | xex | 14.0.2909, 14.0.4404, 14.0.5603 | Profile, Release | — | anark, havok, lua, speedtree, wwise (havok hk403r1, hk452r1, hk410r1, hk450r1, hk451r1, hk330r1, hk402r1, hk400r1) |
| Call of Duty 4 [2007-09-07] (MAP+PDB) | 1 | exe×2 map×6 pdb×6 xex×4 | 415607E6 | 0.0.0.0 | 6132 | xex | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | bink, scaleform |
| Call of Duty 4 [2007-09-07] (PE+MAP+PDB) | 1 | exe×2 map×6 pdb×6 xex×4 | 415607E6 | 0.0.0.0 | 6132 | xex | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | bink, scaleform |
| Need for Speed ProStreet (EUR) [2007-09-16] (MAP+PDB) | 2 | exe map pdb | — | — | 6132 | version strings | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | renderware (sdk 2.0.6132.0) |
| Need for Speed ProStreet (USA) [2007-09-16] (MAP+PDB) | 2 | exe map pdb | — | — | 6132 | version strings | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | renderware (sdk 2.0.6132.0) |
| Need for Speed ProStreet [2007-09-11] (MAP+PDB) | 2 | exe map pdb | — | — | 6132 | version strings | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | renderware (sdk 2.0.6132.0) |
| Need for Speed ProStreet [2007-09-17] (MAP+PDB) | 2 | exe map pdb | — | — | 6132 | version strings | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | renderware (sdk 2.0.6132.0) |
| Need for Speed ProStreet [2007-09-30] (MAP+PDB) | 2 | exe map pdb | — | — | 6132 | version strings | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | renderware (sdk 2.0.6132.0) |
| Need for Speed ProStreet [2007-10-14] (MAP+PDB) | 2 | exe map pdb | — | — | 6132 | version strings | 14.0.2909, 14.0.6021, 14.0.6130 | — | — | renderware (sdk 2.0.6132.0) |
| Burnout Paradise (Beta 5) [2007-10-31] (MAP) | 2 | xex | 45410806 | 0.0.0.0 | 6274 | xex | — | — | — | — |
| Burnout Paradise (NTSC) [2007-10-31] (MAP) | 2 | xex | 45410806 | 0.0.0.2 | 6274 | xex | — | — | — | — |
| Burnout Paradise (NTSC-J) [2007-12-13] (MAP) | 2 | xex | 45410806 | 0.0.0.4 | 6274 | xex | — | — | — | — |
| Burnout Paradise (NTSC-J) [2007-12-17] (MAP) | 2 | xex | 45410806 | 0.0.0.7 | 6274 | xex | — | — | — | — |
| Burnout Paradise (PAL) [2007-10-31] (MAP) | 1 | xex | 45410806 | 0.0.0.1 | 6274 | xex | — | — | — | — |
| Burnout Paradise (PAL) [2007-12-13] (MAP) | 2 | xex | 45410806 | 0.0.0.5 | 6274 | xex | — | — | — | — |
| Grand Theft Auto IV [2008-02-29] (MAP) | 1 | map×2 xex×2 | 54540816 | 0.0.0.0 | 6274 | xex | — | release | — | — |
| The Saboteur [2008-06-26] (MAP+PDB) | 3 | exe×2 map×2 pdb×3 xex×4 | — | — | 6274 | xex | 14.0.2909, 14.0.5603, 14.0.6251, 14.0.6502 | — | — | bink, havok, lua, scaleform, wwise (havok Havok-4.6.0-r1, Havok-3.1.0, Havok-3.0.0, Havok-4.0.0, Havok-4.0, Havok-4.1, Havok-4.5.0; lua Lua 5.1.1, Lua 5.1; scaleform Scaleform 2.0; sdk 2.0.6274.0, 2.0.6534.1; zlib inflate 1.2.3) |
| Alien Vs Predator [2008-10-06] (PDB+XDB) | 1 | pdb×2 xex×2 | 56550804 | 0.0.0.0 | 6534 | xex | 14.0.2909, 14.0.6502 | — | — | — |
| Burnout Paradise (Bikes Update) [2008-08-14] (MAP) | 1 | — | 45410806 | 0.3.0.0 | 6534 | xex | — | — | — | — |
| Burnout Paradise (Cagney Update) [2008-05-31] (MAP) | 1 | — | 45410806 | 0.3.0.0 | 6534 | xex | — | — | — | — |
| Burnout Paradise (Cagney Update) [2008-07-10] (MAP) | 2 | — | 45410806 | 0.0.16.0 | 6534 | xex | — | — | — | — |
| Burnout Paradise (DLC build) [2008-01-30] (MAP+XDB) | 2 | xex×6 | 45410806 | 0.0.0.0, 0.1.0.0 | 6534 | xex | — | — | — | — |
| Highlander [2008-04-08] (PDB) | 2 | pdb xex | 534307D6 | 1.0.3190.0 | 6534 | xex | 14.0.2909, 14.0.6251, 14.0.6502 | XeRelease | — | bink, physx |
| Robert Lundlum's The Bourne Supremacy [2008-01-13] (PDB) | 2 | pdb×3 xex×3 | 565507D4 | 0.0.0.0 | 6534 | xex | 14.0.2909, 14.0.4609, 14.0.5603, 14.0.6130, 14.0.6502 | XeDebug, XeRelease, XeReleaseLTCG | — | bink, fmod, physx, simul |
| Silent Hill Homecoming [2008-06-19] (MAP+PDB) | 2 | exe×3 map×3 pdb×3 xex×4 | 4B4E07E5 | 0.0.0.0 | 6534 | xex | 14.0.2909, 14.0.6130, 14.0.6251, 14.0.6502 | — | — | bink, gamespy, havok (havok hk403r1, hk461r1, hk452r1, hk410r1, hk330r1, hk460r1, hk450r1, hk451r1) |
| Star Wars Battlefront III [2008-03-17] (MAP+PDB) | 1 | map pdb xex | 4C4107D5 | 0.0.0.0 | 6534 | xex | 14.0.2909, 14.0.6251, 14.0.6502 | — | — | havok |
| Need for Speed Undercover [2008-08-19] (MAP+PDB) | 2 | exe map pdb | — | — | 6534, 6995 | version strings | 14.0.2909, 14.0.6502, 14.0.7032 | Opt | — | havok, lua, massive ads, renderware (sdk 2.0.6995.0, 2.0.6534.4) |
| Gears of War 2 (TU1) [2008-11-21] (PDB) | 1 | pdb xex | 4D53082D | 1.1.4638.0 | 7645 | xex | 14.0.2909, 14.0.4609, 14.0.6251, 14.0.6502, 14.0.7151 | XeReleaseLTCG | — | bink, physx, speedtree |
| Gears of War 2 (TU2) [2009-01-06] (PDB) | 1 | exe pdb xex | 4D53082D | 1.2.4711.0 | 7645 | xex | 14.0.2909, 14.0.4609, 14.0.6251, 14.0.6502, 14.0.7151 | XeReleaseLTCG | — | bink, physx, speedtree (sdk 2.0.7645.0; zlib inflate 1.2.3, deflate 1.2.3) |
| Gears of War 2 (TU3) [2009-03-04] (PDB) | 1 | exe×2 pdb×2 xex | 4D53082D | 1.3.4775.0 | 7645 | xex | 14.0.2909, 14.0.4609, 14.0.6251, 14.0.6502, 14.0.7151 | XeReleaseLTCG | — | bink, physx, speedtree (sdk 2.0.7645.0; zlib inflate 1.2.3, deflate 1.2.3) |
| Gears of War 2 (TU6) [2010-04-29] (PDB) | 1 | exe×3 pdb×3 xex | 4D53082D | 1.6.5036.0 | 7645 | xex | 14.0.2909, 14.0.4609, 14.0.6251, 14.0.6502, 14.0.7151 | XeRelease, XeReleaseLTCG | — | bink, physx, speedtree (sdk 2.0.7645.0; speedtree SpeedTreeRT Version 4.1; zlib deflate 1.2.3, inflate 1.2.3) |
| Gears of War 2 [2008-09-14] (PDB) | 2 | exe×13 pdb×55 xex×3 | 4D53082D | 1.0.4636.0 | 7645 | xex | 13.10.3077, 13.10.4035, 14.0.2909, 14.0.4609, 14.0.50727, 14.0.6021, 14.0.6251, 14.0.6502, 14.0.7151 | Profile, XeRelease, XeReleaseLTCG | — | bink, miles, physx, speedtree (miles mss32, MSS32; sdk 2.0.7645.0; speedtree SpeedTreeRT_SDK_v4.1, SpeedTreeRT Version 4.1; zlib deflate 1.2.3, inflate 1.2.3) |
| Lord of the Rings Conquest [2008-10-15] (SYM) | 2 | xex | 45410852 | 0.0.0.0 | 7645 | xex | — | — | — | — |
| Need for Speed Undercover [2008-12-11] (PDB) | 2 | pdb×2 xex×2 | 45410876 | 0.0.0.0 | 7645 | xex | 14.0.2909, 14.0.6502, 14.0.7151 | Debug, Opt | — | havok, lua, massive ads, renderware (sdk 2.0.7645.1) |
| Sacred 2 - Fallen Angel [2008-11-13] | 2 | exe×2 map pdb×4 xex×4 | 4B4D07D1 | 0.0.0.0 | 7645 | xex | 14.0.2909, 14.0.4404, 14.0.6251, 14.0.7032, 14.0.7151 | — | — | granny, kynapse, lua, physx, scaleform (granny granny2-7.0.34; lua Lua 5.0.2; physx PhysX_2.7.3, physx_2.7.3; sdk 2.0.4314.0, 2.0.7151.0, 2.0.7645.0; speedtree Speedtree Manager: %03.03; zlib deflate 1.2.3, inflate 1.2.3) |
| Skate 2 [2008-10-21] (MAP) | 2 | map xex | 4541087F | 0.0.0.1 | 7645 | xex | — | — | — | gamebryo, lua, massive ads, renderware |
| Skate 2 [2008-11-05] (PDB+MAP) | 1 | map×3 pdb×3 xex×5 | 4541087F | 0.0.0.1 | 7645 | xex | — | — | — | gamebryo, lua, massive ads, renderware (sdk 2.0.7645.1, 2.0.6995.0, 2.0.6534.4) |
| Skate 2 [2008-11-11] (XEX +MAP+PDB+XDB) | 1 | map×4 pdb×4 xex×5 | 4541087F | 0.0.0.1 | 7645 | xex | 14.0.2909, 14.0.6502, 14.0.7032, 14.0.7151 | — | — | gamebryo, lua, massive ads, renderware (sdk 2.0.7645.1, 2.0.6995.0, 2.0.6534.4) |
| Skate 2 [2009-01-17] (PDB+MAP) | 2 | map×4 pdb×4 xex×4 | 4541087F | 0.0.0.1, 0.1.0.1 | 7645 | xex | 14.0.2909, 14.0.6502, 14.0.7032, 14.0.7151 | — | — | gamebryo, lua, massive ads, renderware (sdk 2.0.7645.1, 2.0.6995.0, 2.0.6534.4) |
| Skate 2 [2009-05-06] (MAP) | 2 | map xex | 4541087F | 0.0.0.1 | 7645 | xex | — | — | — | gamebryo, lua, massive ads, renderware |
| Spider-Man 4 [2009-10-20] (Unreleased) (PDB) | 2 | exe×4 pdb×4 | — | — | 7645 | version strings | 14.0.2909, 14.0.6251, 15.0.8153 | — | — | bink, lua, scaleform (lua Lua 5.1; sdk 2.0.7645.0; zlib deflate 1.2.3, inflate 1.2.3) |
| Spider-Man 4 [2009-11-20] (Unreleased) (PDB) | 2 | exe pdb | — | — | 7645 | version strings | 14.0.2909, 14.0.6251, 15.0.8153 | — | — | bink, lua, scaleform (lua Lua 5.1; sdk 2.0.7645.0; zlib deflate 1.2.3, inflate 1.2.3) |
| Godfather 2 [2008-11-07] (SP Build) (EXE+XEX+PDB+MAP) | 1 | exe×2 map×4 pdb×5 xex×7 | 45410866 | 0.0.0.0 | 6995, 7776 | xex | 14.0.2909, 14.0.6502, 14.0.7032, 14.0.7151 | _d_mt, _f_mt, _q_mt, _r_mt, _ru_mt | D3D9I 6995, D3DX9I 6995, LIBCPMT 6995, XAPILIB 6995, XAUDI 6995, XBOXKRNL 6995, XGRAPHC 6995, XHV 6995, XMP 6995, XMPD 6995, XNET 6995, XONLINE 6995, XRTLLIBI 6995 | havok, lua, renderware (havok Havok-5.5.0-r1, Havok-3.1.0, Havok-3.0.0, Havok-5.0.0, Havok-5.0.0-r1, Havok-5.1.0-r1, Havok-4.0.0; lua Lua 5.0.2; sdk 2.0.6995.1, 2.0.6995.0) |
| Godfather 2 [2008-11-18] (MP Build) (XEX+PDB) | 1 | map×4 pdb×4 xex×4 | 45410866 | 0.0.0.0 | 7776 | xex | 14.0.2909, 14.0.6502, 14.0.7032, 14.0.7151 | _d_mt, _f_mt, _q_mt, _r_mt | — | havok, lua, renderware (sdk 2.0.6995.1) |
| Portal 09-02-2008 (EXE+XEX+DLL+PDB) | 1 | exe pdb×23 xex | 58410960 | 1.2.2.1 | 7776 | xex | 14.0.2909, 14.0.6130, 14.0.7151 | — | — | bink, havok |
| Bayonetta [2009-05-24] (PDB) | 2 | pdb xex | 53450813 | 0.0.0.0 | 7978 | xex | 14.0.2909, 15.0.8153 | — | — | — |
| Mass Effect 2 (JAP) [2010-09-10] (PDB) | 1 | pdb xex | 4541093D | 0.0.0.0 | 7978 | xex | 14.0.2909, 14.0.6502, 14.0.7151, 15.0.8153 | XeReleaseLTCG | — | bink, physx, scaleform, umbra, wwise (scaleform GFx-2.0, gfx-2.0; sdk 2.0.7978.3) |
| Mass Effect 2 (JAP) [2010-9-10] (PDB) | 1 | pdb xex | 4541093D | 0.0.0.0 | 7978 | xex | 14.0.2909, 14.0.6502, 14.0.7151, 15.0.8153 | XeReleaseLTCG | — | bink, physx, scaleform, umbra, wwise (scaleform GFx-2.0, gfx-2.0; sdk 2.0.7978.3) |
| Alien Versus Predator [2009-08-02] (PDB) | 2 | pdb×2 xex×2 | 56550804 | 0.0.0.0 | 8276 | xex | 14.0.2909, 15.0.8153 | — | — | bink |
| Alien Versus Predator [2009-08-20] (PDB) | 2 | pdb×2 xex×2 | 56550804 | 0.0.0.0 | 8276 | xex | 14.0.2909, 15.0.8153 | — | — | bink |
| Call of Duty Modern Warfare 2 [2009-07-13] (MAP+PDB) | 1 | exe×8 map×10 pdb×10 xex×2 | 41560817 | 0.0.0.0 | 8276 | xex | 14.0.2909, 14.0.6130, 15.0.8153 | — | — | bink, scaleform |
| Call of Duty Modern Warfare 2 [2009-07-13] (PE+MAP+PDB) | 1 | exe×8 map×10 pdb×10 xex×2 | 41560817 | 0.0.0.0 | 8276 | xex | 14.0.2909, 14.0.6130, 15.0.8153 | — | — | bink, scaleform |
| Dungeon Siege 3 [2010-03-05] (PDB) | 1 | pdb×2 xex×3 | — | — | 8276 | xex | 14.0.2909, 15.0.8153 | — | — |  (physx PhysX_2.8.3) |
| EA SPORTS Grand Slam Tennis 2 [2009-07-02] (PDB+MAP) | 2 | map×3 pdb×4 xex×4 | 45410891 | 1.0.1.0 | 8276 | xex | 14.0.2909, 15.0.8153 | — | — | renderware (sdk 2.0.8276.0) |
| Marvel Ultimate Alliance 2 [2009-7-31] (PDB+MAP) | 1 | map×3 pdb×3 xex×4 | 4156082F | 0.0.0.0 | 8276 | xex | 14.0.2909, 15.0.8153 | — | — | bink, fmod, havok, wwise |
| Tony Hawk - Ride (Prototype) [2009-09-07] (SYM) | 1 | xex | 41560820 | 1.0.0.0 | 8276 | xex | — | — | — | — |
| Fight Night Champion [2010-05-18] (PDB+MAP+XDB) | 1 | map×2 pdb×2 xex×2 | 45410915 | 1.0.1.0 | 9328 | xex | 14.0.2909, 15.0.8153, 15.0.8327 | — | — | lua, renderware (sdk 2.0.9328.9) |
| Halo Reach [11860.10.07.24.0147] [2010-06-24] (MAP) | 2 | map×8 xex×8 | 4D53085B | 0.0.0.0 | 9328 | xex | 16.0.10224.0 | debug, profile, release | ALTITLE 11551, ALTITLED 11551, RTDMP 4902, THASH 10724, XAUD 8276, XAUDD 8276, XAUDI 8276, XHV 8276, XHVD 8276 | bink, havok |
| Halo Reach [2010-10-25] (MAP) | 2 | map×6 xex×5 | 4D53085B | 0.0.0.0 | 9328 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile | ALTITLE 11551, RTDMP 4902, THASH 10724, XAUD 8276, XAUDI 8276, XHV 8276 | bink, havok |
| Halo Reach [2011-07-07] (MAP) | 2 | map×3 xex×4 | FFFF0303 | 0.0.0.0 | 9328 | xex | 16.0.10224.0, 16.0.11886.0 | debug | ALTITLE 11551, ALTITLED 11551, RTDMP 4902, THASH 10724, XAUDD 8276, XAUDI 8276 | bink, havok |
| Ryse - Son of Rome [2010-11-10] (PDB+XDB) | 2 | pdb xex | 4D5308BC | 0.1.0.0 | 9328 | xex | 14.0.1835, 14.0.1863, 14.0.2712, 14.0.2909, 14.0.4404, 15.0.8153, 16.0.10224 | Release | — | havok, naturalmotion, renderware, wwise (scaleform GFx_3.1.81, GFx_3.1.80; wwise Wwise v2010.2 build 3697, wwise v2010.2 build 3697, wwise v2009.2 build 3260, Wwise v2009.2 build 3260) |
| Call of Juarez - Gunslinger [2013-04-04] (PDB) | 2 | exe×2 pdb×2 | — | — | 4314, 9407 | version strings | 14.0.2909, 16.0.11886 | — | — |  (sdk 2.0.4314.0, 2.0.9407.0; zlib inflate 1.2.5, deflate 1.2.5) |
| Project Gotham Racing 3 (Tests) [2009-XX-XX] (PDB) | 1 | pdb×25 xex×29 | 00000000 | 2.0.4111.0, 2.0.5426.0, 2.0.6534.0, 2.0.8217.0, 2.0.8378.0, 2.0.9429.0 | 2099, 2863, 5426, 6534, 8378, 9429 | xex | 14.0.1530, 14.0.1835, 14.0.1863, 14.0.2909, 14.0.6130, 14.0.6502, 15.0.8327 | Debug | CMTD 6534, CMTD 8378, D3D9 2099, D3D9D 6534, D3DX9 2099, D3DX9D 6534, LIBCMT 2099, LIBCXD 5426, XAPILIB 2099, XAPILIBD 6534, XAPILIBD 8378, XAUD 2099, XAUDD 6534, XBOXKRNL 2863, XBOXKRNL 5426, XBOXKRNL 6534, XBOXKRNL 8378, XCAMD 8378, XGRAPHC 2099, XGRAPHCD 6534, XHV 2099, XMP 2099, XNETD 6534, XNETTD 6534, XNETYD 5426, XONLINE 2099, XONLINED 6534, XRTLLIB 2099, XRTLLIBD 5426, XUIRNDRD 6534, XUIRUND 6534 | bink (lua Lua 5.0.2; sdk 2.0.2099.6, 2.0.9429.0, 2.0.6534.0, 2.0.9407.0; zlib deflate 1.2.1, inflate 1.2.1) |
| Alice Madness Returns [2010-09-16] (PDB) | 1 | pdb×2 xex×2 | 455007D1 | 1.0.6522.0 | 11164 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | XeRelease, XeReleaseLTCG | — | bink, kynapse, physx (scaleform GFx_3.1.77) |
| DJ Hero 2 [2010-07-06] (MAP) | 2 | map xex | 4156087F | 0.0.0.0 | 11164 | xex | 16.0.10224.0 | Release | — | bink, demonware, fmod, granny, havok |
| Fallout - New Vegas [2010-07-21] (PE+PDB) | 1 | exe×3 pdb×3 xex×3 | 425307E0 | 0.0.0.0 | 11164 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | Release | — | bink, gamebryo, speedtree (gamebryo Gamebryo2.2, gamebryo2.2, Gamebryo Version 2.2.0, Gamebryo 2.1; havok Havok-7.1.0-r1, Havok 3.3, Havok-3.0.0, Havok-3.1.0; zlib deflate 1.2.1, inflate 1.2.1) |
| Monopoly Streets [2010-07-07] (MAP+PDB) | 2 | exe×3 map×3 pdb×3 xex×3 | 454108F5 | 0.0.0.0 | 11164 | xex | 14.0.1802, 14.0.2909, 16.0.10224 | — | — | renderware, wwise (sdk 2.0.6995.0, 2.0.9407.0) |
| NHL 11 [2010-05-31] (MAP+PDB) | 2 | exe map pdb×3 xex | 45418909 | 1.0.0.1 | 11164 | xex | 16.0.10224.0 | — | — | lua, renderware (lua Lua 5.0.2; renderware RenderWare 4.5; sdk 2.0.1901.0, 2.0.8955.0, 2.0.9328.0, 2.0.6995.0) |
| Splatterhouse [2010-06-28] (PDB) | 2 | pdb×3 xex×3 | 4E4D07F0 | 0.0.0.0 | 11164 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | release | — | bink, gamebryo, havok, scaleform, wwise (gamebryo Gamebryo-2.5, gamebryo-2.5, Gamebryo2.5, Gamebryo_GFx_1.0, Gamebryo_Wwise_1.0.7, gamebryo_gfx_1.0, gamebryo_wwise_1.0.7, gamebryo2.5) |
| Splatterhouse [2010-09-30] (PDB) | 1 | pdb×3 xex×3 | 4E4D07F0 | 0.0.0.0 | 11164 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | — | — | bink, gamebryo, havok, scaleform, wwise (gamebryo Gamebryo-2.5, gamebryo-2.5, Gamebryo2.5, Gamebryo_GFx_1.0, Gamebryo_Wwise_1.0.7, gamebryo_gfx_1.0, gamebryo_wwise_1.0.7, gamebryo2.5; havok Havok-5.5.0-r1, Havok-3.1.0, Havok-3.0.0, Havok-5.0.0, Havok-5.0.0-r1, Havok-5.1.0-r1, Havok-4.0.0; zlib inflate 1.2.3) |
| Dance Central [2010-06-07] (MAP) | 1 | map xex | 545607D3 | 1.0.0.0 | 11511 | xex | 15.0.9446.0, 16.0.10224.0 | — | XINP2RWD 9767 | bink |
| Call of Duty Black Ops [2011-09-27] (PE+MAP) | 1 | exe×6 map×10 xex×4 | 41560855 | 0.0.0.0 | 11626 | xex | 16.0.10224.0 | Profile, profile | THASH 10724 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2011-11-15] (MAP) | 1 | exe×6 map×10 xex×4 | 415608C3 | 0.0.0.0 | 11626 | xex | 16.0.10224.0 | Profile, profile | THASH 10724 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2011-11-15] (PE+MAP) | 1 | exe×6 map×10 xex×4 | 415608C3 | 0.0.0.0 | 11626 | xex | 16.0.10224.0 | Profile, profile | THASH 10724 | bink, demonware, scaleform |
| Dragon Ball Raging Blast 2 [2010-20-7] (XEX + MAP) | 1 | map xex | 4E4D0826 | 1.1.1.0 | 11626 | xex | 16.0.10224.0 | — | — | — |
| Driver - San Francisco [2011-02-17] (MAP+PDB) | 2 | map×5 pdb×4 xex×5 | 55530820 | 0.0.0.0 | 11626 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | — | — | bink, massive ads, wwise |
| El Shaddai - Ascension of the Metatron [2010-09-11] (MAP) | 1 | map×3 xex×4 | 494F07D1 | 0.0.0.0 | 11626 | xex | 16.0.10224.0 | Profile | — | gamebryo, naturalmotion, physx |
| Fallout - New Vegas [2010-08-22] (PE+PDB) | 1 | exe×4 pdb×4 xex×2 | 425307E0 | 0.0.0.0, 0.0.0.1 | 11626 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | Debug, Release | — | bink, gamebryo, speedtree (gamebryo Gamebryo2.2, gamebryo2.2, Gamebryo Version 2.2.0, Gamebryo 2.1, Gamebryo 2.0; havok Havok-7.1.0-r1, Havok 3.3, Havok-3.0.0, Havok-3.1.0; zlib deflate 1.2.1, inflate 1.2.1) |
| Fallout New Vegas [2010-11-10] (PDB) | 1 | exe×4 pdb×4 xex×2 | 425307E0 | 0.0.0.0 | 11626 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | Debug, Release | — | bink, gamebryo, speedtree (gamebryo Gamebryo2.2, gamebryo2.2, Gamebryo Version 2.2.0, Gamebryo 2.1, Gamebryo 2.0; havok Havok-7.1.0-r1, Havok 3.3, Havok-3.0.0, Havok-3.1.0; zlib deflate 1.2.1, inflate 1.2.1) |
| Dragon Age 2 [2011-01-27] (MAP) | 2 | map xex×2 | 4541090B | 0.0.0.0 | 11775 | xex | 16.0.10224.0 | release | — | bink, physx, umbra, wwise |
| Kinect Joyride [2010-09-08] | 2 | map pdb xex | 4D53093B | 1.0.0.0 | 11775 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | — | — | havok, lua, scaleform, wwise (havok hk700r1, hk610r1, hk660r1, hk600r1, hk403r1, hk461r1, hk650r1, hk550r1; scaleform scaleform-3.1) |
| Need for Speed Hot Pursuit [2010-09-02] (MAP) | 2 | xex×2 | 45410903 | 0.1.0.0 | 11775 | xex | 16.0.10224.0 | — | — | — |
| Burnout Crash [2011-03-01] (PDB+MAP+XDB) | 1 | pdb×3 xex×3 | — | — | 11886 | xex | 14.0.2909, 14.0.50727, 16.0.11886 | DEBUG, RELEASE | XAPILIB 20353, XBOXKRNL 20353 | — |
| Bulletstorm [2010-12-20] (PDB) | 2 | pdb×2 xex×2 | 454108EF | 1.0.7059.0 | 20209 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | XeReleaseLTCG | — | bink, physx |
| Crysis 2 [2010-12-04] (PDB) | 1 | pdb×16 xex | 454108E3 | 0.0.0.0 | 20209 | xex | 14.0.2909, 16.0.10224 | — | — | fmod |
| Dead Space 2 [2010-11-19] (PDB) | 2 | pdb×4 xex×4 | 454108DF | 0.0.0.0 | 20209 | xex | 14.0.2909, 16.0.10224 | — | — | havok, lua, renderware |
| Dead Space 3 [2012-04-05] (PDB) | 1 | pdb×3 xex×3 | 454108DF | 0.0.0.0 | 20209 | xex | 14.0.2909, 15.0.8153, 16.0.10224 | — | — | havok, lua, renderware |
| Gears of War 3 [2011-03-20] (PDB) | 2 | pdb×2 xex×2 | 4D5308AB | 1.0.8073.0 | 20209 | xex | 14.0.2909, 15.0.8153, 16.0.10224, 16.0.10358, 16.0.11886 | — | — | bink, physx, speedtree (speedtree SpeedTreeCore_v5.0, speedtreecore_v5.0) |
| Halo 4 [2011-08-22] (MAP) | 2 | map×2 xex×2 | 4D530919 | 0.0.0.0 | 20353 | xex | 16.0.10224.0, 16.0.11886.0 | — | LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Silent Hill - Downpour [2011-06-03] (PDB) | 2 | pdb×2 xex×2 | 4B4E0823 | 1.0.6383.0 | 20353 | xex | 14.0.2909, 15.0.8153, 16.0.10224, 16.0.11886 | XeDebug, XeRelease | CPMTD 11886, LIBCPMT 11886 | physx |
| Silent Hill - Downpour [2011-07-13] (PDB) | 2 | pdb xex | 4B4E0823 | 1.0.6383.0 | 20353 | xex | 14.0.2909, 15.0.8153, 16.0.10224, 16.0.11886 | XeRelease | LIBCPMT 11886 | physx |
| Call of Duty Future Warfare (Unreleased) [2011-12-18 - 2012-01-08] (PDB+MAP) | 1 | map×8 pdb×4 pe xex×9 | 4156089E | 0.0.0.0, 1.0.0.1, 4.3.12800.0 | 3338, 20500 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, demonware, scaleform (sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Call of Duty Modern Warfare 3 [2011-09-19] (MAP+PDB) | 1 | exe×9 map×12 pdb×12 xex×3 | 415608CB | 0.0.0.0, 1.0.0.1 | 8276, 20500 | xex | 14.0.2909, 14.0.6130, 15.0.8153, 16.0.11886 | — | D3D9LTCG 8276, LIBCMT 11886, LIBCMT 8276, SHVOUI 8276, X3DAUDL 8276, XAPILIB 8276, XAUD 8276, XAUDLTCG 8276, XBOXKRNL 8276, XGRAPHC 8276, XHV 8276, XNET 8276, XONLINE 8276, XPARTY 8276 | bink, demonware, scaleform (miles MSs14) |
| Call of Duty Modern Warfare 3 [2011-10-11] (MAP+PDB) | 1 | exe×9 map×11 pdb×11 xex×2 | 415608CB | 0.0.0.0, 1.0.0.1 | 20500 | xex | 14.0.2909, 14.0.6130, 15.0.8153, 16.0.11886 | — | LIBCMT 11886, XAUD 8276, XHV 8276 | bink, demonware |
| Crysis [2011-05-26] (PDB) | 2 | pdb×17 xex×2 | 45410968 | 2.0.16.0 | 11886, 20500 | xex | 14.0.2909, 16.0.10224, 16.0.11886 | Release | LIBCMT 11886 | fmod |
| Disney Universe [2011-05-07] (MAP+PDB) | 2 | map pdb xex | 42560803 | 1.0.0.0 | 20500 | xex | 14.0.2909, 16.0.10224, 16.0.11886 | — | LIBCMT 11886 | bink |
| Disney Universe [2011-07-08] (MAP+PDB) | 2 | map pdb xex | 42560803 | 1.0.0.0 | 20500 | xex | 14.0.2909, 16.0.10224, 16.0.11886 | — | LIBCMT 11886 | bink |
| Driver - San Francisco [2011-04-21] (MAP) | 2 | map xex | 55530820 | 0.0.0.0 | 20500 | xex | 16.0.10224.0, 16.0.11886.0 | — | LIBCPMT 11886 | bink, wwise |
| Driver - San Francisco [2011-05-30] (MAP+PDB) | 2 | map×2 pdb×4 xex×4 | 55530820 | 0.0.0.0 | 11626, 20500 | xex | 14.0.2909, 15.0.8153, 16.0.10224, 16.0.11886 | — | D3D9I 11626, D3DX9 11626, LIBCPMT 11626, LIBCPMT 11886, XAPILIB 11626, XAPOBA 11626, XAUD 11626, XAUDIO2 11626, XBDM 11626, XBOXKRNL 11626, XGRAPHC 11626, XHV2 11626, XMASS 11626, XMCORE 11626, XMP 11626, XONLINE 11626, XPARTY 11626, XRTLLIBI 11626 | bink, massive ads, wwise |
| Driver - San Francisco [2011-07-11] (MAP+PDB) | 1 | exe×2 map×5 pdb×5 xex×5 | 5553087E | 0.0.0.0 | 11626, 20500 | xex | 14.0.2909, 15.0.8153, 16.0.10224, 16.0.11886 | — | D3D9I 11626, D3DX9 11626, LIBCPMT 11626, LIBCPMT 11886, XAPILIB 11626, XAPOBA 11626, XAUD 11626, XAUDIO2 11626, XBDM 11626, XBOXKRNL 11626, XGRAPHC 11626, XHV2 11626, XMASS 11626, XMCORE 11626, XMP 11626, XONLINE 11626, XPARTY 11626, XRTLLIBI 11626 | bink, massive ads, wwise (lua Lua 5.1.2, Lua 5.1; zlib deflate 1.2.5, inflate 1.2.5) |
| Gears of War 3 (TU2) [2011-10-12] (PDB) | 2 | pdb xex | 4D5308AB | 1.0.8810.0 | 20500 | xex | 14.0.2909, 15.0.8153, 16.0.10224, 16.0.11886 | — | LIBCMT 11886 | bink, physx, speedtree (speedtree SpeedTreeCore_v5.0, speedtreecore_v5.0) |
| Metro Last Light [2012-10-15] (PDB) | 2 | pdb xex | 5451089E | 0.0.0.0 | 20500 | xex | 14.0.2909, 15.0.8153, 16.0.11886 | — | LIBCMT 11886 | physx (physx PhysX_2.8.3) |
| Skylanders - Spyro's Adventure [2011-08-18] | 1 | pdb xex×2 | 415608AB | 2.0.256.1 | 20500 | xex | 14.0.2909, 14.0.7151, 15.0.8153, 16.0.11886 | — | LIBCMT 11886 | bink, demonware, wwise |
| Skylanders - Spyro's Adventure [2011-08-18] (PDB) | 1 | pdb xex×2 | 415608AB | 2.0.256.1 | 20500 | xex | 14.0.2909, 14.0.7151, 15.0.8153, 16.0.11886 | — | LIBCMT 11886 | bink, demonware, wwise |
| Skylanders - Spyro's Adventure [2011-08-19] | 1 | pdb xex×2 | 415608AB | 2.0.256.1 | 20500 | xex | 14.0.2909, 14.0.7151, 15.0.8153, 16.0.11886 | — | LIBCMT 11886 | bink, demonware, wwise |
| Skylanders - Spyro's Adventure [2011-08-19] (PDB) | 1 | pdb xex×2 | 415608AB | 2.0.256.1 | 20500 | xex | 14.0.2909, 14.0.7151, 15.0.8153, 16.0.11886 | — | LIBCMT 11886 | bink, demonware, wwise |
| Tom Clancy's Ghost Recon Future Soldier [2011-05-31] (PDB) | 2 | exe×2 pdb×7 xex×10 | 55530833 | 0.0.0.0 | 11886, 20500 | xex | 14.0.2909, 14.0.7151, 16.0.10224, 16.0.11886 | Final, Release | CPMTD 11886, LIBCMT 11886, LIBCPMT 11886 | bink (miles MsS80; sdk 2.0.9407.0; zlib deflate 1.2.5, inflate 1.2.5) |
| Voltron - Defender of the Universe [2011-06-21] (PDB) | 2 | pdb xex | 58410B8F | 0.0.0.0 | 20500 | xex | 14.0.2909, 16.0.11886 | release | LIBCPMT 11886 |  (fmod FmodEx/lib/flac-1.2.1, fmodex/lib/flac-1.2.1) |
| Voltron - Defender of the Universe [2011-08-01] (PDB) | 2 | pdb xex | 58410B8F | 0.0.0.0 | 20500 | xex | 14.0.2909, 16.0.11886 | release | LIBCPMT 11886 |  (fmod FmodEx/lib/flac-1.2.1, fmodex/lib/flac-1.2.1) |
| Halo - Combat Evolved Anniversary [06-24-2011](XDK+PDB) | 1 | pdb×2 xex×2 | 4D5309B1 | 0.0.0.0 | 20675 | xex | 14.0.2909, 14.0.7151, 15.0.8153, 16.0.11886 | Release | LIBCMT 11886 | fmod, gamespy, havok (fmod fmod/src/lib/flac-1.2.1; miles MsS94; scaleform GFx_3.0.76) |
| Inversion [2011-07-01] (XEX+PDB+XDB) | 1 | pdb xex | 4E4D0801 | 0.0.0.0 | 20675 | xex | 14.0.2909, 14.0.7151, 15.0.8153, 16.0.11886 | — | LIBCMT 11886 | fmod, gamespy, havok (fmod fmod/src/lib/flac-1.2.1) |
| Assassin's Creed - Revelations [2011-10-05] (MAP+PDB) | 2 | map pdb xex | 55530879 | 0.0.0.0 | 20764 | xex | 14.0.2909, 15.0.8153, 16.0.11886 | final | LIBCPMT 11886 | bink, havok, wwise |
| Batman Arkham City [2012-01-20] (PDB) | 2 | pdb×2 xex×2 | 57520802 | 1.4.1.0 | 20764 | xex | 14.0.2909, 16.0.11886 | XeReleaseLTCG | LIBCMT 11886, XGDT 21128 | physx, wwise |
| Disney Universe [2012-02-28] (MAP+PDB) | 2 | map pdb xex | 42560803 | 1.1.0.0 | 20764 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink |
| Fire Pro Wrestling [2011-10-26] (PDB) | 1 | pdb×2 xex×2 | 58410A82 | 0.0.0.0 | 20764 | xex | 14.0.2909, 16.0.11886 | Debug | CMTD 11886, LIBCMT 11886 | — |
| Halo 4 [07866.11.10.04.1335-0] [2011-10-04] (MAP) | 2 | map×7 xex×7 | 4D530919 | 0.0.0.0 | 20764 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [08028.11.10.11.1702-0] [2011-10-11] (MAP) | 1 | map×7 xex×7 | 4D530919 | 0.0.0.0 | 20764 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise (miles msS76) |
| Halo 4 [08028.11.10.11.1702-2] [2011-10-11] (MAP) | 2 | map×7 xex×7 | 4D530919 | 0.0.0.0 | 20764 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise (miles msS76) |
| Halo 4 [08257.11.10.20.0230-0] [2011-10-20] (MAP) | 2 | map×7 xex×7 | 4D530919 | 0.0.0.0 | 20764 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise (miles Mss87) |
| Halo 4 [08257.11.10.20.0230-2] [2011-10-20] (MAP) | 2 | map×7 xex×7 | 4D530919 | 0.0.0.0 | 20764 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise (miles Mss87) |
| Halo 4 [08912.11.11.14.2334] [2011-11-15] (MAP) | 2 | map×7 xex×7 | 4D530919 | 0.0.0.0 | 20764 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [09550.11.12.21.0300-0] [2011-12-21] (MAP) | 2 | map×7 xex×7 | 4D530919 | 0.0.0.0 | 20764 | xex | 16.0.10224.0, 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Tom Clancy's Ghost Recon Future Soldier [2011-07-26] (PDB) | 2 | exe×2 pdb×8 xex×9 | 55530833 | 0.0.0.0 | 11886, 20764 | xex | 14.0.2909, 14.0.7151, 16.0.10224, 16.0.11886 | Final, Release | CPMTD 11886, LIBCMT 11886, LIBCPMT 11886, XAPILIB 20500, XBOXKRNL 20500, XNET 20500 | bink (sdk 2.0.9407.0; zlib deflate 1.2.5, inflate 1.2.5) |
| Tom Clancy's Ghost Recon Future Soldier [2011-07-28] (PDB) | 2 | exe×2 pdb×4 xex×4 | 55530833 | 0.0.0.0 | 11886, 20764 | xex | 14.0.2909, 14.0.7151, 16.0.10224, 16.0.11886 | Final, Release | LIBCMT 11886, LIBCPMT 11886 | bink (sdk 2.0.9407.0; zlib deflate 1.2.5, inflate 1.2.5) |
| Burn [2012-01-12] (Unreleased Crackdown 3 Alpha) (PDB) | 1 | pdb xex | 58480869 | 0.1.0.0 | 20871 | xex | 14.0.1835, 14.0.1863, 14.0.2712, 14.0.2909, 14.0.4404, 15.0.8153, 16.0.10224, 16.0.11886 | Release, release | LIBCPMT 11886 | havok, lua, wwise (scaleform GFx_4.0.12; wwise wwise v2009.2 build 3260, Wwise v2009.2 build 3260) |
| Hitman Absolution (Prototype) [2012-06-12] (PDB+MAP) | 1 | map×4 pdb×4 xex×4 | 53510804 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | Debug, Release | CMTD 11886, LIBCMT 11886 | naturalmotion, physx, scaleform (fmod fmod4/lib/flac-1.2.1; scaleform GFx_3.3.90, gfx_3.3.90) |
| South Park - The Stick of Truth [v814] [2012-09-27] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (miles mss80) |
| South Park - The Stick of Truth [v858] [2012-11-08] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v859] [2012-11-09] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v860] [2012-11-10] (PDB) | 1 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v861] [2012-11-11] (PDB) | 1 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v862] [2012-11-12] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v863] [2012-11-13] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v864] [2012-11-14] (PDB) | 1 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v865] [2012-11-15] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v870] [2012-11-20] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v873] [2012-11-23] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v875] [2012-11-25] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v876] [2012-11-26] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v877] [2012-11-26] (PDB) | 1 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v877] [2012-11-27] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v878] [2012-11-28] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v879] [2012-11-29] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v880] [2012-11-30] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v881] [2012-12-01] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v882] [2012-12-02] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v883] [2012-12-03] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v884] [2012-12-04] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v885] [2012-12-05] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v888] [2012-12-08] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v889] [2012-12-10] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v890] [2012-12-11] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v891] [2012-12-12] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v892] [2012-12-13] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v893] [2012-12-14] (PDB) | 2 | pdb×2 xex×2 | 54510893 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v895] [2012-12-16] (PDB) | 2 | pdb×2 xex×2 | 545108B1 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v896] [2012-12-17] (PDB) | 2 | pdb×4 xex×4 | 545108B1 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCPMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| South Park - The Stick of Truth [v897] [2012-12-18] (PDB) | 2 | pdb×2 xex×2 | 545108B1 | 0.0.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCPMT 11886 | wwise (scaleform GFx_4.0.17, gfx_4.0.17) |
| Zone of the Enders HD Collection [2012-10-29] (PDB) | 2 | exe pdb xex | 4B4E0842 | 0.1.0.0 | 21076 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, wwise (wwise wwise_v2012.1) |
| Assassin's Creed 3 [2012-12-13] (MAP) | 1 | map xex | 555308AE | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | final | LIBCPMT 11886 | bink, havok, wwise (zlib deflate 1.2.5, inflate 1.2.5) |
| Call of Duty Black Ops II [2012-07-20] (MAP) | 1 | exe map×2 xex | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-07-20] (PE+MAP) | 1 | exe map×2 xex | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-07-23] (MAP) | 1 | exe×2 map×4 xex×2 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-07-23] (PE+MAP) | 1 | exe×2 map×4 xex×2 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-10-02] (MAP+XDB) | 1 | exe×2 map×2 xex×2 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-10-18] (PE+MAP) | 1 | exe×5 map×6 xex×2 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | Profile | LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2012-10-19] (PE+MAP) | 1 | exe×6 map×13 xex×5 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | Profile, profile | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2013-02-26] (MAP) | 1 | map×2 xex×2 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2013-06-11] (PE+MAP) | 1 | exe×2 map×4 xex×2 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2013-08-23] (PE+MAP) | 1 | exe×4 map×6 xex×2 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | Profile, profile | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2013-09-06] (MAP) | 1 | map xex | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCPMT 11886 | bink, demonware, scaleform |
| Call of Duty Black Ops II [2013-09-07] (PE+MAP) | 1 | exe×8 map×14 xex×6 | 415608C3 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | Profile, profile | LIBCMT 11886, LIBCPMT 11886 | bink, demonware, scaleform |
| Crysis 3 [2013-01-15] (MAP) | 1 | map xex | 4541098E | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886 | fmod |
| Crysis 3 Performance (MAP) | 1 | map xex | 4541098E | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | — | LIBCMT 11886 | fmod |
| Halo 2 Anniversary MP Alpha [20810.12.09.22.1647.main] [2012-09-22] (MAP+XDB) | 1 | map×7 xex×8 | 4D530919 | 1.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 2 Anniversary MP Alpha [21646.14.03.21.0841-2] [2014-03-21] (MAP+XDB) | 1 | map×4 xex×5 | 4D530919 | 1.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 2 Anniversary MP Alpha [21651.14.03.28.1525-7] [2014-03-28] (MAP+XDB) | 1 | map×4 xex×5 | 4D530919 | 1.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 2 Anniversary MP Alpha [21652.14.04.04.1153-3] [2014-04-04] (MAP+XDB) | 1 | map×4 xex×4 | 4D530919 | 1.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [16611.12.07.07.0201-0.main] [2012-07-07] (MAP) | 1 | map×6 xex×6 | 4D530919 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [19558.12.08.26.2152.de] [2012-08-26] (MAP) | 1 | map×6 xex×7 | 4D530919 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [2012-08-19] (MAP) | 2 | map×7 xex×8 | 4D530919 | 0.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [2013-01-14] (MAP) | 2 | map×8 xex×8 | 4D530919 | 1.4.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise (miles Mss29, mSS06) |
| Halo 4 [20656.12.09.14.0200.main] [2012-09-14] (MAP) | 1 | map×7 xex×7 | 4D530919 | 1.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [20810.12.09.22.1647.main] [2012-09-22] (SMP+MAP) | 1 | map×7 xex×7 | 4D530919 | 1.0.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [21117.12.11.19.2343-3.main] [2012-11-19] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.2.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [21165.12.12.12.0112-0.main] [2013-01-02] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.2.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [21165.12.12.12.0112-1.main] [2013-01-08] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.2.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [21184.12.12.31.1438.main] [2013-01-02] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.2.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise (miles MSS38) |
| Halo 4 [21198.13.01.08.1052.main] [2013-01-08] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.2.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [21208.13.01.10.1649-0.ca] [2013-01-10] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.1.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [21208.13.01.10.1649-1.ca] [2013-01-10] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.1.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise (miles mss59, mSS47) |
| Halo 4 [21252.13.01.17.1751.main] [2013-01-17] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.4.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Halo 4 [21267.13.01.18.1247.main] [2013-01-18] (MAP) | 1 | map×8 xex×8 | 4D530919 | 1.4.0.0 | 21119 | xex | 16.0.11886.0 | debug, profile, release | CPMTD 11886, LIBCPMT 11886, RTDMP 4902 | bink, havok, wwise |
| Batman Arkham Origins [2013-04-25] (PDB | 1 | pdb xex | 57520828 | 1.1.9469.0 | 21173 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | physx, wwise (wwise wwise_v2012.2) |
| Batman Arkham Origins [2013-04-25] (PDB) | 1 | pdb xex | 57520828 | 1.1.9469.0 | 21173 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | physx, wwise (wwise wwise_v2012.2) |
| Call of Duty Advanced Warfare [2013-05-03] (MAP+PDB) | 1 | map×2 pdb×3 xex | 415608E0 | 1.0.0.1 | 21173 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, demonware |
| Dance Central 3 (Prototype) [2012-08-08] (PDB+MAP) | 1 | map pdb xex | 373307D9 | 1.0.0.0 | 21173 | xex | 14.0.2909, 14.0.6251, 16.0.11886 | — | LIBCMT 11886 | bink |
| Dance Central 3 [2012-08-28] (MAP) | 1 | map xex | 373307D9 | 1.0.0.0 | 21173 | xex | 16.0.11886.0 | — | LIBCMT 11886 | bink |
| Dance Central 3 [2012-09-16] (MAP) | 1 | map xex×2 | 373307D9 | 1.0.0.0 | 21173 | xex | 16.0.11886.0 | — | LIBCMT 11886 | bink |
| Dead Space 3 [2012-08-09] (PDB) | 2 | pdb×3 xex×3 | 4541099D | 0.0.0.0 | 21173 | xex | 14.0.2909, 15.0.8153, 16.0.11886 | — | LIBCPMT 11886 | havok, lua, renderware |
| Gears of War Judgment [2013-01-08] (PDB) | 2 | pdb xex | 4D530A26 | 1.0.10441.0 | 21173 | xex | 14.0.2909, 15.0.8153, 16.0.11886 | — | LIBCMT 11886 | bink, physx, speedtree (speedtree SpeedTreeCore_v5.0, speedtreecore_v5.0) |
| Hitman Absolution [2012-08-27] (MAP+PDB) | 2 | map pdb xex | 53510804 | 0.0.0.0 | 21173 | xex | 14.0.2909, 16.0.11886 | Release | LIBCMT 11886 | naturalmotion, physx, scaleform (fmod fmod4/lib/flac-1.2.1; scaleform GFx_3.3.90) |
| Killer Instinct [2013-05-17] (MAP) | 2 | map xex | 584113F4 | 1.0.0.0 | 21173 | xex | 16.0.11886.0 | — | LIBCPMT 11886 | bink, wwise |
| Teenage Mutant Ninja Turtles - Out of the Shadows [2013-07-10] (PDB) | 1 | pdb xex | 584113C2 | 1.0.7637.0 | 21173 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, physx |
| Assassin's Creed 4 Black Flag (MP Test Build) [2013-09-13] (PDB+MAP) | 2 | map×4 pdb×4 xex×6 | 555308C2 | 0.0.0.0 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCPMT 11886 | bink, havok, wwise (wwise Wwise_2013.1, wwise_2013.1) |
| Assassin's Creed 4 Black Flag [2013-07-23] (MAP) | 2 | map×2 xex×2 | 555308C2 | 0.0.0.0 | 21250 | xex | 16.0.11886.0 | final | LIBCPMT 11886 | bink, havok, wwise |
| Call of Duty Ghosts [2013-05-09] (PE+MAP+PDB) | 1 | exe×6 map×8 pdb×8 pe×8 xex×2 | 4156088E | 1.0.0.1 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, demonware (sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Call of Duty Ghosts [2013-05-21] (PE+MAP+PDB) | 1 | exe×6 map×8 pdb×8 pe×8 xex×2 | 4156088E | 1.0.0.1 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, demonware (sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Call of Duty Ghosts [2013-08-29] (PE+MAP+PDB) | 1 | exe×6 map×8 pdb×8 pe×8 xex×2 | 415608FC | 1.0.0.1 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, demonware (sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Call of Duty Ghosts [2013-09-02] (PE+MAP+PDB) | 1 | exe×6 map×8 pdb×8 pe×8 xex×2 | 415608FC | 1.0.0.1 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, demonware (sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Call of Duty Ghosts [2013-09-09] (PE+MAP+PDB) | 1 | exe×6 map×8 pdb×8 pe×8 xex×2 | 415608FC | 1.0.0.1 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCMT 11886 | bink, demonware (miles MSS12; sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Fable Anniversary [2013-10-15] (PDB) | 2 | pdb xex | 4D530A87 | 1.0.10246.0 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCPMT 11886 | bink, physx, wwise (physx PhysX_2.8.4; wwise wwise_v2012.2; zlib deflate 1.2.3, inflate 1.2.3) |
| Fable Anniversary [2013-11-05] (PDB) | 2 | pdb×3 xex×2 | 4D530A87 | 1.0.10246.0 | 21250 | xex | 14.0.2909, 16.0.11886 | Release | LIBCPMT 11886 | bink, physx, wwise (physx PhysX_2.8.4; wwise wwise_v2012.2) |
| Killer Instinct [2013-08-20] (MAP+PDB) | 2 | map pdb xex | 584113F4 | 1.0.0.0 | 21250 | xex | 14.0.2909, 16.0.11886 | — | LIBCPMT 11886 | bink, wwise (wwise Wwise_2013.1, wwise_2013.1) |
| Assassin's Creed Rogue [2014-09-21] (MAP) | 1 | map xex | 555308CE | 0.0.0.0 | 21256 | xex | 16.0.11886.0 | final | LIBCPMT 11886 | bink, havok, wwise |
| Call of Duty Advanced Warfare [2014-09-04] (PE+MAP+PDB) | 1 | exe×8 map×9 pdb×11 pe×9 xex×2 | 41560914 | 2.0.0.1 | 21256 | xex | 14.0.2909, 16.0.11886 | profile | LIBCMT 11886 | bink, demonware (bink Bink/2.2c, bink/2.2c; sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Call of Duty Advanced Warfare [2014-09-16] (PE+MAP+PDB) | 1 | exe×8 map×8 pdb×10 pe×8 xex×2 | 41560914 | 2.0.0.1 | 21256 | xex | 14.0.2909, 16.0.11886 | profile | LIBCMT 11886 | bink, demonware (bink Bink/2.2c, bink/2.2c; sdk 2.0.7645.0; zlib deflate 1.1.4) |
| Far Cry 4 [2014-10-17] (PDB) | 2 | exe×5 pdb×5 xex×5 | 555308CA | 0.5.0.0 | 21256 | xex | 14.0.2909, 14.0.7151, 15.0.8153, 16.0.11886, 16.10.16045 | — | LIBCMT 11886, LIBCPMT 11886 | bink, gamespy, havok, simul (bink Bink/1.9n, Bink 2.2, bink/1.9n; havok Havok-3.0.0, Havok-3.1.0, Havok-7.0.0; lua Lua 5.1; sdk 2.0.9407.0; zlib deflate 1.2.5, inflate 1.2.5, deflate 1.2.3, inflate 1.2.3, deflate 1.2.1, inflate 1.2.1) |
| Forza Horizon 2 (Prototype) [2014-05-04] (PE+MAP+PDB) | 1 | exe×4 map×14 pdb×12 xex×12 | 4D530AA4 | 1.4.0.0 | 11886, 21256 | xex | 14.0.2909, 16.0.10224, 16.0.11886 | — | CMTD 11886, LIBCMT 11886 | anark, fmod, granny (fmod FMOD/lib/flac-1.2.1; granny Granny/2.8.40, granny/2.8.40; sdk 2.0.9407.0; zlib deflate 1.2.3, inflate 1.2.3) |
| Forza Horizon 2 [2014-10-20] (MAP) | 1 | map×18 xex×12 | 4D530AA4 | 1.6.0.0 | 11886, 21256 | xex | 16.0.10224.0, 16.0.11886.0 | — | CMTD 11886, LIBCMT 11886 | anark, fmod, granny |
| Forza Horizon 2 [2014-10-20] (MAP) | 1 | map×18 xex×12 | 4D530AA4 | 1.6.0.0 | 21256 | xex | 16.0.10224.0, 16.0.11886.0 | — | CMTD 11886, LIBCMT 11886 | anark, fmod, granny |
| Strider [2013-12-05] (MAP) | 2 | map xex | 584113C7 | 1.0.0.0 | 21256 | xex | 16.0.11886.0 | — | LIBCPMT 11886 | wwise |
| Thief [2011-10-14] (MAP+PDB) | 2 | map×3 pdb×3 pe×3 xex×3 | 53510803 | 1.0.4869.0 | 21256 | xex | 14.0.2909, 16.0.11886 | Debug, XeRelease | LIBCMT 11886 | physx, wwise (physx PhysX-3.2.4, physx-3.2.4; wwise Wwise_2013.2, wwise_2013.2; zlib deflate 1.2.3, inflate 1.2.3) |
| Trials Fusion [2014-03-31] (PDB) | 2 | exe×2 pdb×2 xex×2 | 58411401 | 0.0.0.0 | 21256 | xex | 14.0.2909, 16.0.11886 | — | LIBCPMT 11886 | bink, fmod (fmod fmod4/lib/flac-1.2.1; zlib deflate 1.2.5, inflate 1.2.5, inflate 1.2.1, deflate 1.2.1) |
| Warface [2013-11-24] (PDB+XDB) | 1 | pdb×18 xex×3 | 4D530A99 | 0.0.0.0 | 21256 | xex | 14.0.2909, 16.0.11886 | Release | LIBCMT 11886 | fmod (fmod fmod4/lib/flac-1.2.1) |

## Middleware index

| Middleware | Games |
|---|---|
| anark | Forza Horizon 2, Too Human |
| bink | Alice Madness Returns, Alien Versus Predator, Amped 3, AMPED 3, Assassin's Creed - Revelations, Assassin's Creed 3, Assassin's Creed 4 Black Flag, Assassin's Creed Rogue, Bulletstorm, Call of Duty 2, Call of Duty 4, Call of Duty Advanced Warfare, Call of Duty Black Ops, Call of Duty Black Ops II, Call of Duty Future Warfare, Call of Duty Ghosts, Call of Duty Modern Warfare 2, Call of Duty Modern Warfare 3, Call of Duty World at War, Dance Central, Dance Central 3, Disney Universe, DJ Hero 2, Dragon Age 2, Driver - San Francisco, Fable Anniversary, Fallout - New Vegas, Fallout New Vegas, Far Cry 4, Gears of War, Gears of War 2, Gears of War 3, Gears of War Judgment, Halo 2 Anniversary MP Alpha, Halo 3, Halo 4, Halo Reach, Highlander, Just Cause, Killer Instinct, Marvel Ultimate Alliance 2, Mass Effect 2, Portal 09-02-2008, Project Gotham Racing 3, Robert Lundlum's The Bourne Supremacy, Silent Hill Homecoming, Skylanders - Spyro's Adventure, Spider-Man 4, Splatterhouse, Teenage Mutant Ninja Turtles - Out of the Shadows, The Saboteur, Tom Clancy's Ghost Recon Future Soldier, Trials Fusion, Wolfenstein The New Order, Zone of the Enders HD Collection |
| demonware | Call of Duty Advanced Warfare, Call of Duty Black Ops, Call of Duty Black Ops II, Call of Duty Future Warfare, Call of Duty Ghosts, Call of Duty Modern Warfare 3, Disney Universe, DJ Hero 2, Skylanders - Spyro's Adventure |
| fmod | Crysis, Crysis 2, Crysis 3, Crysis 3 Performance, DJ Hero 2, Forza Horizon 2, Halo - Combat Evolved Anniversary, Inversion, Marvel Ultimate Alliance 2, Robert Lundlum's The Bourne Supremacy, Tomb Raider Underworld, Trials Fusion, Warface |
| gamebryo | El Shaddai - Ascension of the Metatron, Fallout - New Vegas, Fallout New Vegas, Skate 2, Skate., Sonic Adventure, Splatterhouse |
| gamespy | Far Cry 4, Halo - Combat Evolved Anniversary, Inversion, Silent Hill Homecoming |
| granny | DJ Hero 2, Forza Horizon 2, Sacred 2 - Fallen Angel |
| havok | Amped 3, AMPED 3, Assassin's Creed - Revelations, Assassin's Creed 3, Assassin's Creed 4 Black Flag, Assassin's Creed Rogue, Burn, Dead Space 2, Dead Space 3, Dead Space Vertical Slice, DJ Hero 2, Far Cry 4, Godfather 2, Halo - Combat Evolved Anniversary, Halo 2 Anniversary MP Alpha, Halo 3, Halo 4, Halo Reach, Inversion, Just Cause, Kinect Joyride, Marvel Ultimate Alliance 2, NBA Live 2006, Need for Speed Undercover, Portal 09-02-2008, Ryse - Son of Rome, Silent Hill Homecoming, Splatterhouse, Star Wars Battlefront III, Test Drive Unlimited, The Saboteur, Too Human, X-Men The Official Game |
| kynapse | Alice Madness Returns, Sacred 2 - Fallen Angel |
| lua | Amped 3, AMPED 3, Burn, Dead Space 2, Dead Space 3, Dead Space Vertical Slice, Devil's Third, Fight Night Champion, Godfather 2, Kinect Joyride, Need for Speed Undercover, NHL 11, Sacred 2 - Fallen Angel, Skate 2, Skate., Spider-Man 4, The Saboteur, Too Human, X-Men The Official Game |
| massive ads | Driver - San Francisco, Need for Speed Undercover, Skate 2 |
| miles | Gears of War 2, Loadout |
| naturalmotion | Devil's Third, El Shaddai - Ascension of the Metatron, Hitman Absolution, Ryse - Son of Rome |
| physx | Alice Madness Returns, Batman Arkham City, Batman Arkham Origins, Bulletstorm, Devil's Third, Dragon Age 2, El Shaddai - Ascension of the Metatron, Fable Anniversary, Gears of War, Gears of War 2, Gears of War 3, Gears of War Judgment, Highlander, Hitman Absolution, Mass Effect 2, Metro Last Light, Robert Lundlum's The Bourne Supremacy, Sacred 2 - Fallen Angel, Silent Hill - Downpour, Teenage Mutant Ninja Turtles - Out of the Shadows, Thief |
| renderware | Dead Space 2, Dead Space 3, Dead Space Vertical Slice, Def Jam Icon 2, EA SPORTS Grand Slam Tennis 2, Fight Night Champion, Godfather 2, Monopoly Streets, NBA Live 2006, Need for Speed ProStreet, Need for Speed Undercover, NHL 11, Ryse - Son of Rome, Skate 2, Skate. |
| scaleform | Call of Duty 4, Call of Duty Black Ops, Call of Duty Black Ops II, Call of Duty Future Warfare, Call of Duty Modern Warfare 2, Call of Duty Modern Warfare 3, Call of Duty World at War, Hitman Absolution, Kinect Joyride, Mass Effect 2, Sacred 2 - Fallen Angel, Spider-Man 4, Splatterhouse, The Saboteur |
| simul | Far Cry 4, Robert Lundlum's The Bourne Supremacy |
| speedtree | Fallout - New Vegas, Fallout New Vegas, Gears of War 2, Gears of War 3, Gears of War Judgment, Too Human |
| umbra | Dragon Age 2, Mass Effect 2 |
| wwise | Assassin's Creed - Revelations, Assassin's Creed 3, Assassin's Creed 4 Black Flag, Assassin's Creed Rogue, Batman Arkham City, Batman Arkham Origins, Burn, Devil's Third, Dragon Age 2, Driver - San Francisco, Fable Anniversary, Halo 2 Anniversary MP Alpha, Halo 4, Killer Instinct, Kinect Joyride, Marvel Ultimate Alliance 2, Mass Effect 2, Monopoly Streets, Ryse - Son of Rome, Skylanders - Spyro's Adventure, South Park - The Stick of Truth, Splatterhouse, Strider, The Saboteur, Thief, Too Human, Zone of the Enders HD Collection |
