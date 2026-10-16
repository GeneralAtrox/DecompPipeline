# DecompPipeline

Behaviour-checked function decompilation with locked-down LLM agents. Can a small model (Haiku) decompile game
functions if the process is rigid and the verdict cannot be argued with? Each function gets one answer from a
machine judge: **PASS** or **FAIL**.

The tool holds no game data. Each game is a **project workspace** (a folder with `project.json`), and each CPU or
platform is a **target** under `targets/`. Today: `dreamcast_sh4` (see its README). A new CPU (for example Xbox
360 PowerPC) is a new target.

## Layout (every file at most 400 lines: `decomp.py sizes`)

| Path | Role |
|------|------|
| `decomp.py` | CLI: `python decomp.py --project <workspace> setup / serve / prepare / check / investigate / systems / sizes` |
| `config.json`, `core/toolcfg.py` | Pipeline defaults (model, effort, attempts, workers, watcher); the machine paths (`ghidra_home`, `java_home`, `vcvars`, `sevenzip`) are null there and set in the git-ignored `config.local.json`, merged over it (core/toolcfg; an unset one stops naming the key) |
| `core/lint.py`, `check.py` | Rules, build, run, compare: the official verdict |
| `core/agent.py`, `agent_cli.py` | TASK.md, sandbox, locked-down `claude -p` session (command line, lean system prompt, effort, output cap) |
| `refine.py`, `core/refine.py`, `readable*.py`, `lean_*.py` | Readable stage; `readable_sandbox.py`: one attempt's sandbox (TASK.md, check.py, context.md); `readable_lean.py` + `lean_uses.py` + `readable_headers.py` + `lean_select.py` (whole, projected or only declared structs) + `lean_layout.py` (struct layouts, projections) + `lean_groups.py` (accessor groups in part) + `lean_index.py` (index.txt) + `lean_hidden.py` (constants a projection hid, batch 26): the lean sandbox headers; `lean_proof.py`: static asserts that every projection keeps the full layout; `header_lint.py` (one owner per name, game.h build) + `header_snapshot.py`: the validated, run-local header snapshot every readable run builds against |
| `docs/DESIGN_BRIEF_HEADERS.md` | The headers section every project's readable DESIGN_BRIEF carries (run `refine.py header` after each edit; one owner per name) |
| `core/oracle_stamp.py`, `callee_drift.py` | The oracle-input stamp in each harness's meta.json; a harness is current (regress `--reuse`/`--parity` reuse it) when its stamp matches and every callee record equals the callee's own harness signature (ghidra.prepare takes the records from the callees' harnesses: Ghidra's view of a callee can be stale) |
| `core/cascade.py` | The loop behind tools/reprepare_stale.py (scan, plan, re-prepare, judge; prepare and judge passed in, tested without Ghidra). |
| `core/sync_run.py`, `sync_names.py`, `sync_text.py`, `raw_names.py` | `refine.py sync-names` (and the start of `verify`): rename raw names the registry has named since and follow prototype changes, judged before writing; the raw-name audit |
| `core/coverage.py`, `global_extents.py`, `raw_consts.py` | `refine.py coverage`: names still missing, globals covered only inside a named global's declared extent, overlapping named globals, pointer constants raw C passes or stores without a name |
| `core/readable_outptr.py`, `varargs_audit.py`, `design_requests.py` | Callee out-pointers into the caller's stack (the original's bytes delivered at the call); `refine.py varargs` (the variadic convention at every recorded call); `refine.py requests` (design gaps the readable agents reported) |
| `core/readable_outrun.py`, `readable_stack.py`, `runtime_rd/rd_trace.c` | The matched raw C runs behind the out-pointer delivery (trace, painted runs, cached); raw.c's guest stack addresses (annotated raw.c, context.md, RR6) and stored stack addresses paired with the readable ones; the trace of how far the original reads a callee's buffer |
| `core/library.py`, `library_decls.py` | `refine.py library`: every readable file compiled as one program with the port's game.h at /W4 /WX, archived with lib.exe /WX, plus one unit of every file's external declarations |
| `core/oracle_fresh.py`, `core/final_audit.py` | Today's oracle stamp computed without writes, the stamp scan and raw recount (`tools/fresh_oracle.py`); the checks of `tools/final_audit.py` |
| `core/lookalike.py`, `lookalike_adopt.py`, `draft.py`, `draft_calls.py`, `draft_run.py`, `draft_gate.py`, `readopt.py` | Readable files without an agent: `refine.py lookalike` (identical functions of another project), machine drafts (`refine.py draft`, `run --draft`), re-adopting earlier attempts (`verify --adopt`, `refine.py adopt`) |
| `core/rodata_strings.py`, `names_insert.py` | `refine.py strings [targets|@FILE|--scope] [--write]`: read-only strings raw.c passes or loads from a literal pool with no registry name get a content-derived `g_str_`/`g_fmt_` name (`const char NAME[N]`, `"machine": "strings"`) in the names file of the system using them most (a tie: the lowest-address user's; none: strings.json); a dry run by default, never renames an entry |
| `core/sig_override.py`, `sig_rewrite.py`, `ret_use.py`, `sh4_asm.py`, `stack_uninit.py`, `data_funcs.py`, `packet_split.py` | Batch 27. A names entry's `"raw_ret"` (void/u32/s32/f32) corrects a raw return type: prepare applies it (meta `ret_override`), the judge compares that register, `tools/sig_override.py` carries matched C, callers and re-judging; `ret_use.py` lists candidates (a void function tail-calling a value-returning one; an f32 no call site reads), never applied by itself. `stack_uninit.py`: frame words read before any write on some path (meta `uninit_stack`, context.md note). `data_funcs.py`: code addresses used only as data values (no function). `sh4_asm.py`: asm.txt parser and flow; `packet_split.py`: packet asm parts |
| `targets/dreamcast_sh4/ghidra_scripts/TcCalls.java`, `runtime_rd` `rd_set_self` | Batch 28. A mocked call whose target is outside guest RAM and no constant of the body (a random word read from the inputs) is an invalid vector: the oracle repairs the word holding it into a fresh chunk pointer (NULL stays a valid target). The readable thunk names the function under test (`rd_set_self`), and a stored, passed or returned host address of it is reported as its guest address |
| `core/asm_pool.py`, `TcProvenance.java` | Batch 29. The Haiku sandbox's asm.txt gets the first words a literal-pool `mova` points at (hex, and the f32 reading of a plain number; the packet itself stays as prepared). In the oracle's rescue pass a store outside RAM stops the run at the store (TcOracle.storeFaults) and the input its base came from (a mocked return, or a load the vector can set outside the own frame) is repaired. TcCatalog: a hardware base OR-ed with an offset is still an address (`or` kept like `add`) |
| `core/call_coverage.py`, `TcReach.java`, `TcCfg.java`, `TcRepair.java`, `tools/call_coverage.py` | Call-site coverage (docs/CALL_COVERAGE.md). The oracle's reach pass (target.json `reach_calls`) adds vectors that reach call sites no first-pass vector reached, by mutating the inputs read just before the branch toward each; a site it cannot reach gets the reason in oracle_summary.json `reach`. The report, the final audit's `calls` check, the doctor's figure and the ledger's `unreached_calls` use it; `tools/call_coverage.py reach` re-prepares old harnesses |
| `docs/HAIKU_WORKFLOW.md` | What a Haiku attempt receives, may run and costs; the knobs; how to measure |
| `core/orch.py`, `job.py`, `brief.py` | Worker pool and queue; one function start to finish; agent brief |
| `core/watcher.py`, `watch_brief.py`, `gate.py`, `javalint.py`, `tools/wtool.py` | Oracle watcher: Opus session, strict gate, Java allow-list linter, agent tool |
| `core/ghidra.py`, `systems.py`, `sysassign.py`, `usage.py`, `paths.py`, `filelock.py` | Headless Ghidra and slots, system clustering and placing later functions, account usage, locations, cross-process locked state writes |
| `core/retire.py`, `doctor.py` | Retiring artefacts of addresses that stopped being functions; the workspace consistency check |
| `core/targets.py`, `fn_claim.py`, `infra.py` | Target lists, each function once; one worker per function (a claim held for a run's whole cycle, by verify, sync-names and check); infrastructure errors retried once and recorded as `infra_error`, never as a verdict |
| `core/server.py`, `ui/index.html` | Monitor API and UI |
| `tools/fake_agent.py`, `fake_watcher.py` | Token-free stand-ins for dry runs |
| `core/audit.py`, `tools/*.py`, `tests/` | `decomp.py audit`, operator tools, the pattern regression suite (see the last section) |
| `targets/<name>/` | `target.json` (CPU, Ghidra language, loader, load address, `locked_scripts`: the oracle scripts TcInputs builds on, compiled with it in every watcher trial and never edited), `ghidra_scripts/` (TcSetup, TcCatalog, TcGraph, TcBase, TcFlow, TcOracle, TcPrepare), `template/TcInputs.java` (starting input generator), `ghidra_lang/`, `runtime/` (the C API and runner) |

A project workspace:

| Path | Role |
|------|------|
| `project.json` | `name`, `target`, `ghidra_name`, `binary` (relative path), `binary_sha256`, `game_end`, `extra_functions`, `priority`, `references`, `broken_control`, `port`; may override any `config.json` key |
| `extra_functions` (project.json) | Functions to decompile beyond the game ranges (e.g. a studio's engine linked between SDK code): a list of hex addresses, or a file in the workspace (one per line, `#` comments). In scope = inside `game_end` / `game_ranges`, or listed (core/scope.py, the one definition). Extras are queued, prepared, matched, given systems of their own (`tools/assign_systems.py`), made readable and audited like game functions, and counted apart (UI overview, `decomp.py audit` `extra:` rows, `tools/final_audit.py` `extras` check: INCOMPLETE while open, completeness.py / coverage.py byte line). The catalog and the byte classes still use the game ranges only. Without the key nothing changes. |
| `oracle/TcInputs.java`, `oracle/hints/<addr>.txt` | This game's input generator and hints (start from the target's template; changed only through the watcher gate) |
| `reference/` | Hand-written controls: accepted C for the `references`, plus the deliberately broken control |
| `ghidra/`, `ghidra_slots/` | Ghidra project and read-only copies for parallel preparation |
| `packets/<addr>/` | What the agent reads: `asm.txt`, `ghidra.c` (split into `_1`, `_2`... past 400 lines) |
| `harness/<addr>/` | Vectors and expected results (never shown to the agent) |
| `work/`, `matched/`, `runs/`, `sandbox/`, `build/` | Latest C per function, passed C, per-attempt transcripts and checks, agent sandboxes, builds |
| `state/` | Catalog, ledger, logs, assembled Ghidra scripts (`state/ghidra_scripts`). Files more than one process writes (ledger.json, readable.json, systems.json, usage.json) are rewritten atomically and read-modify-written under a `<file>.lock` (core/filelock.py: msvcrt/fcntl, bounded wait, an error naming the lock); a leftover `.lock` file is harmless. A Windows open or replace refused because another process has the file open or is replacing it ([Errno 13]) is retried with a backoff for up to 5 s, then raises naming the file; a pass whose `readable` ledger write fails still ends `readable` (core/refine.record_pass), never `error` |
| `retired/<kind>/`, `retired/manifest.json` | Artefacts of addresses that stopped being functions, moved by `tools/retire.py` (never deleted), and why |
| `watcher/` | Watcher workspace, sessions, `history/` (every promoted change: before/after, diff, report) |

New project: create the folder with `project.json`, copy `targets/<target>/template/TcInputs.java` to
`oracle/TcInputs.java`, write the reference C, then `decomp.py --project <folder> setup`. Step by step, for a new
Dreamcast game: docs/NEW_GAME.md.

## Run it

```
python decomp.py --project path/to/workspace serve   # UI at http://127.0.0.1:<port>
```

- **Start workers** (count box, default `workers` in DecompPipeline/config.json, cap `max_workers`): each worker takes one
  function at a time from a shared queue. Every function is judged alone (callees are mocked), so workers never
  wait on each other. Ghidra locks a project while it is open, so up to `ghidra_parallel` preparations run at
  once in slots: slot 0 is the master project (the only one that saves Matched/Failed bookmarks), slots 1..
  are read-only copies in `ghidra_slots/`, re-copied automatically when the catalog changes.
- **Investigate next function**: one worker, one function. **Investigate address**: a specific function.
- **Loop** off: each worker stops after its function. Loop on: workers keep taking functions until the queue is empty.
- **Stop** (per worker) / **Stop all**: kills that worker's Ghidra/Haiku process tree at once. The function goes back to the queue.
- **Copy agent prompt**: JSON brief for a stronger agent: function, system, every attempt and its diffs, files to read and edit.

Queue order: the priority list, then the worker's own system, then a system no other worker is in, then anything;
easiest first within each group. Systems come from `decomp.py systems` (Louvain clustering of
calls, shared globals and address neighbours; `--graph` re-exports the call graph from Ghidra first).

Shared limits for the whole pool:

- `budget_total_usd` (default `null` = off): only for API-key billing. On a subscription the CLI still reports an
  API-equivalent cost per session; it is shown in the UI as an estimate, not a charge.
- **Claude account usage** panel: 5-hour and weekly utilisation and reset times, read from the `rate_limit_event`
  every CLI session prints (no credentials are read). Refreshed by every attempt, by the Refresh button, and every
  30 min while the page is open and idle (a one-line Haiku probe). When a limit is hit, workers sleep until it
  resets; the wait does not use up an attempt.
- Rate limits (HTTP 429 / overloaded) back off 60, 120, 240, 480, 600 s and do **not** use up an attempt.
- An authentication failure stops every worker (log in again, then restart).

Dry runs without tokens: set `TCD_AGENT_COMMAND` to `["{python}", "{root}/tools/fake_agent.py"]` ({root} = DecompPipeline/)
(`TC_FAKE_MODE` = `reference`, `stub` or `tamper`; `TC_FAKE_DELAY`, `TC_FAKE_COST`, `TC_FAKE_RATELIMIT=1` simulate time, cost and one 429).

The `claude` CLI on PATH runs the sessions (its own login). `model` is an alias: on 2026-10-10 the CLI updated and `haiku` moved from
claude-haiku-4-5 to claude-haiku-5-5. Each attempt records the model actually used, its effort and thinking tokens.
`effort` (`low` ... `max`, or `null` for the CLI default, which adapts per turn) is passed as `--effort`; pinned to `high`.
Readable attempts use `readable_effort` (null: `effort`; `refine.py run --effort` overrides it for one run; a raw.c
over `readable_large_bytes`, 4096, uses `readable_effort_large`, medium, and TASK.md says how to work through it) and stop
past `readable_output_cap` output tokens (40000). `agent_system_prompt: "lean"` replaces the CLI's system prompt
with a short constant one (`"cli"` keeps it). Sessions load no CLAUDE.md and no memory. See docs/HAIKU_WORKFLOW.md.

## What "byte matched" means here

Compiler byte matching needs the game's original compiler, which we usually do not have.
Instead the judge is **behavioural and byte-exact**: the original machine code runs in Ghidra's p-code
emulator on 96 random inputs (48 dev + 48 hidden). The agent's C runs on the same inputs and must reproduce,
bit for bit:

1. the return value (on SH-4: r0 or fr0),
2. the final bytes at every address the original writes,
3. every call: target, order and argument registers (callees are mocked with scripted return values),
4. target-specific extra state (on SH-4: the XMTRX back-bank registers xf0-xf15).

The whole build (the agent's work.c and our runtime) uses MSVC `/W4 /WX`: any compiler warning fails the attempt (rule R11).

The agent only ever sees the dev results. The hidden set is checked after the session, so overfitting the visible tests fails.

## Anti-cheat (lessons from the blog post)

| Risk | Defence |
|------|---------|
| Agent edits the tests or the judge | Sandbox files are read-only, hashed before and after. Any change = TAMPER, function fails. |
| Agent claims success | Its words are ignored. Only the official check, re-run by the orchestrator, decides. |
| Overfitting visible tests | 48 hidden vectors with different seeds. |
| Inline asm, emulating the original bytes, hard-coded tables, globals, extra headers | `lint.py` rules R1-R10, checked before compiling. Reading any instruction address fails at runtime. |
| Sloppy C (lossy conversions, unused variables, dubious expressions) | R11: the build uses `/W4 /WX`, so any compiler warning fails the attempt. |
| Wandering, endless retries | Tools limited to Read/Edit/Write on work.c and `python check.py`; $1 and 15 min per attempt; at most 8 attempts; stops after 2 attempts without progress. |
| A weak test that passes anything | The oracle reports block coverage and per-block hit counts; controls below prove the judge rejects wrong code. |

## Oracle watcher (Opus)

Header toggle "Watcher (Opus)". When on, a function SKIPPED because the oracle could not produce enough valid
vectors wakes the watcher at once (and it sweeps the ledger every `watcher_sweep_s` = 60 s; the panel shows the
last sweep, and skips it cannot repair are counted there by reason, since inputs cannot fix those). One Opus session
(`watcher_model`) works in the project's `watcher/ws/`. It can edit only the project's `oracle/TcInputs.java` (that game's input generator)
and `oracle/hints/<addr>.txt` (`ptr r4+0x10c`, `range r5 1 12`, ...), and run only `python wtool.py try|gate|wait`,
which ask the server to run checks. It decides nothing. The gate, run server-side on a frozen snapshot:

1. `javalint.py` allow-lists TcInputs (one import `java.util.*`; no I/O, reflection, threads, lambdas, Ghidra
   API or oracle state; no reseeding; new members named gen*) and hints. Nothing unlinted is ever compiled.
2. Target: enough valid vectors in both sets, block coverage >= 70 % (or all but one), dev results not all
   identical, no hidden vector equal to a dev vector.
3. Regression: the project's `references` plus `watcher_regress_sample` matched functions are regenerated with the new
   generator; their accepted C must still PASS, coverage must not drop, and the broken control must FAIL.

Only then is it promoted (holding every Ghidra slot, compiled once, rolled back if that fails), archived in
`watcher/history/`, and the function requeued. At most `watcher_max_tries` sessions per function,
`watcher_max_trials` tries and `watcher_max_gates` gates per session. Inputs can only change *which* tests
exist; expected results always come from the original code, so a promoted generator cannot make wrong C pass.
TcPrepare (locked) also rejects any vector that patches memory outside `[RAM_LO, RAM_HI)`, the C runner's guest
memory: such an input would let the original read where every C version must fault.

## Operator tools, the audit and the regression suite

### tools/

Every tool takes `--project DIR` like `decomp.py` (repeatable where several make sense) and reads address lists
from `@FILE` (one or more per line, `#` comments). Never put long hex address lists on a command line: Windows
Defender kills such processes as Trojan:Win32/Commando.A!ml.

| Tool | Does |
|------|------|
| `bounded.py SECONDS CMD...` | Runs CMD with a hard limit and kills its whole process tree (`taskkill /T /F`) on expiry, exit 124. Git Bash `timeout` leaves java.exe/python.exe grandchildren running. |
| `watchdog.py --project DIR... [--workers N] [--minutes M]` | Polls the running servers each minute; exits 1 with `ACTION:` as soon as one is unreachable, usage-blocked, finished, short of workers or has its loop off. Always bounded. |
| `requeue.py --project DIR --note TEXT (--rx REGEX \| ADDR... \| @FILE) [--status S,S] [--seed NOTE [--stale]] [--dry]` | Requeues by verdict_detail regex (statuses default `skipped,failed`) or by address. `--seed` starts the re-match from the current matched C (`--stale`: from `matched_stale/` first, to get back C a faulty harness forced out), judged before any agent run. Through the server's `/api/requeue` when it runs; otherwise on the ledger directly, exactly as the server does, after a backup to `state/ledger.before-requeue-<time>.json`. |
| `sigdrift.py --project DIR [--list FILE] [--all] [--json FILE]` | After a checker deploy: matched functions whose stored harness disagrees with the current prototypes (TcSigs.java on a private read-only Ghidra copy). Tier `signature` (signature, result register or support changed: lint R3 fails, requeue) and `compared` (a callee or runtime state compared on more registers: re-judge; or a call records an older callee signature than the callee's own harness: re-prepare it before readable work). `--list` writes the signature tier (with `--all` both) for `requeue.py @FILE`. |
| `reprepare_stale.py --project DIR [--dry] [--rounds 8] [--per-fn 2] [--stamps [--max N] [--slots N]]` | Servers stopped. The callee-record cascade (core/cascade.py): re-prepares matched harnesses whose callee records differ from the callees' own harness signatures until a scan finds none (each caller at most --per-fn times, since a re-prepared caller's new signature can leave its own callers stale; the callees are the reference, never touched). Every re-prepared matched function's raw C is judged on its new harness; a FAIL is listed for tools/requeue.py. Exit 1 while anything is left. `--stamps [--max 100] [--slots N]` first re-prepares the matched harnesses whose oracle stamp is not today's (no stamp, or other oracle inputs), `ghidra_parallel` (or `--slots N`) at once in Ghidra slots, at most --max per run; resumable (a re-prepared harness carries today's stamp): repeat it under bounded.py until none is left. |
| `fresh_oracle.py --project DIR [--recount] [--workers N] [--list FILE]` | Read-only. Lists every matched harness that is not current (no harness, no stamp, an old stamp, callee records that differ), today's stamp computed in memory (core/oracle_fresh); `--recount` re-judges every matched raw C on its own harness with check.py (parallel, builds in a temp folder) and prints passed/total against the catalog's matched count. Exit 0 only when all are current (and all pass). |
| `final_audit.py --project DIR [--recount] [--workers N] [--no-sync]` | The goal's final audit in one process (run it under bounded.py): one PASS/FAIL line each for the fresh oracle, the headers (`refine.py header`'s checks on a snapshot, live game.h current), `refine.py verify` (N/N, N = catalog game functions minus the excluded hw/sys ones; 0 missing, STALE, HARNESS_STALE, failed, FLAKY, RAW_NOT_MATCHED, errors), `refine.py coverage` (all zero), call-site coverage (`calls`: no unreached call site the reach pass has not tried), raw fn_ names, `refine.py library`, completeness, the consistency check, the excluded functions' intent notes (project.json `intent_notes`, default HARDWARE_FUNCTIONS.md), then the readable usage report (INFO) and a verdict (PASS needs `--recount`; else at best INCOMPLETE). Full output in `state/final_audit-<timestamp>.txt`. Writes nothing else but what verify writes (sync-names, readable.json), header snapshots and build/ output. |
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
| `sig_override.py --project DIR [--candidates] [--apply] [--json FILE]` | Validates every names `"raw_ret"` (refused: no catalog function, a bad kind, void while a call site reads the result); `--candidates` lists core/ret_use candidates; `--apply` (servers stopped) rewrites the harness meta, the matched C (kept only on PASS, else listed to requeue), the callers' callee records and judges their matched C, and lists readable files for `refine.py verify`. Backups in state/sig_override_backup/. |
| `stack_uninit.py --project DIR [targets\|@FILE] [--pin] [--json]` | Functions that read a frame word no path wrote first. `--pin` appends `genrange 0xADDR 0 0` to oracle/hints/ADDR.txt (the game's TcInputs must support genrange): re-prepare, then the word is 0 in every vector and readable C writes 0 with a comment. |
| `data_funcs.py --project DIR [--ghidra-project DIR] [--no-ghidra] [--json FILE]`, `--add @FILE` | Code-region addresses that function code uses as values but no function owns, checked read-only by tools/ghidra_tools/TcEntryCheck.java (valid, code, invalid, data, inside). `--add` (re-checked; refuses anything not valid/code) writes project.json catalog_overrides entry (+ extra_functions); then catalog, assign_systems, prepare. Never in bulk. |

### Readable stage: where arguments go (core/readable_abi.py)

A readable prototype's parameters are placed by storage, not by position: the SH-4/SHC convention (ints and
pointers r4..r7, f32 fr4..fr11, then the stack; a variadic prototype's last named parameter and its variable
arguments on the stack) when it covers the original's storages as a set or is a part of them; a names entry's
`"storage": [...]` (r0..r7, fr4..fr11, stack+N, none) where the convention cannot say it; else by position. Its
own entry thunk and the call trap (`RdProto.regs`: 0..7, a..h, S..Z for s0..s7, x; a `*` params suffix for
variadic) both use it. A named callee keeps its raw name `fn_ADDR` in game.h when the raw arguments map onto its
prototype (lint RR4 otherwise); a raw-named callee's parameters are named by storage there (`param_fr4`,
`param_r4`, `param_s2`). `"varargs": "printf"|"null"` on a variadic entry makes the judge compare the stack
words each call passes (core/readable_varargs.py). Callee-saved registers a callee reads (r8..r14, fr12..fr15) are
set with sh4_set_r / sh4_set_fr before the call and compared there. `refine.py verify` and `run` report a function
without verified raw C (no matched/ADDR.c or harness meta) as RAW_NOT_MATCHED and go on. A redo (`run --redo`, or a
stale function's rerun) that does not pass leaves the old readable/src file and its verdict (readable or stale) in
state/readable.json, with the attempt in `redo_failed` (`outcome`: failed, blocked, api_error or error; `attempts`);
a status never says failed, blocked, queued or error about a file that is still there. `refine.py status` counts them.

Lean sandbox: an attempt's game.h declares only the function, its callees, functions used as data and the globals
raw.c uses, plus the functions and globals the current readable file (or the starting work.c) uses; context.md
shows only the types and constants those need (core/lean_select; with batch 24 also what the readable file uses and
the constants whose raw.c value only they have: core/lean_uses.py), index.txt lists everything else (the official
check builds with the full headers). Batch 25: a struct with more than 8 members is projected onto the members raw.c's
offsets reach and the readable file names (`u8 _pad_0xOFF[0xLEN];` for the rest: same size, alignment and offsets,
proved by static asserts against the full definition in `verify --lean` and the preflight: core/lean_proof); a struct
more than 2 pointer hops from the declarations is only declared; the comment rounds read only the shown text (2
rounds); an accessor group shows only the macros raw.c reaches. Batch 26 (core/lean_hidden): a projected-away member's
comment or the declarations' name words bring back a constant group of at most `readable_lean_comment_group_max`
(2) names when a value or the name family matches. The types are shown once, in context.md (the sandbox's
types.h is a one-line stub; TC median 10 KB, was 112 KB). `refine.py run` judges a
do-nothing stub first (preflight) and marks the function `blocked` on a registry, header or stale-callee problem,
with no agent started. Options: `run --effort E` (this run's effort), `verify --lean` (build each passing file from
its lean sandbox headers; a report only), and `@FILE` address lists for `run`/`verify`. The 32-bit readable runner
links at the target's fixed `readable_image_base` (no ASLR): a vector's arbitrary function-pointer value must never
land in the runner's own code (with a random base it sometimes did: a crash, a missing call, a flaky verdict).

A variadic function's own readable C (`...` in its prototype) is judged too (batch 18): the entry thunk passes,
after the named parameters, the entry stack words after the named stack words up to s7 as u32 variable arguments,
and a probe with the same parameters reports where its va_list starts (runner line VAANCHOR); the judge pairs that
host address with the guest word after the last named parameter, so a va_list the function passes on (vsprintf)
must point exactly at the first variable word. The code reads them with va_start / va_arg(ap, u32) / va_end
(rd_rt.h includes stdarg.h; `va_list` is a registry type).

Batch 20. Pointer constants (core/raw_consts.py): an image address raw C passes, stores or compares as a value (not
the operand of rd/wr, not a tc_call target) is a pointer; context.md names it (the function, or the named global whose
extent holds it) instead of listing the bytes at it as a literal-pool constant, `refine.py coverage` counts the data
ones without a name (code-region ones are noted, not counted), and lint RR5 refuses a readable file that writes the
bytes stored at such an address as a number. Callee records are compared only on the argument registers the
callee's own harness takes (readable_check.callee_reads: TC's Xmtrx_MulPop records r4, the entry PR, which it never
reads). Out-pointers (core/readable_outptr.py): a named callee's writable `T *` parameter that the original points at
its own guest stack gets, at that call, the bytes the original's object holds there (a run of the matched raw C with
`B` lines prints them as `OB`; `D` lines copy them to the readable call's host pointer, bytes the original wrote
before the call excepted); the judge stays one-to-one on pointers. A variadic callee gets a context.md note saying
which raw.c `args` words its parameters are (SHC: the last named one and the variable ones on the stack). `refine.py
varargs` audits that convention at every recorded call (exit 1 on a problem); `refine.py requests [TARGETS] [--json]`
groups the missing fields, names and prototypes that failed agents reported, per system. There is no sh4_pr
accessor: a PR value left in an argument register is handled by callee_reads.

Batch 21: one worker per function. Every refine.py command taking targets (ADDR, SYSTEM, @FILE: CRLF or LF, a BOM,
`#` comments) runs each function once and says how many duplicates it dropped (core/targets.py; tools/common.py and
`regress.py --list` likewise). `run` holds the function's claim (core/fn_claim.py: core/filelock.claim on
state/fnlocks/ADDR.lock, with an ADDR.owner record naming the holder) for the whole cycle: raw check, preflight,
sandbox, agent, judge, ledger write. `verify`, `sync-names` and a manual `check` hold it while they build, judge or
record; the agent's own check.py does not (it runs inside the holder's cycle). A rival waits `function_lock_wait_s`
(15) and then skips the function as busy, writing nothing to its row; a run also skips a function another process
finished during this run. verify records a verdict only while the file is still the one it judged (else CHANGED).
An OS refusal of a file the tool itself wrote (core/infra.py: [Errno]/[WinError], LNK1181/C1083 on a file under
build/, sandbox/ or the header snapshots) is retried once and then recorded as `infra_error` (a redo keeps its file's
verdict, redo_failed outcome infra_error); `refine.py status` lists such rows, older `error`/`blocked` ones included,
and a plain `run` tries them again. tools/doctor.py clears an owner record whose lock no process holds (its process
died) and lists the claims held now.

