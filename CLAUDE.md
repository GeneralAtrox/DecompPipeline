# Standing rules for every session and agent working on DecompPipeline

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
- **Heavy jobs go through the job registry:** `py -3.12 tools/jobreg.py run --why "<what it computes>" -- <command>` (also for `ssh laptop2 "..."` calls). It refuses a copy of a running job and a re-run of one that failed or timed out with the same limits, and it points to a finished job's result instead of running it again. Ask first with `jobreg.py check -- <command>`; see what ran with `jobreg.py list`. A named cross-check is `--cross-check "<what it cross-checks>"`; an input the key can't see (laptop2's workspace was re-copied, a network fault was fixed) is `--changed "<what>"`. Both are logged.
- **A hook watches every Bash and PowerShell command** (`.claude/settings.json`) and tells you when a heavy job starts outside the registry. It warns for now; it may later refuse.
- **New files in `tools/` and `core/` need a docstring with a line** `Nearest existing: <tool or module> - <why it doesn't fit>`. `tests/test_tool_index.py` fails without it. A new `docs/proofs/*/` script without that line gets a warning.
- **`tools/scorecard.py` reports per day:** jobs run, reuses and refusals (with the hours saved), cross-checks, and heavy jobs started outside the registry.

**End every report with one line:** `Redundancy check: <what you reused, what you didn't run because something covers it, and anything new you made with why>`. A report without that line is incomplete.
