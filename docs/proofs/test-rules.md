# Test rules: where expected values come from, and proof that a fix's check fails first (2026-11-01)

The user's two rules, in full in `AGENTS.md` "Test rules". The ideas come from zkit-gcnx, which is GPL-3. Only the concepts as the
brief stated them were used: none of its code or files were read.

## Survey

`tools/golden_survey.py`, on this date. "No original source" means the file is the port's or a tool's own output.

| Where | Files | No original source | Original evidence |
|---|---:|---:|---:|
| Toy Commander goldens | 35 | 35 | 0 |
| Toy Commander log baselines | 6 | 6 | 0 |
| DP translator baselines and verdict snapshots | 10 | 10 | 0 |
| DP `test_lockstep.py` `TIMER_GOLDEN` | 1 | 1 | 0 |
| DP `tools/mutation_audit/baseline.json` | 1 | 1 | 0 |
| DP `golden_words.json` (Xbox 360 decoder) | 1 | 0 | 1 |
| **Total** | **54** | **53** | **1** |

**All 53 are now labelled `native-baseline`, because each can be shown to be own output:**

- Toy Commander: `regress.py --accept-*` copies the port's output, and its git log says so (5319bb1 "accepted by the user", then
  d467c6e and b86bbcc "new goldens for DP master"). A check against these frames supports no fidelity claim. They were approved
  by the user's eye, which the label records.
- `targets/*/translator/regress/*.json` (6): `tools/regress.py --update` writes the translator's own verdicts and C hashes.
- `targets/dreamcast_sh4/translator/results/*.json` (4): a day's verdicts on that output.
- `TIMER_GOLDEN`: `lockstep.c`'s own output at 2492861. `mutation_audit/baseline.json`: the audit's own counts.

The files themselves were not changed. The records are in `tests/goldens_provenance.json` and Toy Commander's
`port/tests/{goldens,baseline}/provenance.json`.

**Proved original.** `golden_words.json`: re-run here, `check_decoder.py` compared the decoder with a reference disassembler on
3,683,202 of Too Human's code words: 0 illegal, 0 operand disagreements (`test-rules/x360_check_decoder.txt`, 49 s).

**Other ports.** Power Stone 2, Spawn, Vigilante 8, Robo Pit and Skies of Arcadia have no goldens or log baselines. Their
"expected" is the harness, or nothing. Five other folders hold old copies of Toy Commander's (`play/src`, `play/tc_regress/*`,
`coord/_to_delete_*`), not counted.

**The judge's expected files are original.** 14,952 `harness/*/expected.txt` in the live workspaces come from the oracle, which
runs the original machine code; their record is the meta's `oracle_stamp`. 14,949 have one. The other 3 are Spawn ovl 8c01340e,
8c04aa78, 8c05b8dc, dated 2026-10-11. No tool writes a port's output there; that was read from the code, not proved by a plant.
Toy Commander also has 5 emulator reference recordings (`build/reference/*/manifest.json`), used by lockstep and hw_check.

**What the survey can't see.** It finds tracked files by name (`golden`, `baseline`, `regress/*.json`, `translator/results`) and
`*_GOLDEN =` in tests, plus each named port's two folders. A value pasted into a test under another name is found by nobody.

## Rule 1: the checks

- `port/tools/regress.py` fails a golden or log baseline with no record, or labelled original and not the file recorded.
- `--accept-frames` and `--accept-log` label the file `native-baseline`, and fail over original evidence, keeping the file.
- Its last line counts frames compared against original evidence and against native baselines ("no fidelity claim"). For Toy Commander
  today, that will read 0 and 35. A native baseline's bytes aren't pinned, since it claims nothing.
- `provenance.Ledger.original` refuses a file a port run writes as evidence, and needs the evidence's hash.
- `tests/test_test_rules.py`, part of the standard gate `tests/test_tooling.py`, fails any golden in DP with no record, so a new one must be labelled to land.

Not machine-checked: that an emulator-capture label is true when the evidence is any other file. The label needs a named file and
its hash, so a false one is on the record, not impossible.

## Rule 2: the record

`tools/fails_before.py prove` checks out the base in a throwaway worktree, about 2 s, overlays the changed test files, runs the check
there and then here, and prints a commit trailer. That trailer is the whole record. A check that passes on the base, dies on a name
the base lacks, runs out of time, or fails after the fix too, gets no trailer. A port's check names its own unfixed build with
`--unfixed-cmd`, so nothing is built twice. `check A..B` fails a commit whose subject reads as a fix with no trailer; `--rerun` runs
each trailer again on the commit's parent.

On a real commit (b2d754c6, the PowerPC compare), 8 s for both runs:

```
Fails-before: cdd5dcb13666 py -3.12 -m pytest -q tests/test_ret_width.py::PowerPc => E           AssertionError: 0 != 1 : {'r3'}
```

An unrelated test on the same base: `refused: it passes on the unfixed build (cdd5dcb13666), so it did not reproduce the bug`.

**Limit.** The gate sees only subjects that say fix, bug, regression, crash or hang: 26 of master's last 400, because most subjects
describe the behaviour instead. So a behaviour-worded fix without a trailer lands. AGENTS.md still asks for it, and dp-land can ask.
It's also not retroactive: commits before `RULE_SINCE` aren't asked. A landed miss goes in `tests/fails_before_waived.txt`.

## Planted faults

`tests/test_test_rules.py`, all caught:

| Plant | Result |
|---|---|
| `--accept-frames` over an emulator capture | fails, file unchanged |
| A port frame copied by hand over an emulator capture, close enough to pass `frames.compare` | fails, no longer fidelity |
| A golden with no record; a log baseline with no record | fails |
| Port output (three forms) labelled as an emulator capture | refused |
| Hand-edited record: unknown source, no evidence | fails |
| New golden file or `*_GOLDEN` in DP | found, and fails until labelled |
| Check that passes on the unfixed build | refused |
| Check that only misses a name the fix added | refused |
| Check that still fails after the fix | refused |
| Fix commit with no trailer; `n/a` with no reason | `check` fails |
| Forged trailer | `check --rerun` fails |

The controls pass: a real capture is accepted, a real trailer survives `--rerun`, and an unrelated feature commit isn't asked.

Not run: `regress.py` on a real Toy Commander build. Its tests use a fake port that writes a frame and a log. The judge's compare is
untouched; `regress.py` only gains failures.
