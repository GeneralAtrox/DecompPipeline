# Skies of Arcadia Legends (GSOE8P): translate-and-judge scorecard

This run put DecompPipeline's GameCube route over the whole of Skies of Arcadia Legends' main.dol: prepare,
translate and judge for every catalogued function. It is shorter than `PIKMIN_TRUTH_SCORE.md`. Skies has no public
matching decomp to score against: the one public project, a static recompilation, has close to no matched C.
The kill-rate sample and gc_truth scoring were left out on purpose. This file holds numbers only; per-function lists
stay in the Skies project folder.
Ran 2026-11-08 on build box B (8 prepare slots, 8 judge jobs). DP master 1d115d87, DP defaults throughout (no limits
raised). The one deviation: 801f6120 has an oracle hint, described below.

## Step A: function list (main.dol, 6,751 DP functions)

- main.dol only: 2 text sections (0x80003100 to 0x802ac7e0), no RELs.
- DP's catalog: 6,751 functions.
- The game's script opcode table names 130 distinct handler entry points. 4 of them are missing from the catalog
  (`u32 fn(void)` stubs ×3, `u32 fn(u32, s32)` ×1). Each sits in no catalogued body, just before a catalogued
  function, and is reached only through the table.
- To judge the 4, a side workspace added them as `seed_entries`. They went into a separate workspace so the main
  catalog, and with it every oracle stamp, stayed unchanged.
- Discovery fix, not built: seed function starts from data-section words that point to 4-aligned text addresses
  outside every catalogued body (dispatch tables such as this opcode table).

## Script opcode handlers first

The opcode handlers, the script expression reader 801f6120 and all their callees went first: 1,170 functions.

| Set | Functions | PASS | FAIL | no proof (unproven or skipped) |
|---|---|---|---|---|
| catalogued opcode handlers | 126 | 126 (11 thin) | 0 | 0 |
| expression reader 801f6120 | 1 | 1 (97 vectors, 0 failing, 89 of 105 blocks) | 0 | 0 |
| the 4 uncatalogued handlers (side workspace) | 4 | 4 (96 vectors each, all blocks) | 0 | 0 |
| callees of the above | 1,043 | 965 (56 thin) | 8 | 70 |

Every handler passes. Of the 8 callee failures, 6 touch hardware registers (GX FIFO and similar), 1 is a return value
and 1 a callee-saved register; all 8 are in the failure table below.

### The 801f6120 hint

Without help, the oracle's random inputs fault in 801f6120 ("fault: read"), so it was unproven. The reader
evaluates a token stream, so random words underflow its stack. The hint (`oracle/hints/801f6120.txt`, DP's existing
`genstream`/`genrange` grammar) gives r3 a pointer to a stream of the most common real expression shape. That shape
was harvested from all 258 field scripts on the disc (5,219,241 expressions, 49,995 distinct). It is
"variable, float literal, operator, variable, float literal, operator, operator, end", used 4,453 times. The variable
classes, literal values and operators come from the ranges seen on the disc, not from invented values. The hint
lives in the workspace, not in DP.

## Step B: test vectors, translation, judge (all 6,751 DP functions)

| Result | Functions | Note |
|---|---|---|
| PASS | 6,408 | 94.9% of DP's functions; 628 of them thin (vectors reach under 70% of blocks) |
| FAIL (a vector differs) | 26 | below |
| unproven (no or too few valid vectors) | 192 | below |
| skipped (oracle cannot run it) | 123 | below |
| no verdict (judge could not set up the call) | 2 | a float parameter Ghidra put in an integer register |
| translator gave no C | 0 | ppc-translate produced C for every function |

Pass rate over functions with a verdict: 6,408 of 6,434 (99.6%). PASS coverage: median 100% of blocks,
10th percentile 70.8%, 4,163 at 100%; median 96 vectors. The 4 side-workspace handlers are not in these counts;
with them, 6,412 of 6,755 pass.

The judge marks 81 functions FAIL. 55 of those had no failing vector: one vector set held fewer valid vectors than
the minimum of 16. As in the Pikmin scorecard, they are counted as unproven here.

### Failures by cause (26; all 26 PASS since 2026-11-09, see below)

| Cause | Functions |
|---|---|
| memory write differs (value or presence) | 8 |
| hardware: float written to the write-gather FIFO 0xcc008000 keeps a different NaN bit pattern (0x7fc00000 vs 0xffffffff, quiet bit) | 5 |
| hardware: halfword register read at the wrong offset (0xcc005004 vs 006, 0xcc004022 vs 401e) | 3 |
| hardware: a store to 0xcc006438 missing on some paths | 2 |
| callee-saved register not restored at exit (r14, r20, r25, r29) | 4 |
| return value differs | 2 |
| failures only in the hidden vector set | 2 |

### Unproven by cause (192)

| Cause | Functions |
|---|---|
| oracle: random pointer inputs fault on read | 85 |
| valid vectors below the judge's minimum (1 to 44, 0 failing) | 55 |
| oracle: write outside RAM | 35 |
| oracle: step limit | 9 |
| oracle: mocked callees loop | 7 |
| strict-flags replay failed | 1 |

