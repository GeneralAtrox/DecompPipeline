"""Manual operator steps (docs/DEFINITION_OF_DONE.md gate 2): each time a DP driver needed a person or stopped for
one, a JSON line in state/operator_steps.jsonl of each workspace (or port game folder) it works on. A record only:
no driver reads it back (tools/scorecard.py counts it). Kinds:
  start  a driver started by hand (a person, or an agent standing in for one). Only the outermost driver writes it:
         it hands its run id to its children in DP_RUN, so a driver another driver started writes nothing of its own
  stop   the outermost driver ended without finishing (a nonzero exit, an error): someone must look before it goes on;
         also a server's workers all stopping on a fatal error, or one worker on an error (core/orch)
  act    a person acted on a running driver: the monitor page's buttons (core/server), Ctrl+C, wave.py stop/release
A hands-off end-to-end run is one start and nothing else.
The drivers: decomp.py (setup, catalog, seed, serve, prepare, investigate, systems), refine.py (run, verify, draft,
adopt, sync-names, library), tools/wave.py, tools/prepare_list.py, tools/reprepare_stale.py,
tools/idle_prepare.py, tools/requeue.py, port/tools/build.py, port/tools/lockstep.py, port/tools/regress.py. A new one
calls begin() once its workspaces are known and runs its main through main(). DP_NO_RECORDS=1 turns the log off.
"""
import datetime
import json
import os
import pathlib
import sys

FILE = "operator_steps.jsonl"
ENV = "DP_RUN"
_RUN = {}  # the outermost driver of this process: {"run", "tool", "roots"}


def off():
    return os.environ.get("DP_NO_RECORDS") == "1"


def workspace(arg=None):
    """The workspace a tool works on, as core/paths resolves it (--project, $DECOMP_PROJECT, the current folder),
    without importing core/paths (which raises when there is none): None then."""
    for c in (arg, os.environ.get("DECOMP_PROJECT"), os.getcwd()):
        if c and (pathlib.Path(c) / "project.json").is_file():
            return pathlib.Path(c).resolve()
    return None


def _file(root):
    """state/FILE of a workspace, or of a port game folder (game.json; its state/ made on first use); else None."""
    root = pathlib.Path(root)
    if (root / "state").is_dir() or ((root / "game.json").is_file() and not (root / "project.json").exists()):
        return root / "state" / FILE
    return None


def record(roots, tool, kind, step, run=None):
    """Append one record to each root's log; never raises (a record must not stop the driver)."""
    if off():
        return
    line = json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind, "tool": tool,
                       "step": str(step)[:400], "run": run or os.environ.get(ENV) or _RUN.get("run")}) + "\n"
    for root in dict.fromkeys(str(r) for r in roots if r):
        p = _file(root)
        if p is None:
            continue
        try:
            p.parent.mkdir(exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def begin(tool, roots):
    """A driver starts on `roots`: a start record when no other driver started this one. Returns the run id."""
    if _RUN or os.environ.get(ENV):
        return os.environ.get(ENV) or _RUN.get("run")
    run = f"{datetime.datetime.now():%Y%m%dT%H%M%S}-{os.getpid()}"
    _RUN.update(run=run, tool=tool, roots=[r for r in roots if r])
    os.environ[ENV] = run  # every child process inherits it: part of this run
    record(_RUN["roots"], tool, "start", " ".join([os.path.basename(sys.argv[0])] + sys.argv[1:]), run)
    return run


def end(outcome, ok=(0,)):
    """The outermost driver ends: outcome None or in `ok` finished (a driver's own exit codes that need nobody); any
    other int or a text a stop; "interrupted" a person's Ctrl+C."""
    if not _RUN:
        return
    run = _RUN.copy()
    _RUN.clear()
    if os.environ.get(ENV) == run["run"]:
        del os.environ[ENV]
    if outcome is None or outcome in ok:
        return
    if outcome == "interrupted":
        return record(run["roots"], run["tool"], "act", "interrupted by a person (Ctrl+C)", run["run"])
    record(run["roots"], run["tool"], "stop", f"exit {outcome}" if isinstance(outcome, int) else outcome, run["run"])


def main(fn, *args, ok=(0,)):
    """Run a driver's main: what begin() started ends here, with a stop record when it did not finish."""
    try:
        rc = fn(*args)
    except SystemExit as e:
        end(e.code if isinstance(e.code, (int, type(None))) else str(e.code), ok)
        raise
    except KeyboardInterrupt:
        end("interrupted")
        raise
    except BaseException as e:
        end(f"{type(e).__name__}: {e}")
        raise
    end(rc if isinstance(rc, (int, type(None))) else str(rc), ok)
    return rc
