# Operator tools, the audit and the regression suite

Moved from the README; the entry point is AGENTS.md.

### tools/

Every tool takes `--project DIR` like `decomp.py` (repeatable where several make sense) and reads address lists
from `@FILE` (one or more per line, `#` comments). Never put long hex address lists on a command line: Windows
Defender kills such processes as Trojan:Win32/Commando.A!ml.

| Tool | Does |
|------|------|
| `port/tools/perf.py segment --a BUILD --exe game.exe --out DIR --state FILE [--warmup N] [--signed-in] -- ARGS` | Every late frame and long tick of one stretch of play (a save state, or a replay from a blank) put down to its cause: the game's function (translated or interpreted), a wait, a kit part or the system, worst first, with a frame-time chart. Build box A or build box B; the user's laptop only as `MAIN_LAPTOP_OK=final-timing`. docs/PERF_TOOLKIT.md. |
| `bounded.py SECONDS CMD...` | Runs CMD with a hard limit and kills its whole process tree (`taskkill /T /F`) on expiry, exit 124. Git Bash `timeout` leaves java.exe/python.exe grandchildren running. |
| `watchdog.py --project DIR... [--workers N] [--minutes M]` | Polls the running servers each minute; exits 1 with `ACTION:` as soon as one is unreachable, usage-blocked, finished, short of workers or has its loop off. Always bounded. |
| `requeue.py --project DIR --note TEXT (--rx REGEX \| ADDR... \| @FILE) [--status S,S] [--seed NOTE [--stale]] [--dry]` | Requeues by verdict_detail regex (statuses default `skipped,failed`) or by address. `--seed` starts the re-match from the current matched C (`--stale`: from `matched_stale/` first, to get back C a faulty harness forced out), judged before any agent run. Through the server's `/api/requeue` when it runs; otherwise on the ledger directly, exactly as the server does, after a backup to `state/ledger.before-requeue-<time>.json`. |
| `sigdrift.py --project DIR [--list FILE] [--all] [--json FILE]` | After a checker deploy: matched functions whose stored harness disagrees with the current prototypes (TcSigs.java on a private read-only Ghidra copy). Tier `signature` (signature, result register or support changed: lint R3 fails, requeue) and `compared` (a callee or runtime state compared on more registers: re-judge; or a call records an older callee signature than the callee's own harness: re-prepare it before readable work). `--list` writes the signature tier (with `--all` both) for `requeue.py @FILE`. |
| `reprepare_stale.py --project DIR [--dry] [--rounds 8] [--per-fn 2] [--stamps [--max N] [--slots N]]` | Servers stopped. The callee-record cascade (core/cascade.py): re-prepares matched harnesses whose callee records differ from the callees' own harness signatures until a scan finds none (each caller at most --per-fn times, since a re-prepared caller's new signature can leave its own callers stale; the callees are the reference, never touched). Every re-prepared matched function's raw C is judged on its new harness; a FAIL is listed for tools/requeue.py. Exit 1 while anything is left. `--stamps [--max 100] [--slots N]` first re-prepares the matched harnesses whose oracle stamp is not today's (no stamp, or other oracle inputs), `ghidra_parallel` (or `--slots N`) at once in Ghidra slots, at most --max per run; resumable (a re-prepared harness carries today's stamp): repeat it under bounded.py until none is left. |
| `prepare_list.py --project DIR ADDR\|@FILE... [--slots N] [--out FILE.tsv] [--dry]` | Servers stopped. Prepares a list of functions in Ghidra ahead of the workers, `ghidra_parallel` (or `--slots N`) at once in Ghidra slots (slot 0 the master, the rest read-only copies), and prints or writes (TSV) each one's signature, unsupported reason, valid vectors and block coverage. A function whose harness is already current is left alone; with config `harness_reuse` on, a worker reuses a current harness without opening Ghidra (core/job.reusable_harness: packet and harness files present, today's oracle stamp, no callee record drift). Resumable under bounded.py: only the leftovers, callees before callers, each result logged and appended to `--out` as it ends; a refused or timed-out prepare is recorded in `state/prepare_refusals.json` and skipped while its oracle stamp and kill timeout are unchanged. Exit 1 on any prepare error (a refusal is not one). |
| `fresh_oracle.py --project DIR [--recount] [--workers N] [--list FILE]` | Read-only. Lists every matched harness that is not current (no harness, no stamp, an old stamp, callee records that differ), today's stamp computed in memory (core/oracle_fresh); `--recount` re-judges every matched raw C on its own harness with check.py (parallel, builds in a temp folder) and prints passed/total against the catalog's matched count. Exit 0 only when all are current (and all pass). |
| `port_map.py tree\|native\|drift\|record\|judge --ws WORKSPACE [--port DIR] [ENTRY\|ADDR...]` | Read-only. Port readiness per function from an entry (address or name, default game.json `entry`): ready, needs_raw, needs_readable, kit, native, outside, with edge gaps and indirect calls counted and never `complete`. `native`: which original address each native function implements, and what is only a citation. `drift`: native code whose raw C, readable C, harness or verdict moved since it was written (`record` makes a baseline file; without one, git dates; with neither, it says not known). `tree` trusts the records: a forged row with a wrong file needs `judge`, which rebuilds on a temporary copy with no ledger write. A binding is not a behaviour proof. |
| `final_audit.py --project DIR [--recount] [--workers N] [--no-sync]` | The goal's final audit in one process (run it under bounded.py): one PASS/FAIL line each for the fresh oracle, the headers (`refine.py header`'s checks on a snapshot, live game.h current), `refine.py verify` (N/N, N = catalog game functions minus the excluded hw/sys ones; 0 missing, STALE, HARNESS_STALE, failed, FLAKY, RAW_NOT_MATCHED, errors), `refine.py coverage` (all zero), call-site coverage (`calls`: no unreached call site the reach pass has not tried), the proof gates (`proof`: no `matched_low_coverage` function while the coverage gate enforces), raw fn_ names, `refine.py library`, completeness, the consistency check, the excluded functions' intent notes (project.json `intent_notes`, default HARDWARE_FUNCTIONS.md), then the readable usage report (INFO) and a verdict (PASS needs `--recount`; else at best INCOMPLETE). Full output in `state/final_audit-<timestamp>.txt`. Writes nothing else but what verify writes (sync-names, readable.json), header snapshots and build/ output. |
| `coverage_gate.py --project DIR... [--report] [--top N] [--list FILE] [--json FILE] [--min-pct P] [--allow N] [--min-blocks N]` | Read-only. Per project: matched functions below the coverage gate (`matched_low_coverage`), the share, the block-coverage distribution and the worst ones; `--list` writes them for `@FILE` (the reach pass, hints, requeue); the gate options try other settings for this run only. |
| `proof_backlog.py queue\|status\|enforce --project DIR...` | The proof backlog (docs/PROOF_GATES.md, Rollout). `queue [--out DIR] [--dry] [--mutation FILE]`: each project's flagged functions as `@FILE` queues in `state/proof_backlog/` (reach: an untried unreached call site, for `call_coverage.py reach`; vectors: other unreached blocks; harness: weak_harness; no_data). `status [--check]`: the backlog per project, CLEAR when none is below the coverage gate (the signal to enforce). `enforce on\|off [--apply]`: the project's `coverage_gate.enforce` in project.json. Runs no Ghidra; the queued work does (free slots, servers stopped). |
| `hint_search.py --project DIR --tag T [--max-probes N] [--baseline PROBE.json] [--json OUT] ADDR... \| @FILE` | Automatic test-input hints for weak-coverage functions (a private project copy only). Reads each function's asm for compare constants, zero tests, loop counters, pointer uses and callees; turns them into candidate `oracle/hints` lines; probes each round in one `oracle_probe.py` run; keeps a line only when blocks reached or valid vectors rise and valid vectors never fall under 16. Proof (land hints, 2026-10-30): 10 of 20 weak V8 functions gained (blocks 174 -> 229 on 17), all from the first candidate, so 2-3 probes are enough; no help for a function with no valid vectors (one tried). Kept hints go live only after a re-prepare and a re-judge of the matched C. |
| `mutation_check.py --project DIR [ADDR... \| @FILE \| --references] [--sample N] [--report] [--gate] [--json FILE] [--workers N]` | Host only (no agent, no Ghidra). Seeded mutants (relational, arithmetic, bitwise and shift operators, constants ±1, negated conditions, deleted stores, swapped arguments, return 0) of each matched C, judged with check.py on its own harness, dev + hidden; per function tried/killed/survived/dropped and the score; survivors classed logic / arg_slot / stack. `--report` writes nothing in the workspace; else records go to `state/mutation.json`. `--gate`: exit 1 unless every function's baseline passes and some mutant is killed. `--planted FILE` judges hand-planted mutants (core/mutation.generate's shape) instead of the generated ones. Run it under bounded.py. |
| `junk_ram.py --project DIR [ADDR... \| @FILE] [--src ADDR=FILE] [--original] [--json FILE]` | Report only, Dreamcast. Runs a function's C on every vector twice, with the RAM nobody set (past the image) zero and filled with a per-address junk pattern (the image is padded with it: no runtime change), and lists vectors whose results differ. A difference alone is a lead: the original often reads unset RAM too. `--original` also runs the original machine code on both images with xemu and compares the C's junk run with the original's (the "junk verdict") on the vectors where xemu agrees with expected.txt and the original does not fault. `--original` is the judge's own junk pass (`core/junk_ram`, config `"junk_ram"`: off by default; on, a check that passes is judged again on junk RAM against xemu's run of the original). Proof: land junk and jjudge (2026-10-30). |
| `open_list.py --project DIR [--out MD]` | Markdown list of every unmatched function with tier, size, attempts, blocker kind and reason. |
| `import_from.py --from SRC --project DIR...` | `xproject.py` match + stage (bounded), then sets each target's `priority` to its staged imports. Servers stopped. |
| `usage_report.py ledger\|attempts --project DIR...`, `usage_report.py sessions --hours H` | Token spend by project/model/outcome/tier/size; attempt token anatomy from `runs/`; Claude Code session spend. |
| `usage_report.py readable --project DIR [--since 6h\|ISO] [--summary]` | Readable attempts: cache writes/reads, output, thinking, first call, turns, files read, refused commands, effort, PASS or not from state/readable.json; medians by verdict and effort (an effort A/B). |
| `regress.py` | The pattern regression suite (below). |
| `completeness.py --project DIR [--check] [--state DIR] [--known FILE]` | Accounts for every game-range byte: code, pool, switch table, padding, or a run the project explains in `state/known_runs.tsv` (data with the pointer words naming it, dead code with the references into it, all checked); writes COMPLETENESS.md. `--check` exits 1 on an unexplained byte (unknown or orphan) or a stale entry, and also runs the consistency check below (bytes alone cannot see a removed function's leftover files). |
| `retire.py --project DIR [--dry] [--note TEXT] [--force] [--max N]` | Moves every artefact keyed by an address that is not a function of state/catalog.json to `retired/<kind>/` (never deletes): ledger and readable.json rows, systems.json membership, escalate.json, harness, packets, matched, matched_stale, work, runs, sandbox (ADDR, rd_ADDR), build, import, oracle/hints, readable/src, and readable/names entries nothing refers to (a tail entry current functions call, or a key that is another form of a function by target.json `address_mirrors`, is reported as kept). `retired/manifest.json` lists each move with its reason (the `ledger_removed.json` / `pruned_ledger.json` clause naming it, the known_runs.tsv run or function body that now holds its bytes, else the last ledger backup with its row). Idempotent. Refuses while the server answers, while readable.json has `running` rows, or past `--max` addresses (default max(50, 5% of the catalog)) unless `--force`. `decomp.py setup/catalog/seed` run it after every catalog change; the server shows "removed functions with artefacts left" until it has run. |
| `assign_systems.py --project DIR [--dry] [--attach]` | Puts every game function in no system into one (core/sysassign.py), never renumbering or moving an existing id or member: by call/pointer edges with members (hubs ignored), then shared globals, then being enclosed by one system in address order, repeating until stable; ties go to the system with the nearest member. Edges come from state/graph.tsv and each function's harness/packet meta.json (functions newer than the graph export). Functions with no affinity become new systems, one per address-contiguous or call-connected cluster, numbered after the highest id (`--attach`: the system just before them instead). Records each placement in state/systems_assigned.json; idempotent. Run after `retire.py`. Also places `extra_functions` in systems of their own (Louvain over their own call graph; game systems never change). Before it on a project with a stale graph: `decomp.py systems --graph --extend` (never without `--extend`: that rebuilds and renumbers the systems). |
| `sdk_fid.py build\|apply ...` | `build`: a Ghidra Function ID database for one Katana SDK release (a folder, an installer .iso or an InstallShield data1.hdr) via the SDK's libsplit/elfcnv, re-runnable (skip-if-done stamps). `apply`: one or more databases over a fresh import of the game with the catalog's function boundaries (-> tc_fid.tsv). Rerun steps: STAGE_REPORT_sdkfid.md in the stage history. |
| `sdk_classify.py ...` | Labels every library function sdk / engine / unknown from FID names, library objects and the call graph; writes `classify.tsv`, `validate.tsv` (FID vs readable/names) and `engine_functions.txt` (the engine's decompile scope, for `extra_functions`). |
| `doctor.py --project DIR... [--json]` | Read-only consistency check (core/doctor.py; `completeness.py --check` runs it): fails on any orphan artefact (as retire finds them), a matched function without harness meta or matched C, a project.json reference/priority address that is not a function, a game function in no system (once systems.json exists), a readable file using a raw name the registry has named (fn_ADDR, g_/DAT_ADDR; refine.py verify syncs them), or a matched function's harness whose callee record differs from the callee's own harness signature (re-prepare it, or tools/reprepare_stale.py); notes the raw names left for unnamed functions, readable files whose readable.json status does not describe them, and prints the readable denominator (readable files of current functions next to the matched functions). |
| `storage_pins.py --project DIR [--ledger FILE] [--apply] [--pin-unjudged]` | Readable stage: names entries whose prototype means something else under placement by storage than by position (batch 14), classified by the readable files that judge them (PIN: they pass by position; NATURAL: they failed by position; UNJUDGED: none yet). `--apply` adds `"storage"` pins to readable/names (a data step). Also lists prototypes the placement refuses. |
| `cheat_names.py --project DIR --cht LIST.cht [--region R] [--out MD] [--json FILE]` | Naming hints from a libretro cheat list: each code's guest address and width, the game functions whose literal pools hold it (or a base up to 0x400 below), the designers' name for it, and a warning when the list's region differs from the disc's. Writes readable/cheat_hints.md for the designers; reads only (getting a list needs the user's yes). |
| `sig_override.py --project DIR [--candidates] [--apply] [--json FILE]` | Validates every names `"raw_ret"` (refused: no catalog function, a bad kind, void while a call site reads the result); `--candidates` lists core/ret_use candidates; `--apply` (servers stopped) rewrites the harness meta, the matched C (kept only on PASS, else listed to requeue), the callers' callee records and judges their matched C, and lists readable files for `refine.py verify`. Backups in state/sig_override_backup/. |
| `stack_uninit.py --project DIR [targets\|@FILE] [--pin] [--json]` | Functions that read a frame word no path wrote first. `--pin` appends `genrange 0xADDR 0 0` to oracle/hints/ADDR.txt (the game's TcInputs must support genrange): re-prepare, then the word is 0 in every vector and readable C writes 0 with a comment. |
| `stack_overread.py --project DIR [--src DIR] [--only ADDR...] [--workers N] [--json FILE] [--write]` | Readable C that hands the port host stack garbage (core/stack_overread.py, docs/PROOF_GATES.md section 6): R1, a local's address that reaches an access or a callee chain past the local's end (TC 8c017382's lone `hit_pos`); R2, a local without an initializer copied out (block copy source, or whole by value) before every copied byte is written (TC 8c06fc14's pose frame). cl /EP + pycparser, whole program; `--src` checks other versions of files. Writes state/stack_overread/ with `--write`; requeues nothing. Exit 1 on hits. |
| `name_pass.py --project DIR [--scope SYSTEM,...] [--batch 30] [--slots 4] [--drafts DIR] [--dry-run]` | Names every unnamed function and parameter, one no-tools session per batch on config `readable_model`, types and storage kept from the raw signature (Vigilante 8, on Haiku: 2,297 for $0.12); cost in state/name_pass.json. Then `refine.py header` and `sync-names` |
| `stack_params.py --project DIR --lifted OUT/readable/GAME [--write]` | Types as `void *` each u32 or s32 parameter a caller passes a frame buffer, from the lift's own files (`(u32)frame_bufN`), so the judge can deliver a mocked callee's out bytes (core/readable_outptr). Lists the readable files to verify. |
| `param_unread.py --project DIR [targets|@FILE] [--json FILE] [--write]` | Readable parameters never read while the original reads their storage (core/param_unread.py, docs/PROOF_GATES.md section 7; TC 8c0117f4's 12 discarded camera arguments, which the judge passed). Readable side: a parameter named only in `(void)name;`. Original side: its register or stack word reaches a use in the asm (moves, spills and arithmetic followed; callee records say which arguments a call reads). Static, seconds. project.json `param_unread.waive` takes a hit by hand. Writes state/param_unread/ with `--write`; requeues nothing. Exit 1 on hits. |
| `data_funcs.py --project DIR [--ghidra-project DIR] [--no-ghidra] [--json FILE]`, `--add @FILE` | Code-region addresses that function code uses as values but no function owns, checked read-only by tools/ghidra_tools/TcEntryCheck.java (valid, code, invalid, data, inside). `--add` (re-checked; refuses anything not valid/code) writes project.json catalog_overrides entry (+ extra_functions); then catalog, assign_systems, prepare. Never in bulk. |

### Build-version tables (which SDK build, compiler and middleware a game or symbol set has)

One table per console in `docs/`: `GAMECUBE_BUILD_VERSIONS.md` (319 sets), `XBOX360_BUILD_VERSIONS.md` (293), `XBOX_BUILD_VERSIONS.md` (5), `DREAMCAST_BUILD_VERSIONS.md` (7) and `SATURN_BUILD_VERSIONS.md` (9 retail discs). GameCube, Xbox, Dreamcast and Saturn are made by `targets/gamecube_ppc/tools/symbol_inventory.py` (`--console xbox|dreamcast|saturn` before `scan`, `report` or `one`; readers in `tools/symset_xbox.py` and `tools/symset_sega.py`, shared writer in `tools/symset_consoles.py`); Xbox 360's by `targets/xbox360_ppc/tools/pdb_content.py`. `report` rewrites only the part of a doc between its `<!-- sets -->` and `<!-- indexes -->` markers.

**Not ours, kept apart:** `docs/external/n64/` is a snapshot (taken 2026-11-19, upstream commit `34a4fdf3e7`) of the public Nintendo 64 table from [N64-Decomp-Kit](https://github.com/TheGh0stShip/N64-Decomp-Kit): 66 retail ROMs with libultra release, compiler family and microcode each. GPL-3.0, with its own `LICENSE` and a `SOURCE.md`; never edit it. Refresh it with `py -3.12 -I tools/symset_consoles.py refresh-n64`: it pulls the clone in `External/N64-Decomp-Kit`, copies the files when the commit changed and prints the games added or removed and the values changed; a failed pull or an empty table leaves the snapshot as it is.

**PS2 and Arcade have no table yet** (not surveyed). What exists: `Integrations\PS2\PDBs` holds 26 files in 8 set folders (Shinobi in five builds, Kunoichi, Nightshade, and two Sakura Taisen titles, one with DWARF; plain PS2 executables and one `.elf`), and the Xbox reference folder holds an NHL Hitz Pro PS2 ELF with an `ELF.MAP`. No reader for them exists yet. A PS2 table would need: the Sony runtime library stamps in the executables, the EE-gcc `GCC: (GNU)` string, ELF symbol-table and DWARF counts (`symset_sega.elf_info` reads 32-bit ELFs of either byte order), middleware strings, and the retail build of each PS2 port read from its own game executable. Arcade (Bonanza Bros, Gain Ground) has no symbol sets at all, so its table would be retail-only: the board, CPU and sound driver, read from the ROM sets the projects already hold.

#### Every tool (generated)

The table above details the main operator tools by hand. The list below is generated: every tools/*.py with the first
line of its module docstring, so nothing in tools/ goes unlisted. `python tests/test_tool_index.py` fails when it is
out of date; after adding, removing or redescribing a tool, run `python tests/test_tool_index.py --write` rather than
editing it by hand.

<!-- tools-index:begin (generated from each tool's docstring by tests/test_tool_index.py; do not edit by hand) -->
- `args_marks.py`: The proof gates' third reason (core/unchecked_args): matched functions with a call whose arguments no judge compares, marked "unlisted call args unchecked" at ...
- `arm7_translate.py`: Translates the executed code of an ARMv4 ARM-state program (the AICA sound driver) to C blocks for the AICA ARM7 runtime (port/runtime/arm7_native.h): one C ...
- `asan_landing.py`: Every kit landing that changes host C carries an AddressSanitizer result (AGENTS.md "Test rules", rule 3; the user, 2026-11-09: "Yes, every landing").
- `asm_modules.py`: Hand-written asm modules (core/asm_modules.py): local subroutines that pass state in registers outside the C ABI.
- `assign_systems.py`: Put every game function that is in no system into one (core/sysassign.py): by calls with members, then shared globals, then being enclosed by one system's ...
- `bounded.py`: Run a command with a hard time limit; on expiry kill its whole process tree (taskkill /T /F on Windows).
- `build_prune.py`: Prune a workspace's build/ by hand (core/build_prune.py): the compiler outputs of functions no job holds.
- `call_coverage.py`: Call-site coverage of a project: which call sites no valid vector reaches, why, and re-preparing with the reach pass.
- `cheat_names.py`: Cheat codes as naming hints for the readable stage (docs/proposals/learnings_dc_decomp_kickoff.md, idea 4).
- `common.py`: Shared helpers for the command-line tools in tools/ (not a tool itself).
- `completeness.py`: Account for every byte of a project's game range: function code, literal-pool data, switch tables, instructions in no function ("orphan"), padding, the ...
- `coverage_gate.py`: Coverage gate report (core/proof.py, docs/PROOF_GATES.md): how many matched functions the original's block coverage leaves below the gate (state ...
- `dashboard.py`: one page showing every game's decompilation progress, unit by unit (a treemap, as ByteMatch shows).
- `data_funcs.py`: Code addresses that function code uses as values but that are no function (core/data_funcs.py), checked by Ghidra.
- `decode_check.py`: The decode check of a new workspace (tools/game_setup.py): Ghidra's reading of every instruction of every function against the image's own bytes and the ...
- `doctor.py`: Workspace consistency check (core/doctor.py): orphan artefacts of non-functions, matched functions without their harness meta or matched C, project.json ...
- `fails_before.py`: A bug fix's check must fail on the unfixed build (AGENTS.md "Test rules").
- `fake_agent.py`: Stand-in for `claude -p` so the orchestrator can be tested end to end without spending tokens.
- `fake_watcher.py`: *(no docstring)* Token-free stand-in for the watcher agent (dry runs): set TCD_WATCHER_COMMAND=["{python}", "{root}/tools/fake_watcher.py"]   ({root} = DecompPipeline/) It runs ...
- `final_audit.py`: The goal's final audit in one bounded process: one PASS/FAIL line per check and a final verdict.
- `float_gaps.py`: List the matched and readable functions the two float gaps let through (docs/PROOF_GATES.md, float gaps).
- `fresh_oracle.py`: Fresh-oracle check: every matched harness carries today's oracle stamp; with --recount, every matched raw C passes.
- `fsca_rom.py`: Rebuilds the SH-4 FSCA ROM and its 65,536-entry sine table from first principles.
- `game_setup.py`: One command from a Dreamcast, Saturn or GameCube disc to a workspace ready to prepare: the hand steps of a new game (the target README's disc tool, ...
- `gc_truth.py`: Measure DP's GameCube target against a finished public decompilation (test truth only).
- `gc_truth_decomp.py`: A finished public GameCube decomp's decomp-toolkit config, read for tools/gc_truth.py (test truth only): config.yml, symbols.txt and splits.txt of main.dol and ...
- `gc_truth_dp.py`: DP's side of tools/gc_truth.py's scores: a GameCube workspace's catalog bodies, translator verdicts and readable names; the boundary classes of each truth ...
- `golden_survey.py`: Every golden and expected file, and where it came from (AGENTS.md "Test rules"): the files of DP, of port folders and of decomp workspaces, each against its ...
- `helper_abi.py`: List the matched functions whose C calls a runtime helper without the registers the helper takes (the helper ABI rule, docs/PROOF_GATES.md).
- `hint_search.py`: Automatic test-input hint search: read a function's asm for compare constants, zero tests, loop counters and pointer uses, turn them into candidate ...
- `idle_prepare.py`: Prepare the queue's next functions while the workspace is idle, so its workers later reuse current harnesses (config "harness_reuse", ...
- `import_from.py`: Cross-project import: xproject.py match + stage from a source workspace into one or more target workspaces, then set each target's project.json "priority" to ...
- `is5cab.py`: Extract an InstallShield 5 cabinet set (data1.hdr + data1.cab, one volume) without the installer.
- `jobreg.py`: Job registry: every heavy job (a Ghidra prepare run, a rejudge, a mutation audit, the full test suite, a re-check, a wave, a regress gate) goes through one ...
- `junk_ram.py`: Report-only junk-RAM check (docs/proposals/learnings_dc_decomp_kickoff.md idea 2): run a function's C on each of its vectors twice, with the guest RAM nobody ...
- `land_smoke.py`: Live-prepare smoke for a landing: a real preparation per game with the code being landed, on the live state as it is.
- `mid_entries.py`: Second entry points folded into another function's body (core/mid_entries.py): each needs an entry override.
- `mirror_ptrs.py`: List the functions whose 0x0c...... RAM pointers may be compared raw (the mirror rule, docs/PROOF_GATES.md).
- `mutation_audit.py`: Standing mutation audit (docs/PROOF_GATES.md section 2b): no judge or oracle change may lower the kill rate.
- `mutation_check.py`: Per-function mutation check (core/mutation.py, docs/PROOF_GATES.md): seeded mutants of the matched C, judged on the function's own harness with the official ...
- `name_pass.py`: Name a game's unnamed functions and parameters in one cheap pass, so the machine lift's files read as named C.
- `onboard.py`: AutoOnboard's old entry point, kept so running jobs, handovers, Start Game.bat files and remote runs that call `py -3.12 tools/onboard.py ARGS` go on working: ...
- `open_list.py`: Write a Markdown list of every function to decompile not matched yet (queued, running, failed, skipped, error, not started), with its tier, size, attempts, ...
- `oracle_probe.py`: Oracle probe: prepare any functions (skipped, failed or matched) with the CURRENT working-tree oracle on a private copy of one project, and report how many ...
- `param_unread.py`: List readable functions that never read a parameter whose storage the original reads (core/param_unread.py).
- `port_map.py`: Port readiness map: walk the original's call tree from an entry and say what each function still needs.
- `prepare_list.py`: Prepare a list of functions in Ghidra ahead of the workers, several Ghidra slots at once, and print each one's oracle figures.
- `proof_backlog.py`: Proof backlog (core/proof.py, docs/PROOF_GATES.md "Rollout"): the matched functions the proof gates flag, queued for more test inputs, and the switch from ...
- `ptr_views.py`: List the matched functions whose C writes a RAM pointer in the other view than the original's pool (the mirror rule, docs/PROOF_GATES.md).
- `readable_parity.py`: Verdict parity of the readable runtime on a workspace's existing harnesses: each matched function's raw C judged by the raw judge (core/check.run_check: the ...
- `readable_tx.py`: Readable C without an agent (core/rtx): translate each matched function, lift the raw C to readable C, and judge it with the readable judge and the gates, ...
- `regress.py`: Pattern regression suite: the gate for every checker change (core/check.py, core/lint.py, the target's runtime, Ghidra scripts or input template).
- `regress_project.py`: One project's part of tools/regress.py (run by it in a child process; not meant to be run by hand).
- `regress_targets.py`: Which part of the regress gate a change can affect: tools/regress.py --targeted BASE.
- `reprepare_stale.py`: Re-prepare matched harnesses whose callee records differ from the callees' harnesses, until none is left; with --stamps, first every matched harness whose ...
- `requeue.py`: Requeue finished functions (after a checker fix): by a regex on verdict_detail, or by address.
- `retire.py`: Retire every artefact keyed by an address that is not a current catalog function: move it to retired/<kind>/ in the project (never delete) and append ...
- `scorecard.py`: How far each console is from DecompPipeline's definition of done (docs/DEFINITION_OF_DONE.md): six gates per console, each green, amber or red, with every ...
- `scorecard_data.py`: The readers behind tools/scorecard.py (not a tool itself).
- `scorecard_records.py`: The readers of the records tools/scorecard.py needs that DP keeps for it alone (docs/DEFINITION_OF_DONE.md): the per-worker busy/idle log (core/busylog, gate ...
- `scorecard_runtime.py`: Gate 6 of tools/scorecard.py (docs/DEFINITION_OF_DONE.md, docs/RUNTIME_TARGET.md): does a console's reference game run at 144 fps at 2560x1600 with no emulated ...
- `scorecard_scale.py`: Gate 5 of tools/scorecard.py, scale (split from it at its 400-line cap; docs/DEFINITION_OF_DONE.md): a console's whole binary projected at its measured rates, ...
- `sdk_classify.py`: Classify a game's library functions as SDK, engine or unknown from FID results (tools/sdk_fid.py apply).
- `sdk_fid.py`: Function ID databases built from a Katana SDK release, and FID applied to a game (batch 25a).
- `sdk_fid_lib.py`: Pure parts of tools/sdk_fid.py (no Ghidra, no SDK tools): library formats, the libraries an SDK holds, skip-if-done stamps, command-line chunks, and the FID ...
- `selective_restamp.py`: Selective re-stamp after an oracle change that only some harnesses can see: re-prepare those, prove on a random sample that the others come out byte-identical, ...
- `sig_override.py`: Raw return-type overrides (batch 27): list the candidates, check the names entries' "raw_ret" keys, apply them.
- `sigdrift.py`: Signature drift: matched functions whose stored harness no longer agrees with the current checker's prototypes.
- `sizes.py`: the files over the 400-line cap (AGENTS.md "Layout"), and the ratchet that keeps it from drifting again.
- `sq_unaligned.py`: Make the answers of the unaligned store-queue judge pass (core/sq_unaligned): state/sq_unaligned/<addr>/ for every function with a window register.
- `stack_overread.py`: List readable C that reads a host stack local past its end, or copies one out half written (core/stack_overread).
- `stack_params.py`: Pointer types for the parameters callers pass a frame buffer: a machine pass over the lift's files.
- `stack_uninit.py`: Frame words functions read before anything writes them (core/stack_uninit.py), and pinning them to 0.
- `storage_pins.py`: Registry prototypes whose meaning changes under placement by storage (batch 14), and the "storage" pins that keep it.
- `symset_common.py`: Shared by the symbol-set inventories (targets/gamecube_ppc/tools/symbol_inventory.py, targets/xbox360_ppc/tools/pdb_public.py): the machine gate and priority ...
- `symset_consoles.py`: The scan / report / one commands of `symbol_inventory.py --console xbox|dreamcast|saturn`: lists a console's sets, scans each with that console's reader ...
- `symset_sega.py`: What a Dreamcast symbol set or a Saturn disc holds, read from file contents: the Sega library build stamps ("Shinobi Ver 1.62 Build:Aug 30 1999 17:32:03", ...
- `symset_xbox.py`: What an original Xbox symbol set holds, read from the contents of its XBE, PE, MAP and PDB files: the title and the XDK library versions in the XBE header ...
- `translate_backlog.py`: The translator stage (core/translate_stage, docs/TRANSLATOR_STAGE.md) over the harnesses a workspace already has: what the stage would have done for each ...
- `usage_readable.py`: usage_report.py readable: what each readable attempt cost, read and ran (see usage_report.py).
- `usage_report.py`: Where pipeline tokens go.
- `watchdog.py`: Bounded pipeline watchdog: poll the running servers once a minute and exit early, with the reason, as soon as something needs action (a server unreachable, ...
- `wave.py`: The re-stamp wave as one resumable driver: hold, broker live test and landing, recatalog, re-prepare every stale matched harness of every workspace through the ...
- `wave_game.py`: The game route of `tools/wave.py run --game WS`: one hands-off run of a workspace from its catalog to readable C, translator first (docs/DEFINITION_OF_DONE.md ...
- `worktree_prune.py`: List (and with --apply remove) DP worktrees whose work is done: the standing rule for worktree build-up.
- `wtool.py`: *(no docstring)* The watcher agent's only tool.
- `x360_vote.py`: The Xbox 360 instruction vote: a second, independent check of the PowerPC translator.
- `xemu_check.py`: Cross-check the oracle with a second, independent SH-4 emulator (xemu, targets/dreamcast_sh4/xemu).
- `AutoOnboard/autoonboard.py`: New-game onboarding in one resumable command: from a disc image in a game folder to a scaffolded project, an extracted workspace and a function census (later: ...
- `AutoOnboard/onboard_dc.py`: The Dreamcast adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages.STAGES): disc, extract and census, each calling the existing tools ...
- `AutoOnboard/onboard_estimate.py`: tools/AutoOnboard/autoonboard.py's estimate and translate stages.
- `AutoOnboard/onboard_gaps.py`: GAPS.md for a workspace, from the records a translator pass leaves (tools/AutoOnboard/autoonboard.py's gaps stage; also run on its own, read-only, on any ...
- `AutoOnboard/onboard_gc.py`: The GameCube adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages): disc, extract, census and the boot contract, each calling the existing ...
- `AutoOnboard/onboard_prove.py`: `onboard.py prove`: the three proofs an onboarded game's build gets before it goes live, in one command.
- `AutoOnboard/onboard_remote.py`: Runs tools/AutoOnboard/autoonboard.py's build-box stages on build box B or build box A in one ssh call: one script over stdin, a check that no -benchmark process runs ...
- `AutoOnboard/onboard_sat.py`: The Saturn adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages.STAGES): disc, extract, census, symbols and boot, each calling the existing ...
- `AutoOnboard/onboard_stages.py`: The stages of tools/AutoOnboard/autoonboard.py: per stage its contract (what it needs, the existing tool it calls, where it runs, the verdict it writes) and ...
- `AutoOnboard/onboard_symbols.py`: tools/AutoOnboard/autoonboard.py's symbols stage, one acceptance rule for every console with symbol sets (Xbox 360, GameCube).
- `AutoOnboard/onboard_x360.py`: The Xbox 360 adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages.STAGES): disc, extract, census and boot (its inputs), each calling the ...
<!-- tools-index:end -->

### decomp.py audit

`python decomp.py audit --project A [--project B ...] [--verbose] [--json]` groups every unmatched function by
blocker kind, one column per project. The kinds are data: `targets/<target>/target.json` `blocker_kinds`, in
priority order, each `{id, status: [ledger statuses], detail: regex on verdict_detail, diff: regex on the last
attempt's first-diff lines (diff_all: every line), stage: regex on its stage, fix}`; the first kind with a listed
status and any matching condition wins. A failed/skipped/error function no kind matches is **UNDECLARED** and the
exit code is 2: declare the new kind (or fix its cause) so nothing hides in a long tail. Queued, running, stopped and
not-started functions are counted as pending. `known_blockers` (parking during a run, `core/job.py`) is separate.

### tests/ and tools/regress.py: the gate for every checker change

`tests/patterns.json` lists the project keys (each key's workspace path is this machine's, in the git-ignored
`tests/patterns.local.json` `{"projects": {key: path}}`; a selected key without one stops regress). The fixtures
are game-derived C, so they live with each game, never in this repository: `WORKSPACE/regress/fixtures.json`
`{"fixtures": [{id, pattern, addr, pass, fail}]}`, with `pass` and `fail` file names in that folder
(core/toolcfg.patterns adds them with their project key). `pass` is C a correct checker must PASS, `fail` C it must FAIL on behaviour (a minimal semantic change, described in
`fail_change`; failing on lint or the build does not count). Seeded with every project's `references` and
`broken_control`, plus one Toy Commander function per already-fixed checker pattern: stack arguments s0..s7, a tail
branch, fr12..fr15 runtime state, a switch table (TcSwitch), a shared tail (TcTail), float callee arguments, writes
through pointer arguments, a float return. Spawn workspaces each hold a copy of the shared reference files (`spawn-*.c`). A fixture
with `"runtime": "readable"` judges its raw C with the readable runtime (`readable_check.check_raw`, the 32-bit
build refine.py uses) on the same harness; the readable runtime implements the whole raw API (r0..r3 parameters,
r8..r14 and FPU state, RETX results, callees taking r0..r3, P4 stores), so every matched function runs there. One
with `"runtime": "readable-src"` judges readable C (`#include "game.h"`) as refine.py does, with raw names and
prototypes plus only the fixture's own `"names"` (a `readable/names` file: `{"functions": {...}, "globals": {...}}`).

`--parity` also judges each sampled or listed function's raw C with the readable runtime (item `rd.ADDR`, expected
PASS), so both judges must agree; `--list FILE` (one project) judges exactly the matched functions listed in FILE
instead of a sample. The full Toy Commander parity run, in bounded chunks of about 550:
`python tools/bounded.py 7300 python tools/regress.py --projects tc --parity --list CHUNK.txt --timeout 7000`
(a chunk of 558 took 66 minutes in batch 14 on a busy machine, more than a 3600 s bound).

`python tools/regress.py [--projects tc,sp|all] [--only RX] [--sample N] [--seed S] [--jobs N] [--catalog]` judges
every selected fixture plus N seeded random matched functions per project (default 15; 0 with `--only`) with the
**current working tree**, against harnesses prepared afresh: per project, a private copy of the Ghidra project
(a fresh `ghidra_slots/slot1`, else the master) plus `--jobs` slot copies in `tests/ghidra_work/<key>/` (Ghidra
refuses project paths with a `.`-element), and state copies, scripts, packets, harnesses and builds in
`tests/.work/<key>/`. Both are gitignored and reused while the live catalog is unchanged. No live harness, packet,
ledger or Ghidra slot is written. `--catalog` re-runs the catalog fixups (TcPools, TcDead, TcRuns, TcOrphans, TcPrune,
TcSwitch, TcCont) and TcCatalog on the private copy first (needed when those scripts change; changed scripts make a new copy). A fixture
with `"needs_catalog": true` (its function exists only after a new catalog rule) does the same for its project
without `--catalog`. The table's `live` column says whether the fresh harness is byte-identical to the
live one (harnesses of functions matched before a checker change keep their old vectors). Exit 1 when any
PASS-expected item fails, any FAIL-expected item passes, or anything errors. `--reuse` (and `--parity`) reuse a
harness whose meta.json `oracle_stamp` (core/oracle_stamp.py: a hash of the oracle scripts, facts, counts, hints,
catalog, fsca table, binary and Ghidra install) is current and re-prepare the rest; the run prints how many were
reused and prepared. `--reuse` is never a gate run. `--jobs` defaults to a quarter of the cores, at most what free
RAM holds (3 GB a preparation), 1..8. An item judged on no vector (0/0) reads ORACLE, never FAIL; a group with
ORACLE or an out-of-time refusal is prepared once more alone after the parallel run (the oracle's deadline is
wall-clock, so load can cause it). `python tests/test_tooling.py` checks the tooling itself (effort, output cap,
ORACLE mapping, --jobs bounds, oracle stamp, usage parsing, lean selection, tests/test_retire.py: retire, the
consistency check, honest redo status, and tests/test_locks_systems.py: two processes hammering the readable ledger
lose no entry, the bounded lock wait, system assignment) in seconds, without Ghidra.

**Deploy rule.** Any change to the checker (`core/check.py`, `core/lint.py`, `targets/*/runtime/`,
`targets/*/ghidra_scripts/`, the input template) runs, with all servers stopped and before any restarts:

```
python tools/bounded.py 7200 python tools/regress.py --projects all --sample 15
```

across every project (fixtures, the seeded sample and the broken controls), and must exit 0. A failing item is
investigated, never waved through by loosening the check. A newly fixed checker pattern gets its own fixture.
