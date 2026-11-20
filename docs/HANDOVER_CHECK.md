# The register hand-over check

`core/handover.py` (console-free) and `targets/xbox360_ppc/port/tools/handover.py` (the Xbox 360 exporter and command
line). Tests: `tests/test_handover.py`. The first run, on Too Human, is at the end.

## The fault class

A port-form function is its own C function and its registers are C locals. A register's value crosses from one translated
function to another only through a channel the translator builds. A **hand-over fault** is a register that the original
code carries across a call, tail call or return, and that no channel carries in the emitted C: the reader gets 0, a stale
value from an earlier call, or its own old value. The judge cannot see it: it runs one function alone, supplies that
function's inputs and mocks its callees.

The channels of the Xbox 360 port form (`xenontranslate/translate.py`, `fn_parts.py`, `port/runtime/xb_call.c`):

| Register | Caller to callee | Callee back to caller |
|---|---|---|
| r3..r10, f1..f13 | `TcArgs.r` / `.f`, every one, at every call | r3 or f1 as the C result, the other through `tc_lastr()` / `tc_lastf()`; neither when the callee is taken as void |
| r1, r12 | `TcArgs.sp`, `TcArgs.r12` (low 32 bits) | none needed (r1 is restored by the callee's own code) |
| r2, r13 | constants of the image and the thread | |
| r14..r31 | only to a funclet (`TcArgs.nv`); otherwise as r0 below | none: the caller keeps its locals |
| r0, r11, f0, f14..f31, and r14..r31 to a non-funclet | the thread's `tc_gr` / `tc_fr`: the caller stores, the callee loads, **only when** the callee's "read before set" line was in gen/ when the caller was translated, and only at a constant target | none: zeroed after the call, or kept when `keeps.py` proves the callee leaves it alone |
| v0..v127 | the thread's `tc_vr`: stored before a call and a return, loaded at entry and after a call | the same |
| CR fields, XER carry / overflow, CTR, FPSCR, VSCR | none | none (CTR is zeroed after a call) |
| LR | none needed (a constant at entry) | |

## Proven cases (Too Human fault ledger, 2026-11-11)

| Fix | The caller wrote | The callee read | The C lost |
|---|---|---|---|
| 842bc5b1 | 8296fea8's callers: eye, direction, up in v1..v3 | v1..v3 before setting them | no vector channel: zeros in, NaN matrices out; and v1 never came back |
| ef0c50f6 | 8316d0c0: the decoder's constants in v64..v77 | 83149a38 and its siblings: v69, v72 | the channel carried v1..v13 only |
| a970b91c | the code before 82cbd238 (same routine, a catalogue function that is a label of its caller): r11 | 82cbd238: r11 | no channel for r0 / r11 / f0 / non-volatile registers |
| f29cc3eb, 00023f62 (return side) | 8216bff8: r4 before calling 8216bdf8; 82c66b88: r7 before 82c66960 | (the caller reads it back after the call) | the C zeroed a volatile register the callee leaves alone |
| c2848d01, a5b9d3c6 (return side) | | the caller reads f1 after an r3 call, or a copy of r3 across a float call | the other result register |
| 5724669d | 82eb5490 (a leaf cut at its inline table) | the case 82eb54d0 translated alone | a body cut wrongly made a hand-over where the code has none. Not this check's: it trusts the bodies |

Record forms (a5b9d3c6, CR0 from the low 32 bits) and CTR's width (6cb70a5e) are faults inside one function. They touch
this class only where a condition field set in one function is tested in another: the "no channel" row.

Sub-classes with no proven case, which the check still looks for: a condition field, carry or CTR handed to a callee
(`fwd-nochannel`); a volatile register or condition field a callee sets for its caller beyond r3 / f1 (`ret-set`,
`ret-status`); a callee reached only through a pointer that reads r0 / r11 / a non-volatile register (`ptr-target`,
`entry-uncalled`); a callee taken as void whose r3 the caller reads (`ret-kind`). Not looked for: a callee that changes a
non-volatile register and does not restore it; the compiler's save and restore stubs called as functions rather than
inlined (the lifter inlines the catalogue's `helper_ranges`; a stub outside them is an ordinary function to the check);
a 64-bit r12; argument registers (all 21 are always passed, so the check takes them as carried without reading the C).

## How it works

1. **Export** (per console). Each function is lifted once by the translator's own front end
   (`FunctionTranslator.build()`), before any pass, and written as blocks of reads and writes. Registers the caller's
   `TcArgs` brings are left undefined at entry so they show as needs. A return reads r3, f1 and the vector registers the
   function writes. The same worker reads the function's emitted `gen/fn_ADDR.c`: which registers it stores into the
   file before each call, which it loads.
2. **Solve** (`core.handover.Image`). `writes(G)`: the volatile and status registers G or anything it calls assigns;
   everything for a pointer call or code not analysed. `need(F)`: the live-in of F's entry by faint liveness (an
   assignment nothing reads is no read; a save of an entry value into the frame is no read, as `fn_parts.uncarried`),
   where a call to G reads `need(G)` and ends the life of `writes(G)`. Both to a fixpoint over the image, so a register
   passed through several functions is a need of each. `own_need(F)`: the same with callees reading nothing.
3. **Check** every edge against the C (`core.handover.check`; the kinds are listed in its docstring). A forward
   finding is reported where the register is made (the caller's own code writes it and its C does not store it) and
   where it is read (a function's own code reads it first and its C does not load it), not on the functions it only
   passes through: the files are the thread's. `readers()` names the reading function under an edge.

Two things are deliberately not read. `mfcr` copies the whole condition register into an integer; which field is then
picked out is not followed, so it is no read of any field (on Too Human it is always the field a vector compare just
set). The fallback of a jump table (an index past the table) is a pointer jump the code never takes; it is no write of
the function, as in `keeps.own_writes`.

It needs no build and no run. `export` lifts every function (minutes, in worker processes); `check` takes seconds and
can be repeated with `--overlay` (planted C) and `--path` (the findings on and under given functions).

## What a finding is not

A finding says the C and the code disagree about a register on an edge, by static liveness. It does not say the edge
runs, or that the value matters. Liveness over-reads: a register read on any path counts. `ret-ptr` and `ret-unknown`
have no callee to look at and are leads only. A function the lifter refuses is "code not analysed": its callers' reads
after it are `ret-unknown` and its own needs are not known.

## Another console

A translator exports the same records from its own blocks and supplies an `Abi` (which registers are arguments, which
have a file, which nothing carries) and an `Evidence` (what its emitted C does). The GameCube translator
(`targets/gamecube_ppc/translator/ppctranslate`) has the same block form; it has no register file yet, so every need
outside its argument registers would be `fwd-nochannel` until "live argument registers" is ported.

## First run: Too Human, gen/ of live kit a506e71f (2026-11-11)

94,306 functions, 255,101 call edges (221,627 constant, 28,747 pointer, 4,727 into code not analysed). Export 100 s to
15 min on build box A by load, check 2 min. Each proven fault planted back on the real C was reported on exactly its edge
(5 of 5: a970b91c's load, ef0c50f6, 842bc5b1 both sides, 00023f62's keep). a970b91c's store side could only be planted
on the made-up image: no constant edge of Too Human both writes and stores a file register its callee needs.

| Kind | Edges | Callees | Mostly |
|---|---|---|---|
| fwd-unstored | 680 | 133 | r14..r31; 261 lead to 82ec5460 (setjmp: it saves them), 253 to 82b64a00 |
| fwd-unloaded | 1 | 1 | 82dea9ac r11 |
| vec-unstored / vec-unloaded | 2 / 2 | 1 / 2 | 82765488, 82cb0260 v0 |
| fwd-nochannel | 2 | 2 | 82cb0260, 82cb2d60: CR0 into a function that starts inside its caller's loop |
| ret-set | 632 | 15 | 82a4fcc0, 82b2b250, 82ed35f0: calls that do not return |
| ret-zeroed | 0 | | |
| ret-kind | 4 | 2 | f1 |
| ret-status | 1 | 1 | |
| ret-ptr / ret-unknown | 2 / 21 | | |
| entry-uncalled | 1,670 | 586 | r14..r31 and f14..f31 down to setjmp; 98 functions that read r11 and 7 that read f0 first |
| ptr-target | 2,729 | 234 | the same |

A sample of 20 (seed 1111, spread over the kinds), read from the instructions: 4 real disagreements between the code
and the C (whether they run is not known), 6 saves by setjmp (no fault unless longjmp's restore matters), 7 false
(5 after a call that does not return, 2 from f1 taken as a result of every function), 3 undecided. So about a third of
rows are false until the check knows which callees do not return and which results a function has.

## Open follow-up: 14 mid-entries that read r18 with no supplier (2026-11-13)

Found while fixing the frame-relay read (281349f7: `uncarried` took a 64-bit, float or vector store into the frame as a
save of the entry value, so the read of its source vanished).

**What changed:** after that fix, 14 port-only mid or joined entries read r18 before setting it. They are in the ranges
82a4a3d0..82aa1b10 and 82c392b0..82c3ab90; the exact list is in build box A `C:\dpx\TooHuman\unc\out\scan_changed.json`.

**Why they look like code holes, not functions:**
- Each one starts `lwz r24,X(r18)` straight after its parent's `blr`, an illegal word or a `b`, 0xc to 0x360 bytes
  inside a catalogue parent.
- Nothing reaches them as far as a static scan can see. No `fn_` in the live gen/ calls them. No `b`, `bl` or `bc`
  targets them, no data word or `lis`+`addi` constant holds their address, and Too Human's eh.json has no landing on
  them.
- Nothing hands over r18 to them either; only 4 `tc_stg(0x12u, …)` exist in all of gen/, for other callees.

The kit's hole and leaf pass (`--holes`) should probably not make them entries at all. That pass has not been
checked yet.

**The gap:** a target computed at run time (an indirect call through a value built in code) would not show up in that
static scan. A run that stops on a missing or unexpected function, with its game log, would settle it. Until then the
C of these 14 reads a stale `tc_gr[18]`; before 281349f7 it read 0.

Also changed by 281349f7: 826f5c8c (r11) and 82c66104 (f0, r11). Each is reached only by a `bc` inside its own parent,
which sets the register first, and the parent's C is unchanged.
