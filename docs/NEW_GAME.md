# Decompiling your own Dreamcast game

This is the route Toy Commander took, written for the next game. The pipeline holds no game data: everything
game-specific lives in a **project workspace** that you create, and the disc image stays yours. A worked example
of a finished workspace is the Toy Commander repository (`decomp/`: project.json, matched and readable C,
headers, names, the design briefs and the progress log).

The short version: the target's own translator writes every function's raw C, DP's judge checks it on the
original code's test vectors, and an agent works only on what the translator could not pass (step 4). The
readable round and a native port follow (steps 6 and 8); a port can already run on the translator's C.

## What changed since the last public update (26 October 2026)

- **Port from translator C** (step 8): a port compiles the translator's judged C wherever readable C does not
  pass yet, for the main binary and for overlays, so a game boots before its readable round is done.
- **Judge, SH-4:** fipr/ftrv rounded once, as the console computes them (fmac already was), and lint R9/RR8
  refuse C that rounds the code's fmac twice; FPSCR.SZ taken from the callers' code, so functions that run with
  64-bit moves are judged in that mode; callees that hand results back in r4..r7; hand-written assembly modules
  judged and translated as one unit (`tools/asm_modules.py`, `tools/mid_entries.py`); a callee's stores into the
  store queues; hints for callee results drawn from a range, run flags and mocked readers. Each change landed
  only on proof that the judge lost none of the mutants it killed before.
- **Prepare:** `ghidra_kill_s` gives a slow machine more wall time without changing any stamp; a failed prepare
  keeps the old harness. `tools/selective_restamp.py` refreshes only the harnesses a judge change touches.
- **Port kit** (port/README.md lists every module; port/CONTRACT.md each SDK module and the game.json key that turns it on): Kamui 1, more of
  Kamui 2, ADX and Sofdec movies with sound, a game's own sound driver on an emulated AICA ARM7, the VMU screen,
  save file images, the console's heap layout, fibres, gdFs and ADXF, controllers set up without
  pdInitPeripheral, translucent modifier volumes, the game's own float maths in place of the host's, and the x87
  precision following FPSCR.PR.
- **Testing a port:** `port/tools/lockstep.py` compares a port with Flycast frame by frame (RAM and the display
  lists); `port/tools/bu_sweep.py` runs the port past many blockers in one go and lists each one.

## One command: tools/AutoOnboard/autoonboard.py

A new game starts with the onboarding tool (every future game is static decompilation first). Put the disc image in
the game folder (`C:\Projects\Phangrade\Integrations\<Console>\<Game>`), then:

```
py -3.12 tools/AutoOnboard/autoonboard.py --project <game folder> --console dreamcast --name "<Game>" --host build box B --remote-project <its folder there>
```

