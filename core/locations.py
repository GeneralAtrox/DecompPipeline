"""A game's LOCATIONS.md: where its files and emulators are, per machine. The user: "Each project should have an md about
where to find files, emulators". It lives in the game folder, above the workspace's decomp folder (a workspace with no
decomp folder above it, a private copy, keeps it in the workspace). tools/game_setup.py writes the skeleton, never over
an existing file, and core/doctor notes a missing one; missing is not a failure, the file judges nothing.

Nearest existing: tools/dashboard.py add - writes a game folder's progress.json, which says where the work stands, not
where things are, and only when someone adds the game to a machine's page.
"""
import pathlib

MACHINES = ("Main laptop", "Build box B", "Build box A")
HEADINGS = ("Disc and game files", "BIOS and firmware", "Emulators", "Extracted data", "Workspaces and clones",
            "Launcher mirror and live line", "Logs", "Not allowed here", "Known absent")


def file_for(ws):
    """The game's LOCATIONS.md for this workspace: <game>/decomp, <game>/decomp/main and the like give <game>."""
    ws = pathlib.Path(ws).resolve()
    for p in (ws, *list(ws.parents)[:2]):
        if p.name.lower() == "decomp":
            return p.parent / "LOCATIONS.md"
    return ws / "LOCATIONS.md"


def skeleton(game):
    lines = [f"# {game}: where things are", "",
             "Actual paths only, checked on that machine. Something looked for and not there goes under Known absent.",
             "Keep this current when something moves; what every game of a console shares can point at the console's file."]
    for machine in MACHINES:
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
