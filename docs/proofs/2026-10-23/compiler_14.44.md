# Proof: moving the judge's compiler to MSVC 14.44 on both laptops (2026-10-23)

**Result:** 440 fresh judgements per run, on four consoles.

- **Today's judge against the new toolchain.** Today's judge is cl 19.38.33145 with Windows SDK 10.0.28000.1839. The new toolchain is cl 19.44.35229 (MSVC 14.44.35207) with SDK 10.0.28000.2114. Every verdict, score, failing count, vector list and first difference is identical. The exceptions are two known-noise values and the wording of one compiler warning.
- **This laptop against laptop2.** Both on the new toolchain, the two laptops give identical judgements, with the same two noise values masked.

The judge was not changed. Only the toolchain it runs with changed, and the live judge was not switched; that happens at landing.

## The toolchains

| | Today's judge (this laptop) | Target (both laptops) |
|---|---|---|
| vcvars | VS 2022 Community `vcvars64.bat`, no arguments | `vcvars64.bat 10.0.28000.0 -vcvars_ver=14.44` (and vcvars32 for x86) |
| MSVC toolset | 14.38.33130 | 14.44.35207 |
| cl, x64 and x86 | 19.38.33145 | 19.44.35229 |
| Windows SDK | 10.0.28000.1839 | 10.0.28000.2114 |

- **Where the 14.38 came from.** Unpinned, vcvars prefers `VC\Auxiliary\Build\Microsoft.VCToolsVersion.v143.default.txt` over the newest toolset, and on this laptop that file names the side-by-side 14.38.33130. Every live workspace's cached environment said 14.38 too.
- **Laptop2's vcvars.** Laptop2 runs its `vcvars_v143` wrappers (in its tools folder), which call VS 2022 Build Tools with the same arguments. The wrappers used here are in `compiler_14.44/vcvars/`.
- **Both builds of SDK 28000 install to the folder `10.0.28000.0`.** This laptop's SDK was upgraded in place from 1839 to 2114 so it matches laptop2's.
- **Hashes.** These are the SHA-256 content hashes of the SDK folders, identical on both laptops (measured by the coordinator session):

| SDK folder (Windows Kits\10\…) | hash |
|---|---|
| Include\10.0.28000.0\ucrt | CD894C2CC3743F4A |
| Include\10.0.28000.0\um | 5F07055EC6CD75B5 |
| Include\10.0.28000.0\shared | 0431B380A71CF6EE |
| Lib\10.0.28000.0\ucrt\x86 | F7F7812A6A5B6C22 |
| Lib\10.0.28000.0\ucrt\x64 | 35B067BE8B89EF72 |
| Lib\10.0.28000.0\um\x86 | 1523B571BBE0E970 |
| Lib\10.0.28000.0\um\x64 | BF2F795DA96B4761 |

- **vcvars must exit cleanly.** Before judging, every target run ran both vcvars (64 and 32) with their output kept. A nonzero exit or any `[ERROR:` line failed the run. This check rejected an earlier target, SDK 10.0.26100.0: on this laptop that SDK was only the driver kit's partial copy (no `Windows.h`, no ucrt), and vcvars had silently built a mixed environment from it.

## The sample (results.tsv)

Each function's live source is snapshotted into a private frozen copy of its workspace. Every run judges the same copy, with the same files. The copy holds:
- the project file and the binary;
- the state the judge reads (no ledgers);
- the sampled functions' harness/ and packets/ folders;
- the C files.

| Console | Judge code | Raw C (x64 judge) | Readable C (x86 judge) |
|---|---|---|---|
| Toy Commander (SH-4) | DP d5cd42c | 34 matched PASS, 6 earlier failed attempts FAIL (incl. the current failure 8c07564c) | 20 PASS |
| Dragon Force 2 ws-btl (SH-2) | DP d5cd42c | 40 sh2-translate outputs: 32 PASS, 8 FAIL | — |
| Skies decomp-gekko (GameCube) | DP d5cd42c | 34 matched PASS, 6 FAIL (the 4 failed, 1 stopped, 1 earlier attempt) | — |
| Too Human (Xbox 360) | xbox360-ppc 6505588 | 40 xenon-translate (66b6b4a) outputs: 39 PASS, 1 FAIL | — |

