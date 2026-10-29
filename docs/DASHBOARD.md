# The progress dashboard

`Dashboard.bat` (or `python tools/dashboard.py`) builds one page and opens it: each game's road from disc to native
port, the work in progress, and every function as a treemap by unit. Most of it is read from the workspaces. Two
small files are kept by the agents, so a person can see where things stand without asking.

**Keep them current.** Whoever starts, finishes or blocks a piece of work updates the board in the same step. Whoever
reaches a port milestone or finds or fixes a playtest bug updates the game's `progress.json`.

## GAME/progress.json (the game's own folder, next to port/)

```json
{
 "updated": "2026-10-29 16:30",
 "milestones": {"builds": "yes", "boots": "2026-10-29 (menus)", "playable": null, "matches": null},
 "now": "What is being done for this game right now, in one plain sentence.",
 "next": "What comes after it.",
 "bugs": [{"id": "B21", "text": "GALLERY: B does not leave it", "state": "open"}]
}
```

A milestone is `null` until it is reached, then a short text (a date, or "yes"). `matches` means zero divergence from
the original game. A bug's `state` is `open` or `fixed`; fixed bugs are not shown.

## The work board (config key "board", else PROJECTS_DIR/board.json)

```json
{"updated": "2026-10-29 16:30", "work": [
 {"name": "junk-out", "state": "running", "where": "laptop2", "started": "2026-10-29 13:50",
  "games": ["PowerStone2"], "what": "One plain sentence.", "result": "Latest outcome.", "unblocks": "land33"}]}
```

`state` is `running`, `waiting`, `queued` or `done`. `games` uses the game folder names; each listed game shows a
"waits on" chip while the item is not done.

## Read from the workspaces

Functions found (state/catalog.json), tests built (harness/*/meta.json), raw C proven and in progress
(state/ledger.json), readable C (state/readable.json), units (state/systems.json), port set up (port/game.json). A
workspace is any folder with project.json + state/ledger.json under a console root of tools/scorecard/spec.json; a new
game folder needs its root added there. `dashboard_mirrors` / `dashboard_refresh` in config.local.json add another
machine's live copies (the newest copy wins).
