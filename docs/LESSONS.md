# Lessons, 2026-11-08

What cost DP time or gave wrong answers, and what the pipeline does about it now. Condensed from two waste audits
(October 2026), the combined re-stamp plan, the runtime spike notes and the dated proof records. Those records stay
in git history; this file keeps only what applies to anyone running DP on their own games.

The scorecard's "No waste" gate reads the findings table at the end of this file (`tools/scorecard_data.py`). A new
waste audit goes in `docs/WASTE_AUDIT_<date>.md` with the same `#`, `Finding` and `Status` columns; the newest by the
date in its title is the one the gate reads.

## The kind of waste that matters

- **The same step run again on unchanged inputs** is the waste to hunt. A slow step that cannot be avoided is fine.
  Walk each flow (prepare, judge, readable, verify, wave, regress) and ask where a result is computed twice.
- **Every reuse is keyed by a hash of every input it stands for**, never by a time, a folder or a name. The judge's
  verdict memo (`core/verdict_memo.py`), the kept runners, the runtime objects and the raw out-pointer runs all work
  this way. A FAIL is never kept; a PASS is reused only while its key is the same.
- **No gate is weakened to save time.** Two checks of the same fact on the same inputs in one run may merge; nothing
  else. A speed change that touches the judge goes in only with the owner's yes.
- **Proof for a speed change:** byte-identical output, or the same verdicts field by field, on real inputs, with
  before and after timings. A saving that is only reasoned says so.

## Stamps and caches

- **Stamp what decides the output, not where it lives.** The Ghidra stamp once hashed the install's folder, so the
  same Ghidra in another folder or on another machine re-prepared every harness. It now holds the version, build,
  decompiler, language files and extension jars (`tests/test_ghidra_identity.py`).
- **Scope a stamp to its inputs.** The judge stamp once hashed every file under the target folder, a translator's
  tree included, twice per function: 36 s a call on the Xbox 360. Scoped, it takes 5 ms.
- **Hash shared inputs once per scan, not once per function.** The oracle stamp scan hashed 54 shared files for every
  function, about an hour per re-stamp wave. `oracle_stamp.common()` now hashes them once.
- **The compiler is part of the judge's identity.** cl's full version, the SDK version, a content hash of the SDK's
  headers and libraries, and the toolset join the judge stamp. Two builds of one SDK version can differ only in
  content. Pin the toolset with `vcvars_args` in `config.local.json`; a vcvars run that fails stops the load and
  caches nothing (`tests/test_compiler_identity.py`).
- **Every cache carries its key.** A compiler environment cache copied with a workspace once kept the old compiler's
  paths. Caches now store the vcvars script, its arguments and cl's version, and reload when any differs.
- **Environment captures drop secrets.** A captured build environment once held a broker pass key.

## Prepares and Ghidra

- **A cold JVM costs 6 to 10 s per prepare; a served slot about 1 s,** with byte-identical output (proven on
  Dreamcast, GameCube and Xbox 360 harnesses). Serve slots in long runs.
- **A machine-wide broker needs its deadlocks designed out.** Each of these stalled every session's Ghidra once:
  - a waiter kept its slot server's idle JVM while a ticket ahead of it needed one (now it gives the server back);
  - a thread held a token while it waited for the master copy, whose own thread waited for a token (copies are now
    refreshed before the token);
  - a process the HOLD stops filed a ticket and sat at the queue's head (held processes now wait outside the queue);
  - the test suite queued in the machine's broker folder (`DP_BROKER_DIR`; `tests/conftest.py` gives the suite its own);
  - files of processes from before a restart survived when Windows reused their PIDs (anything older than the boot
    time is reaped).
- **Brake, don't kill.** A RAM guard that killed wave chunks lost their scans, in-flight prepares and warm JVMs. The
  wave now starts no new prepare above 85% while one runs, and keeps the hard stop as the safety gate.
- **Size slots by measured headroom,** not a fixed count: each running JVM at its measured peak with its children.
- **Measure before removing a "repeat".** The second auto-analysis in `seed()` looks redundant but changes the
  catalog; without it records differ and a function disappears. It stays.
- **Narrowing a stamp by guessing which functions a script change affects is unsound** (hand classifiers mispredicted
  5 to 43%). Make re-prepares cheap instead, and use `tools/selective_restamp.py --change <name>` only where it proves
  which functions a change can alter.

## Judge and oracle

- **Zero lost kills is the gate for any judge change.** Judge the same seeded mutants with the old and the new judge
  and compare them one mutant at a time: a total can hide a lost kill behind a gained one.
