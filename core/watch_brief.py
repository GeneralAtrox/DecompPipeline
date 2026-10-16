"""WATCH.md: the task given to the watcher agent (Opus) for one function the oracle could not test."""
import json

WATCH = """# WATCH: make the oracle able to test fn_{addr}

You are the oracle watcher in an automated decompilation pipeline. A machine judges your work.
Explanations and claims are ignored; only the result of the server-side gate counts.

## Background

The pipeline tests decompiled C against the ORIGINAL SH-4 machine code, which runs in Ghidra's emulator
(the oracle). For each function the oracle generates random inputs (registers, scratch memory, globals,
call return values), runs the original code on each one and records what it does. A run that faults
(touches memory outside RAM) or hits the step limit is not a usable test vector.

fn_{addr} was SKIPPED: the oracle could not produce {mv}+ valid vectors per set.
Last oracle summary (target/oracle_summary.json):

    dev valid {dev_valid} of {dev_tried} tried, hidden valid {hidden_valid} of {hidden_tried} tried
    pointer repairs used: {repaired}
    blocks reached: {blocks_hit_all} of {blocks_total}
    invalid runs by reason: {invalid}

Example failed runs (the loads just before the fault are listed; values that were used as pointers):

{examples}

## Your job

Change how test INPUTS are generated so that most runs of fn_{addr} are valid and reach its code,
without weakening the test for any other function. Inputs only: expected results always come from
running the original code, so a correct generator cannot make wrong C pass.

Typical causes and fixes:
- an argument or a field is a pointer to a structure, and a random value is not a valid address:
  write a hint `ptr r4+0x10c`, or improve the pointer repair in repairVec;
- a pointer chain through a global: `ptr 0x8c0c78a0+0x1c8` (the word at *(0x8c0c78a0)+0x1c8);
- a loop count or index is random and huge: `range r5 1 12`;
- something no existing hint form expresses: add a gen* helper to TcInputs.java, plus (if per-function)
  a new hint form `gen<name> ARGS` that applyHints understands.

## Files in this directory

- `TcInputs.java`: the input generator. You may Edit it. It is shared by EVERY function.
- `hints/{addr}.txt`: per-function hints for fn_{addr} only. You may Edit it. Format is in TcInputs.java.
- {locked}: the locked oracle (read-only; read them to understand
  how TcInputs is called: makeVec, applyHints, repairVec).
- `target/asm*.txt`, `target/ghidra*.c`: the function's disassembly and Ghidra's decompilation.
- `target/meta.json`, `target/oracle_summary.json`: its signature and the last oracle result.
- `wtool.py`: your only tool for running things (see below).

## Rules (all enforced; breaking one rejects your change)

- W1. Edit only TcInputs.java and hints/{addr}.txt. Do not create, rename or delete files.
- W2. TcInputs.java is checked by an allow-list linter before it is compiled: the only import is
  `java.util.*`; no I/O, reflection, threads, lambdas, annotations, try/catch, Ghidra API or script state;
  only allow-listed library calls; new class-level members must be named gen*, Gen* or GEN_*; nested
  classes hold fields only; one field per declaration; never assign rnd and never use Random.
- W3. Never try to tell dev vectors from hidden vectors, never make vectors repeat, and never generate
  inputs that avoid the function's real work (coverage and distinct results are checked).
- W4. Never weaken inputs for other functions. The gate regenerates tests for reference functions and
  a sample of already-matched functions with your generator: their accepted C must still pass, coverage
  must not drop, and a deliberately broken C must still fail.
- W5. Do not try to read or reach anything outside this directory. Every attempt is logged.

## Tools

Run each of these exactly as written (use a Bash timeout of 600000 ms):

- `python wtool.py try`  : lint, then run the oracle on fn_{addr} with your current files and show the
  new summary (valid vectors, faults, coverage, examples). Takes 1-10 minutes. Use it as often as needed
  (up to {max_trials} times).
- `python wtool.py gate` : the full strict gate (target + regression). If every check passes, your change
  is promoted and fn_{addr} is queued again. At most {max_gates} gate runs per session.
- `python wtool.py wait` : if a command printed "STILL RUNNING", this keeps waiting for the same result.

## Procedure

1. Read target/oracle_summary.json, the examples above, target/asm*.txt and TcInputs.java.
2. Work out which input is invalid (which register or memory word becomes the bad pointer or count).
3. Prefer the smallest fix: a hint in hints/{addr}.txt. Change TcInputs.java only for a general
   improvement that helps many functions.
4. `python wtool.py try` until the target has enough valid vectors and good coverage.
5. `python wtool.py gate`. If it fails, read the failing checks, fix, and try again.
6. Final message: exactly one line, `RESULT: PROMOTED` or `RESULT: GAVE UP <one sentence>`.
"""


def watch_md(cfg, addr, summary):
    s = summary or {}
    ex = s.get("examples") or []
    examples = "\n".join("    " + str(e).replace("\n", "\n    ") for e in ex[:6]) or "    (none recorded)"
    return WATCH.format(
        addr=addr, mv=cfg["min_valid_vectors"], dev_valid=s.get("dev_valid"), dev_tried=s.get("dev_tried"),
        hidden_valid=s.get("hidden_valid"), hidden_tried=s.get("hidden_tried"), repaired=s.get("repaired", 0),
        blocks_hit_all=s.get("blocks_hit_all"), blocks_total=s.get("blocks_total"),
        invalid=json.dumps(s.get("invalid") or {}), examples=examples,
        max_trials=cfg.get("watcher_max_trials", 12), max_gates=cfg.get("watcher_max_gates", 3),
        locked=", ".join(f"`{n}`" for n in cfg["locked_scripts"]))


def format_result(kind, res):
    """Text shown to the agent for a try/gate result."""
    out = []
    s = res.get("summary") or {}
    if s:
        out.append(f"oracle: dev valid {s.get('dev_valid')}/{s.get('dev_tried')}, hidden valid "
                   f"{s.get('hidden_valid')}/{s.get('hidden_tried')}, blocks {s.get('blocks_hit_all')}/"
                   f"{s.get('blocks_total')}, repairs {s.get('repaired', 0)}, hint lines {s.get('hints', 0)}")
        out.append("invalid runs by reason: " + json.dumps(s.get("invalid") or {}))
        for e in (s.get("examples") or [])[:4]:
            out.append("example: " + str(e)[:1200])
    for name, ok, detail in res["checks"]:
        out.append(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if kind == "gate":
        out.insert(0, "GATE PASSED - change promoted; " + res.get("archive", "") + ". Your work is done."
                   if res.get("promoted") else "GATE FAILED - nothing was promoted.")
    else:
        out.insert(0, "TRY " + ("looks good for the target (the gate also runs regression)"
                                if all(c[1] for c in res["checks"]) else "has failing checks"))
    return "\n".join(out)
