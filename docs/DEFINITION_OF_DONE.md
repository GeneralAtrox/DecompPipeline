# Definition of done

DecompPipeline (DP) exists so that every console the user chooses to support gets a full toolset and an extremely
optimised workflow: no duplicate work, not a second spent unless it is productive, so that very complicated projects
can be taken on. Too Human (Xbox 360, about 94,000 functions) is the baseline project.

DP is **done for a console** when all five gates below are green for it. DP is **done** when every supported console
is done and gate 5 is green on Too Human with the Xbox 360's own measured rates.

`tools/scorecard.py` measures every gate from data DP already keeps and prints one table per console. The numbers it
judges against live in one file, `tools/scorecard/spec.json`; this document explains them.

```
python tools/bounded.py 600 python tools/scorecard.py --pending prewave-judge divergence [--json out.json]
```

`--pending REF` also looks for a source on a branch that has not landed; such a source counts as partial, never as
done. `--projects-dir DIR` is the folder the spec's game roots are relative to (config key `projects_dir`, else DP's
parent folder). The tool only reads.

## How a gate is coloured

Each check has a number, a target and a source.

| Check status | Meaning |
|---|---|
| pass | meets the target, from a landed source |
| partial | close (a share at 90% of the target or more; a cost up to twice the target), or the source is on a branch or outside DP |
| fail | far from the target, or the piece does not exist |
| unmeasured | DP has no source for this number yet; the missing source is named, and no number is shown |
| info | context only (for example the reference game's name); never counts |

A gate is **green** when every check passes, **red** when any check fails, and **amber** otherwise. An unmeasured
number can never make a gate green.

## Supported consoles

| Console | DP target | Game roots (under the projects folder) |
|---|---|---|
| Dreamcast | `dreamcast_sh4` | DreamCast/ToyCommander, DreamCast/Spawn In the Demon's Hand, DreamCast/PowerStone2 |
| Saturn | `saturn_sh2` | SegaSaturn/Dragon Force, SegaSaturn/Dragon Force 2, SegaSaturn/SaturnBomberman, SegaSaturn/Robopit (Grandia was only a pilot and is not scored) |
| GameCube | `gamecube_ppc` | GameCube |
| Xbox | `xbox_x86` (not built) | Xbox |
| Xbox 360 | `xbox360_ppc` (not built) | TooHuman |

A workspace is any folder under a root holding `project.json` and `state/ledger.json` whose target matches. Pilot
workspaces of one binary (same `binary_sha256`) count each function once.

## Gate 1: full toolset

Every stage exists in DP and runs without a person. Number: stages present, out of 9. Source: files in the DP
checkout (`targets/<target>/…`, `core/`, `port/`), probed as listed in the spec.

| Stage | Present when (the contract a new stage must meet) |
|---|---|
| disc extraction | a disc tool in `targets/<target>/tools/` (`gdrom.py`, `satdisc.py`, …) |
| catalog | `targets/<target>/ghidra_scripts/TcCatalog.java` |
| oracle and harness | `TcPrepare.java`, `TcOracle.java` and `runtime/runner.c` for the target |
| own translator | `targets/<target>/translator/` (Saturn's sh2-translate, GameCube's ppc-translate and Dreamcast's sh4-translate were imported on 2026-10-22). A translator of the user's own not yet imported into DP counts as partial until it is; a third-party translator (dcrecomp, DolRecomp, XenonRecomp, xboxrecomp) never counts |
| judge | the target's `target.json` and `runtime/tc_rt.c`, with `core/check.py` and `core/compare.py` |
| agent only on translator FAIL | `core/translate_stage.py`, and a `translator` entry in the target's `target.json` |
| readable C | `targets/<target>/runtime_rd/rd_runner.c` |
| port build | `port/runtime/<cpu>_rt.c` or `targets/<target>/port/` |
| lockstep divergence finder | `port/tools/lockstep.py` and the automated finder `port/tools/divergence.py`, on top of a port |

## Gate 2: disc to working game, hands-off

A reference game (the console's furthest-along game) goes from disc to a running port with nobody touching it.

| Check | Target | Source |
|---|---|---|
| raw verified: matched / functions in scope | 100% | each workspace's `state/ledger.json`; scope = core.audit's game ranges minus the excluded tier |
| readable verified, of matched | 100% | `state/readable.json` (status `readable` with a `verified` stamp) |
| final audit | PASS | the newest `state/final_audit-*.txt` (`tools/final_audit.py`) |
| port builds | yes | the game's `port/game.json` |
| zero divergence | 0 diverged, 0 float-close, 0 errors, at least 1 clean scenario | `state/divergence.json` |
| manual operator steps | 0 | `state/operator_steps.jsonl` of the game's workspaces and of its port folder (`port/state/`), written by DP's drivers (core/oplog): every hand start after the first, every stop that waits for a person, every act of a person on a running driver (the monitor page's buttons, Ctrl+C, `wave.py stop`/`release`). Counted since the log began; to measure one end-to-end run, start it with those logs moved aside. Unmeasured while no log holds a record, and at 0 steps unless one run id has records in every workspace of the game and its port folder |

## Gate 3: no waste

| Check | Target | Source |
|---|---|---|
| waste audit open findings | 0 (a fixed finding, or one proven unavoidable, is closed) | the newest `docs/WASTE_AUDIT_*.md`, its findings table's Status column. One still in another worktree is reported as not landed |
| agent tokens per finished function | 20,000 or less (raw + readable) | ledger and readable.json attempts |
| agent cost per finished function | USD 0.005 or less (raw + readable) | ledger and readable.json `cost_usd` |
| agent seconds per matched function, raw | 10 s or less | ledger attempts' `duration_s` |
| prepare time per function, median | 2 s or less | `state/prepare_timing.jsonl` |
| idle share | 10% or less | `state/worker_busy/*.log` (core/busylog): of the time each worker (the server's, `decomp.py investigate`'s, `refine.py run`'s) exists, the share not spent in a Ghidra prepare, the translator, an agent session or a judge/build. Waits for a slot, a prefetch, a rate or usage limit or a claim, and the bookkeeping between steps, are idle. Only worker lives that recorded their end count (a killed process loses the idle time after its last event). Unmeasured while no log exists or none ended (the 52% of the 2026-10-20 speed-up study was a one-off) |

The cost targets assume the translator does the raw C and an agent runs only on a translator FAIL; at today's
agent-on-every-function rates they fail by design, and that is the point of the translator goal.

## Gate 4: trust

| Check | Target | Source |
|---|---|---|
| pre-wave item 1, targeted vectors | landed | `targets/<target>/ghidra_scripts/TcBound.java` |
| pre-wave item 2, stricter judge | landed | `core/strict_judge.py`, `targets/<target>/runtime/tc_strict.h` |
| pre-wave item 3, coverage in verdicts and mutation audit | landed | `TcDirs.java`, `tools/mutation_audit.py`, `tools/mutation_audit/baseline.json` |
| mutation-audit kill rate, lowest sampled workspace of the console | 85% or more | `tools/mutation_audit/baseline.json`, counted as `tools/mutation_audit.py` counts it (lint and build drops left out) |
| zero divergence on every port | 0 not clean | every game's `state/divergence.json` |

The 85% minimum sits above today's baseline (74.8–85.1%, 2026-10-22) on purpose: the targeted vectors and the
stricter judge exist to raise it, and the audit's own rule (a baseline only moves up) keeps it there.

## Gate 5: scale (Too Human)

A Too Human-sized project fits a stated budget, projected from the console's own measured rates.

| Check | Target | Source |
|---|---|---|
| baseline size | (a count) | `External/xenon-dp/DESIGN.md` (XenonRecomp's full run: 94,279 functions) |
| projected agent cost | USD 500 or less | functions x (raw + readable cost per finished function) |
| projected machine hours, raw stage | 72 h or less | functions x max(agent seconds / `max_workers`, prepare median / `ghidra_parallel`), both from `config.json` |
| projected machine hours, raw + readable | 72 h or less | the raw hours above plus functions x readable agent seconds per readable function / `max_workers`: readable.json attempts' `duration_s` (recorded from 2026-10-22), over the rows whose every agent attempt carries one; rows only the machine made readable (draft, sync, look-alike) are left out, so the figure is an upper bound |

Scope: disc to 100% verified raw C and readable C. The port and lockstep are not projected. A console with no rates
of its own gets an info row at the best-measured console's rates, for scale only; it never counts.

## The targets (approved by the user, 2026-10-22)

The user approved the targets in `tools/scorecard/spec.json` as proposed on 2026-10-22:
- per function: tokens 20,000, cost USD 0.005, agent time 10 s, prepare 2 s;
- idle 10%;
- kill rate 85%;
- Too Human within 72 h and USD 500.

Changing one is a one-line edit to the spec, and it needs the user's yes like any other change to the definition of
done. The scorecard picks it up on its next run.

## Records DP keeps for the scorecard

Three records exist only so these gates can be scored. They never change a verdict (tests/test_records.py: the
judge's output is byte-identical with them on and off), and `DP_NO_RECORDS=1` turns the two logs off.

| Record | Written by | Cost |
|---|---|---|
| `state/operator_steps.jsonl` (gate 2) | each driver's start and stop (core/oplog) | one line of about 150 bytes and under 1 ms per hand start, stop or act |
| `state/worker_busy/PID-START.log` (gate 3) | each worker, around its real work (core/busylog) | one line of about 19 bytes and under 0.5 ms per event; about 10 events (under 200 bytes, a few ms) per function: about half a megabyte for a whole console |
| `duration_s` on readable attempts (gate 5) | core/refine, as core/job does for raw attempts | one number per attempt |

Each number stays "unmeasured" until its record exists for the console. A source DP does not keep yet: a
`state/divergence.json` for each game with a port; today only Toy Commander has one, from the unlanded divergence
branch (gates 2 and 4).
