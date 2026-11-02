# Proposal: four more ideas from nativeshvc for the port kit (nothing built; waits on the user's yes)

Source: the README and the `docs/` pages and `tests/README.md` of github.com/jeff-strutb/nativeshvc, read as prose on
2026-10-30 (AGPL-3.0: ideas only, no code read, fetched or copied). The first kitlearn round took the contract, the
KNOWN list, the enhancements table, the no-game-data refusal and the script comparison (`port/CONTRACT.md`,
`port/KNOWN.md`, `docs/proposals/kit_conformance.md`, `docs/proposals/kit_original_pacing.md`). This page covers what
that round did not: the parts of the pages it had not read. Games below: TC (Toy Commander), Spawn, PS2 (Power
Stone 2), V8 (Vigilante 8). I have not counted their scripts or recordings; where a benefit depends on that, it says so.

Each idea: what it is, what DP has that is closest, the benefit, the cost, a recommendation.

## 1. A cost profile measured per access, never typed in

**What it is.** nativeshvc's console adds time to every bus access of the game's C, by kind (work RAM read, ROM read,
register write and so on). The weights come from a tool that runs the original on the reference emulator, counts the
accesses per frame by kind and fits the weights to the real frame lengths (non-negative least squares, only on frames
where the CPU was busy for at least 5%; the fewest non-zero weights win a tie; a kind with under 1,000 samples gets
0). The profile is a small versioned text file holding the tool version, the reference build, the ROM hash and the
counts and weights; a `check` command exits non-zero when the source hash no longer matches (the profile is stale).
A report gives the bias, mean error, 95th percentile and the slowest 5% of frames, and flags a poor fit. Frame rate is
only ever a check on the result, never an input to the fit. "Original" pacing switches the weights on; "Enhanced"
switches them off at run time, within a frame.

**Closest in DP.** `docs/proposals/kit_original_pacing.md`: the same goal, but its cost unit is the *frame* (how many
vertical blanks Flycast took, from the lockstep steps file) and its model keys on the TA list size. The kit has no
per-access hook: readable C reads guest RAM through plain loads, so counting accesses would need an instrumented
build of the readable C (not designed; I did not check how translator C could count them).

**Benefit.** Per access, for the Dreamcast, is the wrong unit: slowdown on this console is mostly the GPU, the TA
list size and disc loads, not CPU bus accesses, and the SH-4's caches make per-access cost depend on state the port
does not model. So the per-access fit would be a poor model for TC, Spawn, PS2 and V8 alike. What does transfer is the
*discipline*, which the pacing proposal does not yet state:

- a versioned `pacing.txt` header with the reference build, the recording's commit and input hash, and a `check` that
  reports it stale when the game or the recording changed;
- a fit report with bias, mean error, 95th percentile and the slowest 5% of frames, and a threshold above which the
  profile is refused (flagged POOR) instead of used;
- fit on the frames where the cost was real (a frame that missed its blank for a disc load is left out, as nativeshvc
  leaves out waits and DMA);
- the frame rate used only to check the result.

**Cost.** Wording and about 60 lines in the planned `port/tools/pacing_profile.py` (the report and the stale check).
No new tool, no new run.

**Recommendation.** Do not build a per-access profile. Add the four rules above to the pacing proposal's design when
the user says yes to that proposal. Nothing to do until then.

## 2. A reference-compare count and a KNOWN list the runner enforces

**What it is.** nativeshvc compares 1,164 generated test cartridges against bsnes access for access (every read, every
picture, the length and end of every DMA) and publishes the count as three buckets that add up: 1,108 match, 42 differ
by a documented choice of the contract, 14 differ and are not yet explained. The 56 are in a plain-text `KNOWN` file,
one line each (test name, reason), in two labelled groups: *chosen by the contract* and *not yet explained*. The runner
reports a test in KNOWN as KNOWN, never as passing, and reports a KNOWN test that starts passing, so the file cannot
go stale. Each check was shown able to fail (a planted error caught) before it counted. The test cartridges are built
by the project's own assembler from a case list, not copied from games.