### Skipped by cause (123)

| Cause | Functions |
|---|---|
| unsupported instruction (paired-single 51, mfmsr/mtmsr/mftb 20, FPSCR 4, sc 3, dcbi 2, others 10) | 90 |
| oracle vector generation did not finish in its time limit | 19 |
| executes code outside its body | 8 |
| stack parameter beyond the registers | 3 |
| other harness refusals | 3 |

Paired-single is the largest skip here (at least 51, against 4 on Pikmin): psq_l/psq_st with a GQR type or scale the oracle
does not model, ps_mul and ps_merge. These are the math and GX library routines.

### TcTrack gaps and the static run-on scan

| Gap | Functions on Skies | Of them PASS / FAIL / no proof |
|---|---|---|
| (a) a recorded call to a target no callee record lists ("none") | 0 | 0 / 0 / 0 |
| (a') pointer call with an empty CA line | 3 | 3 / 0 / 0 |
| (b) body runs on past its end (static scan of main.dol) | 27 | 0 / 0 / 27 (3 skipped, 24 unproven) |
| 8f72a3fa's stamp scope applied to GameCube (ported, 527e8e85) | 27 | 0 / 0 / 27 |

As on Pikmin, both gaps end as skipped or unproven, never as a false PASS. None of the 26 failures come from them.
None of the 8 outside-body skips is a run-on. Porting 8f72a3fa to `gamecube_ppc` (about half a day,
per the Pikmin scorecard) would affect at most 27 functions here.

## The 26 failures, 2026-11-09: all PASS

DP commits e937dd5a, 527e8e85 and 2ca394b6, judged in a clean clone on build box B (DP defaults, no limits raised, no
waivers). On the 297-function regress set (the 26, the 271 of `regress/skies_all.json`) no verdict went PASS to FAIL.
The causes were both sides':

| Fix | Side |
|---|---|
| callee-saved registers at exit are compared (tc_xr / tc_xrf): the r14/r20/r25/r29 failures | translator |
| hardware reads keep their order and stay unconditional; the last call's r4 is read before the next call | translator |
| the structurer's flag hop keeps its value through a second hop | translator |
| an inner self-call frame starts with the last call's f1, not 0.0 | translator |
| px_lfs keeps a signalling NaN; an invalid FP op gives the 750CL's +NaN; fnmadd/fnmsub keep a NaN's sign | translator |
| a read never hands the fault handler zeroed bytes for a page already written (TcBank) | oracle |
| psq_l / psq_st through an unquantized GQR keep NaNs and store the hardware's single (TcFixups) | oracle |

Each fix has a planted wrong answer: the new C with the old fault put back, judged against the new oracle.

| Plant | Function | Judge |
|---|---|---|
| old structurer C | 800d8214, 8025ce88 | FAIL 2/96, FAIL 45/96 |
| old structurer C | 801a2d60 | PASS 0/98: the new oracle's vectors no longer reach the path; the two above hold the fix |
| flushed subnormal load | 800e6e58 | FAIL 21/100 |
| halfword zeroed by the old TcBank | 80211298 | FAIL 3/97 |
| inner frame's f1 = 0.0 | 80211298 | FAIL 1/97 (hidden set) |
| fnmsub negating a NaN | 801231f8 | FAIL 4/96 |
| x86 default NaN (helpers without the NaN line) | 801231f8 | FAIL 4/96 |
| old oracle's canonical NaN on float stores | 801e203c, 8024f838, 80252100, 80252150, 802521d8, 801231f8 | FAIL 40, 4, 5, 4, 4, 1 of 96 |
| old oracle's canonical NaN on float stores | 8019dba8 | PASS: the judge counts its FIFO NaN words as equal, by design |

The old translator's C fails the round-2 oracle for 19 of the 22 functions without paired singles (planted2,
2026-11-09); the other 3 (801231f8, 8019dba8, 801e203c) were oracle-side and have the plants above. Pikmin, judged
with the same translator (2ca394b6, build box A, 6,618 functions): no PASS became FAIL, 9 FAIL became PASS.

## Cost and time per stage

| Stage | Wall | Machine time | Agents |
|---|---|---|---|
| setup: gcdisc, Ghidra import, catalog | 65 s | Build box B, 1 Ghidra | $0 |
| prepare, opcode set (1,170) | 0.84 h | 8 slots | $0 |
| prepare, the rest (5,581) | 3.6 h (legs 3.0 and 0.6) | 8 slots | $0 |
| prepare, all (6,751) | 4.5 h | 35.2 slot-hours on 8 slots | $0 |
| translate and judge, whole catalog | 0.46 h at 8 jobs | translate 387 s, judge 3.4 thread-hours | $0 |
| expression harvest for the 801f6120 hint | minutes | main laptop | $0 |
| side workspace (4 seeds, setup to judge) | minutes | Build box B | $0 |

Preparation's cost is again its tail: median 1.5 s per function, 90th percentile 18.6 s. 109 functions ran over
300 s (up to 764 s), and the slowest 10% took 87% of the slot-hours.
