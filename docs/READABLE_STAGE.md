# The readable stage

Moved from the README; the entry point is AGENTS.md. The layout table in AGENTS.md names each module.

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
arguments are certain; everything else stays raw, so the lints refuse it. The readable pass (core/rtx, one pass for
every console with a translator; targets/<target>/rtx.py names its runtime) lifts the translator's raw C to readable
C with no agent: structured control flow, folded expressions, typed locals and frame structs, fields and indexes from
the headers, the prototypes' types and the registry's names, mirror addresses (target.json address_mirrors) by
the global's name, and constants by the header name the other readable files write at the same callee argument or
member (core/rtx/usage: one name only, never from the function's own file); what it has no form for it refuses
(docs/proofs/READABLE_TX.md). `refine.py run` judges its machine file first, then the draft (config `readable_draft`;
`readable_machine` false turns the pass off): a PASS that also reads as named C (no register-named variables, raw
names, Ghidra names or gotos) and passes the adoption gate (core/draft_gate: no literal other than 0/1 whose value a
header #define or enum names; any length, unless `readable_draft_max_lines` sets a limit) is adopted
(`source: draft`, `readable_by: machine`) with no agent; an agent's later pass sets `readable_by: agent`, and an
agent working on a machine file is told to name it. With `--draft start` (the default; `adopt` stops there) a file
that is not adopted is the agent's work.c (the machine file when it passes the judge); `refine.py draft TARGETS [--adopt]` judges drafts alone; `refine.py draft --fmac [--list | --adopt]` takes the readable files that write the code's fmac as two roundings (they pass a judge whose vectors cannot tell, and drift the game) and replaces each with its machine file when that one passes and calls sh4_fmac (old file backed up). Lint R9/fmac (raw C) and RR8 (readable C, core/fused) fail such a file outright: `verify` marks it stale for that route and `run --redo`; matched raw C goes back through `tools/requeue.py` and `translate_backlog --record`. The harness-staleness check, the watcher gate and the mutation audit leave the rule out. Re-adoption
(core/readopt.py): `refine.py verify --adopt` tries a STALE file's earlier attempts newest first and adopts the first
strict PASS (old file backed up to state/readopt_backup, `source: readopt attempt-NN`), else lists the function in
state/redo.txt for `refine.py run @state/redo.txt --redo`; `refine.py adopt TARGETS` does the same for failed rows.
The header parser keeps a `#define` whose trailing comment continues on the next line in one item.

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
