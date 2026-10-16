"""Cross-project import: xproject.py match + stage from a source workspace into one or more target workspaces,
then set each target's project.json "priority" to its staged imports (so they are judged first).

usage: python tools/import_from.py --from SOURCE_DIR --project TARGET_DIR [--project TARGET_DIR ...]
       [--timeout S] [--dry]
  --dry      run match + stage but leave project.json alone
Each xproject step runs bounded (--timeout, default 900 s; the process tree is killed on expiry).
Only with the target's server stopped: the server reads project.json at start.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from bounded import kill_tree  # noqa: E402
from common import TOOL, workspace  # noqa: E402


def run_bounded(cmd, timeout):
    """(returncode or None, output, 'timeout' or None); the whole process tree is killed on expiry."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out, None
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        out, _ = p.communicate(timeout=60)
        return None, out or "", "timeout"


def write_priority(ws, staged):
    pj = ws / "project.json"
    text = pj.read_text(encoding="utf-8")
    indent = 2 if text.startswith('{\n  "') else 1  # keep the file's own layout
    cfg = json.loads(text)
    cfg["priority"] = staged
    pj.write_text(json.dumps(cfg, indent=indent) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--project", action="append", required=True)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    src = workspace(a.src)
    rc_all = 0
    for t in a.project:
        ws = workspace(t)
        for step in ("match", "stage"):
            rc, out, why = run_bounded([sys.executable, str(TOOL / "xproject.py"), "--project", str(ws),
                                        "--from", str(src), step], a.timeout)
            last = (out.strip().splitlines() or [""])[-1]
            print(f"{ws.name} {step}: rc {rc}{' ' + why if why else ''}  {last[:120]}", flush=True)
            if rc != 0:
                rc_all = 1
                break
        else:
            imp = ws / "import"
            staged = sorted(p.stem for p in imp.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem)) \
                if imp.exists() else []
            if not a.dry:
                write_priority(ws, staged)
            print(f"  priority {len(staged)}{' (dry: project.json unchanged)' if a.dry else ''}")
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
