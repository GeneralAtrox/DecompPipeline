# Learnings from other Dreamcast decomps (2026-10-30)

Read for reference only (never copied into DP): `consolesplayingconsoles/dc-decomp-kickoff` (a Claude Code skill that
starts a Dreamcast decomp) and the games done with it: Crazy Taxi and Virtua Tennis, then Zombie Revenge (the
furthest: 98.7% of functions, 69.0% instruction coverage), Sonic Adventure, Boku Doraemon and Illbleed (kick-offs). Their bar is lower than DP's
(a function is done when its C agrees on random-plus-directed cases, with no gate on how much of it the cases ran:
82.8% of instructions on Crazy Taxi, 70.2% on Virtua Tennis; no hidden set, no mutation check), so their 100% is not
comparable with DP's. These are the ideas DP takes, in order.

## 1. Hardware functions counted apart (DONE 2026-10-30)
They leave functions with no C form out of the denominator. DP's dashboard now does the same for hardware functions
the port kit replaces (state "kit", docs/DASHBOARD.md): 100% means every function that can be proven is proven.

## 2. Junk-memory check (proof done 2026-10-30, round junk: tools/junk_ram.py, report only; a judge change awaits the user)
The runner starts RAM as the game image plus zeros (runtime tc_rt.c), so C that reads the wrong place in unset memory
can agree with the original by luck. Their `overreads.py` re-runs every case with unset RAM filled with junk.
1. Proof first: plant wrong-offset reads into a few passing functions' C and see whether today's judge catches them.
2. If not: a report-only mode (unset RAM filled with a per-address junk pattern, both sides) listing functions whose
   result changes; nothing recorded.
3. Making it part of the judge is a judge change: the user's yes and 0 lost kills.

## 3. Automatic input-hint search (after v8-novec reports how often aimed hints work)
For NOVECTORS and weak-coverage functions: read the function's compare constants, loop counters and pointer uses
from its asm, turn them into candidate `oracle/hints/ADDR.txt` lines (`range`, `genrange`, `ptr`), and keep a hint
only when `tools/oracle_probe.py` shows new valid vectors or newly reached blocks (their coverage-guided growth: keep
the case that reaches the most code, set one input to a value the code compares against, keep it if a new
instruction runs). No Ghidra script changes, so no other harness moves; only hinted functions re-prepare. It is the
automatic form of the block-targeted reach pass in docs/PROOF_GATES.md.

## 4. Cheat codes as names (tool BUILT 2026-10-30: tools/cheat_names.py; each download needs the user's yes)
libretro-database has lists for all four of our Dreamcast games, but for USA discs (Toy Commander and Power Stone 2
are Europe), so the tool warns on a region mismatch and lists apart the addresses no game function uses.
A cheat code is an address with a meaning ("infinite health P1"): a named global. A game-free kit tool reads a list,
decodes each code to its address and width, keeps codes for the exact disc (region and version), matches addresses to
the globals DP found, and hands the descriptions to the readable design step as naming hints (checked, not trusted).
Sources: libretro-database `cht/Sega - Dreamcast` (plain text, mostly raw addresses: start here), GameHacking.org,
archived Xploder DC / CodeBreaker lists (some CodeBreaker and Xploder codes are encrypted and need decoding).

## 5. One engine-notes file per game (with each readable round; in docs/NEW_GAME.md section 6 since 2026-10-30)
Each of their repos has the author's notes on the game's code and data (docs/engine.md). DP rounds leave notes in
many places; a game's readable round also writes one `docs/ENGINE.md` in the game folder: how its code and data are
organised, what was learned.

## 6. Other builds of the same game (when the user supplies one; each download needs the user's yes)
Their step 4 ("boosts") uses whatever other build exists: a prototype or debug build, another region, a later port,
the same game on another console. DP already carries C and names over between SH-4 builds (core/xmatch.py,
tools/import_from.py), so an SH-4 build of one of our games is worth importing only when it adds names: a debug or
prototype build with symbols or assert strings. Candidates: arcade (NAOMI) builds of Power Stone 2 and Spawn, other
regions, prototypes. A build for another CPU (Vigilante 8 on N64 or PS1) gives names only through matching debug
strings, never code.

Power Stone 2's arcade build (the user's NAOMI ROM set, 2026-10-30): the boot ROM holds a plain SH-4 program
(0x270000 bytes loaded at 0x0C021000, "POWER STONE 2 JAPAN"); 5,656 of the Dreamcast build's 7,593 functions of 32
bytes or more start with the same 16 bytes there. It has no symbols: its only names are 24 source file names of the
NAOMI library, which the Dreamcast build lacks. So no names; what it gives is the split between code shared with the
arcade game and code only the Dreamcast version has (shop, adventure, VMU), a hint for systems and readable design.

## Later (the mod stage; the user's call)
- A text map: every string with every pointer to it, and the text each disc file holds. It is the ground for
  translation mods (two of their projects, Boku Doraemon and Illbleed, are Japanese games aimed at translation).
- A per-game explorer: models, skeletons, animations, textures, sounds and text, with the readable C beside the
  original assembly (their "Shuriken Explorer"). A game-free kit tool of our own.
- Asset readers for shared Dreamcast formats (Ninja models and motions, PowerVR textures, ADX/AHX, DTPK/SMLT/SMPB
  sound banks), our own code.

## Not taken
Byte-identical rebuilds with the SDK's assembler (DP's goal is a proven native port), code-size reduction, and their
names or code.
