# Research: development symbols (PDB and MAP) for Xbox 360 games

Written 2026-11-07 for Too Human, as a guide for future Xbox 360 projects. It records which symbol sets were looked at,
what each one named in Too Human's retail build, and what was learned about choosing them.

**Sources.** Development builds of other Xbox 360 games that ship with their debug symbols. They are never committed,
pushed or published. DP takes only function names and prototypes from them, for the game's own workspace:
no code and no data.

## How a symbol set helps

A PDB names the functions of the game it came from. Any of that code that is compiled into our game as well can be
matched to it and named. There are two separate kinds of shared code, and a set only needs one of them to be useful:

| Shared part | What it names | What the set must have |
|---|---|---|
| **The Xbox 360 SDK libraries**, compiled into every game: graphics (`xgraphics`, `d3d9`, `d3dx9`), audio (`xaudio`, `xmedia`), `xapilib`, the C runtime | Most of the hardware-facing code, which is where a port's bring-up stops | Any engine. The closer its SDK build is to the game's, the more functions match byte for byte |
| **The engine and middleware**: Unreal Engine 3, Havok (`hk*`), Wwise (`ak*`) | Engine and library code | The same engine or library, from a nearby year. The SDK doesn't matter |

## Reading a game's SDK build

The XEX import-library records hold a version word for each library. Too Human's is `0x201B5300`.
Take `(word >> 8) & 0xFFFF` to get the build: `0x1B53` = **6995**, so Too Human was built with SDK **2.0.6995.0**.
The record is optional header `0x000103FF` in the XEX2 header. An update file (`.XED`) uses the same XEX2 header. A bare `.pe` (the image without its XEX header) has no such records; search it for `2.0.NNNN.0` version strings instead, which gives the libraries' versions rather than an exact link record.

## The sets, and what they named in Too Human

Too Human's retail catalogue has **88,187 functions**. "Named" means retail functions given a name from that set
by the matching pass. Its match kinds are: exact bytes, call graph, strings, shape and vtables.
The merged list `names_merged.json` holds
**15,574 names**: the development build, Skate 2, 807 of Godfather 2 SP's 855 new names (2026-11-07) and 77 of Need
for Speed Undercover's 113 (2026-11-08). The names another set gives otherwise stay out: 48 and 36. `names_merged_support.json`
counts each name's supporting sets (9,568 have two or more; Need for Speed Undercover backed 5,221 names already there). Before Godfather 2, every other set's new names added gave **16,228 (18.4%)**.
"N new" counts names no set before it in the pass had given. The matching tool is
`targets/xbox360_ppc/tools/pdb_names.py` (`--pdb` and `--xex`, or `--image` for a bare `.exe`; `--prior` counts the new names).

