# Combined r15 re-stamp plan (2026-10-18)

The r15 round's harness changes need **one combined re-stamp per game**. The monitor runs it; no other session
re-stamps a real workspace. This doc lists, per game in run order (TC, Spawn main, Spawn ovl, PS2):

- the functions to re-stamp;
- what is rejudged and requeued after that;
- the Ghidra time at 1 and 2 slots.

The lists are in `docs/restamp_r15/<game>/`. Each line reads `addr  # reasons`, and every source that put a
function there is named.

- They come from `docs/restamp_r15/sets.py`, which is static: packets, harness meta and the `state/` lists, with no
  Ghidra. Its other inputs are copied into `docs/restamp_r15/inputs/`: Spawn's r15-oracle class lists, the TcLive
  "?" functions, Spawn's ovl jump-ins and frames_static.py's large frames.
- Run it again on the final tree before the re-stamp, because the counts below are from master 5476b77.
- Usage: `python docs/restamp_r15/sets.py KEY WORKSPACE OUTDIR`.

## 0. Prerequisites and order

1. **Spawn's TcLive large-frame fix must land and pass its gate first** (branch r15-live).
   - 2e7d39d's switch rule wrongly fires on every large-frame function (`mov.w #..,r1; add r1,r15`).
   - Its gate runs after PS2's TC staging proof.
   - Until it lands, no game's combined re-stamp starts. A re-stamp on the current rule would stamp the false
     switch into the large-frame packets.