(on a build box itself: `--here` instead of `--host`). It runs the stages project, machine, disc, extract, census, symbols,
boot, register, estimate, translate and gaps, each calling the tools below and writing a verdict to `<game>\.work\onboard\`; a
second run resumes where the first stopped, `--from`/`--only` re-run a stage (later verdicts go stale, kept), `--until` stops after one, `prove` checks the build, `--dry-run` shows what each would call.
Anything missing stops it with a hard error naming what would supply it. It always ends with
`.work\onboard\OVERVIEW.md`: where the project is and the next steps. The scaffold's `.gitignore` keeps the disc,
game data and every decompiled or translated C file out of git. Built so far: project for every console; disc,
extract, census, estimate, translate and boot for the Dreamcast, the Xbox 360 and the GameCube (symbols for the
Xbox 360 and the GameCube; their boot lists inputs only); gaps for every console; the rest are declared and
stop with "not built yet: <what it will call>". The census count is what analysis finds, a floor: Toy Commander needed
3.5% more (24 added by hand, 122 by catalog rules written during that project). Giving a scratch census the 24 hand
entries finds only 7 of the 122; tools/data_funcs.py named 13 real ones but, fed in, found 15 of 146 with 1 false extra,
so entry tables are not scanned: the census verdict states the expected shortfall and GAPS.md lists it as a next step.
Estimate prepares and translates a
seeded sample stratified by size (`.work\onboard\estimate_detail.json`: projected hours with a 10-90% range; the translator's
PASS share also widened to the sample's 95% Wilson interval, so 60 of 60 PASS reads "94-100%", not "100-100%").
Translate runs `wave.py run --stop-after translate --tx-chunk N` in bounded runs (one under 50 minutes, or
`--seconds`): each chunk is prepared, translated and recorded before the next starts, N sized from the estimate so a
chunk ends within two thirds of a run, and the next run translates only what is left. It needs no `harness_reuse`
(recording refuses a stale harness); the agent step after it does, and turning that on stays a person's decision. A
run that does not finish writes a partial GAPS.md that says how much is done. Gaps writes `.work\onboard\GAPS.md`:
untranslated functions by reason, refused or unjudged ones, and for the Dreamcast the library calls the kit has no
function for (`port/tools/kit_features.py` reads the list from `port/sdk/*.c`); other consoles say "kit gaps: not
known - no feature list". Boot writes a starter `port/game.json` (the entry function left to a person), names every
input a build still lacks (the entry, a function registry, the translator's C judged in port form by `dp_judge.py`,
MSVC, ninja) and, once they are there, builds into `port/build/onboard` and runs 20 s headless on the fixed clock:
built or not, vertical blanks reached, scenes drawn, the first error line. Gaps and boot need only the census, so they
also run on a partial pass (`--only gaps`, `--only boot`). When the job registry points to an earlier finished job
whose result this project lacks (a project made again), the stage runs it once more with `--changed` saying so.

**Xbox 360** (`tools/AutoOnboard/onboard_x360.py`, `tools/AutoOnboard/onboard_symbols.py`): the disc is an `.iso` (XDVDFS: offset 0, XGD1,
XGD2, XGD3) or a bare `.xex` in the game folder; title updates (delta patches) and LZX-compressed XEXes are refused. A
retail-encrypted XEX needs the console's retail key on the build box (`"xex_retail_key"` in DecompPipeline's
`config.local.json`, or `DP_XEX_RETAIL_KEY`; only the user supplies it, and no stage prints or copies it). Without it
the disc stage stops naming it, unless `--inputs <workspace>` names a workspace of the same title made where the key
is: its four catalog inputs are copied once the XEX header (title ID, original PE name, load address, image size,
entry point) matches. Extract runs `x360setup.py setup --no-ghidra`; the disc's files go to `game-data\` only under 5 GB
(Too Human's 6.6 GB are not extracted: the user says where). Symbols finds development builds' PDB or MAP files beside
their own XEX in `<game>\reference`, in `config.local.json` `"symbol_sets": {"xbox360": [...]}`, or in
`Integrations\Xbox360\reference\symbols`; a named folder that is absent is an error. It ranks them (same title ID, then
SDK build), matches the best four with `pdb_names.py --verify`, and accepts a set only when callee agreement on its
exact-byte pairs is at least 90% over at least 50 pairs and 50 calls, and at least 50 points above a deliberately wrong
pairing. A verified set counts as this game's only with at least 50 exact pairs in the game's own modules; else only
its SDK library names are used (Too Human's PDB on Mega Man 10 passes the callee check through the shared XDK code,
853 library names and 55 wrong game-class names). "No verified match" is ok with zero names. The names stay in `.work\onboard\symbols\names.json`. Census runs
`x360setup.py ghidra` (the whole catalog, then decode audit gate G2). Gaps also groups refusals by the instruction that
caused them (`mftb`, `dcbzl`, unconfirmed VMX ops; the catalog scan's own refusals through `ppcscan.refusing_ops`) and
lists the kernel and XAM imports the game calls that the kit has no case for (`port/tools/kit_imports.py` reads the
kit's runtime source). Boot lists what a bring-up needs (a Direct3D cut for this image from `boundary.py`, the disc's
files, MSVC, vcpkg's SDL3) and does not run one yet.

**GameCube** (`tools/AutoOnboard/onboard_gc.py`, `tools/AutoOnboard/onboard_symbols.py`, `targets/gamecube_ppc/tools/gc_names.py`): the disc
is a plain `.iso`/`.gcm`, CISO, RVZ or WIA image (`gcdisc.py`); a Wii disc is refused. Disc reads the header, the
file system, main.dol, every REL module (loose, Yaz0 or in a RARC archive) and the Dolphin SDK build lines. Extract
runs `game_setup.py setup --no-ghidra` (`gcdisc.setup`: the RELs are linked onto main.dol into `linked.dol` by
`gcrel.py`, so the census catalogues their code; a link error stops it) and copies the disc's files to `game-data\`
under 5 GB. Census is the Dreamcast's (`game_setup.py ghidra`) plus a REL check: a module not linked, or linked with no
catalogued function, is an error naming it. Symbols runs after the census, because the names are matched to its
catalog: set folders (an ELF with a symbol table, or a CodeWarrior map with its `.dol`) in `<game>
eference`,
`"symbol_sets": {"gamecube": [...]}` or `Integrations\GameCube\PDBs`, facts from `docs/GAMECUBE_SYMBOL_SETS.json`,
ranked by game ID, then the nearest SDK OS build; `gc_names.py --verify` matches with `pdb_names.pair()` and the same
acceptance rule as the Xbox 360 (a "library" is the SDK, MSL, MetroTRK or known middleware). Gaps says there is no
GameCube port kit and lists, from the game's code, the hardware register blocks it touches and the SDK and OS
functions it calls by matched name. Boot lists the inputs only.

## 0. Machine setup (once)

- Python 3.12, Ghidra 12.x and the JDK it asks for: the line `application.java.min=` in `<ghidra_home>/Ghidra/application.properties` (it is 21 in Ghidra 12.1.2, which our machines run; an outside user reports `application.java.min=25` in Ghidra 12.2, where JDK 21 made sleigh fail with "java could not be found"; we have not run 12.2), Visual Studio 2022 with the C++ workload (the judge builds every
  candidate with MSVC), and the `claude` CLI logged in (it runs the agent sessions).
- Create `config.local.json` next to `config.json` (git-ignored, merged over it key by key) with this machine's
  paths: `ghidra_home`, `java_home`, `vcvars` (vcvars64.bat) and, for tools/sdk_fid.py only, `sevenzip` (else
  7z on PATH). Tracked `config.json` keeps them null; a command that needs one stops naming the key. Override any
  other default there too (`claude` is the CLI command). To build and judge with the same compiler as the other
  machines, set `vcvars_args`, vcvars' own arguments: the Windows SDK and the MSVC toolset, e.g.
  `"10.0.28000.0 -vcvars_ver=14.44"`. Both must be fully installed (the SDK with its UCRT); a cached compiler
  environment of another setting is loaded again. With `vcvars_args`, `vcvars` must be Visual Studio's own
  vcvars64.bat (in `VC/Auxiliary/Build`), not a wrapper that passes its own pins: those would be passed twice.
  The judge warns when it is not. For the regression suite, `tests/patterns.local.json`
  (also git-ignored) maps each `projects` key of tests/patterns.json to its workspace path.
- Install the SH-4 language once: `python targets/dreamcast_sh4/ghidra_lang/install_lang.py <ghidra_home>`.
- The translator needs nothing more: it is part of DP (`targets/dreamcast_sh4/translator/`, sh4-translate), and
  config `translate_first` (on by default) runs it before any agent.
- Once a workspace exists (step 2), `python tools/doctor.py --project <workspace>` checks it for consistency.

Every long command should run under `tools/bounded.py SECONDS ...`, which kills the whole process tree on time-out.

## 1. Get the main binary off your disc

Choosing between regions of a new game: check which regions libretro-database's cheat list covers (`cht/`, for the
consoles where it has one). When the regions are otherwise equal, prefer the one with a list: its addresses then apply
directly (see the cheat-hints line in the readable stage). It is a tiebreaker only; leftover symbols or debug names,
and speed and language needs, come first. Record the choice and why in the game's notes. This is for new games; an
existing game stays on its disc. A list for another region still helps: Toy Commander's USA address falls 8 bytes after
our Europe disc's missile decrement, so check such hints near the same function.

The short way does steps 1 and 2's setup in one command, for a Dreamcast, Saturn or GameCube disc. It extracts the
binary into the workspace, writes `project.json` and `oracle/TcInputs.java`, runs `decomp.py setup`, and then
checks Ghidra's decoding of every function against the target's own decoder:

```
python tools/bounded.py 3600 -- python tools/game_setup.py setup <image.gdi|.cue|.iso> <new_workspace>
```

(Saturn overlays: `--file NAME@ADDR`; carried decisions: `--fields FILE`; see the tool's docstring. The Xbox 360 has
`targets/xbox360_ppc/tools/x360setup.py`.) The hand steps it replaces follow; you still need the scan below to
find overlays and the library list.

Fill in the game's `LOCATIONS.md`, which setup leaves in the game folder: per machine, where its disc, BIOS, emulators,
extracted data, clones, launcher mirror and logs are (AGENTS.md). Another route to a workspace gets the skeleton from
`python tools/game_setup.py locations <workspace>`.

Then add the game to this machine's progress page (`Dashboard.bat`, docs/DASHBOARD.md); nothing is added by itself:
`python tools/dashboard.py add "<game folder>"`. It writes a starter `progress.json` there. Keep that file current:
what is being done now and next, the port milestones (builds, boots, playable, matches the original) and open
playtest bugs. Everything else on the page is read from the workspace.

```
python targets/dreamcast_sh4/tools/gdrom.py info    <image.gdi|.cue>
python targets/dreamcast_sh4/tools/gdrom.py extract <image.gdi|.cue> <new_dir>
```

`extract` writes the boot file named in IP.BIN (for example `1ST_READ.BIN`); it loads at 0x8c010000. Keep it
outside the workspace and any repository (`<game>/artifacts/disc/`, the workspace's `binary` points at it).

Then check what else on the disc is code, and how the boot file is linked:

```
python targets/dreamcast_sh4/tools/gdrom.py scan <image.gdi|.cue> <disc_scan.tsv>
```

It scores every file as SH-4 code and lists the candidates. The boot file is the decomp's main binary. Any other
candidate that is the game's own code (an overlay, as Spawn's 28 are) needs a workspace of its own; another
program (Power Stone 2's DreamPassport browser, the Sega loaders MAIGO/SG_DPLDR) is not the game. Small files
can be false hits: look at a candidate before you act on it. For each candidate it also prints:
- the link form: a boot file linked at `0x0c...` (the P0 mirror; Spawn and Power Stone 2 are) needs
  `"seed_functions": true` in project.json, or Ghidra finds only a fraction of the functions;
- the library version strings (Kamui, Shinobi, the NAOMI LIBRARY, CRI, modem...): this is the library list you
  name in step 3.

## 2. Create the workspace

A folder with `project.json`. Toy Commander's is the template; the fields a new game needs first:

| field | meaning |
|-------|---------|
| `name`, `ghidra_name` | display name; Ghidra project name |
| `target` | `dreamcast_sh4` |
| `binary`, `binary_sha256` | the boot file (relative to the workspace) and its hash, so a wrong file is refused |
| `game_end` | first address of the SDK code linked after the game (see step 4). `game_setup.py setup` writes the end of the boot file (`8c010000` + its size) for a Dreamcast game, so the game range is the whole boot file until you narrow it; never `0`, which makes the range empty and the fixups (TcRuns, TcJumpIn, TcCont) run nowhere |
| `references` | 2-3 functions you decompile by hand first: the controls that prove the judge works (`[]` until setup) |
| `broken_control` | one reference plus a deliberately wrong copy of it, which must FAIL |
| `port` | the monitor UI's port (one per workspace that serves at the same time) |
| `seed_functions` | `true` for a game linked at `0x0c...` (step 1's scan says so); harmless otherwise |
| `ghidra_timeout_s` | one function's prepare (default 900 s); setup, catalog, seed and graph get `ghidra_setup_timeout_s` (default 3600 s) |
| `ghidra_kill_s` | the wall time before one function's prepare is killed (default: `ghidra_timeout_s`). The oracle budget in the facts still comes from `ghidra_timeout_s` (counted in work with `oracle_work_per_s`), so raising this lets a loaded or slower machine finish the same harness without changing any stamp; machine-local, e.g. in config.local.json |
| `sq_copy_callees` | block copies whose stores into the store queues the oracle must count: `{"callee": {"rK": "lo-hi"}}`, the register holding the queue offset and its range (Spawn's vertex copy 8c0d7b40). Only when a row that copies to the queue fails or draws nothing |

Then, in this order (the references need the catalog and the disassembly):

- copy `targets/dreamcast_sh4/template/TcInputs.java` to `oracle/TcInputs.java` (the test-vector generator;
  adjust it when your game's functions need different inputs);
- `python decomp.py --project <workspace> setup` builds the Ghidra project and the function catalog (Power Stone
  2's 3 MB boot file: 7 minutes, 9,597 functions);
- pick 2-3 small game functions from `state/catalog.json` (no `hw_refs`/`sys_ops`, a leaf, one with a call, one
  with floats), `decomp.py export work/refs ADDR...` writes their disassembly and Ghidra's C, and `decomp.py
  prepare ADDR` shows the signature the harness expects (`harness/ADDR/meta.json`);
- write the reference C in `reference/ADDR.c` (guest-memory API: `rd32`/`wr32`..., `tc_call` with `TcArgs`; TC's
  and Spawn's `reference/` are examples) and the broken one (one changed constant);
- run the controls: `decomp.py check ADDR --work reference/ADDR.c` must PASS, the broken file must FAIL; then list
  them in `references` and `broken_control`.

The judge is behavioural: each candidate runs on the same test vectors as the original code (under the SH-4
model in `targets/dreamcast_sh4/runtime`) and must give the same registers, memory and calls. Byte matching
against a compiler is not needed, so games built with Hitachi SHC (which nobody has) work.

## 3. Split game code from the Sega SDK

Most Dreamcast games link Katana SDK libraries (Kamui, Shinobi, Ninja, sound, the CRI middleware) after the
game. You don't decompile those; you name them.

- `tools/sdk_fid.py build` turns an SDK release you own into Ghidra Function ID databases (once per machine: the
  databases serve every game); `tools/sdk_fid.py apply --project WS --out DIR --fidb R10.1=PATH --fidb R9=PATH`
  names the SDK functions in your game (DIR outside any repository; Power Stone 2: 3 minutes).
  - What `build --sdk` accepts (read from `tools/sdk_fid.py`, `resolve_source`): a folder, an installer `.iso`, or an
    InstallShield 5 `data1.hdr`. A folder that holds no `data1.hdr` is used as it is, so an SDK you have installed or
    extracted yourself (for example with `unshield`, which also handles multi-volume and InstallShield 6 sets that
    `tools/is5cab.py` refuses) can be passed directly. It looks for the SDK's `libsplit.exe` and `elfcnv.exe` under
    it (`--tools DIR` if they are elsewhere).
  - Japanese SDK line: the examples above name only the English R9 and R10.1 releases. The rest of this note is an
    outside user's finding, not ours, and we have not checked it: their Dreamcast game links Shinobi 1.70 / Ninja
    01100114 (mid-2000), the English R11b (Feb 2001) has only Shinobi 1.66, and the Japanese R2.00J (Sep 2000) looked
    like the match. Read which SDK release your game links from its version strings (`gdrom.py scan` lists them).
- Find `game_end`: the first library function after the game's code. FID names the SHC C runtime (`_divls`,
  `memcpy`, `sprintf`...): on TC, Spawn and Power Stone 2 the runtime starts the library range. Code an earlier
  workspace decompiles as game code (`xproject.py ... match` / `report`, step 4) belongs below it. Set it, then
  re-run `sdk_fid.py apply` (it reuses the import; it only re-merges, 20 s).
- `tools/sdk_classify.py --project WS --fid DIR --from EARLIER_WS ...` sorts the library range into SDK and the
  studio's own engine code. The SDK databases lack the libraries Sega shipped apart (the NAOMI LIBRARY, the
  network and sound layers): `--from` each earlier workspace counts a byte-identical copy of its library code as
  SDK (on Power Stone 2 the engine candidates fell from 678 to 5).
- Engine functions linked beyond `game_end` go in `extra_functions` (read `DIR/engine_functions.txt` first: a
  small unnamed function in the middle of an SDK block is SDK code the databases miss).

The SDK, and the databases built from it, are Sega material: keep them out of any repository.

## 4. Match round: the translator first, agents only on a FAIL

Each function's raw C is first written by the target's own translator (Dreamcast: sh4-translate). DP's judge,
unchanged, decides it exactly as it decides an agent's: same lint, build and test vectors. A PASS is a match that
costs no agent tokens (its ledger row says `"source": "translated"`). Only a FAIL goes to an agent, which starts
from the translated C and its first differences. On Toy Commander, Spawn and Power Stone 2 the translator's C
passes for nearly every matched function (its README, `targets/dreamcast_sh4/translator/README.md`, has the
figures and the causes of the rest). How the stage works: docs/TRANSLATOR_STAGE.md.

- **Without agents first (recommended):** prepare harnesses ahead (`tools/prepare_list.py --project WS @FILE`, or
  `tools/idle_prepare.py`), then `python tools/translate_backlog.py --project WS --record --json OUT.json` judges
  the translator's C on every harness the workspace has and finishes each PASS as a worker would. It never starts
  Ghidra; stop the server first. A harness made before a judge change has an old stamp, and `--record` records
  nothing on it: re-prepare it first.
- **Hands-off, in one start:** `python tools/wave.py run --game WS` runs this whole round and the readable round
  (section 6) with no one at the controls: prepare (served slots), translate and record, an agent only on each
  translator FAIL (`decomp.py investigate`, a few at once), then `refine.py run` over the matched functions. It
  needs `"harness_reuse": true` in project.json, resumes where it stopped (`--stop-after STEP` ends after a step,
  `--tx-chunk N` translates and records N functions at a time), and writes
  `state/wave_game_summary.json`; the run is one start in `state/operator_steps.jsonl` (tools/wave_game.py).
- **Then the agents, for what is left:** `python decomp.py --project <workspace> serve` opens the monitor UI.
  Start workers (Haiku by default): each one takes a function, runs the translator, and only on a FAIL writes raw
  C against the guest-memory API until it gets a PASS. Callees are mocked, so functions are independent.

- Reuse earlier games first: for each earlier workspace, best first, `xproject.py --project WS --from OLD match`,
  `report`, `stage` and `names` (`match` rewrites `state/import.json`, so run the four per workspace; a later
  `stage` overwrites an earlier one's file for the same function). A worker judges a staged `import/ADDR.c`
  before any agent run: a PASS is matched for free, a FAIL is the agent's starting point (Power Stone 2: 532
  staged from Spawn and Toy Commander, most passing with no agent).
- Without the UI: `POST /api/start {"workers": N}` adds N workers and `POST /api/loop {"enabled": true}` keeps
  each one taking the next function (without it a worker does one function and exits); `GET /api/state` has the
  counts and the log, `POST /api/stop` (optionally `{"worker": ID}`) stops them. `project.json` `priority` (a list
  of addresses) puts the pilot at the front of the queue. Every function is prepared in Ghidra on demand, one per
  slot (`ghidra_parallel`), so the slots, not the workers, cap the rate of cheap functions; each slot is a Ghidra
  process (up to `GHIDRA_HEADLESS_MAXMEM`, 2G by default).
- Pilot about 20 functions first and read the failures. A failure class that repeats is a tooling problem: fix
  the pipeline (and run the gate, below), not the functions one by one.
- Functions that touch hardware registers or system instructions are excluded; record their intent instead.
- `decomp.py audit` lists every unmatched function by blocker kind.
- `tools/stack_uninit.py` lists functions that read a stack word no path wrote; `--pin` fixes it to 0 in every
  vector (through the `genrange` hint of `oracle/TcInputs.java`; a copy older than 2026-10-16 lacks it: copy the
  template again), then re-prepare.
- Call sites no test vector reaches are never compared, so matched C there is unverified (Toy Commander's port
  crashed on such calls). The oracle's reach pass (on by default: target.json `reach_calls`) adds vectors that
  reach them and gives the reason for each call it cannot reach; a matched function's ledger row lists them in
  `unreached_calls`. `tools/call_coverage.py report` ranks the functions and prints the project figure; where a
  reason shows an input the pass cannot build (a script, a grid of linked records), write `oracle/hints/ADDR.txt`
  and re-prepare. Harnesses made before the pass: `tools/call_coverage.py reach` re-prepares them and judges the
  matched C; requeue the FAILs with `tools/requeue.py --seed NOTE @state/reach_failed.txt`. Details:
  docs/CALL_COVERAGE.md.
- `tools/data_funcs.py` lists code addresses used only as data (a function pointer stored once); add the real
  ones with `--add`, one by one, then `catalog`, `tools/assign_systems.py` and `prepare`.

## 5. Systems and design

- `decomp.py systems` groups functions into systems; `tools/assign_systems.py` places any stragglers.
- A designer (a strong model, one system at a time) writes the system's header (`readable/include/sys/sNN.h`),
  names (`readable/names/SNN.json`: functions, globals, read-only strings) and requests. The rules are in
  `docs/DESIGN_BRIEF_HEADERS.md`; Toy Commander's `readable/DESIGN_BRIEF.md` is a complete example.
- With no designer yet, `tools/name_pass.py` names every function and parameter in one pass (one no-tools session per 30, on config `readable_model`; Vigilante 8, on Haiku: 2,297 functions for $0.12). Only names: types, order and storage stay the raw signature's, and the judge still decides every file. It is what lets a passing machine file be adopted; run it before the first readable chunk.
- `refine.py header` must end with `headers OK` after every design edit.
- Cheat codes name globals: with the user's yes to get the game's list (libretro-database `cht/Sega - Dreamcast` first), `tools/cheat_names.py` writes `readable/cheat_hints.md` before the designers start; a designer names a global from it only when the code that uses the address agrees.
- A raw return type the decompiler got wrong is corrected in the names entry (`"raw_ret": "s32"`), never in raw C:
  `tools/sig_override.py --candidates` lists likely ones, `--apply` (servers stopped) carries the harness, matched
  C and callers, then `refine.py verify` the files it lists.
- Run `tools/sig_override.py --candidates` after the raw stage and add `raw_ret` for the `read` and `fr0_exit` rows: a function typed void has no result the judge compares. Vigilante 8 had 74, most of whose call sites read the result, and it stopped 36 lifts. Then `tools/stack_params.py` after a lift run types the parameters callers pass a frame buffer.

## 6. Readable round

`python refine.py --project <workspace> run SYSTEM` rewrites each matched function as normal C (typed
pointers, structs, named callees and globals), judged on the same harness. A machine draft goes first and is
adopted when it passes and reads as named C. `docs/HAIKU_WORKFLOW.md` describes what an attempt sees and costs.
Never hand-fix a readable file: fix the design or the pipeline and re-run.

The round also writes the game's `docs/ENGINE.md` (in the game's folder): how its code and data are organised (systems, main loop, memory layout, file formats, quirks) and what the round learned, in one place, kept current by each later round.

## 7. Gate and audit

- Any change to the checker or the pipeline goes stage, then gate, then deploy, with backups. The gate:
  `python tools/bounded.py 3500 python tools/regress.py --projects all --sample 15`.
- `tests/patterns.json` lists the project keys (`tc`, ...); `tests/patterns.local.json` points each at a
  workspace on your machine (each rebuilt from your own disc). Add your game's key there, and its fixtures in the
  workspace, never in DecompPipeline (they are game-derived C): `regress/fixtures.json`
  `{"fixtures": [{id, pattern, addr, pass, fail}]}` plus the C files it names, a C file the checker must PASS and a
  one-change copy it must FAIL. The suite finds them through the workspace path.
- `tools/final_audit.py` checks the whole workspace (oracle stamps, headers, readable files, call-site coverage)
  in one run; `tools/doctor.py` prints the call-site figure too.

## 8. A port

After the readable round, DecompPipeline's `port/` kit (port/README.md) builds your game as a 32-bit x86 Windows
program: it maps guest RAM at its Dreamcast addresses, loads the boot file from your own disc at run time,
compiles the readable C at `/W4 /WX`, and stubs every SDK and engine function until a platform layer replaces it.
The kit holds no game code; your game's repository holds a port folder with:

1. `game.json`: `decomp` (this workspace), `entry` (the first function to call, `void name(void)`, with its
   address), `image_base` (where the boot file loads), and optionally `disc`, `boot_file` (else IP.BIN's),
   `must_work` and `fsca_table`. port/CONTRACT.md lists every key.
2. `sdk`: the kit modules whose function names your registry has. `python xproject.py --project <workspace>
   --from <earlier workspace> names` carries names over for byte-identical library functions; leave out a module
   your registry lacks names for and write your own. With `sy_sys`, also `console` (country, language, broadcast).
3. `sources` and `hardware`: new C for your game-range hardware functions (game.json `hardware` names them, and
   the build checks the names against the registry) and for any SDK replacement bound to your own code.
4. Build and run, always bounded and with `--seconds`:
   `python tools/bounded.py 1500 python port/tools/build.py --game <port folder> --run --seconds 20`
   (`--engine` adds the `extra_functions` readable C). Each stub is logged at its first call. Put the ones
   start-up cannot get past in `must_work.txt`: the run stops at the first (exit code 3). Replace them one by one
   with engine readable C, a kit module or your own source, until the main loop runs.
5. Translator C, before or beside the readable round (off by default): judge the port form once,
   `python targets/dreamcast_sh4/translator/tools/dp_judge.py --ws <workspace> --out <dir> --sp-input --all`,
   then set game.json `"translator_c": {"dir": "<dir>", "mode": "mix"}`. `mix` compiles readable C where it
   passes and the translator's judged C for the rest; `only` uses translator C everywhere; `tx_first` lists
   functions whose readable C yields to translator C. The port takes only the very text judged there (each row
   keeps the C's sha256). Overlays: overlays.json `translator_c`.

6. The game log's map: copy `<workspace>/port_starter/scene_dump.json` (tools/game_setup.py writes it) to
   `<port folder>/scene_dump.json` and fill in the fields the readable C names (game mode, stage, round, how many
   characters, each player's health or lives, the timer, each character's action state), `"log": true` on the ones to
   list when they change. Every play run then writes what the player did and how the game went into `port.log`, and
   a playtest report is answered from that log, not from questions to the player (port/README.md "Game log").

Toy Commander's `port/` (game.json, must_work.txt, its hardware and Kamui 2 files) is a complete example. A
replacement that only needs SDK names belongs in the kit's `sdk/`; one bound to your game's own code stays with
your game.

### Adding a per-game option

An improvement of the game's own (Dragon Force's Quick dialogue, say) is declared once, in a table in the game's C, and
the base (`port/base/game_options.c`, `console.h` `PortOption`) gives it the rest on every console: the menu entry, the
`settings.txt` key, the `--id` flag, the recording's note and the value.

```c
static const PortOption k_options[] = {
    /* id, menu, label, kind, choices, values, n, default, original, flags, available */
    {"quick-dialogue", "Game", "&Quick dialogue: Start skips a scene (from the next start)", PORT_MENU_CHECK, NULL, NULL,
     0u, 1u, 0u, PORT_OPTION_NOTE, NULL},
};
const PortOptions port_game_options = {k_options, 1u};
/* the game's code then reads port_option("quick-dialogue") (1 on, 0 off; a choice's index) */
```

- `default` is the play run's value: an improvement is on there. `original` is the game exactly, which the game's
  code must leave untouched (off means the original). A check run, a scripted run and the fixed clock get `original`
  unless the flag is given, so the goldens never see it.
- `PORT_OPTION_NOTE`: the value is fixed at the run's start and kept in the recording as `*id` when it is not the
  original, so a replay does what the recording did (an older recording plays the original); a menu change applies
  from the next start. Without it the option is live, changes at once and is never recorded, so it must not change
  what the game does (fast forward's speed is one: `port/base/speed.c`).
- A choice gives `choices` (the menu's labels) and `values` (the flag's and the settings file's words); a check takes
  on/off. `available` (NULL: always) greys the entry.
- A console kit declares its own the same way in `port_console_options`. A game without a table links an empty one.

## Publishing your workspace

Publish what you wrote (matched and readable C, headers, names, briefs, docs). Never publish the disc image, the
boot file or extracted assets, SDK material, Ghidra projects, packets, harnesses or test vectors: they are
regenerated from the user's own disc. Toy Commander's `.gitignore` is an allow-list that enforces this.
