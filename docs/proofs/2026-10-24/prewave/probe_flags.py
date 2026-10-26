"""tools/oracle_probe.py of one DP tree, with config overrides and a shared Ghidra copy dir.

usage: python probe_flags.py TOOL GHIDRA_TAG FLAGS_JSON <oracle_probe args...>
  TOOL        the DP tree (worktree) whose oracle prepares
  GHIDRA_TAG  tests/ghidra_work/probe_<GHIDRA_TAG>: shared by the runs of one workspace (they run one after another)
  FLAGS_JSON  {key: value} over the project's config for this run only (like regress.py --set)
"""
import json
import pathlib
import runpy
import sys

tool, gtag, flags = pathlib.Path(sys.argv[1]).resolve(), sys.argv[2], json.loads(sys.argv[3])
sys.argv = [str(tool / "tools" / "oracle_probe.py")] + sys.argv[4:]
sys.path[:0] = [str(tool), str(tool / "tools")]
import regress_project as rp  # noqa: E402

_setup = rp.setup


def setup(a):
    a.ghidra_work = str(tool / "tests" / "ghidra_work" / f"probe_{gtag}")
    r = _setup(a)
    r[3].update(flags)
    print(f"config over the project's: {flags}; ghidra copies {a.ghidra_work}", flush=True)
    return r


rp.setup = setup
runpy.run_path(sys.argv[0], run_name="__main__")