**Closest in DP.** `tools/lockstep.py`, `tools/hw_check.py`, `tools/ram_compare.py` (per game, frame by frame),
`port/KNOWN.md` (prose; nothing reads it), `docs/proposals/kit_conformance.md` (the small hardware programs, waiting on
a toolchain download) and, for planted errors, the mutation check on the judge (`core/mutation.py`, docs/PROOF_GATES.md).
`regress.py` already fails on a log class missing from a script's baseline.

**Benefit.** Three separable pieces, each different in value:

1. *The three-bucket tally.* Per game and per script, "N frames compared against Flycast: N identical, N known,
   N unexplained" (the numbers come from the recordings; none is counted here). It turns "lockstep passes" into a number the dashboard can show, and the user can see a
   game moving. It uses what exists (lockstep recordings; I did not count how many each game has).
2. *KNOWN entries with stable ids that the tools read.* `lockstep.py compare` and `hw_check.py` would print
   "KNOWN: store-queue resend" instead of a bare difference, and "FIXED: <id> no longer differs" when an entry should
   leave the file. That is the part of nativeshvc's design that keeps the list true. It helps every game once the tools
   tag their differences with an id (the store-queue model, `pvr_todo` classes and OVERLAY MISS already have a name in
   the log).
3. *A generated corpus of 1,000-odd test programs.* The SNES project can generate them because its own 65816
   interpreter builds ROMs from a case list. The kit has no SH-4 machine code path (it runs C), so the counterpart is
   the conformance programs of `kit_conformance.md`, about ten to start; a count of ten is not a corpus.

**Cost.** (1) about 80 lines in a new `tools/conformance_tally.py`, or a mode of `lockstep_frames.py` (nearest
existing: `lockstep_frames.py`, which already pairs the frames); (2) ids in `KNOWN.md` plus about 60 lines to read them
in `lockstep.py compare` and `hw_check.py`, one working session with tests; (3) as in `kit_conformance.md`.

**Recommendation.** Yes to (1) and (2) together as one small change; they need no download. (3) stays as the existing
proposal: yes only with the toolchain. For checks, require the planted-error test the SNES project asks for: each new
comparison gets one case where a deliberately wrong kit input makes it report a difference (`tests/test_lockstep.py` already
plants a one-word difference and checks it is reported; the tally and the tag reader would carry the same kind).

## 3. Save states by replay from boot, with a digest

**What it is.** nativeshvc's scripts can `save NAME` and `load PATH`. Where the operating system can fork the process,
the saved state is a forked process; where it cannot (Windows, or when asked), the state is rebuilt by replaying from
power-on the steps that made it. The two paths must produce identical memory dumps byte for byte (a unit check). The
playbook adds: long scripts are cut every ~500 frames into saved states and the chunks that add no new coverage are
dropped.

**Closest in DP.** `runtime/session.c` (the session recording from boot, `--replay`, `--stop-at`, the scene dump's
`how_to_reach` command) already is replay from boot. The fork path is not available on Windows, so the replay path is
the only one the kit would ever use. What is missing is the *check* and the *script verbs*:

- a recording has no state digest, so a replay that went out of step is found only when a pad read lands on another
  vertical blank (`session: replay out of step`). `port/KNOWN.md` "Real-time recordings at boot" records one that went
  out of step (the boot splash's fade loop ran 33 frames instead of 9) with the cause open; a digest of the
  guest RAM every N vertical blanks, stored in the recording and compared on replay, would show the first frame where
  they differ, which narrows that cause;
- a `.script` cannot start from a recording's moment: a test of a deep scene (a late TC mission, a PS2 fight, a V8
  level) replays or plays through to it each time. `--replay FILE --stop-at V` does it from the command line but the
  script form (`seconds`, `frames`, `input`, `args`) has no line for it beyond `args`.

