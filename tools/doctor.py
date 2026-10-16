"""Workspace consistency check (core/doctor.py): orphan artefacts of non-functions, matched functions without their
harness meta or matched C, project.json addresses that are not functions, game functions in no system, readable files
using raw names the registry has named, matched functions' harnesses with callee records that differ from the
callees' harnesses; plus the readable denominator and the raw names left for unnamed functions. Read-only.

usage: python tools/doctor.py --project DIR [--project DIR ...] [--json]
Exit 1 when any project fails. completeness.py --check runs the same check.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import workspace  # noqa: E402
from core import doctor  # noqa: E402  (pure: no core.paths)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", action="append", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    bad, out = 0, {}
    for p in a.project:
        ws = workspace(p)
        ok, lines, facts = doctor.check(ws)
        bad += not ok
        out[str(ws)] = {"ok": ok, **facts}
        if not a.json:
            print(f"== {ws}")
            print("\n".join(lines))
    if a.json:
        print(json.dumps(out, indent=1))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
