# DecompPipeline

Turns a game's machine code into C that behaves exactly like the original, one function at a time, and builds that
C into a native port.

- **Translator first.** Each CPU's translator writes C for every function. A judge runs the original code in an
  emulator and the C on the same inputs, and gives one verdict: PASS or FAIL. Locked-down agents only fix what fails.
- **Targets:** Dreamcast SH-4, Saturn SH-2, GameCube PowerPC, Xbox 360 PowerPC (`targets/`).
- **Port kit:** `port/` builds a game's C as a native program ([port/README.md](port/README.md)).
- **No game data here.** Each game is its own workspace folder.

This repository is run by agents. Everything they need, and the full reference, is in **[AGENTS.md](AGENTS.md)**.
A new game, from disc to port: [docs/NEW_GAME.md](docs/NEW_GAME.md). Progress at a glance: run `Dashboard.bat`
([docs/DASHBOARD.md](docs/DASHBOARD.md)).

Needs Python 3.12, JDK 21, Ghidra 12 and Visual Studio 2022 (C++). `python -m pip install -r requirements.txt`; machine
paths go in a git-ignored `config.local.json`.