**Benefit.** The digest helps every game whose recordings are real-time (TC's demo that went out of step from float
rounding, per its notes; any play recording a tester sends) and pins "determinism" down as a test instead of a
claim. Start-from-a-moment helps the games whose interesting scenes are deep: I would expect V8 and PS2 to gain most,
but I have not measured how long their scripts take to reach those scenes.

**Cost.** Digest: about 60 lines in `runtime/session.c` (if it has the room: it must stay under 400 lines; otherwise a
new `runtime/session_digest.c`), a test that a changed recording is reported at its first differing frame. The script
line: about 20 lines in `tools/regress.py` plus a golden for one fixture. A long replay already runs as fast as the
machine allows on the fixed clock (`--fast-until N` hands over to real time at vertical blank N).

**Recommendation.** Yes to the digest (it also serves the open boot bug), after the user's yes. The script line only
when a game's script actually needs a late scene; today `args` carries `--replay` already, so nothing blocks it.

## 4. Preservation levels mapped to DP's stages

**What it is.** nativeshvc calls a game *fully preserved* when it has three forms, each with its own proof:

| Level | What it is | Proof |
|---|---|---|
| ASM | The decompiled source that assembles to the original ROM, byte for byte | The whole ROM rebuilt: 0 bytes differ |
| C | The same game as readable C, behaviour-exact, access for access | Two comparisons against the reference emulator (each call's inputs and outputs; the whole game in lockstep, identical every frame) |
| Enhanced | The C improved (frame rate, widescreen), built on the C, which stays | Its own checks; the original stays as the record |

**Closest in DP.** The stages already exist under other names. The mapping, which does not fit exactly:

| nativeshvc | DP | Where it is proved |
|---|---|---|
| ASM (bytes) | none: the disc's code is the oracle (Ghidra); no rebuild to the same bytes | no counterpart: DP's judge compares behaviour (registers, memory) on generated inputs, not bytes |
| C (behaviour-exact) | **raw C** (judge PASS, translator or agent) then **readable C** (named, typed, judged again) | the judge per function; `final_audit.py`; lockstep for the whole game against Flycast |
| Enhanced | **the port** with its enhancements on (`--present interp`, widescreen, texture pack: `port/CONTRACT.md` "Enhancements") | each is off in a check run, so goldens and lockstep see the original (`tests/test_port_contract.py`) |

**Benefit.** One word per game on the dashboard (`docs/DASHBOARD.md`, each game's `progress.json`) and in Phangrade's
catalogue: *raw*, *readable*, *enhanced*, each with the check that earns it, so TC, Spawn, PS2 and V8 are comparable at
a glance, and "enhanced" cannot be claimed for a game whose enhancements change what a check run sees. The user's
frame-rate rule fits the third level as nativeshvc states it: the original tick stays, the enhanced form draws
between ticks.

**Cost.** A short section in `docs/DEFINITION_OF_DONE.md` (the three words, what proves each, and that the byte-exact
level has no DP counterpart), one optional `level` field in `progress.json` the dashboard shows, no code in the kit.

**Recommendation.** Yes to the words and the dashboard field; it is cheap and removes the question each time. No
attempt at a byte-exact level: DP's compiler is not the original's, and the judge's behaviour proof is what the user
has accepted.

## Smaller notes from the same pages (no action proposed)

- **Fatal, never guess.** nativeshvc ends the run with its own exit code on an address with no C or a wait that cannot
  finish. The kit logs `OVERLAY MISS` and returns 0 in a play run and stops only in a checked build or `--must-work`.
  That split is right for a game a person plays; check runs already use the strict one.
- **What the game may not say.** The SNES contract forbids a game to state time itself; the kit's contract does not
  need to, because the sim tick comes from the game's own vertical-blank waits.
- **Contract mismatch.** The nativeshvc pages leave the run-time version check undefined; kitlearn's `contract` key in
  game.json stops on another major version, so nothing is borrowed here.
- **Licence.** AGPL-3.0 with a network clause. Nothing in this proposal copies from it.

Not built. Waiting on the user's yes for each numbered idea (1: part of the pacing proposal; 2: yes or no; 3: digest
yes or no; 4: yes or no).
