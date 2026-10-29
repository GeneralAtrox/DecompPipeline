# The progress dashboard

`Dashboard.bat` (or `python tools/dashboard.py`) builds one page and opens it: each game's road from disc to native
port, the work in progress, and every function as a treemap by unit. Most of it is read from the workspaces. Two
small files are kept by the agents, so a person can see where things stand without asking.

**Keep them current.** Whoever starts, finishes or blocks a piece of work updates the board in the same step. Whoever
reaches a port milestone or finds or fixes a playtest bug updates the game's `progress.json`.

## Which games are on the page: only the ones an agent added

Nothing is searched for. The page shows the game folders in this machine's list
(%LOCALAPPDATA%/DecompPipeline/dashboard/projects.json), and only an agent changes it:

- **A new game the agent sets up:** add it once its workspace exists (`tools/game_setup.py setup` prints the command).
- **An existing game:** only when the user asks for it.

```
python tools/dashboard.py add "<game folder>"      # the folder that holds decomp/ (or research/), or progress.json
python tools/dashboard.py remove "<game folder>"
python tools/dashboard.py list
```

`add` records the folder's workspaces (project.json + state/catalog.json, also at the same place in each mirror) so
the page never walks the disk; run it again when the game gains a workspace. A game with a workspace and no
progress.json gets a starter one; a game with no workspace needs a progress.json with a `console` first (below).

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

### Games not on the pipeline

A game made another way (its own native port, research only, not started) can be added too: its `progress.json`
has a `console`, `framework` (how the game is being made) and its own `road`, which replaces the steps read from a
workspace:

```json
{"console": "Xbox", "game": "PhantomCrash", "framework": "own native Windows port",
 "road": [{"name": "Disc", "value": "extracted", "state": "done"},
          {"name": "Playable", "value": "missions 1-3", "state": "now"}],
 "milestones": {}, "now": "...", "next": "...", "bugs": []}
```

A step's `state` is `done`, `now` (the one being worked on; at most one), `part` (started, not finished) or `todo`.

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
workspace is one `add` listed for the game. Every function of the decompile scope (core/scope.py: the game ranges plus
`extra_functions`) counts, so 100% means all of them: one with no ledger row yet is not started, or refused when it
touches hardware (core/coverage.excluded_functions; the port kit replaces it). A workspace without a ledger (judged by the translator only) shows the translator's latest verdict per function,
from state/translator_verdicts.json (tools/translate_backlog.py writes it on every run). `dashboard_mirrors` / `dashboard_refresh` in config.local.json add another
machine's live copies (the newest copy wins).
