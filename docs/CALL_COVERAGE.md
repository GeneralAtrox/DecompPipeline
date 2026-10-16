# Call-site coverage and the reach pass

The judge compares a callee's arguments only at the calls a test vector actually runs. A call no vector reaches is
never compared, so matched C can pass with a wrong argument there. Toy Commander's port crashed six times on such
calls: a frame address (`mov r15,rN; add #k,rN`) dropped to 0, and a NULL matrix. The frame-address case also has a
lint rule (R12, core/frame_args.py); this page covers the general fix.

## The figure

Each harness's `oracle_summary.json` has `call_sites` (site → number of valid vectors that reach it) and, from the
reach pass on, `reach`:

```
"reach": {"runs": 312, "added": [2, 2], "found": ["8c057650", "8c05766a"],
          "unreached": {"8c0576a0": "no path from the entry reaches it in the control flow"}}
```

`core/call_coverage.py` sorts each unreached site into one of two classes:

- **explained**: the reach pass tried it and gives a reason;
- **untried**: the harness predates the pass (it has no `reach` key), so nobody has looked.

The figure (`call sites reached N/M (P%)`, with the explained and untried counts) appears in four places:

- `tools/call_coverage.py report`;
- `tools/doctor.py`;
- the final audit's `calls` check;
- the judge's verdict (`coverage.calls_untried`, `coverage.calls_reasons`). A matched function's ledger row keeps
  the reasons in `unreached_calls`, and the weak-match note names the sites.

The final audit **fails** while a matched function has an untried site. An explained site does not fail it, because
it is a known, stated gap rather than a silent one.

## The reach pass (TcReach.java)

The pass runs inside prepare, after both vector sets are made, for every function with an unreached site. Turn it
off with target.json / config `"reach_calls": {"runs": 0}`. For each unreached site it:

1. **Finds a frontier**: a block some valid vector reaches, ending in a branch, whose successor no vector reaches
   but from which the site's block can be reached (TcCfg.java builds the body's control flow).
2. **Reruns a vector up to that branch** (TcOracle `watchPc`) and records the loads just before it, the calls made
   so far and r0..r15 there.
3. **Mutates the inputs feeding the branch**, about 12 runs at a time. The inputs are the latest RAM loads (not the
   own frame, not code, not the function's constant data: TcCfg.isConst spans its instructions and the pool words
   and `mova` targets, such as a `braf` jump table, they address), the scripted results of the last three calls,
   and the parameter registers. The
   new values come from: the function's compare constants (TcGuide), 0/1/-1, bit flips, neighbours, and the
   values the run held at the branch ±1. Half the runs are **direct**: TcCfg records the registers the compare
   before each branch reads (`cmp/*`, `tst`, `dt`), and an input still holding one of those values is set to an
   operand ±1. So `cmp/hi r5,r4`, with r4 the parameter as passed, is met by r4 = r5+1. Sometimes two inputs
   change at once.
4. **Reruns with the usual repairs** (TcVectors.runRepaired). A valid run that reaches a new block joins the corpus,
   so chained conditions are met one at a time.

Each newly reached site gets one added dev vector and, when a further mutation still reaches it, one hidden vector.
They get ids after the first pass's vectors, which stay byte-identical; a function with every site reached is
unchanged apart from `"reach": {"runs": 0, ...}`.

The pass is deterministic: its generators are seeded by the function seed, its collections are ordered, and it has
a run budget (`runs`, default 800). Its wall-clock limit (`seconds`, default 150) only cuts it short, and the reason
then says so. Frontiers that several sites share are budgeted once (144 mutated runs each).

### Reasons for a site it cannot reach

| Reason | Meaning |
|---|---|
| no valid vector to start from | the first pass produced no valid vector at all |
| no path from the entry reaches it in the control flow | dead code, or reached only through a computed jump the block model does not see |
| reached only by invalid runs (N, mostly: ...): no input tried lets a run that gets there end normally | mutated runs got there but none was valid; typically a fatal-error path that never returns (TC 8c0575f4's overflow screen loops forever) |
| no reached block branches toward it | the blocks before it are reached only by fall-through from unreached code, or every run ends first |
| not tried (branch(es) at X): budget note | the run budget or the time limit ran out before its frontier got a turn |
| the branch(es) at X toward it read no input the reach pass can set | the condition depends on nothing in RAM, no scripted result and no parameter |
| N mutated runs of the inputs feeding the branch(es) at X did not reach it (K invalid, mostly: ...) | tried and failed; the most common reason the mutated runs were invalid is named |

For a site that stays unreached, the options are the usual ones: a hint (oracle/hints/ADDR.txt) that sets the
needed input, or accepting the stated gap.

## Old harnesses

Harnesses made before the pass carry no `reach` key. Re-prepare them with the servers stopped:

```
python tools/call_coverage.py --project DIR report --list state/untried.txt
python tools/bounded.py 14400 python tools/call_coverage.py --project DIR reach @state/untried.txt
```

`reach` keeps each old harness in `state/reach_backup/ADDR/`, re-prepares it in the Ghidra slots (`ghidra_parallel`,
or `--slots N` for this run; TC: 208 functions in 17 min at 6 then 8 slots, about 0.5 GB of RAM per slot), and
judges the function's matched raw C on the new harness. A FAIL means the matched C differs from the original at a
call (or on a path) no vector reached before. The FAILs go to `state/reach_failed.txt`; re-match them:

```
python tools/requeue.py --project DIR --seed "NOTE" @state/reach_failed.txt
```

Then serve and start, and run `refine.py run / verify / header` on the re-matched functions.

A FAIL forced by a faulty harness is undone the same way: once the harness is fixed and re-prepared, judge the C in
`matched_stale/` with `decomp.py check ADDR --work matched_stale/ADDR.c`, and requeue the ones that pass with
`--seed "NOTE" --stale`; the job judges that C first and keeps it without an agent run. (TC: reach vectors that
mutated in-function constant data, before TcCfg.isConst, forced 3 of 10 re-matches; 8c0144da's re-matched C
followed a mutated jump table.)

Any change to an oracle script (including this pass) changes the oracle stamp, so every harness becomes stale. A
full re-stamp (`tools/reprepare_stale.py --stamps`) costs Ghidra time but no tokens.