Batch 22: the original's guest stack (core/readable_stack.py). raw.c runs on a guest stack whose r15 is 0x8cffe000 at
entry: an address at or above it is a stack argument (0x8cffe000 + 4N, a parameter), one below it the own frame. The
sandbox's raw.c carries a comment on every line that writes such an address as a number (`0x8cffe000 = stack
argument 0 = `y1`, not a global`; line numbers unchanged), context.md lists them under "Stack addresses in raw.c",
and lint RR6 refuses a readable definition that leaves out (or only `(void)`s) a parameter raw.c reads from its stack
slot. A guest stack address the original stores to memory (8c027610: &tex into g_pvrHeaders[2].texture) pairs with
the host stack address the readable code stores there, one to one with the pointer arguments
(readable_check.map_stack_args); another local's address, or a non-pointer, still differs. Callee buffers
(core/readable_outptr.py, readable_outrun.py): the bytes delivered at an out-pointer call are the names entry's
`"out": {"param": BYTES | "size_param"}` when given; for a `void *` / byte pointee with a size-named sibling
parameter (size, len, length, bytes, nbytes, or ..._size) that argument at the call (File_Read); for a struct its
sizeof; for a scalar or `void` pointee at least its sizeof and as far as the original reads the buffer after the call
(bytes it did not write itself), measured by a trace run of the matched raw C (runner line T, answer OT,
runtime_rd/rd_trace.c; up to the next stack address a call is passed; the eight word reads raw C makes to fill a
call's s0..s7 are no use). A callee whose record compares no register there (Gd_GetToc's raw signature is void) is
compared on it from then on, when every call passes a guest stack address. The raw runs' output is cached in
build/ADDR/<dir>_runs.json. runtime_rd/rd_p4.c also decodes `and dword ptr [m], 0` / `or [m], -1` (83/81/80 /4 or /1)
as a store, which MSVC /O1 emits for `*p = 0` (a P4 address there crashed: TC 8c058dca).

Batch 23: readable work without an agent. `refine.py lookalike --from SRC_WS` (core/lookalike.py, lookalike_adopt.py)
finds matched functions whose code is identical, relocations masked (core/xmatch.normalize; a target's mirror pool
addresses pair canonically), to another project's function with a passing readable file, reports near-identical ones
apart (not used), maps the source file's callees and globals through the paired slots (a global through any slot in
its declared extent, all giving one base), and writes state/lookalike.json: the report and a names proposal (never
over a name the target has; one source name for several target addresses is a conflict). `--apply` writes it to
readable/names/lookalike_KEY.json and include/sys/lookalike_KEY.h; `--judge` judges the rewritten files against a
scratch snapshot; `--adopt` adopts strict PASSes (`source: lookalike KEY:ADDR`). Machine drafts (core/draft.py,
draft_calls.py, draft_run.py): matched raw C with the registry's prototype and parameter names, accesses at named
scalar globals as names, literal-pool reads as constants, address values by name, and tc_call by name where the
arguments are certain; everything else stays raw, so the lints refuse it. `refine.py run` judges the draft first
(config `readable_draft`): a PASS that also reads as named C (no register-named variables, raw names, Ghidra
names or gotos) and passes the adoption gate (core/draft_gate: at most `readable_draft_max_lines` non-blank lines,
default 6, and no literal other than 0/1 whose value a header #define or enum names) is adopted (`source: draft`)
with no agent; with `--draft start` (the default; `adopt` stops
there) a failing draft is also the agent's work.c; `refine.py draft TARGETS [--adopt]` judges drafts alone. Re-adoption
(core/readopt.py): `refine.py verify --adopt` tries a STALE file's earlier attempts newest first and adopts the first
strict PASS (old file backed up to state/readopt_backup, `source: readopt attempt-NN`), else lists the function in
state/redo.txt for `refine.py run @state/redo.txt --redo`; `refine.py adopt TARGETS` does the same for failed rows.
The header parser keeps a `#define` whose trailing comment continues on the next line in one item (102 TC names were
lost to the lean selection before).

