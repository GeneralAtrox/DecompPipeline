# Standing rules for every session and agent working on DecompPipeline

How the pipeline works (layout, judge, tools, docs map): read `AGENTS.md` first.

## Nothing appears on the user's desktop

The user's rule (2026-10-30), in full at the top of the games folder's `CLAUDE.md`: on the main laptop, never open a visible
window or take focus, and never restart something the user has open. Run tests with `--headless` (or the kit's no-window run
when it exists); a test that needs a real window runs on build box B or build box A. Never let a test read the user's real input.

## Ports support any aspect ratio

The user's rule (2026-10-31), in full in `docs/RUNTIME_TARGET.md`, "Any aspect ratio": "all projects should support widescreen and not be hardlocked to 4:3. It should support any aspect ratio." In play runs the game world fills the window at any shape, on by default with the original 4:3 selectable. Nothing is stretched or cropped, gameplay results don't change, and the 144 fps target holds at every shape. Checks and goldens stay at the original shape.

## Ports let every controller button be used

The user's rule (2026-10-31), in full in `docs/RUNTIME_TARGET.md`, "Modern controllers": "we should support more buttons. Older consoles had much more primitive game pads." LB, RB, stick clicks, Back, Guide and the right stick can each take any of the game's actions, set on the game's own key screen or a kit Controls screen, and saved apart from the game's save. New buttons start unassigned, so original play and checks don't change.

## Ports share one base

The user's rules (2026-10-31): "We should ensure all projects have a consistent titlebar menu" and "We should have a consistent project base for all projects to use, so we dont have to reset this up". Every port stands on one console-free base: window, menu bar, settings, Copy agent prompt (copy icon, Help entry, F9), session recording and replay, game log, crash handling, controllers, aspect options, presenting and the launcher's path. Menus, order, names and shortcuts are the same on every console; a console-only entry comes through a per-console hook. A console kit supplies only its machine: no kit makes its own window, reads pads itself, opens its own log or builds its own menu.

## Ports target 144 fps at 2560x1600, without emulated hardware in the play path

The user's rule (2026-10-31), in full in `docs/RUNTIME_TARGET.md`. On the user's laptop, through the launcher: 6.94 ms per shown frame at the 99th percentile, including a game tick and its draw, with loading screens the only exception. It applies to every port and every new project, and new gameplay and native feature work on a game pauses while that game is below the target; raw C, readable C and translators carry on. Emulated hardware is for bring-up and as the answer key for tests; it does not stay in a game's default play path. Replace it only by recording its behaviour, proving the replacement with zero differences side by side, switching per game with the old model still selectable, and only then modernising, one switched change at a time. Raw C and readable C are still judged exactly; native code matches the behaviour, proven on the whole game by replays and side-by-side runs, and is free to change anything behind that boundary for maximum performance (the document lists both sides). Checks and the judge keep their flags and fixed clock. Report performance against this target, and keep the emulated-layers table in that document current.

## No redundant work

Waste has been fixed several times in this pipeline and keeps coming back (see `docs/WASTE_AUDIT_2026-10-22.md` and `docs/WASTE_AUDIT_2.md`). So every session and agent checks, every time, that what it builds or runs isn't redundant and doesn't waste time.

**Before creating anything new** (a tool, script, pipeline stage, process step, proof script, workspace, folder, branch or job):
1. Search what already exists:
   - `tools/`, `core/`, `docs/` and the scripts in `docs/proofs/*/`.
   - Every branch and worktree (`git branch -a`, `git worktree list`). Use `git log --all -S<keyword>` to find code that existed once.
   - Any other work folders or plan files your brief names.
2. If something already does it, or nearly does it, reuse or extend that. Never write a parallel version.
3. If something new really is needed, say what it does, the nearest existing thing and why that doesn't fit, and the time it saves.

**Before running any heavy job** (Ghidra prepare, rejudge, mutation audit, test suite, re-check or long build):
4. Say what it computes. Check that no existing result, running job or planned later step already covers it.
   - If a result already exists for the same inputs (commit or oracle stamp, workspace, function list and flags), reuse it.
   - A planned later step that covers it means, for example, a re-check that re-prepares everything anyway.
5. Check that no other session, agent or machine is already running it.
6. Never re-run something that failed or timed out with the same limits and expect a different result.

**The one exception:** a deliberate cross-check is allowed only when it's named as one, e.g. "both machines give byte-identical results".

**How it's checked:**
- **Heavy jobs go through the job registry:** `py -3.12 tools/jobreg.py run --why "<what it computes>" -- <command>`, run on the machine doing the work: inside the ssh call, `ssh HOST "py -3.12 tools/jobreg.py run ... -- <command>"`, never around it. It refuses a copy of a running job and a re-run of one that failed or timed out with the same limits, and it points to a finished job's result instead of running it again. Ask first with `jobreg.py check -- <command>`; see what ran with `jobreg.py list`. A named cross-check is `--cross-check "<what it cross-checks>"`; an input the key can't see (build box B's workspace was re-copied, a network fault was fixed) is `--changed "<what>"`. Both are logged.
- **A hook watches every Bash and PowerShell command** (`.claude/settings.json`) and tells you when a heavy job starts outside the registry. It warns for now; it may later refuse.
- **New files in `tools/` and `core/` need a docstring with a line** `Nearest existing: <tool or module> - <why it doesn't fit>`. `tests/test_tool_index.py` fails without it. A new `docs/proofs/*/` script without that line gets a warning.
- **`tools/scorecard.py` reports per day:** jobs run, reuses and refusals (with the hours saved), cross-checks, and heavy jobs started outside the registry.

**End every report with one line:** `Redundancy check: <what you reused, what you didn't run because something covers it, and anything new you made with why>`. A report without that line is incomplete.