| Set (build date) | Engine | SDK build | Named in Too Human | Mainly |
|---|---|---|---|---|
| **Too Human's own development build** (2007-07-20) | Silicon Knights' own Unreal Engine 3 branch (its module paths say `SKRealEngine`) | 5632 | **14,138 (16.0%)** | Everything: SDK libraries, Havok, Wwise, Anark and 2,628 of the game's own functions. By far the best source |
| Gears of War 2 (2008-09-14) | Unreal Engine 3 | 7645 | 5,602 (6.4%); 701 not named by any other set | SDK graphics: 2,351 of 2,742 `xgraphics`, 501 `d3d9ltcg` |
| Mass Effect 2 (Japan, 2010-09-10) | Unreal Engine 3 | 7978 | 5,721 (6.5%); 410 new | The old Wwise libraries (`akaudioengine`, `akmusicengine`) |
| Gears of War (2006-09-21) | Unreal Engine 3 | about 3802 (`.pe` only, no XEX header: the image holds version strings 2.0.3529.0 and 2.0.3802.0) | 4,408 (5.0%); 36 new | SDK graphics. Only 126 of the game's own functions |
| Halo 3 (2007-03-09, MAP only) | Bungie's own | 5426 | 1,719 (1.9%); 31 new | Old Havok (`hkcollide`, `hkdynamics`) |
| Skate 2 (2008-11-05) | EA's own | 7645 | 1,255 (1.4%); 552 new | SDK audio and system: `xaudioltcg`, `xhv`, `xapilib`, `d3d9ltcg` |
| XAM system library, debug (2015-04-22) | — | — | 169 (0.2%) | System calls |
| Xbox 360 kernel, debug (2015-04-22) | — | — | 24 (0.0%) | Kernel calls |
| Bourne (2008-01-13), `XeReleaseLTCG` build | Unreal Engine 3 (High Moon) | 6534 | 4,743 (5.4%); 12 new | SDK graphics: `xgraphics`, `d3dx9`, `d3d9ltcg` (4,463 SDK names in all). 176 engine-side (`engine`, `core`, `hm_engine`, `xenonshared`, the game's modules): more than Highlander, all but 12 already named |
| Bourne, `XeRelease` build (`XeDebug` skipped) | Unreal Engine 3 (High Moon) | 6534 | 4,313 (4.9%); 1 new beyond the LTCG build | The same libraries; 147 engine-side |
| Highlander (2008-04-08) | Unreal Engine 3 | 6534 | 4,101 (4.7%); 34 new | SDK graphics: `xgraphics`, `d3dx9` (3,914 SDK names). 132 engine-side (`xenonshared`, the game's modules). New: `xgraphics`, `xenonshared`, `xmp`, PhysX |
| The Saboteur (2008-06-26), `WildStar_p` | Pandemic's own | 6274 (its `.XBLD` section) | 4,470 (5.1%); 286 new | Old Havok (`hkcollide`, `hkdynamics`, `hkbase`: 2,608) and old Wwise (`akaudioengine`, `akmusicengine`, `aklowerengine`: 1,106). New: 261 Wwise (`akmusicengine`, `akaudioengine`, `akstreammgr`) |
| Silent Hill Homecoming (2008-06-19), `Slayer_f` | not Unreal Engine 3 | 6534 (its `.XBLD` section) | 2,327 (2.6%); 19 new | Newer Havok (`hkpcollide`, `hkbase`, `hkpdynamics`: 1,513) and SDK (`d3d9`, `xaudio`, `libcmt`: 663) |
| Godfather 2 SP build (2008-11-07), `f_mt` (final) build; `q_mt` and `r_mt` also run (`d_mt` debug and `ru_mt` skipped) | EA's own: no Unreal module paths among its 102,495 functions; EA libraries (`ears_*`, `plasmasdk`, `rwaudiocore`, `apt`, `lua`) | 7776. Its two `-replay` XEXes link 6995 but have no PDB or MAP | **6,485 (7.4%)**; 821 new. All three builds: 6,782; 855 new | SDK (5,130 names, 4,048 exact) and newer Havok (`hkpdynamics`, `hkpcollide`, `hkbase`). New: `xgraphics` 308, `hkpdynamics` 114, `libcmt` 109, `d3d9` 102, `xaudio` 35. All three builds settle 1,224 of the earlier sets' 2,854 disagreements (604 exact) and adds 195 (63 exact) |
| Godfather 2 MP build (2008-11-18), `f_mt`, `q_mt`, `r_mt` | EA's own (as the SP build) | 7776 | 6,486 (7.4%) from `f_mt`; 6,809 from all three; 0 new beyond the SP build | The same libraries as the SP build: 28 more retail functions named, all already in the merged list. Settles 11 more disagreements, adds 11 |
| Need for Speed Undercover (2008-08-19), `SpeedMilestoneOpt-enUS`, an optimised build linked 2008-08-14 (its `.exe` is an LZX XEX, decompressed with `xexmagic -d` and run with `--image`) | EA's own (RenderWare, Lua) | **6995**: all 12 static libraries in its XEX header | 6,461 (7.3%); 113 new beyond the merged list, which already held Godfather 2's names; 77 merged | SDK (4,974 names, 4,337 exact: `xgraphics` 2,186, `d3dx9` 1,003, `d3d9ltcgi` 350, `libcmt` 303, `xhv` 225, `xaudio` 201) and newer Havok (`hkbase`, `hkpcollide`, `hkpdynamics`). Settles 1,431 of the 2,854 disagreements (739 exact), gives a third name for 72 and adds 90 (31 exact) |

**Looked at and not kept.** Each was recorded so nobody checks it again:
- Burnout Paradise Beta 5 (2007-10-31, SDK 6274) and its 2008-05-31, 2008-07-10 and 2008-08-14 updates (SDK 6534):
  only Criterion's own binary name maps, no PDB, and their SDKs are already covered.
- Call of Duty Black Ops II, Halo Reach, Halo 4, Bulletstorm, Thief, Batman Arkham City, Batman Arkham Origins and
  Marvel Ultimate Alliance 2: 2009–2014 SDKs, with the newer Havok (`hkp*`) and Wwise (`aksoundengine`) or neither.
- Fable Anniversary, Fable The Lost Chapters (Mac), Resistance (PS3) and X-Men Destiny (Wii): not Xbox 360 code.
- Xenon Recovery 6995 with symbols (two builds, linked 2008-02-15 and 2008-02-29): the console's system software at
  Too Human's version, meaning the kernel (`xboxkrnlc`/`xboxkrnld`), XAM, the debug monitor `xbdm` and dashboard parts
  (`hud`, `signin`, `vk`, `voicemail`, `connectx`, `createprofile`, `xmsgr`), plus a 19 KB `xapi` stub. None of this is
  linked into a game, so it names nothing in Too Human's code; the 2015 XAM and kernel symbols named only 169 and 24.
  Its use is as a reference for how the 6995 system calls behave, for the kit's kernel layer.

## What was learned

1. **The game's own development build beats everything else together.** It named 16% of Too Human, including the
   game's own code, which no other set reaches. Look for one first.
2. **Engine code barely carries across games.** Gears of War named 126 of Too Human's own functions and Gears of War 2
   named 248, though both are Unreal Engine 3. Of the 2008 Unreal Engine 3 builds, Bourne's LTCG build matched more of
   Too Human's engine-side code (176) than Highlander (132), but between them they added only 28 engine names nobody
   else had. Licensees changed the engine heavily, and Too Human's branch is
   Silicon Knights' own. Other Unreal Engine 3 games help most with the SDK and middleware code they link.
3. **For SDK code the SDK build matters, not the engine.** Gears of War 2 and Skate 2, on SDK 7645, named most of the
   graphics and audio libraries. Four more sets on 6274 and 6534 then added only 18 SDK library names between them:
   builds on either side of 6995 name the same SDK functions. So an exact 6995 set is no longer worth hunting for names.
   Taken if found, it would confirm byte for byte the names other sets gave by call graph or shape, and settle the 300 to
   1,000 retail functions each set names differently. Godfather 2 (SDK 7776) is the nearest set above 6995 so far. Its
   final build named 4,048 SDK functions at the exact tier (the same bytes once addresses are masked). Of the 2,854
   retail functions the earlier sets name differently, its three builds agree with one of the two names on 1,224 (604 exact): 455
   with the merged list and 769 with the other set. It gives a third name for 158 and adds 195 new disagreements (63
   exact). Need for Speed Undercover's optimised build (2008-08-14) is the first 6995 set found with a PDB: all 12
   static libraries in its XEX header are 6995. It named 4,337 SDK functions at the exact tier, 289 more than
   Godfather 2, and settles 1,431 of the 2,854 disagreements (739 exact). Exact means the same instructions once
   addresses are masked, and most of those functions matched the 7776 build as well. So it favours 6995 without
   proving it byte for byte.
4. **The release date predicts the SDK build badly.** Games built from April to August 2008 were still on 6274 or 6534;
   from September they were on 7645. Too Human's 6995 was in use only briefly, so few games have it. Read the XEX
   rather than guessing from the date.
5. **Prefer the shipping configuration** (`XeReleaseLTCG`, or a final/profile build). Retail games are built that way,
   so the bytes match more often: Bourne's LTCG build named 4,743 and its plain release build 4,313, only 1 of which no other set had. A debug build (`_d`, `XeDebug`) is compiled differently and matches far less.
6. **Middleware is version-specific too.** Old Havok (`hkcollide`/`hkdynamics`) and old Wwise
   (`akaudioengine`/`aklowerengine`) came from Halo 3, Mass Effect 2 and The Saboteur, not from the newer `hkp*` or
   `aksoundengine` games. The Saboteur's Wwise added 261 names, the most of any 2008 set; Silent Hill Homecoming's
   newer `hkp*` Havok matched 1,513 functions through the shared base libraries but added 5.
7. **A MAP without a PDB adds little** once a PDB of the same SDK is in use: it gives names but no types or prototypes.

## For a new Xbox 360 project

1. Read the game's SDK build from its XEX.
2. Search for the game's own development build first.
3. Then look for a set with the same SDK build, or the nearest on either side, for the SDK libraries.
4. Then look for sets with the same middleware versions (read the library names in the PDB).
5. Engine-family sets come last.

Keep a table like the one above in the project's notes.
