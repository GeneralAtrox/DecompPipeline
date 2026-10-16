"""Put every game function that is in no system into one (core/sysassign.py): by calls with members, then shared
globals, then being enclosed by one system's functions in address order; ties go to the system with the nearest
member. Never renumbers or moves an existing id or member; functions with no affinity at all become new systems
(one per address-contiguous or call-connected cluster), numbered after the highest id.

usage: python tools/assign_systems.py --project DIR [--dry] [--attach]
  --dry     report what would be added, per system and per rule; change nothing
  --attach  put no-affinity clusters into the system just before them in address order instead of new systems
Writes state/systems.json (under its file lock) and records each placement in state/systems_assigned.json.
Idempotent: a second run finds nothing to assign. Run it after tools/retire.py (retire removes systems members that
are no longer functions; completeness.py --check fails until both are clean).
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import workspace  # noqa: E402
from core import sysassign  # noqa: E402  (pure: no core.paths)


def run(ws, dry=False, attach=False, log=print):
    """Returns the plan (None when the project has no systems.json)."""
    p = sysassign.plan(ws, attach) if dry else sysassign.apply(ws, attach)
    for line in sysassign.report(p):
        log(line)
    if p and p["assign"]:
        log("dry run: nothing changed" if dry else f"written: {sysassign.SYSTEMS}, {sysassign.ASSIGNED}")
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--attach", action="store_true")
    a = ap.parse_args()
    ws = workspace(a.project)
    print(f"== {ws}")
    run(ws, dry=a.dry, attach=a.attach)


if __name__ == "__main__":
    main()