2. **The ftrc runtime change (TcFpu saturating ftrc / sh4_ftrc, xemu report) lands in the same step as the
   re-stamp.**
   - Don't rejudge ftrc functions between the two.
   - **Rejudge an ftrc function only on its re-stamped harness.** Its `rejudge.txt` line says so ("ftrc: judge only
     on its re-stamped harness").
3. **Per game:**
   - re-stamp `restamp.txt`;
   - then rejudge `rejudge.txt`, leaving out the functions that are also in `requeue.txt`, because the new C is
     judged anyway;
   - then queue `requeue.txt`.
   - Ghidra-heavy work stays at one job per workspace. "2 slots" means `--jobs 2` inside that one job.
4. Phase D's items 6+8 gate, the full TC + games + sst set on the final tree, is the combined gate. This plan adds no
   gate of its own.

## 1. Totals

| game | re-stamp | of which ftrc | rejudge (all) | rejudge, not in requeue | of which ftrc | requeue | requeue not matched now | Ghidra h, 1 slot | Ghidra h, 2 slots |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TC | 598 | 141 | 339 | 329 | 135 | 24 | 4 | 5.1 | 3.9 |
| Spawn main | 311 | 40 | 98 | 95 | 37 | 106 | 41 | 4.0 | 3.1 |
| Spawn ovl | 294 | 124 | 141 | 119 | 105 | 89 | 1 | 2.8 | 2.2 |
| PS2 | 981 | 297 | 736 | 690 | 270 | 296 | 0 | 7.8 | 5.9 |
| **all** | **2,184** | **602** | **1,314** | **1,233** | **547** | **515** | **46** | **19.8** | **15.0** |

**Added 2026-10-19 (helper ABI, `tools/helper_abi.py`, docs/PROOF_GATES.md section 5):**
- TC's re-stamp gains 8c03a4b0, making 599.
- TC's requeue gains 5: 8c03a4b0, 8c0248e0, 8c0253f8, 8c03a134, 8c04cb68. After the must phase, 3 more join it
  (8c0344a4, 8c090b1c, 8c09cebc; their raw C fails on the re-stamped harness), making 32.
- Spawn main's requeue gains 2, making 108: 8c0393ac, 8c0396e0.
- 8c03a4b0 and 8c0248e0 crashed TC's port. Each gets an `oracle/hints` file so that its re-stamp reaches the
  8c067c64 call. Haiku is allowed for these two only, after TC's must phase.

**How the hours are worked out:**

- Each prepare is 26 s. That is item 3's L0 measurement: 0.72 prep-h per 100 functions, about 10–16 s of it JVM
  start-up.
- Each game also gets a sample phase of 100 functions.
- Slow cases add their extra time on top: Spawn's 10 class-3 step-limit functions take about 409 s each, and TC's
  class-7 8c09cf44 takes 142 s.
- 2 slots take 0.76× the 1-slot time. Item 3's wall time at jobs 2 was 0.55 h against 0.72 prep-h.
- Rejudge is runner time, and requeue is queue work afterwards. Neither is counted in the Ghidra hours.

## 2. Where the re-stamp sets come from

Each set holds only functions that are matched now, except the r15-oracle classes and the ovl jump-in parents: those
are taken from all harnesses, prepared, then requeued when they are unmatched.

| source | TC | Spawn main | Spawn ovl | PS2 |
|---|---:|---:|---:|---:|
| `--change fret` (TcFret) | 280 | 151 | 45 | 711 |
| `--change fargs` (TcFguide) | 34 | 21 | 29 | 25 |
| r15-oracle classes | 5 (class 6: 4, class 7: 1) | 41 (classes 1–5: 15/12/10/1/3) + 2 TcJumpIn join | — | 2 (class 8) |
| TcExit halts (`bra .`, or a callee that is one) | 0 | 9 | 0 | 9 |
| xemu ftrc (every ftrc function) | 141 | 40 | 124 | 297 |
| xemu ghmem (8c0682b4, 8c1453cc) | — | — | — | 1 (8c1453cc; 8c0682b4 is not matched) |
| TcLive large-frame packets (`add/sub/mov rN,r15`) | 128 | 86 | 153 | 113 |
| TcLive: callers of a large-frame r8..r14 writer | 174 | 0 | 0 | 0 |
| ovl TcJumpIn parents (Spawn's 14 entries join 11 parents) | — | — | 11 | — |
| **union** | **598** | **311** | **294** | **981** |

**Notes on these sources:**

- **Large-frame packets:** the set is the union of my operand scan and Spawn's frames_static.py list
  (`inputs/large_frames.tsv`).
  - By function, Spawn's list has TC 128, main 88, ovl 166 and PS2 114 (120 / 84 / 166 / 103 without the frames sized
    by an immediate).
  - The matched ones are exactly my 128 / 86 / 153 / 113. The rest have no matched C, so they are prepared when they
    are queued anyway.
  - After the r15-live fix every one is followed exactly, except Spawn main 8c024cf8. That one really moves to a new
    stack and keeps the old rule; it is in main's `left_out.txt`.
  - Only TC has callers of a large-frame function that changes r8..r14 (174).
  - In sp, ovl and PS2, every large-frame function pushes the registers it changes. So the fix most likely leaves
    those harnesses unchanged, and their re-stamp should be a no-op that confirms it.
- **The 14 ovl jump-ins** (Spawn, `inputs/ovl_jumpin_functions.tsv`, from a private ovl copy catalogued with
  4468f59's scripts): each entry joins its parent at the re-stamp.
  - The 11 parents are re-stamped. 7 of them weren't in the set from the other sources.
  - The 14 entries stop being harnesses of their own; they are listed in ovl's `left_out.txt`.
  - 8c5b23a0, the parent of four entries, has no matched C, so it is requeued on the joined harness.
  - 8c5953e2 is the TcLive "?" callee of 8c595160, a shared epilogue that pops r11–r14. The join should clear that
    "?"; 8c595160 stays on the rejudge list to confirm it.
- **Spawn main `left_out.txt`** lists 12 class functions from the r15_oracle report. They get no prepare and no
  requeue:
  - AICA mailbox / wave RAM: port-replaced;
  - real MMIO and interrupt-counter polls: can't be fixed;
  - the mid-entry stack switch and the P2 uncached entry: excluded;
  - four TcJumpIn continuations, which join their parents 8c0129c6 / 8c021282.

## 3. Rejudge and requeue (after the re-stamp)

**Rejudge** means the verdict may change with no new C:

| source | TC | Spawn main | Spawn ovl | PS2 |
|---|---:|---:|---:|---:|
| `state/float_gaps/rejudge.txt` | 226 | 63 | 19 | 583 |
| `state/xemu_fix/rejudge.txt` | — | — | — | 112 |
| TcLive "?" (r_live re-check) | 23 | 1 | 1 | 0 |
| every ftrc function, **only on its re-stamped harness** | 141 | 40 | 124 | 297 |
| named: 8c182850 (mirror rule, PASS 0/98 on the fixed-oracle staging harness) | — | — | — | 1 |

**Requeue** means the function needs new C:

| source | TC | Spawn main | Spawn ovl | PS2 |
|---|---:|---:|---:|---:|
| `state/ptr_views/requeue.txt` (C keeps a pool RAM pointer in the other view) | 0 | 63 | 77 | 279 |
| `state/float_gaps/requeue.txt` | 20 | 1 | 12 | 16 |
| `state/xemu_fix/requeue.txt` | — | — | — | 1 (8c1453cc) |
| `state/mirror_ptrs/requeue.txt` | — | — | — | 2 |
| r15-oracle class functions not matched now | 4 (class 6) | 41 | — | 0 |
| named: 8c021282 (TcJumpIn joined 8c021536: re-match, live C tail-calls it) | — | 1 | — | — |
| named: 8c1453cc (ghmem: C copies Ghidra's zeroing, FAIL 96/98 on the hint harness) | — | — | — | 1 |

**Named functions:**

- **8c021282** (Spawn main) is re-stamped (fret, class 5 join) and then requeued.
- **8c1453cc** (PS2) is re-stamped (ghmem) and then requeued.
- **8c182850** (PS2) is re-stamped (ftrc) and listed for rejudge after its re-stamp. ptr_views also flags it
  (pool 0c86d07c/0c86d088 written as 0x8c…), so it is on the requeue list too, and the requeue wins.

**Overlap:** these functions are on both lists and are only requeued: TC 10, main 3, ovl 22, PS2 46.

**Class 7** (TC 8c09cf44, TcSq) is re-stamped but not requeued. Its probe is still open.

**PS2's TC staging proof** (r15-xemu, notes r15-xemu-tc/) checks out against these lists:
- its 67 re-stamp and 66 rejudge functions are all inside TC's ftrc set;
- its one requeue, 8c078f94, is on TC's requeue list (float_gaps literal: the pool's -6.0f at 0x8c079348, where the
  C has KC = 0.5f). KA and KB really are 0.5f in the pool.
- Of its 142 candidates, only 8c0923e0 is outside the set, because it has no matched C.
- Before it, sets.py's asm line pattern ran past the end of a no-operand line (`_nop`, `rts`), so it hid the
  instruction after one. Fixing that added 8c01c178 and 8c039eb0 to TC's ftrc set (139 → 141). No other game or
  set changed.

## 4. Running it (monitor, 2026-10-18)

- It starts when the monitor gives the word, after Spawn's TcLive fix lands.
- Rerun `sets.py` on the final tree first.
- One game at a time, and one Ghidra-heavy job at a time.
- RAM, within that job:
  - add a slot while RAM is under 85%, up to 4 slots;
  - drop one slot at 90%;
  - stop at 95%;
  - resume after 5 readings under 78%.
- Order and owners:
  1. TC: this session runs it. TC's workspace is shared, and phase D is busy with items 6+8.
  2. Spawn main, then ovl: Spawn runs them.
  3. PS2: PS2 runs it.
- Each game's rejudge and requeue follow as soon as its re-stamp finishes.

## 5. TC requeue result (2026-10-19)

The 32 of `tc/requeue.txt` were seeded from their matched C (`requeue.py --seed`) and run on TC's server with one
Ghidra slot (every requeued job re-prepares) and Haiku workers. TC commits 802d13e and b8a8a70.

| | count | functions |
|---|---:|---|
| matched | 29 | 13 passed their seed; 15 by Haiku (8c09b164 newly matched); 8c09cebc after escalation to Opus (`state/escalate.json`) |
| readable | 29/29 | 11 rewritten by Opus, 8c03a4b0 and 8c0248e0 among them; the other 18 passed as they were |
| skipped | 3 | 8c09acc4, 8c09ae88, 8c09af40: the oracle reads machine code through R8 |

- **f32 returns:** 8c05dd10, 8c0611c8, 8c061ba4 and 8c061c88 failed first. The new harness compares fr0, and they
  return f32 while their signatures said void. The fix was `"raw_ret": "f32"` in readable/names, applied with
  `sig_override.py --apply` (their 16 callers were re-recorded and all pass). Haiku then re-matched them.
  Xmtrx_Determinant's prototype now returns f32.
- **Seeds can hide a wrong call:** a seed passes unchanged when the wrong call is still unreached. That happened to
  the helper-ABI flags 8c0253f8, 8c03a134 and 8c04cb68. They were requeued unseeded, so Haiku wrote them again from
  the new context.md, which lists r0/r1 as compared. helper_abi now flags 0 in TC. Function C is never edited by
  hand.
- The two hinted functions, 8c03a4b0 and 8c0248e0, reached their division call and were re-matched.
- The raw recount after the first pass was 2675/2675. All 32 harnesses are current.
