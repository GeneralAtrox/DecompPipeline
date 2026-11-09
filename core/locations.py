"""A game's LOCATIONS.md: where its files and emulators are, per machine. The user: "Each project should have an md about
where to find files, emulators". It lives in the game folder, above the workspace's decomp folder (a workspace with no
decomp folder above it, a private copy, keeps it in the workspace). tools/game_setup.py writes the skeleton, never over
an existing file, and core/doctor notes a missing one; missing is not a failure, the file judges nothing.

Nearest existing: tools/dashboard.py add - writes a game folder's progress.json, which says where the work stands, not
where things are, and only when someone adds the game to a machine's page.
"""
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
MACHINES = ("Main laptop", "build box A", "build box B")  # published names; machines() gives this checkout's own
MACHINES_ENV = "DP_MACHINES"
HEADINGS = ("Disc and game files", "BIOS and firmware", "Emulators", "Extracted data", "Workspaces and clones",
            "Launcher mirror and live line", "Logs", "Not allowed here", "Known absent")


def file_for(ws):
    """The game's LOCATIONS.md for this workspace: <game>/decomp, <game>/decomp/main and the like give <game>."""
    ws = pathlib.Path(ws).resolve()
    for p in (ws, *list(ws.parents)[:2]):
        if p.name.lower() == "decomp":
            return p.parent / "LOCATIONS.md"
    return ws / "LOCATIONS.md"


def machines(tool=ROOT):
    """The machine headings of a new LOCATIONS.md, the machines' own names when this machine has them: DP_MACHINES
    (separated by ;), else the git-ignored machines.local.json "machines", else MACHINES. Not config.local.json: the
    judge's stamp hashes the whole config (core/verdict_memo)."""
    got = [m.strip() for m in os.environ.get(MACHINES_ENV, "").split(";") if m.strip()]
    if not got:
        try:
            got = json.loads((pathlib.Path(tool) / "machines.local.json").read_text(encoding="utf-8")).get("machines")
        except (OSError, ValueError, AttributeError):
            got = None
    return tuple(str(m) for m in got) if isinstance(got, list) and got else MACHINES


def skeleton(game, tool=ROOT):
    lines = [f"# {game}: where things are", "",
             "Actual paths only, checked on that machine. Something looked for and not there goes under Known absent.",
             "Keep this current when something moves; what every game of a console shares can point at the console's file."]
    for machine in machines(tool):
        lines += ["", f"## {machine}"]
        for h in HEADINGS:
            lines += ["", f"### {h}", "", "Not filled in yet."]
    return "\n".join(lines) + "\n"


def write_skeleton(ws, game):
    """Writes the skeleton when the game has no file yet. Returns the path, or None when one exists."""
    f = file_for(ws)
    if f.exists():
        return None
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(skeleton(game), encoding="utf-8", newline="\n")
    return f
