# Haiku attempts: what they get, what they may do, what they cost

This is how the pipeline spends small-model (Haiku) sessions, for any game. Nothing here names a game: a new
project gets the same sandboxes, the same lockdown and the same knobs. The numbers in "What it costs" were
measured on the reference project; re-measure a new project with the usage report (last section).

There are two kinds of attempt:

- **raw** (`decomp.py`, the server's workers): write C that matches one function's machine code, against a raw
  API (`rd32`/`wr32` on guest addresses, calls through `tc_call`). Judged by `core/check.py`.
- **readable** (`refine.py run`): rewrite one matched raw function as normal C (typed pointers, structs, named
  callees and globals). Judged by `core/readable_check.py` on the same harness, with the readable runtime.

Both run the same way: one locked-down CLI session per attempt, in a fresh sandbox folder, then a machine verdict.
The agent never judges itself: whatever it says, only the check after the session counts.

## 1. What an attempt receives

The first (and only) user message is `Follow TASK.md (reproduced below) exactly.` plus the TASK text. It names
the working directory. Everything the agent needs is in that folder; every file except `work.c` is read-only, and
a changed read-only file fails the attempt ("tamper").

### Readable sandbox (`sandbox/rd_ADDR/`, built by `core/readable_sandbox.py build_sandbox`)

| file | what | typical size |
|---|---|---|
| TASK.md | the job, the rules R1.., the procedure; points to context.md first | 4.6 KB |
| context.md | parameter mapping, every declaration raw.c needs (callees, globals, constants), and the definitions of the types and constants they use (big structs projected, far ones only declared: `core/lean_select.py`) | TC median 10.6 KB, p90 56 KB (was 113 / 141 KB) |
| raw.c | the verified raw C: the specification | 0.05-10 KB |
| work.c | the agent's file: a stub, or the previous attempt's work | small |
| game.h | lean: only this function, its callees, functions used as data, the globals raw.c uses, and the functions and globals its current readable file (or starting work.c) uses | TC median 0.3 KB, p90 0.6 KB |
| types.h | a one-line stub since batch 25: the types are shown once, in context.md (before: the same types body again, TC median 112 KB) | 0.1 KB |
| index.txt | every other type, and the other constants whose value raw.c writes (name, value, header): the safety net | TC median 13.8 KB |
| rd_rt.h | base types (u8..s32, u64, f32) and the sh4_* helpers | 2.6 KB |
| check.py | a launcher: runs the official readable check, dev vectors only | 0.6 KB |

context.md repeats what game.h holds, so a normal attempt reads TASK (already in the prompt),
context.md and raw.c, edits work.c and runs the check. The official check builds against the project's full
headers, so a name found in index.txt compiles. Selection rules (all generic): code references, transitively;
constant groups a field comment names (`/* MODE_* */`, `/* 0x12c: EventType */`); groups whose comment names an
included type or a declared function when raw.c writes one of their values; accessor macros whose offset raw.c
adds; uncommented groups right under an included type or under a section note naming a declared name; any
distinctive constant (0x400 or more, at most two definitions) whose value raw.c writes; since batch 24 a constant
whose value (0x10 or more, enum values counted and expressions evaluated: core/lean_uses.py) raw.c writes and no
other constant has; and every type, constant, macro, function and global the function's current readable file (and
the work.c an attempt starts from) uses, so a redo keeps its names and `refine.py verify --lean` proves each file
builds from its sandbox.

Since batch 25 the selection shows views of the items (core/lean_select.py, all generic):
- a struct with more than 8 members is projected (core/lean_layout.py): only the members whose bytes hold an offset
  raw.c writes (offset 0 always; an address inside a declared global counts as an offset into it; an embedded
  struct gets the offsets that fall inside it) and the members the current readable file names; the rest becomes
  `u8 _pad_0xOFF[0xLEN];`, so size, alignment and every shown offset stay exact. core/lean_proof.py proves that
  with static asserts against the full definition (verify --lean and the preflight; a failure is a build failure);
