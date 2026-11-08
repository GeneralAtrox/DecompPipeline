# Work counters: baselines and proof

Each game's folder holds its scenes, tracked baselines, reason ledger and replays. How the counters work and how to run them:
`port/README.md`, "Work counters". Why: `docs/RUNTIME_TARGET.md`, "Work counters". The idea comes from zkit-gcnx (GPL-3); no
code was taken from it.

| Game | Build | Scenes (blanks counted) |
|---|---|---|
| Toy Commander | `port/tools/build.py --game play/src/ToyCommander/port --engine` | front end 100-3000, play 14000-15000 |
| Power Stone 2 | `port/tools/build.py --game play/src/PowerStone2/port --engine --sq-fast` | front end 300-2800, play 3000-5855 |
| Spawn | `port/tools/build.py --game play/src/Spawn/port --engine` | front end 100-3100, play 20000-21000 |
| Robo Pit | `Robopit/port/build.py` (`DP_ROOT` at the checkout) | logos 100-2900, front end 2900-8000 |

A baseline belongs to the kit and the game's source together, so a game's own change moves it too and needs the same reason in
the ledger. Use the build flags above, since they change the counts. The discs are found at the paths in each spec, on the main
laptop; another machine needs its paths in the spec's args.

## Proof (2026-11-01)

**Planted faults** (`plant.py`, results in `plant_results.txt`). Each fault was a real source edit in the shared kit, in a real
build. All six failed on the expected row, and the clean rebuild passed.

| Fault | Toy Commander (Dreamcast kit), front end | Robo Pit (Saturn kit), front end |
|---|---|---|
| Added allocation | `allocs` 1 -> 4357 | `allocs` 4 -> 5104 |
| Extra draw | `draws` 5808 -> 7260 | `draws` 5100 -> 10200 |
| Repeated upload | `uploads` 5808 -> 10164 | `uploads` 5100 -> 10200 |

**They repeat.** `accept` ran every scene of all four games twice with identical counts, in parallel with other jobs on a busy
machine, and `check` then passed all eight scenes on separate runs.

**They change nothing.** With counters on and off, the WAV and the sampled frames (every 200 blanks) were byte-identical: Toy
Commander's front end and play stretch, Power Stone 2's and Spawn's play stretches, and Robo Pit's WAV over both scenes. Robo Pit's
frames were not compared on and off.

**Cost when off.** Nothing is patched; each hook is one test of a flag. The hottest scene, Power Stone 2's play stretch, makes
about 59,500 of those tests per blank. At about a quarter of a nanosecond each, that is about 15 microseconds per blank, about
0.2% of 6.94 ms. This is computed, not measured: a timing run can't resolve a difference that small.

## What the first baselines show

Leads for the 144 fps work, not yet looked into:

| Scene | Finding |
|---|---|
| Spawn play | 749 textures created over 1000 shown frames |
| Toy Commander front end | 1452 textures created over 1451 shown frames, about one each frame |
| Spawn front end | 1369 textures created; up to 14 allocations in one blank |
| Power Stone 2 play | 296 draws per shown frame; 45 textures created and 92 allocations after warm-up |
| Spawn play | 267 draws per shown frame |

## What they don't catch

- Time. A draw that costs ten times as much counts the same. Timing runs stay the judge.
- A native worker thread's allocations, and a driver's or SDL's: only the game's thread and the exe's own imports count.
- A Map to read, left out because a poll's count depends on the GPU's speed.
- Work in a path no scene reaches.
- A kit's calls past `base/present.h`'s macros: `tests/test_work_counts.py` refuses them in `port/`, but a future kit outside
  `base`, `runtime`, `pvr` and `saturn` has to be added to that test.
