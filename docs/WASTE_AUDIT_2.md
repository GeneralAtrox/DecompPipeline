# Waste audit 2, 2026-10-23

A second, independent waste audit of DP, on branch `waste-audit-2`. Its base is `waste-final` 67f2965, which
contains the integration branch `claude/integration-2026-10-22-7e0060` 3736d25. It covers every console:
Dreamcast, Saturn, GameCube, Xbox 360 and Xbox. The goal is a pipeline that is direct, slim and fast, where every
action is warranted and nothing runs twice on unchanged inputs.

How to read the tables:

- **fixed** means the fix is committed on `waste-audit-2` and proven: byte-identical output, or the same verdicts.
- **fixed (patch)** means the fix is written and proven, but it touches the judge's files. It is kept as a patch
  under `docs/proposals/` and lands only on the user's yes. This is the convention the first audit used for its
  judge set.
- **fixed (branch)** means the fix is committed on its owner's branch (named in the row), and this audit checked it
  there: the same output or verdicts, before and after numbers on real inputs. It lands with that branch.
- **open** means found and measured, but not fixed here. The row says what blocks it.

Nothing on this branch changes what the judge decides. Rows 24, 26 and 28 change how it runs and when a kept
verdict or compiler setup may be reused (26 and 28 are stricter). They go with the judge set, for the user's yes.

## Findings, ranked by time saved