- a struct more than 2 pointer hops from the declarations is only declared (`typedef struct X X; /* 0x.. bytes;
  members not shown */`) unless the readable file names one of its members;
- the comment rounds read only the shown text, and run twice (was 4); a group whose comment names a type counts only
  for types at most 1 hop away. Batch 26 (core/lean_hidden.py): a dropped member's comment, or the declarations'
  name words (`WeaponDef *def, s32 level` -> WEAPON_LEVEL_MIN/MAX), still bring a constant group back when it has at
  most `readable_lean_comment_group_max` (2) such names and a value raw.c writes or the name family matches (a
  #define group shown in part); TC: 21 of the 139 hidden names back, context.md median +110 bytes;
- an accessor macro group shows only the macros raw.c's offsets reach, plus the macros built on them.

Since batch 20 context.md also says (all generic, from raw.c's text and the registry): which image addresses raw.c
uses as pointers (passed, stored, compared) and the name each one has, so an attempt passes `Render_BeginFrame` or
`g_text` rather than the bytes at that address (lint RR5 refuses those bytes written as a number); and, at a call to
a variadic callee, which `args` words its parameters are (SHC puts the format and the variable arguments on the
stack, so raw.c's other `args.r` words there are leftovers). A failed check whose callee fills one of the attempt's
locals through a pointer adds a NOTE: the judge puts the callee's output (the original's bytes) in that local at the
call, so the attempt only has to pass the right local and write what raw.c writes into it before the call. There is
no accessor for PR: a PR value left in a callee's argument register is not compared when the callee does not read it.

Since batch 22 the sandbox's raw.c marks every guest stack address it writes as a number, at the end of that line
(`/* 0x8cffe000 = stack argument 0 = `y1`, not a global */`, `/* 0x8cffdfac = own frame, entry r15 - 0x54 (a
local), not a global */`), and context.md lists them under "Stack addresses in raw.c (never globals)": a stack
argument is the parameter there, an own-frame address a local of the attempt's. Lint RR6 refuses a definition that
leaves out a parameter raw.c reads from its stack slot. Lint RR7 (core/readable_lint.void_result_lint) refuses a
function declared void cast to a value-returning type with its result used: a void callee leaves the caller's own
value in r0 (raw.c's `jsr @r0` reusing the callee's address), while the mocked callee returns a random r0 unless
the function's oracle hint pins it (`genret N 0xADDR`, as for TC's Mat3_Mul); TASK R4 says to call that function
by name, and the harness needs that hint first. A parameter raw.c does not use that the asm passes on unwritten to
a callee taking it (core/arg_passthrough: an opaque thunk hid it from the harness, which then compares nothing
there) gets a context.md NOTE to pass it on. Storing the address of a local (`header->texture = &tex`)
is judged like passing it: it must be the same local the original's address stands for. A callee buffer typed by its
element (`u32 *toc`, `s32 *out_cell`) receives as many bytes as the original reads back after the call, so the
attempt declares the whole object (`u32 toc[102]`, `s32 cell[3]`); a designer can state the size in the names entry
(`"out": {"toc": 408}` or `{"buf": "size"}`).

### Raw sandbox (`sandbox/ADDR/`, built by `core/agent.py build_sandbox`)

TASK.md, context.md (signature, calling convention, the oracle's summary), the packet files Ghidra wrote for the
function (disassembly, decompiler output, callee table), the target runtime header, work.c, check.py.

## 2. What it may run

Command line (`core/agent_cli.py`):

- tools `Read, Edit, Write, Bash`; allowed `Read`, `Edit(./work.c)`, `Write(./work.c)`, `Bash(python check.py)`;
  permission mode `dontAsk`: anything else is refused, never asked about;
- `--safe-mode --restricted --strict-mcp-config --disable-slash-commands --no-session-persistence`: no MCP servers,
  hooks, plugins, skills, slash commands or settings files;
- environment `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`: no CLAUDE.md and no memory
  from the user profile or any parent folder;
- `--system-prompt` (config `agent_system_prompt: "lean"`, the default): a ~150-token constant prompt replaces
  the CLI's interactive-coding prompt. It is the same for every session and project, so it stays a cached prefix.
  `"cli"` keeps the CLI's own prompt.

The only command is `python check.py`. The CLI also lets through read-only commands and `cd` into the working
directory, so `cd <sandbox> && python check.py` (any quoting or slash style) runs as the check. Every other
command is refused with the CLI's message "Permission to use Bash has been denied"; the system prompt tells the
agent that a refusal means "run exactly `python check.py`". No allow rule is added for the cd form: a pattern like
`Bash(cd * && python check.py)` would allow `cd` to any folder first.

check.py prints `DEV PASS` or `DEV FAIL` with the first differences and always exits 0. Only dev vectors are
shown; the verdict after the session also uses the hidden vectors.

## 3. Before an agent is spent

Before any session, `refine.py run` takes a snapshot of the headers (`core/header_snapshot.py`). It copies
`readable/include` and `readable/names` into `state/readable_snapshots/run-...`, writes the snapshot's own game.h,
and validates it with the header lint (one owner per name, `core/header_lint.py`) and a game.h build at /W4 /WX.
After that, every sandbox, preflight and check in the run reads only the snapshot. That includes the agents'
check.py, which passes `--headers DIR`. Designers' edits during the run therefore change nothing it compiles
against.

When the live headers do not validate, the run prints why. It then checks again every `readable_header_retry_s`
(60) for up to `readable_header_wait_s` (900). After that it uses the last good snapshot
(`state/readable_snapshots/last_good.json`). If there is none, it exits with code 2 and starts no agent. Each
attempt and each passing row records the snapshot's 16-digit `headers` hash.

Then, per function, `refine.py run` stops before starting a session when the problem is not the agent's to solve:

| check | status | fix |
|---|---|---|
| raw C not matched / no harness | `raw_not_matched` | match it first |
| the matched raw C fails its own harness | `harness_stale` | `decomp.py prepare ADDR` (servers stopped), run again |
| a callee record that differs from the callee's own harness signature (Ghidra's stale view of the callee; core/callee_drift) | `blocked` | servers stopped: `decomp.py prepare ADDR` (since batch 18 a prepare takes the records from the callees' harnesses), or `tools/reprepare_stale.py` for every such harness, repeating until none is left (tools/doctor.py lists them) |
| registry refusal (unknown type, bad prototype) | `blocked` | fix the names/registry |
| a do-nothing stub does not lint or build against the lean sandbox | `blocked` | fix the header |
| another process holds the function (a second run, a verify, a sync-names, a manual check; core/fn_claim) | none: `busy` in the run log, the row untouched | nothing: the holder finishes it |
| an OS refusal of a file the tool itself wrote ([Errno]/[WinError], LNK1181 on build/ADDR/..., core/infra) | retried once, then `infra_error` (never `blocked`, `failed` or `error`) | run it again; `refine.py status` lists them |

One worker per function (batch 21): `run` lists each function once (an address listed twice, a CRLF list merged
with an LF one, a system and one of its addresses: "N duplicate(s) dropped"), and holds the function's claim
(state/fnlocks/ADDR.lock) for the whole cycle. Two `run` processes with overlapping lists are safe: the second waits
up to `function_lock_wait_s` (15) and then skips the function as busy, or finds it finished during its own run and
skips it. A claim whose process died is cleared by `tools/doctor.py`.

Paths still not caught before a session: a lean sandbox missing a name the agent wants (index.txt covers it, but
the agent must look); a harness whose dev vectors cannot tell two behaviours apart (the hidden set decides); an
API failure mid-session (`api_error`: the function goes back to `queued`, no verdict); the account's usage window
running out (sessions end with an API error).

No agent is spent on renames either: `refine.py verify` first runs `sync-names`, which rewrites a readable file for
names and prototypes the registry gained since it passed (no tokens; the file is written only when the judge passes
it, as a `kind: "sync"` attempt). Only a file it cannot rewrite and that then fails is STALE and worth a redo.

## 4. What it costs (reference project, Haiku, effort high)

Per attempt, from the usage report (`usage_report.py readable` / `attempts`):

| | readable PASS | readable not PASS | raw PASS | raw not PASS |
|---|---|---|---|---|
| attempts | 472 | 167 | 2250 | 186 |
| first call (system prompt + tools + TASK) | 7.3k | 7.4k | 7.8k | 7.8k |
| cache writes, median | 22k | 38k | (mean 24k, all) | |
| cache reads, median | 101k | 176k | (mean 91k, all) | |
| output, median (p90) | 4.1k (16.7k) | 22.0k (53.0k) | 6.0k (33.9k) | 21.4k (79.7k) |
| thinking, median | 2.7k | 19.6k | 89% of output | |
| turns, median (p90) | 10 (19) | 13 (19) | | |

Readable: 444 functions, 426 passed, 1.40 attempts per passed function. A failing attempt costs about 5x the
output of a passing one; most of it is thinking.

Those first-call numbers are from before the lean start-up (CLI system prompt). The lean system prompt removes the
CLI's prompt, and the lean sandbox shrinks what an attempt reads:

| read set (TASK + context.md + raw.c, plus game.h + types.h before) | median | mean |
|---|---|---|
| before (whole headers, 53 KB game.h) | 60.3 KB, ~17.2k tokens | 61.0 KB, ~17.4k tokens |
| after (lean) | 8.8 KB, ~2.5k tokens | 15.9 KB, ~4.5k tokens |

types.h is bimodal: 0.2-3 KB for most functions, 17-33 KB for functions on the project's big structures.

## 5. The knobs

| knob | where | default | effect |
|---|---|---|---|
| effort (raw) | config `effort` | `medium` | `--effort` of raw attempts; null = the CLI's adaptive default. medium: attempt-1 pass unchanged against high (120/120 in both; r15 A/B on 60 + 60 Toy Commander functions), -28% $ and -25% agent time per verified function |
| effort (readable) | config `readable_effort`, `refine.py run --effort` | null: falls back to `effort` | readable only; `--effort` wins for that run (A/B) |
| effort (large readable) | config `readable_large_bytes`, `readable_effort_large` | 4096, `medium` | a raw.c over that size (0/null: off) uses this effort, and TASK.md gets "A long function" (work top-down, write as you go, check early); `--effort` still wins |
| prompt cache lifetime | config `cache_ttl`, `readable_cache_ttl` | `5m`, null | CLAUDE_CODE_PROMPT_CACHE_TTL of raw attempts / readable sessions; null = the CLI's choice (1 h on a subscription). 5m: -20% per verified raw function ($0.0139 -> $0.0112) at the same attempt-1 pass rate (60/60; r15 A/B) |
| shared prompt prefix (raw) | config `shared_prefix` | true | the generic task text (goal, files, rules R1-R12, SH-4 reminders, procedure) and tc_rt.h go into the system prompt (`--system-prompt-file`, beside the transcript), the same for every session, so they are a cached prefix; TASK.md keeps the per-function part. Lean system prompt and targets without a build_sandbox hook only. -13% base-rate cost per function at the same verdicts and attempt-1 pass rate (60/60; r15 A/B) |
| attempts (raw) | config `max_attempts`, `stall_limit` | 8, 2 | sessions per function; stop after 2 without progress |
| attempts (readable) | `refine.py run --attempts` | 3 | sessions per function; each sees the last work.c and its result |
| workers (raw) | config `workers` / `max_workers` | 4 / 10 | parallel sessions in the server |
| workers (readable) | `refine.py run --workers` | 2 | parallel functions |
| output cap (readable) | config `readable_output_cap` | 40000 | stop a session past this many output tokens (thinking included) |
| output cap (raw) | config `output_cap` | none | same, for raw attempts |
| time cap | config `attempt_timeout_s` | 900 | session killed (process tree) after this |
| budget cap | config `attempt_budget_usd` | 1.0 | the CLI's `--max-budget-usd` (notional on a subscription) |
| killed sessions' spend | config `agent_rates` | haiku rates | a session that ends with no result event (timeout, output cap, stop) has no total_cost_usd; its cost_usd is then estimated from the requests it made (usage plus thinking deltas, x5 over 100k), marked `cost_estimated`, instead of $0. Reads 0.77-0.98 of the CLI total on finished sessions. A model with no rates keeps None |
| system prompt | config `agent_system_prompt` | `lean` | `cli` restores the CLI's prompt |
| machine readable file (readable) | config `readable_draft`, `refine.py run --draft`; `readable_machine` | `start`; `true` | `off`; `adopt`: the readable pass's machine file (core/rtx), else the draft, that passes the judge, reads as named C and passes the adoption gate (no literal a header names; any length unless `readable_draft_max_lines` is set) is adopted (`readable_by: machine`), no agent; `start`: a file not adopted is the agent's starting work.c; `readable_machine: false`: drafts only |
| header wait (readable) | config `readable_header_wait_s`, `readable_header_retry_s` | 900, 60 | how long `run` waits for live headers that do not validate before it uses the last good snapshot |

There is no turn cap: turns of passing and failing attempts have the same p90 (19), so a turn cap cannot tell them
apart. Output can: the readable cap of 40000 is below the failing p90 (53k) and cuts only 7 of 472 passing
attempts (1.5%; the passing p90 is 16.7k). On the reference data it would have saved 601k of 7.67M output tokens
(7.8%) against 537k spent by those 7 passing attempts (which then get another attempt). Caps measured:

| cap | output saved on failing attempts | passing attempts cut |
|---|---|---|
| 30000 | 12.7% | 14 |
| 40000 | 7.8% | 7 |
| 53000 | 4.0% | 4 |
| 60000 | 2.8% | 2 |

A capped session is recorded with `killed: "output_cap N"` and judged like any other (its work.c may pass).

## 6. Measuring

Readable attempts, per attempt and summarised (read-only; run any time):

    python tools/usage_report.py readable --project DIR [--since 6h|2d|2026-10-13T20:00] [--summary]

Per attempt: cache writes, cache reads, output, thinking, first-call context, turns, files read, refused commands,
the effort, and the verdict from state/readable.json. Summary: medians and means for PASS / not PASS / all, PASS
rate by effort, attempts per passed function, the most read files, and the refused commands. A session killed
before its result event (time or output cap) takes the ledger's numbers.

Raw attempts: `python tools/usage_report.py attempts --project DIR`; the ledger: `... ledger --project DIR`.

### An effort A/B

Pick about 30 functions per arm that are not readable yet: sort candidates by raw.c size and deal them
alternately into two lists (same size mix, no overlap), then:

    python tools/bounded.py 14400 python refine.py --project DIR run @arm_a.txt --effort high
    python tools/bounded.py 14400 python refine.py --project DIR run @arm_b.txt --effort medium
    python tools/usage_report.py readable --project DIR --since <start time> --summary

`--summary` splits PASS rate by effort; compare attempts per passed function and output per attempt, not output
per PASS alone (a cheaper effort that needs more attempts can cost more). `@FILE` takes addresses and system ids separated by
white space or commas (CRLF or LF, a BOM, `#` comments); each function runs once. With `--redo` on readable functions, a redo that does not pass keeps the old file and its verdict
(readable or stale) and records the attempts in `redo_failed`; the usage report counts each attempt's own verdict,
so such a redo still counts as not passed.

### Before any agent (batch 23)

Three ways to a readable file without tokens: `refine.py lookalike --from OTHER_WS --adopt` (identical functions of
an earlier game), the machine draft `refine.py run` judges first (`--draft`, above; `refine.py draft TARGETS`
measures them alone), and `refine.py verify --adopt` / `refine.py adopt` (an earlier attempt that passes the current
judge). Each is judged by the full readable judge, every lint included; nothing else is adopted.

## 7. For a new game

Nothing to change here. The project supplies its headers (readable/include), names and registry; the lean
selection, the lockdown, the preflight and the report work from those. Check with
`refine.py verify --lean` that the project's passing readable files still build from their lean sandboxes (a
report only: the official check always uses the full headers).
