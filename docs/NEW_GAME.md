# Decompiling your own Dreamcast game

This is the route Toy Commander took, written for the next game. The pipeline holds no game data: everything
game-specific lives in a **project workspace** that you create, and the disc image stays yours. A worked example
of a finished workspace is the Toy Commander repository (`decomp/`: project.json, matched and readable C,
headers, names, the design briefs and the progress log).

## 0. Machine setup (once)

- Python 3.12, a JDK 21, Ghidra 12.x, Visual Studio 2022 with the C++ workload (the judge builds every
  candidate with MSVC), and the `claude` CLI logged in (it runs the agent sessions).
- Create `config.local.json` next to `config.json` (git-ignored, merged over it key by key) with this machine's
  paths: `ghidra_home`, `java_home`, `vcvars` (vcvars64.bat) and, for tools/sdk_fid.py only, `sevenzip` (else
  7z on PATH). Tracked `config.json` keeps them null; a command that needs one stops naming the key. Override any
  other default there too (`claude` is the CLI command). For the regression suite, `tests/patterns.local.json`
  (also git-ignored) maps each `projects` key of tests/patterns.json to its workspace path.
- Install the SH-4 language once: `python targets/dreamcast_sh4/ghidra_lang/install_lang.py <ghidra_home>`.
- Once a workspace exists (step 2), `python tools/doctor.py --project <workspace>` checks it for consistency.

Every long command should run under `tools/bounded.py SECONDS ...`, which kills the whole process tree on time-out.

## 1. Get the main binary off your disc

```
python targets/dreamcast_sh4/tools/gdrom.py info    <image.gdi|.cue>
python targets/dreamcast_sh4/tools/gdrom.py extract <image.gdi|.cue> <new_dir>
```

`extract` writes the boot file named in IP.BIN (for example `1ST_READ.BIN`); it loads at 0x8c010000.

## 2. Create the workspace

A folder with `project.json`. Toy Commander's is the template; the fields a new game needs first:

| field | meaning |
|-------|---------|
| `name`, `ghidra_name` | display name; Ghidra project name |
| `target` | `dreamcast_sh4` |
| `binary`, `binary_sha256` | the boot file (relative to the workspace) and its hash, so a wrong file is refused |
| `game_end` | first address of the SDK code linked after the game (see step 4); `0` until you know |
| `references` | 2-3 functions you decompile by hand first: the controls that prove the judge works |
| `broken_control` | one reference plus a deliberately wrong copy of it, which must FAIL |
| `port` | the monitor UI's port |

Then:

- copy `targets/dreamcast_sh4/template/TcInputs.java` to `oracle/TcInputs.java` (the test-vector generator;
  adjust it when your game's functions need different inputs);
- write the reference C in `reference/ADDR.c` and the broken one;
- `python decomp.py --project <workspace> setup` builds the Ghidra project and the function catalog;
- run the controls: `decomp.py check ADDR --work reference/ADDR.c` must PASS, the broken file must FAIL.

The judge is behavioural: each candidate runs on the same test vectors as the original code (under the SH-4
model in `targets/dreamcast_sh4/runtime`) and must give the same registers, memory and calls. Byte matching
against a compiler is not needed, so games built with Hitachi SHC (which nobody has) work.

## 3. Split game code from the Sega SDK

Most Dreamcast games link Katana SDK libraries (Kamui, Shinobi, Ninja, sound, the CRI middleware) after the
game. You don't decompile those; you name them.

- `tools/sdk_fid.py build` turns an SDK release you own into Ghidra Function ID databases; `tools/sdk_fid.py
  apply` names the SDK functions in your game.
- `tools/sdk_classify.py` sorts the library range into SDK and the studio's own engine code.
- Set `game_end` to the start of the SDK range; engine functions linked beyond it go in `extra_functions`.

The SDK, and the databases built from it, are Sega material: keep them out of any repository.

## 4. Match round (raw C)

`python decomp.py --project <workspace> serve` opens the monitor UI. Start workers (Haiku by default): each one
takes a function, writes raw C against the guest-memory API, and gets PASS or FAIL. Callees are mocked, so
functions are independent.

- Pilot about 20 functions first and read the failures. A failure class that repeats is a tooling problem: fix
  the pipeline (and run the gate, below), not the functions one by one.
- Functions that touch hardware registers or system instructions are excluded; record their intent instead.
- `decomp.py audit` lists every unmatched function by blocker kind.
- `tools/stack_uninit.py` lists functions that read a stack word no path wrote; `--pin` fixes it to 0 in every
  vector (your `oracle/TcInputs.java` must support the `genrange` hint, as Toy Commander's does; the target
  template's does not yet), then re-prepare.
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

Toy Commander's `port/` shows the next step: a 32-bit x86 program that maps the guest RAM at its Dreamcast
addresses, loads the boot file from your own disc at run time, compiles the readable C, and stubs every SDK and
engine function until the platform layer replaces it.

## Publishing your workspace

Publish what you wrote (matched and readable C, headers, names, briefs, docs). Never publish the disc image, the
boot file or extracted assets, SDK material, Ghidra projects, packets, harnesses or test vectors: they are
regenerated from the user's own disc. Toy Commander's `.gitignore` is an allow-list that enforces this.
