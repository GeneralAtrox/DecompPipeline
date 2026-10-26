# Readable C by machine (readable-tx, 2026-10-23)

The readable pass (`core/rtx`, with `targets/<target>/rtx.py` naming each console's runtime) turns the translator's
raw C into readable C with no agent. It writes structured control flow, folded expressions, typed locals and frame
structs, fields and indexes from the headers, the prototypes' types and the registry's names. Anything it has no form
for, it refuses. In the pipeline (`core/draft_run.before_agent`) its machine file is judged first:

- **It passes** the readable judge, the readability gate and the literal check: it is adopted at any length, with
  zero agent tokens. The ledger row gets `readable_by: machine`. The user's decision: an agent reviews it the next
  time it works on the function.
- **It passes the judge but not a gate:** it is the agent's starting work.c, and the agent is told to name it.
- **It fails, or there is none:** the machine draft runs as before.

**The judge and the gates are unchanged.** `core/readable_check`, `draft_run.readable_gaps` and `draft_gate.reasons`
are the same code. The one gate change is the user's decision on length: `MAX_LINES` is now None (it was 6), and
config `readable_draft_max_lines` sets a limit again. Every adopted file below was re-checked with the unchanged
gates (`readable_tx/regate.py`): 876/876, 418/418 and 202/202 pass.

## How it was measured

`tools/readable_tx.py` translates each matched function, lifts it, and runs the real readable judge and the gates in
a mirror workspace. The game workspaces were only read. A final lift-only run of every function used the final
code. Judge verdicts were kept only for files whose text is byte-identical to that final lift (`readable_tx/delta.py`),
and every changed file was judged again. Agent costs come from each game's ledger
(`state/readable.json` attempts, `tokens_in + tokens_out` and `cost_usd`, without machine, draft, readopt and
lookalike attempts). Per-function stages: `readable_tx/stages.json`. Totals: `readable_tx/tally.json`.

## Results per game

| Game | Functions | Adopted, zero agent tokens | Passes the judge, fails only the literal check | Lifts, names missing | Lift refuses | Judge fails |
|------|-----------|----------------------------|------------------------------------------------|----------------------|--------------|-------------|
| Toy Commander | 2,698 | **876 (32.5%)** | 864 | 65 | 853 | 40 |
| Power Stone 2 | 6,516 | **418 (6.4%)** | 966 | 2,465 | 2,605 | 62 |
| Spawn | 1,875 | **202 (10.8%)** | 228 | 459 | 978 | 8 |

The share left for agents is 67.5% of Toy Commander, 93.6% of Power Stone 2 and 89.2% of Spawn.

### Agent tokens and dollars

| Game | Agent spend on the whole game so far | On the adopted functions (agents already did them) | Adopted functions no agent has done yet |
|------|--------------------------------------|----------------------------------------------------|----------------------------------------|
| Toy Commander | $156.08, 914.9M tokens (2,674 functions) | 868 functions: **$8.48, 116.1M tokens** | 8: about $0.08, 1.1M tokens |
| Power Stone 2 | $34.57, 328.1M tokens (2,357 functions) | 336 functions: **$1.49, 19.9M tokens** | 82: about $0.36, 4.9M tokens |
| Spawn | $4.06, 40.0M tokens (421 functions) | 85 functions: **$0.29, 4.1M tokens** | 117: about $0.40, 5.6M tokens |

- **The saving per game** is the money agents spent on the functions the pass now adopts. On a game that has not
  been done yet, that is what the pass saves when it runs first. Together: **$10.26 and 140.0M tokens** on three
  games.
- **The estimates for functions not yet done** use the mean cost of the adopted functions agents did do. That is
  $0.0098 per function on Toy Commander, $0.0044 on Power Stone 2 and $0.0034 on Spawn. The game-wide means are
  higher ($0.058, $0.015 and $0.010), because the adopted functions are the easy ones.
- **Adoption is small in dollars.** It is 32.5% of Toy Commander's functions but 5.4% of its agent dollars. The pass
  takes the short, regular functions. The long ones are where agents spend, and they still need agents.
- **The start value is not measured.** The 2,058 functions that pass the judge and fail only the literal check reach
  the agent already passing; the agent only has to name constants. That should cost less than a function from a
  failing draft, but this run does not measure it.

## What changed this round (all through the judge, no hand-edited C)

- **Mirror addresses.** Spawn and Power Stone 2 raw C names RAM by its P0 mirror (0x0cfe0000 for g_workHead at
  0x8cfe0000). The readable lint (RR3) refuses the number. The pass now writes `(u32)&g_workHead & 0x1fffffffu`, the
  same bits. The agents write the same thing through each game's `RAM_P0_PTR` macro. Each target's
  `address_mirrors` (target.json) is its spec's `mirrors`, and a test checks that they agree. Before this fix, 51 of
  Spawn's 52 judge failures and 370 of Power Stone 2's 384 were this lint. The fix adopted 8 more Spawn and 50 more
  Power Stone 2 functions on the first rerun. Writes through a mirror pointer still fail the compare: the readable
  runtime has no mirror. The judge refuses those, so they stay agent work.
- **Constants by usage** (`core/rtx/usage.py`). A literal at a callee argument or a member takes a header name only
  when the game's other readable files write a name of that value at the same place. An example is
  `Xmtrx_SelectStack(2)`, where the other files pass `XMTRX_STACK_SCENE` and 2 is `XMTRX_STACK_CAMERA`.
  - It writes a name only when exactly one fits. A member must have one family of names in use there, because many
    structs share member names.
  - The function's own file is never evidence. Its readable file is the agent's answer, and in the pipeline it does
    not exist yet.
  - A callee parameter typed as an enum takes its name from that enum.
  - `x & 0xff` / `x & 0xffff` become `(u32)(u8)x` / `(u32)(u16)x`, the same bits.
  - This adopted 14 more Spawn functions on the first rerun, with no regressions.
- **The other new forms:**
  - addresses inside array globals (`(u32)g_arr + off`);
  - constant offsets into scalar pointers (`uv[1]`). Never through `void *`, and only at the pointee's own type, so
    no cast ends up on the left of an assignment;
  - the vector library's fr12–fr15 state, kept around calls;
  - 2-D and one-element array members;
  - void calls kept;
  - float stack arguments as bits.

  Each was a judge failure, now fixed.
- **Tried and dropped.** Refusing a call whose prototype argument the raw C never sets lost 47 adoptions (33 Toy
  Commander, 14 Power Stone 2) and gained nothing. The raw C passes 0 there and the lift keeps that. The judge already
  refuses the bad cases. See the first follow-up.

## Why the literal check stops 2,058 judge-passing files

The literal check refuses any integer other than 0 or 1 whose value some header names. In the gate-only files, the
flagged literals sit here (`readable_tx/litctx.py`, occurrences):

| Game | Offsets (`p + 8`) | Shifts | Bit ops | Indexes | Local compares/sets | Callee arguments | Member compare/set |
|------|-------------------|--------|---------|---------|---------------------|------------------|--------------------|
| Toy Commander | 2,894 | 443 | 507 | 476 | 1,078 | 212 | 26 |
| Power Stone 2 | 1,803 | 754 | 467 | 294 | 1,116 | 1,385 | 328 |
| Spawn | 549 | 275 | 168 | 65 | 274 | 94 | 25 |

- **Most of these are machine arithmetic, not constants.** They are struct offsets the registry does not name, shift
  amounts, masks and loop bounds. Writing a header name that happens to share the value would pass the gate and make
  the C wrong. An example: `ADXT_RMODE_REPLAY` for a matrix-stack index. The pass names a literal only where the
  game's own files show which name belongs at that place.
- **705 gate-only files** (339 Toy Commander, 276 Power Stone 2, 90 Spawn) fail only for literals in offsets, shifts,
  bit ops, indexes and multiplies. **1,430** (711, 539, 180) fail only for those plus local compares and sets.
- **The literal check is stricter than the accepted agent standard.** Run unchanged on the agents' own accepted
  readable files (`readable_tx/agentgate.py`), it passes 58.1% of Toy Commander's (1,559/2,683), 42.7% of Power Stone
  2's (1,011/2,369) and 70.4% of Spawn's (328/466).
- **This is the user's call; nothing was changed.** The largest single rise in adoption would come from a gate
  decision, for example not counting literals in arithmetic positions.

## What is left for agents, by cause

**The lift refuses** (`readable_tx/causes.py`):

| Cause | Toy Commander | Power Stone 2 | Spawn |
|-------|---------------|---------------|-------|
| A computed call (a function pointer) | 395 | 887 | 262 |
| The registry has no global, name or prototype there | 109 | 971 | 401 |
| A stack-frame form (a local passed by pointer, typed frame words, mixed widths, array locals, sp in a loop) | 153 | 395 | 160 |
| A callee-saved register or result the prototype does not match | 32 | 185 | 10 |
| CPU state with no C form (FPU banks and modes, SR, store queues) | 106 | 48 | 119 |
| A computed jump (a switch over code addresses) | 11 | 82 | 12 |
| A variadic callee | 38 | 26 | 11 |
| Other | 9 | 11 | 3 |

**Lifts, but names are missing (the readability gate):**

- Toy Commander 65, Power Stone 2 2,465, Spawn 459.
- Every one calls a callee the registry has not named (`fn_8c…`). Most also keep `param_N` names from their own
  prototype.
- This is naming, the agents' job by design. These files become gap-free as their callees get names, so naming
  callees first (bottom-up) turns more of them into adoptions.

**Judge failures:**

- Toy Commander 40 (32 compare, 8 build). The compare failures are mostly the known reference split: the readable
  runtime's `sh4_fmac` is fused (rounded once), while Toy Commander's matched reference rounds twice (the user's fmac
  decision lands pre-wave). Some are `fsca`, where the agent's own `Math_Sin` also fails in the mirror workspace.
- Power Stone 2 62 (39 compare, 23 build, mostly C4700 on a Vec3 local, as in the first follow-up).
- Spawn 8 (all compare).

### The fixes that would raise adoption most

1. **The literal check** (the user's decision; the gate stays as it is until then). 2,058 files already pass the judge
   and read as named C. 705 of them fail only on arithmetic literals.
2. **Computed calls typed from function-pointer members.** That is 1,544 refusals (395 + 887 + 262). A call through
   a member whose header type is a function pointer has a known prototype, so its arguments can be placed by the ABI.
   After that, the registry gaps: 1,481 refusals for a missing global, name or prototype. Each global named in the
   registry, by an agent or a registry step, unblocks every function that touches it.

## Other consoles

- **Saturn (Grandia, ws-field, 48 SH-2 translations):** 34 lift. 5 are gap-free; 27 have only `param_N` names. The
  14 refusals are mostly "no global there". The research workspace has no readable registry, so the judge cannot pass
  there yet.
- **GameCube (Skies of Arcadia, decomp-gekko, 83 PPC translations):** 18 lift. The refusals are callees with no
  prototype placement (31), no global (14), unknown types without a registry `types.h` (7), stack addresses, computed
  calls, and stfs of a double not proved single. Same registry limit for the judge.
- **Too Human:** no translator and no matched functions, so not applicable.

## Follow-ups (not done here)

- **A callee signature that drops an argument.** Power Stone 2's `Shc_QuickMoveWordsR3` (8c187958) has the meta
  signature r0, r2, r3. Its prototype has `size, dst (r1), src, unusedR3`. So the translator never sets r1, and the
  raw C copies to address 0. It is called from 701 functions. The raw judge passes them; whether r1 really carries
  `dst` there needs checking in the translator's signature data.
- **The fmac reference split** on Toy Commander (pre-wave, the user's decision).

## Tests

- `tests/test_readable_tx.py`: 23 tests, `python -W error`, also run from `tests/test_tooling.py`.
- `tests/test_tooling.py`: 827 tests, OK (4 skipped).
- `test_batch23` and `test_batch24`: OK.
