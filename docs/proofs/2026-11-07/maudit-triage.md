# Mutation audit, Dreamcast: the 19 flips, the 5 lost kills, and the edge pass re-proof (2026-11-07)

Run on build box A through jobreg on private copies (`maudit_triage/stage.py`: project.json, bin, state, Ghidra project,
oracle, reference, the sample's matched C and packets, and each triage function's live harness). Each function was
prepared twice at HEAD (2463faa3 plus nothing): `today` (every switch as the target sets it) and `all_off` (the seven
trust switches off: branch_coverage, targeted_vectors, junk_ram, mock_junk, mock_writes, call_snapshots, exit_frame).
The matched C and the lost-kill mutants were judged on the live harness, `today` and `all_off`
(`maudit_triage/triage.py`). The per-switch bisect never ran: nothing that failed `today` passed `all_off`.

jobreg: triage ps2 `20261107-021658-cde9863e-57db`, spawn-main `20261107-022040-68f92d3c-34b3`, tc
`20261107-022535-a24a1600-49f2`; re-proof `20261107-022655-8f70e0cd-10d4`. All exit 0.

## The 19 flips

None of the seven switches is involved: every one fails the same way with them all off. Three causes.

**(c) The copy lacks `readable/names/*.json` (4, plus 8c05d9c0's failure mode).** The live harness meta has
`"ret_override": {"from": "void", "to": ...}`, applied by `core/sig_override.apply_from_names` from the workspace's
names entries (`raw_ret`). The staged copy had no `readable/names`, so the fresh prepare kept Ghidra's raw `void`.
The matched C then fails lint R3 ("signature must be exactly: void ..."). The callers do read the result, so the
override is right. For example, spawn 8c049a74 reads fr0 (`fmov fr0,fr4; fcmp/eq`) after calling 8c049a40.
These have not been re-run with the names carried over.

**(b) TcOut r4..r7 exports at tail jumps (7d498852, 2026-10-28) (9 functions).** The live harnesses date from
10-25 to 10-27 and have no `r_exports`. Today's do, and the C does not hand those registers back. The static walk
(`core/regout`, `maudit_triage/exports_why*.py`) finds the reading call sites. In every case they are tail jumps.
The walk starts past the tail caller's code, in its literal pool or padding nops or at the next function: regout's
documented "r1..r7 all read" case, which the readable judge has an opt-in for (`exit_unread`). The raw judge has
none. Following each tail chain up to the real `jsr`/`bsr` call sites, no real caller reads them.

**(b, probable) TcTflag T exports (98e2bde0, 10-28; tail rule 79c86aaa) (4 functions).** At each real call site the
next instructions write T (`tst`, `cmp`) before any `bt`/`bf`, call onward, or save and restore SR (`stc sr`, `and`,
`ldc`). This is a 6-instruction look, not a proof.

**(a) Real defects in the port (2).**

| key | function | live | today (= all_off) | evidence | verdict |
|---|---|---|---|---|---|
| ps2 | 8c03cbf6 | PASS 0/96 | lint R3: `void fn(u32 param_r4)` | live `ret_override` void→s32 (names S11.json); caller 8c03cbfe `cmp/eq #i,r0` after the call | (c) |
| ps2 | 8c0498dc | PASS 0/100 | 100/100: r4 at exit | r_exports r4; readers only tail jumps in 8c049a14 (8c049a8e into data, 8c049be8 a mid-body `bra`); 6 real sites read none | (b) 7d498852 |
| ps2 | 8c07d342 | PASS 0/96 | 95/96: r5,r6,r7 | reader only tail 8c0b2c9c@8c0b2d78, walk past its code; 9 real sites read none | (b) 7d498852 |
| ps2 | 8c0aa444 | PASS 0/96 | 96/96: r5,r6,r7 | reader only tail 8c0aa530@8c0aa550 past its code; 8c0aa530 has no static caller (pointer) | (b) 7d498852 |
| ps2 | 8c0ca3c0 | PASS 0/98 | 93/98: r4,r5 | no reader at its 2 sites or the 76 real sites up its tail chains | (b) 7d498852 |
| ps2 | 8c17bac0 | PASS 0/96 | 96/96: r4,r6,r7 | reader only tail 8c17b800@8c17b804 (nops follow); 39 real sites none | (b) 7d498852 |
| ps2 | 8c17ed90 | PASS 0/98 | 97/98: r6,r7 | readers tail 8c17e720@8c17e748, 8c17e750@8c17e76e (nops); 25 real sites none | (b) 7d498852 |
| spawn-main | 8c023b60 | PASS 0/96 | 96/96: T at exit | t_export; 8c0208c0's 10 sites write T first or call on; 8c020b00 returns, and 8c0208c0's callers write T (`cmp/pl`) or call on | (b) probable, 98e2bde0 |
| spawn-main | 8c031786 | PASS 0/96 | 96/96: r4..r7 | readers tail 8c03dc22@8c03dc2a, 8c045732@8c045778 (nops); 107 real sites none | (b) 7d498852 |
| spawn-main | 8c049a40 | PASS 0/96 | lint R3: `void fn(u32 param_2)` | live `ret_override` void→f32; caller 8c049a74 reads fr0 | (c) |
| spawn-main | 8c04fc30 | PASS 0/96 | 1/96 (hidden set only) | t_export; caller 8c0116c0 calls onward right after both sites (`mov.l @pc; jsr`) | (b) probable, 98e2bde0 |
| spawn-main | 8c053040 | PASS 0/96 | 96/96: r4 | reader only tail 8c054f40@8c054f4e (nops); 100 real sites none | (b) 7d498852 |
| spawn-main | 8c0530a0 | PASS 0/96 | 96/96: r4 | reader only tail 8c053160@8c053166 (nops); 16 real sites none | (b) 7d498852 |
| spawn-main | 8c058560 | PASS 0/96 | 16/96: T at exit | t_export; 8c0116c0 calls on or `stc sr`/`and`/`ldc`; 8c0583b0 `cmp/ge` before `bf` | (b) probable, 98e2bde0 |
| spawn-main | 8c0588d0 | PASS 0/96 | 96/96: r4,r5,r6, T | r: tails 8c0587b4/d0/e0 (nops), one chain ends at 8c0587a6@8c0587a8, which is data (illegal opcodes); T: callers `cmp/ge` first | (b) 7d498852 + 98e2bde0 |
| spawn-main | 8c05d9c0 | **FAIL** lint R3 (must be `u32 fn(...)`) | 67/96: r0 callers read (1294df8b) | names override void→u32; the matched C declares `void`; caller 8c05dbc4@8c05dc46 does `tst r0,r0; bf` | **(a)**: drops the u32 result its caller tests (the workspace's own harness has failed since 11-01) |
| spawn-main | 8c063620 | PASS 0/96 | 96/96: r5 (expected 0x24, got 0x04) | the original advances r5 by 0x20 (and changes r6); 8c063180's `bsr` at 8c063486 then calls 8c0635e0 without setting r5/r6; 8c062d6a also reads them up its tail chains | **(a)**: the C must hand r5/r6 back (sh4_set_r) |
| tc | 8c01c58e | PASS 0/96 | lint R3: `void fn(u32 param_r4)` | live `ret_override` void→s32 | (c) |
| tc | 8c053a90 | PASS 0/96 | lint R3: `void fn(u32 param_1)` | live `ret_override` void→s32 | (c) |

Count: (a) 2, (b) 13 (9 TcOut, 3 TcTflag probable, 1 both), (c) 4.

## The 5 lost kills

These are not today's judge. The matched C hashes equal the baseline's, and `core/mutation.generate` has not changed
since 789f901f, so these are the same mutants. Each one passes on the live harness as well as on `today` and
`all_off`. The baseline outcomes were recorded at 789f901f (2026-10-24) on that change's proof-copy harnesses, which
no longer exist. Every harness since leaves the mutated block unhit (`oracle_summary.json` `blocks_unhit`): the live
ones (re-prepared 10-25 to 10-27), `today` and `all_off`. So no commit can be named without the 10-24 vectors.

| key | function | mutant | statement | block | coverage since 10-25 |
|---|---|---|---|---|---|
| ps2 | 8c0e55a6 | m02 `+ -> -` | line 66 `wrf32(p + 0x2cu, f2)` in h_51c6 | 8c0e51c6 | unhit; reach: "144 mutated runs ... at 8c0e5624 did not reach it" |
| spawn-main | 8c01bcc8 | m03 `\| -> &`, m05 `0x10 -> 0x11`, m10 `+ -> -` | lines 86, 76, 78: the second record copy, run only when 0 < r6 <= (s16)[r4+6] | 8c01bd18 | unhit; branch 8c01bd16:f unhit; bound pass "open": 0 |
| spawn-main | 8c029060 | m08 `+ -> -` | line 67 `rd8(q + 0xbu)` | 8c0290d2 | unhit (8c0290ce:t/f unhit) |

## Re-proof of the fixed edge pass (nextId, 2463faa3)

| key | functions changed | edge vectors | repeated ids | cross-check vs a real off prepare | gained | lost | flips | planted wrong answer |
|---|---|---|---|---|---|---|---|---|
| ps2 | 64 | 284 | none | 0 differ | 9 | 0 | 0 | 8c0525fe FAIL 96/96 |
| spawn-main | 29 | 98 | none | 0 differ (incl. 8c01de10) | 5 | 0 | 0 | 8c04aa78 FAIL 5/100 |
| tc | 32 | 131 | none | 0 differ (incl. 8c0103c4) | 9 | 0 | 0 | 8c04cb24 FAIL 13/96 |

`mutation_check` exits 1 in each audit log because some functions are below its score bar. That is its normal
report, not an error.

## What `mutation_audit.py baseline` would need

- **The lost kills.** `baseline` refuses any lost kill, and `--reason` covers only a thin run, so today's run is
  refused for these 5. Two ways out:
  - Get coverage of 8c0e51c6, 8c01bd18 and 8c0290d2 back, so the kills return.
  - Change the code to record named, accepted losses with a reason, for example
    `--accept-lost ps2:8c0e55a6:2 spawn-main:8c01bcc8:3,5,10 spawn-main:8c029060:8 --reason "blocks unhit since the 10-25 re-prepare"`.
- **The flip records.** A flip is an error record. `baseline` merges the run over the old file
  (`dict(old, **cur)`), so each flip would replace that function's baseline outcomes and drop it from every later
  gate. Either:
  - re-run on copies that carry `readable/names`, which clears the 4 (c);
  - or change the merge to keep the old outcomes where the run's record has an error.
- **The (a) and (b) items.** The 2 (a) are port fixes in the workspaces. The 13 (b) need TcOut (and TcTflag, where
  confirmed) to stop treating tail-jump sites as reads. Until then they stay flips.