Sync: `refine.py sync-names [TARGETS] [--dry]` (core/sync_run.py), also run at the start of `verify` (not with
`--no-sync` or `--lean`), rewrites readable files that use a raw name the registry has named since (fn_ADDR in the
definition, calls and comments; g_/DAT_ADDR) or whose definition no longer matches its registry prototype. Arguments
and parameters are paired by storage and converted (clean, then with casts, then only the names the judge refuses
raw); the file is written only when the judge passes it, recorded in readable.json as an attempt `{"kind": "sync"}`
with no tokens, and the row becomes readable. A refused sync keeps the file and notes `sync_refused`; verify then
judges the old file as before (STALE only for a real break). Coverage: `refine.py coverage [TARGETS]` (no target:
every system's functions) counts a referenced global address as named only inside a named global's declared extent
(sizeof of its declaration, from the headers: core/global_extents), lists every other address with the named global
it follows, and lists named globals that overlap.

Library: `refine.py library [--out DIR] [--workers N]` (core/library.py; default out build/library) compiles every
readable/src file as the port will: one game.h in port form (prototypes, `extern` globals; check's game.h instead
makes other functions and globals macros on guest addresses), plus raw prototypes of unnamed functions from their
harness signatures. Each file is a translation unit at the judge's compiler, flags (/W4 /WX) and include path,
compiled in parallel and cached by content hash; the objects are archived with lib.exe /WX (a symbol two files define:
LNK4006); and one generated unit holds game.h and every file's external declarations under `#line` directives
(core/library_decls: prototypes, objects as extern, static items left out, a declaration naming a file-local type
skipped and counted), so two files declaring one name with different types fail (C has no type-checked linking).
It lists each failing file with its first diagnostic, groups diagnostics by kind (the message with names blanked)
with counts and examples, and says whether they point into files or headers. Exit 0 only with zero diagnostics.
Coverage leaves the excluded hardware/system functions (catalog tier "excluded") out of every count and says so.

Headers: `refine.py header` writes game.h and runs the header lint (`core/header_lint.py`). The lint fails, naming
every file and line, when two headers under readable/include define the same macro, typedef, tag, enum constant or
global, identical copies included, or the same ordinary identifier under two kinds. It then builds game.h at the
judge's flags (/W4 /WX) and exits 1 on any problem.

`run`, `verify`, `sync-names` and `check` build only against a validated, run-local snapshot of readable/include and
readable/names (`core/header_snapshot.py`, `state/readable_snapshots/`). Designers' edits during a run change
nothing the run compiles against. When the live headers fail, `run` waits up to `readable_header_wait_s` (900) and
then uses the last good snapshot; `verify` and `check` stop with exit 2. Attempts and passing rows record the
snapshot hash (`headers`). A STALE line in `verify` says whether the file last passed against older headers or
against the same ones.

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
