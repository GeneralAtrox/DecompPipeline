# AGENTS.md: how DecompPipeline works

The reference for agents (and people) working in this repository. Rules every session follows: [CLAUDE.md](CLAUDE.md)
(no redundant work; heavy jobs through `tools/jobreg.py`; reports end with a `Redundancy check:` line).

## What it is

Behaviour-checked function decompilation. Each function gets one answer from a machine judge: **PASS** or **FAIL**.
Each CPU's translator writes the first C for every function (docs/TRANSLATOR_STAGE.md); a PASS needs no agent. A
FAIL goes to a locked-down `claude -p` agent (Haiku by default), which only edits that function's C and can never
argue with the verdict. A readable stage then turns matched C into named, typed C (docs/READABLE_STAGE.md), and
`port/` builds it as a native program (port/README.md).

The tool holds no game data. Each game is a **project workspace** (a folder with `project.json`), and each CPU or
platform is a **target** under `targets/` with its own README: `dreamcast_sh4`, `saturn_sh2`, `gamecube_ppc`,
`xbox360_ppc`. A new CPU is a new target. A new game, from disc to port: docs/NEW_GAME.md.

## Where to read next

| Doc | What it covers |
|-----|----------------|
| docs/NEW_GAME.md | Disc to native port, translator first |
| docs/TRANSLATOR_STAGE.md | The translators, the backlog tool, how a translated PASS is recorded |
| docs/HAIKU_WORKFLOW.md | What an agent attempt receives, may run and costs |
| docs/PROOF_GATES.md, docs/CALL_COVERAGE.md | Coverage gate, mutation check, call-site coverage |
| docs/READABLE_STAGE.md | The readable stage: argument placement, lean sandbox, drafts, sync, library, headers |
| docs/TOOLS.md | Every operator tool, `decomp.py audit`, the regression suite |
| docs/DASHBOARD.md | `Dashboard.bat`: each game's road from disc to port, the work board, the function treemap; the two files agents keep current |
| docs/DEFINITION_OF_DONE.md | When a game counts as done |
| port/README.md | The port kit's modules and what a game turns on in its game.json |

## Layout (every file at most 400 lines: `decomp.py sizes`; files listed in tests/sizes_over_400.txt may only shrink)

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
| `core/proof.py`, `mutation.py`, `mutation_ops.py`, `tools/coverage_gate.py`, `tools/mutation_check.py`, `tools/proof_backlog.py` | Proof gates (docs/PROOF_GATES.md). Coverage gate (config `coverage_gate`): a matched function whose original's blocks the valid vectors reach too little of (default under 90 %, or more than one unreached from 3 blocks up) is `matched_low_coverage`: not a FAIL, not skipped, not verified (ledger row `proof`, the server's counts, the doctor's figure, the final audit's `proof` check, which FAILs while the gate enforces: the default, for a new game; an existing game is report-only until its backlog is clear; oracle_summary.json `blocks_unhit`, written by TcPrepare, names the unreached blocks). Mutation check (config `mutation_check`, report-only by default): seeded mutants of the matched C judged on the same harness; survivors outside unread TcArgs slots and stack stores flag `weak_harness` |
| `core/orch.py`, `job.py`, `brief.py` | Worker pool and queue; one function start to finish; agent brief |
| `core/translate_stage.py`, `tools/translate_backlog.py`, `targets/<name>/translate.py` | Translator first (docs/TRANSLATOR_STAGE.md): the target's own translator (target.json `translator`; the translator itself in `targets/<name>/translator/`, or the folder config.local.json `translators` names) writes each function's raw C before any agent; the unchanged judge decides it; a PASS is a match with ledger `source: translated` and no agent tokens, a FAIL seeds agent attempt 1. The backlog tool runs it over existing harnesses, never Ghidra |
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
| `agent_notes/<addr>.md` | A reviewer's note on a fault the judge cannot see (e.g. a host local read past its end, docs/PROOF_GATES.md section 6), appended to the raw and readable agents' TASK.md as "Reviewer's note". Data, not C: requeue the function to act on it |
| `reference/` | Hand-written controls: accepted C for the `references`, plus the deliberately broken control |
| `ghidra/`, `ghidra_slots/` | Ghidra project and read-only copies for parallel preparation |
| `packets/<addr>/` | What the agent reads: `asm.txt`, `ghidra.c` (split into `_1`, `_2`... past 400 lines) |
| `harness/<addr>/` | Vectors and expected results (never shown to the agent) |
| `work/`, `matched/`, `runs/`, `sandbox/`, `build/` | Latest C per function, passed C, per-attempt transcripts and checks, agent sandboxes, builds |
| `state/` | Catalog, ledger, logs, assembled Ghidra scripts (`state/ghidra_scripts`). Files more than one process writes (ledger.json, readable.json, systems.json, usage.json) are rewritten atomically and read-modify-written under a `<file>.lock` (core/filelock.py: msvcrt/fcntl, bounded wait, an error naming the lock); a leftover `.lock` file is harmless. A Windows open or replace refused because another process has the file open or is replacing it ([Errno 13]) is retried with a backoff for up to 5 s, then raises naming the file; a pass whose `readable` ledger write fails still ends `readable` (core/refine.record_pass), never `error` |
| `retired/<kind>/`, `retired/manifest.json` | Artefacts of addresses that stopped being functions, moved by `tools/retire.py` (never deleted), and why |
| `watcher/` | Watcher workspace, sessions, `history/` (every promoted change: before/after, diff, report) |