- **An old mutation baseline shows false regressions.** One recorded before a deliberate judge change shows lost
  kills the branch did not cause. Gate the base code against the same baseline first, and refresh the baseline from
  the base's own audit when landing.
- **Build failures are not kills.** A transient build failure, or a warning under `/WX`, counted as a kill can hide a
  survivor; judge build failures again on both sides.
- **New judge rules go behind a per-target flag, off by default,** and with the flag off a named cross-check shows the
  harnesses byte-identical. That check once caught a new pass numbering its vectors from the valid count, so its ids
  collided with an earlier pass's: new vectors take ids after the highest one written.
- **Keep a change out of every stamp while its flag is off.** A scope that moves only the functions a change can reach
  is fine; a change that touches every function (every call's mock, every argument list) has no sound scope:
  re-stamp everything once.
- **Choose inputs with the strict rules off, then replay them with the rules on.** Choosing with junk-injecting rules
  on gives fewer and different inputs. Drop replays that fail and check no function falls below the minimum.
- **Junk values must be ordinary values.** Junk float registers hold normal floats (a random sign and fraction with
  1.0's exponent); raw random bits made denormals the oracle refuses, and functions lost most of their vectors.
- **Every runner paints its stack with a fixed pattern before each vector.** Unset argument slots otherwise read
  leftovers that can equal the expected value (hiding kills) and change between runs and machines.
- **The junk-RAM pass** fills never-written RAM with seeded junk and compares the C with the reference emulator on the
  same junk: it catches C that relies on zeroed memory (uninitialised locals, an out-parameter read as 0). Judge only
  functions whose zero-RAM vectors the emulator reproduces, compare from the image both runs started from (not zero),
  and let a word that is NaN on both sides match.
- **Rules about what crosses a call need hand-planted wrong answers.** Generated mutants never move a store across a
  call; a store sunk past the call leaves the same final memory, and only a call-time snapshot of the bytes behind a
  pointer argument catches it.
- **Compare edges need their own vectors.** A relational or constant mutant of a compare differs only where the
  operands are equal or one apart, which random vectors rarely hit.
- **Vector ranges leave blind spots.** Wrong float-to-integer conversions survived because no vector held a value
  between 2^31 and 2^32, or -0.0.
- **Coverage belongs in the verdict.** "Every block and branch direction reached" is a different claim from "proven
  where tested"; label thin passes, and leave wrong values on reached paths to replays.
- **Registers passed through, self-calls and tail jumps:**
  - an argument register live at entry and compared at a call is an input, even if the function never writes it;
  - a call to the function's own entry is mocked and logged unless the translator writes real recursion;
  - analysis that walks callers from the return address follows a tail jump to the jumper's callers (bounded, and an
    unresolved chain keeps every register): reading past the jump gave false exports that failed correct C.
- **Mocked callees make harness artefacts.** A mock that writes nothing into an out buffer leaves stale words the
  console never shows. Prove the console never has that state (sentinel-fill in replays) before masking, bound the
  rule from the original's runs, and prefer masking the affected bytes to dropping a vector: a drop once lost a kill.
- **Don't model a state the console never has.** A pass built for a state that only a port bug caused rewarded C that
  was wrong in play. Check the console's own RAM first.
- **An invalid input is not a refused function.** An odd jump target on SH-2 is an address error the original never
  reaches: drop the vector, keep the function.
- **A memory-mapped hardware window is a fixed function of the address,** the same in the oracle and both runners,
  listed with evidence; then functions that read it can be judged instead of refused.
- **Passing the judge proves behaviour, not names.** A reference answer key (a public decomp, library source) may score
  DP after the fact; it never feeds a name, type or line of code into DP.

## Translators

- **Whole-program analysis belongs outside the per-function loop.** The GameCube translator re-ran its return
  analysis for every function (2.3 s each; 0.3 s once cached). The Xbox 360 translator had the same pattern.
- **The official judge reuses the runner the agent's last check built** from the same inputs, and the runtime is
  compiled once per content. Rebuilding them cost hours per readable pass.
- **Don't walk guest memory to find writes.** A runner that scanned 24 MB byte by byte per test vector spent a second
  per judgement; track the writes instead.
- **Ledgers are written compactly.** An indented JSON dump on every update grows with the game.
- **Keep a FAIL's verdict for a later job only under the same key,** so a translator does not re-judge the same C.

- **Compilers round decimal float constants differently.** The console's compiler truncated where MSVC rounds to
  nearest, so C keeps the disc's exact bits where they differ. Harnesses carry the rounding mode the game plays in.
- **Prove fused multiply-add per target:** prepare both ways, compare harnesses, count the vectors that differ.
- **Re-translate after a translator model changes:** the C's hashes change, and a port takes only judged text.
- **A register a function reads before it sets it is an input, whatever the calling convention says.** Too Human's
  hand-written movie decoder passed its constants in v64..v77 from one function to the next. The judge seeds and
  compares only the convention's registers and had never judged those functions; the port form carried v1..v13, so
  the C read 0 and every movie picture was garbage. The picture was blacked out for four days instead of compared
  plane by plane with the emulator's. The Xbox 360 translator now finds such reads, refuses them in the judge's form
  and carries the vector file in the port form. A wrong picture is traced to its first wrong function, not hidden.

## Re-stamps after the harness changes

- **One combined re-stamp per game.** When several harness-script changes are pending, take the union of the
  functions each can alter, land every prerequisite fix first (a re-stamp on a known-wrong rule stamps the bug into
  the harnesses), then re-stamp, then rejudge, then requeue. Run the set-building step again on the final tree.
- **A function on both lists is only requeued:** its new C is judged anyway.
- **A runtime change that alters a verdict lands in the same step as its re-stamp.** Rejudge such functions only on
  their re-stamped harness.
- **A seed can hide a wrong call.** A seeded requeue passes unchanged when the wrong call is still unreached. When the
  fault is a call, requeue unseeded so the worker writes the function again from the new context.
- **A harness that starts comparing a register finds wrong signatures.** Functions that return a float but were
  declared void failed once the return register was compared; the fix is the signature, then the callers re-recorded.

## Harness pitfalls

- **Ghidra refuses** a project path with an element starting with "." and a project whose recorded owner differs
  from the account name (case counts).
- **Oracle scripts copied into a workspace go stale** against the target's template; check scripts and stamps before
  relying on an old workspace.
- **A step that does nothing fails loudly.** A step that skipped every function and recorded "done" made later
  restarts misread the state.
- **A timeout costs one vector, not all.** Runners flush after each vector, so the output names the hung one: leave it
  out and run the rest.
- **The same harnesses and pinned compiler give the same verdict on every mutant on every machine;** only host stack
  addresses may differ. Use that as a cross-machine check.
- **Profile the oracle when prepares time out:** per-step regexes and per-call set rebuilds were the cost once.

## Setup and tests

- **One command from disc to workspace** (`tools/game_setup.py setup <disc> <workspace>`), with a decode check of
  every function found. Hand steps were the commonest source of differences between machines.
- **Every import is listed in `requirements.txt`;** `tests/test_requirements.py` checks it.
- **A suite run does no expensive setup it can cache** (the Xbox 360 test once decrypted the executable on every run).
- **A test never waits on a live shared service.** Mock it or give the suite its own.
- **Worktrees pile up.** `tools/worktree_prune.py` lists the clean, landed ones (`--apply` removes them).
- **No fixed sleeps:** every wait is on a condition or a bounded poll.

## Measuring

- **Measure a saving before claiming it.** Audit estimates were off by up to 25 times (1 s a call measured 0.04 s; a
  copy blamed where the catalog step cost the time). Keep a fix proven identical even when its saving is small.
- **On a shared machine wall clock follows the queue:** report summed and median per-item times too.
- **Say "not concluded"** when a feature can't be exercised under real load, and cover its decisions offline.
- **Samples lean small.** A call-closure sample favours small, low-level functions; report its size quartiles against
  the whole game.
- **On a translator-first route the readable stage is nearly all the cost.**
- **Byte-matching a game against a reference SDK needs guards:** relocations masked, a minimum of unmasked bytes, one
  unique name, a library address range and a neighbour from the same object agreeing. Prove the guards with planted
  wrong answers (a flipped byte, a copy at a game address, a real look-alike).

## Ports and frame timing

- **The SDK layer keeps the alignment the console guarantees.** An allocator header put lists 4 bytes into a 32-byte
  line, and that one cause showed as three unrelated picture faults. Confirm with the original's RAM.
- **Judge stubbed library calls by how callers use the result:** a stub returning the wrong constant is the likeliest
  fault.

- **Measure on whole replays with a thread-CPU clock, and with no proof flags on.** A frame dump's PNG write showed
  up as a 300 ms "hitch" that was the proof's own cost.
- **Loads are allowed to be slow; other spikes are not.** Reading a whole movie file at its start cost up to 200 ms;
  stream it. A tool that cannot tell loads apart cannot give a verdict on a whole replay.
- **A real-clock wait must catch up.** Waiting for the next boundary after "now" loses a whole slot for every slow
  tick: the guest clock slows and the sound, mixed per blank, starves. Each blank is due one tick after the last,
  and a debt over a few ticks is dropped (`--catch-up`).
- **A frame cap shows frames in slots,** with a high-resolution sleep, not a fixed sleep after each frame: that adds
  every wake-up's delay to the gap.
- **The present thread goes at the end of the render,** not after the tick's vertical-blank wait.
- **In-between frames pair triangles by likeness, not by position in the batch.** Games send a mesh's triangles in a
  different order each tick; pairing by index blends the wrong ones and cracks meshes and shadow volumes. Pair by
  distance, depth, texture coordinates and colour, blend per vertex, and never slide a texture-coordinate jump.
- **Audio look-ahead follows sustained slow scenes, not single spikes;** raising it on a spike held ticks over a slot.
- **Hot spin reads stay inline.** Sending every read of a named spin word out of line cost about 14% of samples.
- **Compare builds by alternating runs** (A, B, A, B) on the same machine: host load moves the level between runs.
  Only the target machine's timing is a verdict.
- **A fault known to be intermittent is never cleared by one pass.** Too Human's movie-worker fault (one try in 3 to
  13, recorded in its handover since 2026-11-07) went live on one lucky launcher check, and the verdict did not name it;
  the user's first run died in the first movie. Go live only on repeated real-clock runs with a fault count, and every
  verdict names each known fault with its measured rate.

