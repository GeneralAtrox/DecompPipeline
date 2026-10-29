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
- **Port kit** (port/README.md lists every module and the game.json key that turns it on): Kamui 1, more of
  Kamui 2, ADX and Sofdec movies with sound, a game's own sound driver on an emulated AICA ARM7, the VMU screen,
  save file images, the console's heap layout, fibres, gdFs and ADXF, controllers set up without
  pdInitPeripheral, translucent modifier volumes, the game's own float maths in place of the host's, and the x87
  precision following FPSCR.PR.
- **Testing a port:** `port/tools/lockstep.py` compares a port with Flycast frame by frame (RAM and the display
  lists); `port/tools/bu_sweep.py` runs the port past many blockers in one go and lists each one.

## 0. Machine setup (once)

- Python 3.12, a JDK 21, Ghidra 12.x, Visual Studio 2022 with the C++ workload (the judge builds every
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

The short way does steps 1 and 2's setup in one command, for a Dreamcast, Saturn or GameCube disc. It extracts the
binary into the workspace, writes `project.json` and `oracle/TcInputs.java`, runs `decomp.py setup`, and then
checks Ghidra's decoding of every function against the target's own decoder:

```
python tools/bounded.py 3600 -- python tools/game_setup.py setup <image.gdi|.cue|.iso> <new_workspace>
```

(Saturn overlays: `--file NAME@ADDR`; carried decisions: `--fields FILE`; see the tool's docstring. The Xbox 360 has
`targets/xbox360_ppc/tools/x360setup.py`.) The hand steps it replaces follow; you still need the scan below to
find overlays and the library list.

Setup also puts the game on this machine's progress page (`Dashboard.bat`, docs/DASHBOARD.md), wherever the
workspace lives, and writes a starter `progress.json` in the game folder. Keep that file current as the game moves:
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
| `game_end` | first address of the SDK code linked after the game (see step 4); `0` until you know |
| `references` | 2-3 functions you decompile by hand first: the controls that prove the judge works (`[]` until setup) |
| `broken_control` | one reference plus a deliberately wrong copy of it, which must FAIL |
| `port` | the monitor UI's port (one per workspace that serves at the same time) |
| `seed_functions` | `true` for a game linked at `0x0c...` (step 1's scan says so); harmless otherwise |
| `ghidra_timeout_s` | one function's prepare (default 900 s); setup, catalog, seed and graph get `ghidra_setup_timeout_s` (default 3600 s) |
| `ghidra_kill_s` | the wall time before one function's prepare is killed (default: `ghidra_timeout_s`). The oracle budget in the facts still comes from `ghidra_timeout_s` (counted in work with `oracle_work_per_s`), so raising this lets a loaded or slower machine finish the same harness without changing any stamp; machine-local, e.g. in config.local.json |

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
- `refine.py header` must end with `headers OK` after every design edit.
- A raw return type the decompiler got wrong is corrected in the names entry (`"raw_ret": "s32"`), never in raw C:
  `tools/sig_override.py --candidates` lists likely ones, `--apply` (servers stopped) carries the harness, matched
  C and callers, then `refine.py verify` the files it lists.

## 6. Readable round

`python refine.py --project <workspace> run SYSTEM` rewrites each matched function as normal C (typed
pointers, structs, named callees and globals), judged on the same harness. A machine draft goes first and is
adopted when it passes and reads as named C. `docs/HAIKU_WORKFLOW.md` describes what an attempt sees and costs.
Never hand-fix a readable file: fix the design or the pipeline and re-run.

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
   `must_work` and `fsca_table`. port/README.md lists every key.
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

Toy Commander's `port/` (game.json, must_work.txt, its hardware and Kamui 2 files) is a complete example. A
replacement that only needs SDK names belongs in the kit's `sdk/`; one bound to your game's own code stays with
your game.

## Publishing your workspace

Publish what you wrote (matched and readable C, headers, names, briefs, docs). Never publish the disc image, the
boot file or extracted assets, SDK material, Ghidra projects, packets, harnesses or test vectors: they are
regenerated from the user's own disc. Toy Commander's `.gitignore` is an allow-list that enforces this.