New project: create the folder with `project.json`, copy `targets/<target>/template/TcInputs.java` to
`oracle/TcInputs.java`, write the reference C, then `decomp.py --project <folder> setup`. Disc to port: docs/NEW_GAME.md.

## Run it

First, once per machine: `python -m pip install -r requirements.txt` (networkx, psutil and pycparser for the
pipeline, numpy, Pillow and zstandard for the port kit, pytest for the tests).

```
python decomp.py --project path/to/workspace serve   # UI at http://127.0.0.1:<port>
```

- **Start workers** (count box, default `workers` in DecompPipeline/config.json, cap `max_workers`): each worker takes one
  function at a time from a shared queue. Every function is judged alone (callees are mocked), so workers never
  wait on each other. Ghidra locks a project while it is open, so up to `ghidra_parallel` preparations run at
  once in slots: slot 0 is the master project (the only one that saves Matched/Failed bookmarks), slots 1..
  are read-only copies in `ghidra_slots/`, re-copied automatically when the catalog changes. With `prefetch`
  (config, loop on), a worker reserves its next function when its agent starts and prepares it meanwhile, one deep,
  so the prepare and its slot wait are off the worker's critical path; a stop gives that function back. With
  `harness_reuse` (config, default off; on in the gamecube_ppc target.json; a project.json overrides it), a function
  whose harness is current (core/job.reusable_harness: today's oracle stamp, no callee record drift) takes no slot
  and no Ghidra, prefetched or not; the decision is made once, where its prepare would start. A requeued function's
  old C is still judged first on that harness. Off, every function is prepared, as before; the stamp does not cover
  the Ghidra project's own state (types, labels, names later prepares apply), so reuse is on only where it is proven.
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
| A weak test that passes anything | The oracle reports block coverage and per-block hit counts; controls below prove the judge rejects wrong code. The coverage gate keeps a match whose original the vectors barely run out of the verified count, and the mutation check measures, per function, whether the harness rejects small wrong changes (docs/PROOF_GATES.md). |

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

## Changing the checker

Any change to the judge (`core/check.py`, `core/lint.py`, `targets/*/runtime/`, `targets/*/ghidra_scripts/`, the
input template) must never weaken it: it lands only with tests, 0 lost mutant kills (`tools/mutation_check.py`) and
other games' verdicts unchanged unless explained. Run the regression gate in docs/TOOLS.md ("the gate for every
checker change") before any restart. A script change moves the oracle stamp of every harness; re-prepare only the
functions it can alter (`tools/selective_restamp.py --change <name>`).
