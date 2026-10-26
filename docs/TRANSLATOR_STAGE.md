# The translator stage: translator first, agent only on a FAIL

A target's own machine-code-to-C translator writes each function's raw C before any agent attempt. DP's judge,
unchanged, decides that C exactly as it decides an agent's. A translated PASS is a match that costs no agent tokens.
Only a FAIL goes to an agent, and the agent starts from the translated C and its first differences.

## Where it sits

In `core/job.investigate`, for one function:

1. prepare the harness (Ghidra), or reuse a current one; skip the function if the oracle made too few valid vectors;
2. attempt 0, as before: an import (`import/ADDR.c`) or the function's own C from before a requeue
   (`matched_stale/ADDR.c`); a PASS ends the job;
3. **the translator** (this stage): the translator writes the C, the official check judges it (dev + hidden); a PASS
   ends the job;
4. agent attempts 1.., as before.

The stage is off, and the job is exactly as it was, when any of these holds: the target has no `"translator"` entry,
this machine gives no folder for it, config `"translate_first"` is false, or a project sets `"translator": null`.

## Configuration

`targets/<target>/target.json` names the command. The translator is part of DP, in `targets/<target>/translator/`
(Saturn: sh2-translate; its origin repository and commit in `ORIGIN.md` there):

```json
"translator": {
  "name": "sh2-translate",
  "command": ["{python}", "{target}/translate.py", "--repo", "{repo}", "--binary", "{binary}", "--addr", "{addr}",
              "--meta", "{meta}", "--target-json", "{target_json}", "--bodies", "{bodies}", "--out", "{out}"],
  "timeout_s": 120
}
```

`config.local.json` (git-ignored, this machine) may point a translator at another folder instead, such as a
development checkout; without an entry the stage uses `targets/<target>/translator/`:

```json
"translators": {"sh2-translate": "<another sh2-translate folder>"}
```

Placeholders: `{python}` (DP's interpreter), `{tool}` (DecompPipeline), `{target}` (the target folder), `{repo}` (the
translator's folder), `{workspace}` (the workspace folder), `{binary}`, `{addr}` (8 hex digits), `{meta}` (`harness/ADDR/meta.json`: signature, parameter
storage, callees, body blocks), `{packet}` (`packets/ADDR`), `{target_json}`, `{bodies}` (`state/function_bodies.txt`),
`{out}` (the C file to write).

The contract, the same for every target:
- exit 0 with `{out}` written: that C is judged;
- exit 3: this function cannot be translated (the reason on the last output line);
- any other exit, no file, or past `timeout_s` (the whole process tree is killed): no C, the agent starts from scratch.

A target whose translator has another interface gets a small adapter in its target folder (Saturn:
`targets/saturn_sh2/translate.py`, which maps DP's inputs onto sh2-translate's `translate_function`; GameCube:
`targets/gamecube_ppc/translate.py` for ppc-translate, which also reads the workspace's other harnesses to tell
where each callee returns its result, r3 or f1, and keeps that whole-program pass's slow part in
`state/translator/`). A new console's translator (SH-4) needs only its own entry and, if needed, its own adapter.

## What is recorded

- The attempt: attempt 0 with `"model": "translated"`, `"translator"` (name), `"translator_rev"` (its git revision,
  `-dirty` with local changes; else its `REVISION` file; else `unknown`), `"cost_usd": 0.0`, `"duration_s"` (translate
  seconds), `"judge_s"`, and the judge's usual fields. Files: `runs/ADDR/translated/work.c` and `check.json`.
- The ledger row: `"translator"`: `{name, rev, ok, why, translate_s}`; `why` says why there was no C.
- A match's `"source"`: `"translated"` (the translator's C passed), `"agent"`, `"import"` or `"previous"`, so a
  translated match is never counted as agent work. Rows matched before this stage have no `source` (agent work).
- The audit's failure reasons read the agent's attempts, never attempt 0 (as for imports).
- The match goes through the same finish as any other: `matched/ADDR.c` is the translator's C unchanged, and the
  proof gates (coverage, mutation) apply.

## On a FAIL

Agent attempt 1 starts with `work.c` = the translated C, and its task carries the official result and first
differences (the usual "Previous attempt" text) and a note that a translator wrote it, so most of it is right. When an
import or the function's earlier C also failed, the one with the better score seeds attempt 1 (the earlier on a tie).

## Backlog runs without Ghidra

`tools/translate_backlog.py --project WS [--only ...] [--jobs N] [--record] [--json OUT]` runs the stage over the
harnesses a workspace already has. It never starts Ghidra. It reports, per function: the verdict; whether the harness
is current; translate and judge seconds; and the ledger's agent figures for comparison. With `--record`, a PASS of a
function not yet matched is finished as a worker finishes it; stop the server first. Without `--record` its runs and
builds go to a folder of its own (`--keep DIR` keeps them), so it runs beside a live server with no private copy.

## Pilot: Saturn, Grandia FIELD.BIN (2026-10-22, sh2-translate 0b0d26a)

On private copies of the three Grandia workspaces, using the existing harnesses only:
- **122 distinct functions** (185 counting the overlaps between workspaces). All passed from the translator alone:
  0 failed, and every function got C. That includes 3 that no agent had matched.
- **Per function:** 0.22 s to translate, 0.28 s to judge, 0 tokens. The agents took a 15.7 s session on average
  (median 12.1 s, max 254 s) on the same 119 functions in ws-1pct8: 4.6 M input tokens, 198 k output, USD 0.20.
- **Not tried:** 34 harnesses with too few valid vectors. 32 are ws-field harnesses made before the Saturn pool-load
  fix (31 of those functions passed in ws-1pct8 with a newer harness); 2 fault on hardware reads. Only 2 distinct
  functions are left: 060398ae and 060398cc.
- **Re-prepare of those 2 (Ghidra, private work dirs, today's oracle):** still 0 of 384 valid vectors each (35 s for
  both). Both are busy-wait loops on a byte read of the SH-2 on-chip register FTCSR (0xfffffe11, bit 7 ICF, the flag
  the other CPU's MINIT/SINIT raises). The oracle refuses any hardware read, so no attempt can be judged (agent or
  translator). Listing the read in `hw_ops` with a `hw_reads` value, on a private copy only, did not help: the byte
  read is not served the value, every vector spins to the step limit, and the function is refused (858 s).
  `hw_ops` models 32-bit reads only (`tc_hw_read32`). sh2-translate does translate both functions (to `rd8` of the
  register). Covering them needs byte-sized listed hardware reads in the oracle, runtime and translator. That is a
  judge change, so it needs a decision. Also, 060398ae stores to MINIT at 0x21000000, which Saturn's target.json
  `memory.mmio` does not list as hardware.
- **Caveat:** these are the easiest functions (DP's queue takes easy functions first). Harder functions will show the
  stage's FAIL path.