| # | Finding (flow) | Saving | Status | Proof |
|---|----------------|--------|--------|-------|
| 1 | `judge_stamp` hashed every file under the target folder, including a translator's tree, on every call, twice per function in refine (all consoles; worst on Xbox 360) | Xbox 360 checkout with the translator tree: 36 s → 5 ms a call, about 72 s per function (about 20 h per 1,000 functions). Others: 5–20 ms a call | **fixed** 693d011 | Same stamp bytes for every judge input; `tests/test_verdict_memo.py` (stamp bytes kept, translators left out) |
| 2 | A served slot took a machine-wide token for every prepare; under the shared queue its idle JVM blocked others, so give_way closed it: 0 of 200 prepares were served under load (prepare, wave) | 8.6 s of Ghidra time per prepare (live cold median 9.7 s). Proven: 20 Skies prepares 582 → 118 s, 30 Toy Commander prepares 652 → 200 s (median 7.4 → 0.8 s). Since the first audit: 447 live prepares, all cold, about 64 min. About 15 h for a 6,400-function game | lease **fixed** 162714e (with `prepare_list --serve` 293e744); turning serving on for live runs is row 16 | Proofs B and C (`docs/proofs/2026-10-23/PROOFS.md`): served output byte-identical to cold (140 of 140 files on GameCube, 213 of 213 on Dreamcast); other sessions' longest queue wait 38 s while a Lease was held (62 s during a cold run). Offline: 34 broker and 15 server tests |
| 3 | GameCube readable judge rebuilt its whole runtime and runner every judgement, and recompiled `tc_ppc.c` unoptimised in every build (readable, verify) | 2.1 s per judgement (median 5.79 → 3.70 s); about 3.9 h per readable pass over Skies' 6,751 functions | **fixed (patch)** `docs/proposals/gc_readable_judge.patch`; lands on the user's yes | 83 Skies functions: same verdicts (83 PASS), runner output byte-identical for 81. The other 2 also change between runs of the old code. 231 readable tests pass: proof A, `docs/proofs/2026-10-23/PROOFS.md` |
| 4 | GameCube translator re-ran the whole-program return analysis for every function (translator stage) | 2.30 → 0.30 s per function (median); about 3.75 h per full Skies run (6,751 functions) | **fixed** 63c140d | Identical C for 100 of 100 functions; identical kinds for all 6,751 |
| 5 | GameCube readable runner walked all 24 MB of guest memory byte by byte to find writes, per test vector (readable, verify) | 1.0 s per judgement (median 3.70 → 2.68 s); about 2.1 h per readable pass over Skies | **fixed (patch)** `docs/proposals/gc_readable_judge.patch`; lands on the user's yes | The same run as row 3, with the build change alone as the middle step: proof A |
| 6 | Readable ledger rewritten as an indent=2 dump on every update (readable) | 150 → 72 ms an update on Toy Commander, about 0.23 s per function (about 10 min on TC, 25 min on a 6,400-function game; it grows with game size) | **fixed** 4a03f0a | Same data, still valid JSON; the refine and adopt tests |
| 7 | First audit's oracle stamp scan (F2), re-measured on real workspaces | 300 TC harnesses: 6.4 → 0.2 s; 180 Dragon Force 2 harnesses: 3.2 → 0.1 s (about 21 ms → 0.7 ms each) | **fixed** in the first audit (10314a8); confirmed here | Same results (digest-equal) as master's code on both workspaces |
| 8 | Translator stage hashed the harness again right after the job stored the same hashes (translator stage) | 26 ms per function on Power Stone 2, about 3 min a game | **fixed** 6e462df | The after-translate check is unchanged |
| 9 | `translate_backlog` wrote its runs and builds into the workspace's own `build/ADDR/official`, so every pilot first copied the whole workspace by hand (translator stage) | One whole-workspace copy per pilot (Xbox mirror: 103 MB) and the hand step | **fixed** fd58e7e | A Skies copy: 2 PASS, 2 FAIL, no file written in its `runs/` or `build/`, temp folder removed |
| 10 | The Xbox 360 test decrypted the retail XEX on every suite run (tests) | 7.1 s → 0.01 s a suite run | **fixed** 81dc51e | Same figures asserted |
| 11 | The scorecard parsed each workspace's ledger twice (scorecard) | 0.21 → 0.13 s per workspace on TC | **fixed** 2da24db | Same figures on TC and two other workspaces |
| 12 | Every prepare ensured, hashed and touched the 1 MB SH-4 FSCA table, also for PowerPC targets that never read it (prepare) | A 1 MB read and hash per GameCube and Xbox 360 prepare | **fixed** 70d31b0 | `target.json` `"fsca_table": false` on both; Dreamcast and Saturn unchanged |
| 13 | Served PowerPC prepares never had their facts deadline shifted: TcServe read the facts file from the last argument, which became hw_ops (prepare) | Correctness of serving on PowerPC (needed before 4 is turned on there) | **fixed** 39dae1b | `tests/test_ghidra_server.py` checks the real facts file at index 9; proof B: Skies served prepares byte-identical to cold |
| 14 | No rule for when a worktree may go: 41 DP worktrees, about 3.4 GB in the clean, landed ones (between sessions) | About 3.4 GB of disk now; build-up stopped from here on | **fixed** f2fb7e7 (`tools/worktree_prune.py`: lists by default, `--apply` removes) | `tests/test_worktree_prune.py`; nothing was deleted (the list for the user is below) |
| 15 | The scorecard picked the newest waste audit by file name, so it kept reading the first audit (scorecard) | The waste gate read the wrong report | **fixed** c63b20c | `tests/test_scorecard.py` (audit order) |
| 16 | Serving is off in live runs: every live prepare since the first audit was cold (prepare) | The saving in row 2 | **fixed** de02aab (`docs/proposals/ghidra_serve_on.patch` applied on the Lease; the user's go, 2026-10-23) | Proofs B and C, run again on the result: on laptop2, as this laptop's RAM was held by the user's game: served output byte-identical to cold (Skies 140 of 140, Toy Commander 213 of 213); served 56.8 s against 332–361 s cold, and 313.5 s against 593–636 s; the cold runner beside a served one waited at most 0.15 s for a slot (`serve_on_laptop2.json`) |
| 17 | The slot cap (2, a 3rd under 82% RAM) was never measured; work waited at the cap while RAM had room 67% of the time (between sessions) | Queue wait, not repeated work: in the replay of the sampled hour, 131 of 279 waiting ticket-minutes served sooner, about 930 prepares started earlier | **fixed** ffc6862 (the user's yes, 2026-10-23): the broker sizes extra slots by measured headroom, each running JVM counted at its measured peak with its children, each granted token not yet started at a full peak. It keeps the 95% stop and the 85% resume rule, starts nothing at 88% or more, and never a JVM that would cross the stop line. A RAM log (`ram_log.jsonl`) records each minute a ticket waits | Proof K: broker tests; the replay of the sampled hour runs 2.44 → 5.05 JVMs on average and peaks at 85.8% RAM, with no reading at 95% or more. Today's 99% readings were traced: none came from the number of Ghidra JVMs |
| 18 | Xbox 360 PowerPC TcFixups runs per-step regexes, and TcFacts parses `code_ranges` the same way, in the prepare script (prepare) | Measured: 7–16% of the Ghidra script time of long-emulation functions (82a468a8 70.5 → 59.7 s, 82e97a88 329.4 → 297.8 s); no measurable change on short ones. (The estimate was 1–1.4 h per game.) | **fixed (branch)** `xbox360-speed` 1481dae. It re-stamps every harness, so it rides the judge-gaps re-stamp | Proof H: 20 Too Human prepares on one slot, 140 of 140 files byte-identical (`oracle_stamp` aside); the judge gives the same verdicts on both (19 PASS, 1 FAIL) |
| 19 | The Xbox 360 translator (xenon-tx branch) has the GameCube translator's pattern from row 4 (translator stage) | Measured: translate 12.7 → 1.15 s per function (median of 24; the first call builds the shared cache in both) | **fixed (branch)** `xenon-tx` b9fadab | Proof G: 25 Too Human functions, byte-identical C and the same verdicts (24 PASS, 1 FAIL); 418 → 137 s of translating in all |
| 20 | `seed()` runs full auto-analysis again; the graph is a separate whole-program run (setup) | None: the second analysis changes the result. The graph run is 7.4 s once per game | **unavoidable** (proof D: without the second analysis, 4 of 4,203 catalog records differ and one function disappears; the change was not landed) | Proof D, `docs/proofs/2026-10-23/PROOFS.md` |
| 21 | Hand steps: Xbox 360 setup (catalog_inputs, ghidra_setup, project.json, TcInputs by hand; the decode audit outside the broker); Dreamcast and Saturn project.json and TcInputs by hand; GameCube two setup commands (setup) | Operator time once per game | **fixed** for Dreamcast, Saturn and GameCube: `tools/game_setup.py setup <disc> <workspace>` (this branch), one command from the disc to a ready workspace, with a decode check of every function Ghidra found. **fixed (branch)** for the Xbox 360: `xbox360-speed` 1ef98e4 makes `x360setup`'s decode audit cover the whole setup list | Proofs I and J: on Toy Commander, Dragon Force 2 and Skies, every file equals the hand step's (Skies' GQR1 pin aside, see below), and the decode check passes (0 disagreements in 1,099,751 instructions of 12,478 functions); about 2 minutes, with no hand step. Too Human: the audit is byte-identical to the hand one (2,443 functions) and G2 passes |
| 22 | A readable run re-judges its raw C at the start, on unchanged inputs, every run (readable) | A later run on unchanged inputs: 60 Toy Commander functions 27.8 → 1.6 s, 83 Skies functions 65.5 → 1.5 s (about 0.4–0.8 s per function) | **fixed (patch)** `docs/proposals/raw_pass_memo.patch`; lands on the user's yes with the judge set | Proof E: the same answer for every function in every run (59 + 83 PASS; the one FAIL is judged each time). 78 tests pass with the patch |
| 23 | `translate_backlog --record` drops FAIL rows, so the job translates and judges the same C again later (translator stage) | One translate and judge per FAIL on each later job: 1.0–2.6 s each on GameCube (8 of 97 Skies functions), more where the translator is slower | **fixed (patch)** `docs/proposals/translated_fail_memo.patch`; lands on the user's yes with the judge set | Proof F: 97 Skies functions, the same verdict, score, summary and first differences in all three runs; the later job took all 8 FAILs (10.3 → 0.21 s). 853 tests pass with the patch |
| 24 | Each agent `check.py` starts as a cold process, and the header lint runs on every judgement (agent attempts) | 0.18 to 0.27 s (median) per agent check | **fixed** 83b3177 (the user's yes, 2026-10-23): a warm checker (`core/warm_check.py`) per agent run serves the checks; anything it cannot serve runs cold, as before. It changes how the judge runs, not what it decides, so it goes with the judge set | Proof L: the same verdict, score and first differences on all 162 checks across two consoles (Toy Commander raw 57 and readable 45, Skies on GameCube raw 60), every one served warm; median per check 0.546 → 0.370 s, 1.196 → 0.956 s and 2.567 → 2.300 s |
| 25 | The oracle stamp hashed the Ghidra folder's path, so the same Ghidra in another folder or on another machine re-prepared every harness (prepare, between machines) | Every harness of every game, once per machine or move (about 9.7 s each cold) | **fixed** dae199d: the stamp holds what decides the output (version and build, decompiler, the language's files, an extension's jars), not the path. It re-stamps every harness, so it rides the judge set's re-stamp | Proof M: the same install in two folders gives the same stamp; a changed version, language file, decompiler or extension jar changes it (`tests/test_ghidra_identity.py`) |
| 26 | The judge stamp did not name the C compiler: a verdict made with one toolset could be reused under another (judge memo) | Correctness: no stale verdict reuse across machines. New key `vcvars_args` pins the Windows SDK and the toolset on every machine | **fixed** 8b71051, 67cb8e8, e6fb914, 9826200 and 0268c07: cl's full version, the SDK version, a content hash of the SDK's headers and libraries (two builds of SDK 10.0.28000.0, .1839 and .2114, differ only there) and the toolset of each cached environment (64-bit and 32-bit) join the judge stamp;`vcvars_args` is applied at all four environment loads. Stricter, so it goes with the judge set, for the user's yes | Proof M: each setting loaded and reported by cl and the environment; without the key the command is byte-identical. This laptop runs 14.38.33130 today; the landing value `10.0.28000.0 -vcvars_ver=14.44` loads cl 19.44.35229. A vcvars run that exits nonzero (an SDK only partly installed, like 26100 here) or misses the pin stops the load and caches nothing (`tests/test_compiler_identity.py`) |
| 27 | No `requirements.txt`: networkx and pycparser were needed and listed nowhere, so 10 tests failed on a fresh machine (setup) | Operator time on each new machine | **fixed** 52db11c: `requirements.txt` and a README line | Proof M: `tests/test_requirements.py` checks every tracked file's imports against the list |
| 28 | Each workspace's compiler environment cache (`state/msvc_env.json`, `msvc_env_x86.json`, the port kit's) was used whatever made it: a copied workspace, a changed vcvars or toolset kept the old compiler paths (judge, port build) | Correctness: no build or verdict with a stale compiler. One environment load (2.7 s) per workspace when its key is first written | **fixed** 8b71051, 67cb8e8, e6fb914, 9826200 and 0268c07, with row 26: each cache has a key (the vcvars script, `vcvars_args`, cl's full version) and is loaded again when any of them differs. A hit runs no cl. It goes with the judge set | Proof M: a hit in 1 ms in a new process; another vcvars, other arguments, cl or a gone PATH reloads it (`tests/test_compiler_identity.py`) |

## Checked and found clean

- **Broker lock order with leases.** A lease waits outside `_LOCK` and `_SLOT_COND`. `give_back` closes only idle
  servers. A stale server closes before its successor waits. Held tickets never contest. A server started under a
  thread's token has no lease, and the broker still counts its JVM. No lock cycle was found.
- **Duplicate prepares across sessions.** About 45 accidental duplicates in three days, about 7 min in all.
  Negligible.
- **RAM.** The peak private memory of one prepare JVM is 952 MB, about 3% of the machine. Machine RAM had a median
  of 72% and a maximum of 84% over the sampled hour. No spike came from DP's own processes.
- **Stamp scope.** A prepare-code change re-prepares everything. No sound narrowing exists: the changed methods
  run for every function, and hand classifiers mispredicted 5–43% on the r15 sample. The answer is to make
  re-prepares cheap (rows 2 and 16), not to trust stale harnesses.
- **The translator stage makes no copies.** The adapters read the binary, `meta.json` and bodies in place.
  `revision()` runs git once per process. The backlog tool builds the runtime objects once.

## The first audit's fixes, re-measured

- **Live logs since the first audit ran on master** (d5cd42c, 2026-10-21). Master contains none of the first
  audit's fixes: they are on the integration branch and `waste-final`. So the live logs show the old behaviour:
  - Too Human: 268 prepares, all cold, median 9.9 s;
  - Dragon Force 2: 179 prepares, all cold, median 9.6 s;
  - Toy Commander's served prepares (the first audit's proof): 211 at a median of 1.1 s.
- **F2 (stamp scan)** was re-run on real workspaces, old code against new: same results, about 30× faster (row 7).
- **F3 (regress), F4 and F10 (readable passes), F8 and F9 (wave).** No live run used them yet, so there is nothing
  new to re-measure. Their proofs stand in `docs/proofs/2026-10-22/PROOFS.md`.
- **The judge set (F1, F5, F6)** still waits for the user's yes. Row 3 extends it to the GameCube.

## For the user

**To decide:**

1. The judge-set patch for GameCube (rows 3 and 5): `git apply docs/proposals/gc_readable_judge.patch`, together with
   the first audit's judge set, which it builds on.
2. Keeping the raw judge's PASS across readable runs (row 22): `git apply docs/proposals/raw_pass_memo.patch`
   (proof E). It changes when the judge runs, not what it decides, so it joins the judge decisions.
3. Keeping a translator's FAIL for a later job on the same inputs (row 23):
   `git apply docs/proposals/translated_fail_memo.patch` (proof F). It joins the judge decisions too.
4. Rows 24, 26 and 28 are committed on this branch and go with the judge set: the warm agent checker (row 24, how
   the judge runs), and the compiler in the judge stamp with the `vcvars_args` pin and the compiler cache's key
   (rows 26 and 28, stricter). Row 25 re-stamps
   every harness, so it lands with the judge set's re-stamp.
5. **The switch to MSVC 14.44** (the user's choice, at landing): set `"vcvars_args":
   "10.0.28000.0 -vcvars_ver=14.44"` in each laptop's `config.local.json`. SDK 10.0.28000.0 is the full SDK
   the judge already uses here; it loads live with cl 19.44.35229 (proof M). Each laptop needs that SDK and the
   14.44 toolset fully installed: a load that fails now stops the judge instead of caching anything. Today this
   laptop's judge runs 14.38.33130, the v143 default. The compiler proof (4a63d12) judged all 440 the same with
   both. On laptop2, also point `vcvars` at Visual Studio's own `vcvars64.bat` (the 2022 Build Tools'
   `VC/Auxiliary/Build` folder), then delete the `vcvars_v143` wrappers in its tools folder: they pass the same
   pins, which would then be passed twice (the judge warns when `vcvars` is not Visual Studio's own script). The switch re-stamps everything once. On a new
   machine, also `python -m pip install -r requirements.txt`.

**Disk you can free.** These worktrees are clean and their commits are landed. Nothing was deleted. Run
`python tools/worktree_prune.py --base master --base claude/integration-2026-10-22-7e0060 --sizes` to see the list
again, and add `--apply` to remove the ones idle for more than a day. The branch refs stay.

| Worktree (folder) | Branch | Size |
|-------------------|--------|------|
| session fa3d0bcc…, `dp-base` | detached at master | 1,521 MB |
| session b8712ded…, `dp-base` | detached | 1,189 MB |
| session 8d8ac4e1…, `dp-all` | broker-all | 360 MB |
| session 8d8ac4e1…, `dp-ref` | detached | 239 MB |
| session db68d097…, `dp-tx` | translator-stage | 114 MB |
| session ca25112d…, `dp-gcrel` | gc-rel | 9 MB |
| session 469f5316…, `dp-rd` | readable-sat-gc | 8 MB |
| session 8245b3df…, `dp-dod` | dod-scorecard | 7 MB |
| session ca25112d…, `dp-gcdisc` | gc-disc | 7 MB |
| session 8d8ac4e1…, `dp-hw` | hw-ops2 | 4 MB |

`DecompPipeline-lane2-rbase` is landed but has 16 changed files, so it is **not** on this list.

**Handed over, and checked back** (proofs G, H and I):

- **Row 19** is fixed on `xenon-tx` (b9fadab), and **row 18** on `xbox360-speed` (1481dae). Both hold: the same
  output and the same verdicts on real Too Human inputs. They land with their branches. Row 18 re-stamps every
  harness, so land it with the judge-gaps branch, whose re-stamp re-prepares everything anyway, not apart from it.
- **Row 21** is fixed on all four consoles (proofs I and J).
  - Xbox 360: `x360setup` on `xbox360-speed` gives the same files as the hand workspace, and since 1ef98e4 its
    decode audit covers the whole setup list by default, as the hand step did. It lands with that branch.
  - Dreamcast, Saturn and GameCube: `tools/game_setup.py` on this branch. It reuses each target's own disc tool and
    a decode check shared by the three. A helper could be shared with `x360setup` when the branches meet; nothing
    here touches that file.

**Not waste, noticed in passing.**

- The Skies `project.json` sets `GQR1` to `00000000`. The game's start-up code writes only 0x08040804 to it, and
  `gcdisc.py` pins that (proof J). The Ghidra catalog is the same either way, but `fixed_regs` also sets the
  oracle's starting registers, so changing the live value is for the GameCube owner and the user.
- Three catalog functions have no instruction at all: Toy Commander 8c099470, and Dragon Force 2 0603e328 and
  060fc000. Each is a one-byte body. The new decode check lists them for the catalog owners (proof J).
- The GameCube readable runner's output for 8020cba4 and 80213920 changes on every run, with the old code as well.
  The changing words are float register fields that hold host addresses. Both still PASS, because the compare
  does not read those fields. It is the judge's area, so it is reported here, not changed (proof A).
- The judge stamp hashes the cached compiler environment's file, which holds this machine's Visual Studio paths.
  So the same compiler installed in another folder gives another stamp, and its verdicts are judged again there.
  That is safe (never a stale verdict) and costs one re-judge per machine; narrowing it is for the judge set.

## Scorecard

The waste gate reads this report (row 15). No finding is open: the gate shows 0 open findings. Row 20 is unavoidable
(proof D). Rows 22 and 23 are fixed as patches (proofs E and F). Rows 18 and 19 are fixed on their owners' branches
(proofs H and G). Row 21 is fixed here for Dreamcast, Saturn and GameCube, and on `xbox360-speed` for the Xbox 360
(proofs I and J). Rows 16, 17 and 24 were approved by the user and are fixed here (proofs B, C, K and L). Rows 25 to 28
came from setting up the second laptop (proof M).

What still waits for the user's yes, though fixed: the judge set, which now holds rows 3, 5, 22, 23, 24, 26 and 28, and
row 25 with its re-stamp.

Row 24 was split out of row 22: the old row 22 also named the cold agent check, which the raw-PASS patch does not
change.
