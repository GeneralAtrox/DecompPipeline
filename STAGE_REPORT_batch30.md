# Stage report: batch 30 (engine-design leftovers)

**Verdict: GATE PASS; deployed 2026-10-15 23:27 (backup backup/batch30-20261015-232707).** Built in C:/Projects/DecompPipeline_stage30 by engine-design and merged by the coordinator
into C:/Projects/DecompPipeline_stage31, a fresh copy of live after batches 28 and 29 (see "Merge and gate" at the
end). Tests on stage31: 259 of 259 (`python -W error tests/test_tooling.py`).

Brief: the user's "do the leftovers" list for engine-design (batch 27 follow-ups). It has four small tooling fixes.
Each fix has one pass control and one fail control in tests/test_batch30.py. Live fails 9 of its 10 tests.

## Changed files (copy these to live)

| file | change |
|---|---|
| core/sig_rewrite.py | `_tail_to_value` accepts a trailing comment on the last `tc_call` line (`/* ... */` or `//`), and the comment stays after `return (KIND)tc_call(...);` |
| core/stack_uninit.py | `scan(..., callees=)`: a frame alias in the callee's own argument registers (meta `regs`, e.g. Shc_StrCpyUnaligned r0 / r1) escapes like r4..r7. `scan_packet` reads meta.json `callees` when not given |
| core/ghidra.py | prepare passes `meta["callees"]` to `stack_uninit.scan_packet` |
| core/raw_names.py | `was_map` / `load_was`: a names entry's `"was": ["g_oldName"]` (a designer rename). `find(..., was)` reports an old name in code or a comment (not in a string, not after `.` / `->`). An old name that is some entry's current name is skipped |
| core/sync_run.py | `was_names(reg)` (cached on the registry); `needs_sync` and `raw_report` pass it |
| core/sync_names.py | non-raw names are renamed only where not a member (`(?<!\.)(?<!->)`). `(void)callee(...)` as a statement whose callee is now void: the cast is dropped (`drop_cast`); a used result is still refused |
| tests/test_batch30.py | new: TailComment (2), CalleeStorage (3), Was (2), SyncWasAndVoid (3) |
| tests/test_tooling.py | imports the batch 30 cases |

## Effect on TC (measured on the stage, no files written)

- stack_uninit over 2543 packets: 14 -> 12 functions read an uninitialized frame word. 8c04ce18 and 8c04dc3c drop
  out: they pass the frame address in r0 / r1 to Shc_StrCpyUnaligned. The other 12 are unchanged.
- sig_rewrite: 8c01c6d0 (`"raw_ret": "s32"`, S03.json) was applied with this stage's tools/sig_override.py
  --apply. The matched file passes and the readable file verifies.
- was renames: 5 `"was"` keys were added to TC names (S17, S18, S21, S32 x2). Those globals were renamed by hand
  in batch 27, so the scan finds nothing left to sync.
- void cast: sync-names (stage) synced the 10 movie-middleware callers. None needed the cast drop. The case it fixes
  was 8c0703f4 (batch 27, fixed by hand).

## Deploy notes

- Copy the 8 files above. No config or format change. Old names entries without `"was"` behave as before.
- Every newly prepared packet gets the new uninit result. Already-prepared packets keep their meta
  `uninit_stack` until re-prepared. To refresh 8c04ce18 / 8c04dc3c, re-prepare them (or rescan their meta).

## Ideas not done (for the coordinator)

- The sync cast tier leaves round trips such as `(LibVideo *)(uintptr_t)((s32)(uintptr_t)player->video)` when the
  old raw call cast a typed field to s32 and the new parameter has the field's type. Collapsing
  `(T *)(uintptr_t)((s32)(uintptr_t)X)` to `X`, when X already has type T, would make the "cast" tier read like
  "clean". This batch did it by hand in 5 files.
- A names entry's `"storage"` cannot say fr0..fr3. Shc_F32ToU32 (8c09ac00, SHC runtime: f32 in fr0 -> u32) is
  `u32 Shc_F32ToU32(void)` for now.
- sig_rewrite handles only a tail call's value. When r0 holds a constant at `rts` (TC 8c08d48c 0x7dc, 8c08d540 0x7dd:
  the state just stored), `apply` reports NOT_REWRITTEN. It could append `return K;`. Both were fixed by hand.

## Merge and gate (coordinator)

- stage30 was cut from live before batches 28 and 29. Batches 28/29 changed none of the six core files above, so
  they were copied over a fresh copy of live (stage31). tests/test_tooling.py was merged by hand: it keeps the
  batch 28/29 imports and adds the batch 30 ones. Tests: 259 of 259.
- Fixture drift fixed: tc-call-target-8c08d48c (batch 28) still declared `void fn_8c08d48c`. engine-design's later
  `"raw_ret": "s32"` (Lib_MovieStop returns the stored state 0x7dc) made R3 refuse both copies. Both now return
  `s32` and end `return 0x7dc;`. Alone: 2 items, 0 unexpected (pass PASS 0/96, fail FAIL 21/96).
- Full gate: `python tools/bounded.py 7200 python tools/regress.py --projects all --sample 15 --jobs 2`
  (work31/gate/all.log, all.json; 22:47..23:27, 40.0 min). 395 items, 10 unexpected: the 8 known Spawn samples
  (gend 8c565534, 8c565868, 8c5661fc, 8c56643c; sstma07 8c9c8f80, 8c9c9100; sstma08 8c9c89a0; sstma09 8c9c92c0) and
  the two 8c08d48c fixture items above, run before the fix. **Result: PASS.**

## Deploy

Copied to live: the six core files, tests/test_batch30.py, tests/test_tooling.py (merged), the two 8c08d48c
fixtures and this report. Backup of the replaced files: backup/batch30-20261015-232707 (NEW_FILES.txt lists the
added ones).