- **How the sample was picked.** Functions are evenly spaced by address. Too Human's 40 were chosen from 80 screened functions, and only 1 of the 80 failed. The pickers are `pick_tc.py`, `pick_df2_skies.py`, `pick_th.py` and `gen_th.py`.
- **Fresh judging.** No verdict memo is read: `judge.py` calls `core/check.run_check` (the target's hook where it has one) and `core/readable_check.check_readable` directly. Before every run it deletes the copy's cached compiler environments (`state/msvc_env*.json`) and its whole `build/` folder, which includes the runtime objects.
- **Banners.** Every run records the cl banners of the environments the judge itself loaded, together with VCToolsVersion and WindowsSDKVersion.

## The comparisons

Every field of every judgement was compared (`compare.py`), except its timing. That covers verdict, stage, score, failing/total, the per-set vector lists, the first differences, lint and build messages, run errors and coverage.

| # | Comparison | Runs | Result |
|---|---|---|---|
| 1 | Compiler alone: 19.38 against 19.44.35229, both on SDK 1839 (this laptop) | base against after_pin | identical except the C4100 wording |
| 2 | **Today's judge (19.38 + SDK 1839) against the target here (19.44.35229 + SDK 2114)** | base against t28 | **identical except the C4100 wording** |
| 3 | **Target here against target on laptop2** | t28 vs l2a, t28b vs l2b | **identical** |
| 4 | Run-to-run noise, today's judge | base vs base2 | identical |
| 5 | Run-to-run noise, target here | t28 vs t28b | identical |
| 6 | Run-to-run noise, target on laptop2 | l2a vs l2b | identical |
| 7 | Today's judge, SDK 1839 against 2114 (this laptop's in-place SDK upgrade) | base vs today2114 | identical |

All seven comparisons hold with two maskings: the noise below, and the private copy's folder, which differs between laptops (`<copy>/` in compare.py).

**The only real difference:** Skies 801f6120 is a stopped attempt that fails to build under both compilers, with the same verdict, stage and score. MSVC 19.44 reworded warning C4100 from "unreferenced formal parameter" to "unreferenced parameter". That wording is part of the build messages an agent reads.

**The noise:** Toy Commander 8c03054c and 8c058938 are FAILs whose C passes uninitialised stack words to a call. The "got 0x…" values in their first differences are host stack contents, one of them the upper half of a randomised host address (0x7ff6 or 0x7ff7). They change on every run, under the same compiler. Their verdicts, scores, failing vectors and expected values never change. compare.py masks only those values, and only for those two functions.

## What this proof does not cover

- **The GameCube byte-match check (ppc_match).** It uses Metrowerks mwcc, not MSVC, and DP master's config has no `mwcc_root`. In every run it reports the same configuration error for Skies 801f58b8, whose verdict comes from the behaviour check alone.
- **The port builds.** CMake builds the ports with its own toolset choice. Only the judge's compilers were measured here.
- **Infrastructure:** Python 3.12.x on both laptops.

## Run notes

- **One failed run, redone.** The first target pass for Toy Commander here hit 97% RAM; the user's game was running. It paused, as the RAM rule requires, until its one-hour limit ended it. It was re-run in full, and that rerun is t28.
- **No results from the earlier attempt.** The first attempt at the target, with SDK 26100, produced no judgements on this laptop because vcvars failed. Laptop2's SDK-26100 runs, and this laptop's runs on SDK 1839 with the explicit 28000 argument, were also identical to the target. They are not part of the proof, since neither is the final toolchain.

## Reproducing

All the scripts are in `compiler_14.44/`.
1. `setup_mirror.py` makes a frozen copy per workspace from a sample.
2. Judge each copy with one of:
   - `judge.py --dp <DP checkout> --mirror <copy> --out <run>.json --vcvars <vcvars64.bat>`, for a pinned toolchain;
   - `judge.py ... --toolset configured`, for the config's vcvars.
3. `run_l2.py` runs all four copies in turn under laptop2's config, each run time-limited.
4. `compare.py A:toolset B:toolset` compares two runs.

`results.tsv` lists each judgement's verdict, score and failing count, which are the same in all five runs (base, t28, t28b, l2a, l2b).