## Findings

| # | Finding | Status |
|---|---------|--------|
| 1 | Official judges rebuilt the runner the agent's last check built | fixed (runner reuse by input key) |
| 2 | Oracle stamp scan hashed shared files per function | fixed (`oracle_stamp.common`) |
| 3 | Regress copied every project again when its mode switched | fixed (a copy per mode) |
| 4 | The draft cycle ran the raw judge twice on the same inputs | fixed (verdict memo) |
| 5 | Raw out-pointer runs were cached per build folder | fixed (per function, full key) |
| 6 | The readable judge compiled its runtime on every check | fixed (once per content) |
| 7 | Live prepares started a cold JVM | fixed (served slots) |
| 8 | The wave's RAM guard killed chunks instead of braking | fixed (`--brake 85`) |
| 9 | Wave step 5 re-judged what step 4 had just judged | fixed (verdict memo) |
| 10 | Broker deadlocks: idle server, token held over a copy, held head ticket | fixed |
| 11 | Broker files outlived a restart | fixed (reaped by boot time) |
| 12 | Judge stamp hashed the whole target tree, translators included | fixed (scoped) |
| 13 | GameCube readable judge rebuilt its runtime each judgement | fixed |
| 14 | GameCube and Xbox 360 translators re-ran whole-program analysis per function | fixed (cached) |
| 15 | GameCube readable runner walked all guest memory per vector | fixed |
| 16 | Ledger rewritten indented on every update | fixed |
| 17 | Translator stage hashed the harness twice | fixed |
| 18 | Translator backlog wrote into the workspace, so pilots copied it by hand | fixed |
| 19 | A test decrypted the Xbox 360 executable every suite run | fixed |
| 20 | Scorecard parsed each ledger twice and read the oldest audit | fixed |
| 21 | Every prepare hashed the SH-4 FSCA table, PowerPC too | fixed (`fsca_table: false`) |
| 22 | Served PowerPC prepares read their facts file from the wrong argument | fixed |
| 23 | No rule for removing worktrees | fixed (`tools/worktree_prune.py`) |
| 24 | Slot cap fixed at 2 while RAM had room | fixed (measured headroom) |
| 25 | Hand setup steps per console | fixed (`tools/game_setup.py`) |
| 26 | Second auto-analysis in `seed()` | unavoidable (changes the catalog) |
| 27 | Readable run re-judged unchanged raw C at its start | fixed (memo) |
| 28 | Each agent check started cold | fixed (`core/warm_check.py`) |
| 29 | Ghidra stamp hashed the install's folder | fixed (identity, not path) |
| 30 | Judge stamp did not name the compiler; compiler caches had no key | fixed |
| 31 | No `requirements.txt` | fixed |
| 32 | A process the HOLD stopped still queued; the suite used the machine's broker | fixed |
