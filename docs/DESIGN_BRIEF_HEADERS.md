# Designer guidance the tool ships: headers

Every project's readable design brief (`readable/DESIGN_BRIEF.md` in the workspace) carries the section below.
Paste it there, after "Rules for the design". In the commands, replace `TOOL` with the pipeline folder and
`PROJECT` with the workspace. The tool cannot edit workspace files itself.

---

## Headers: one owner per name

- **After every edit** of a header (`readable/include/...`) or a names file (`readable/names/*.json`), run:
  ```
  python TOOL/refine.py --project PROJECT header
  ```
  It writes game.h and checks that no name is defined twice under `readable/include`. It also builds game.h at
  /W4 /WX. It must end with `headers OK`. Fix every `HEADER PROBLEM` line before you do anything else, including
  starting a refine run.
- **Never define a name that another header owns.** That covers macros, typedefs, struct, union and enum tags,
  enum constants, and globals. Names from the registry count too: game.h declares them. An identical copy fails
  as well, because the next edit to either copy makes them differ. If you need a name that another system's header
  owns, use it from that header. If you want it changed or moved (for example, into `common.h`), ask for it in your
  request file. A forward declaration (`struct Toy;`) defines nothing, so you may write one.
- **Why it matters.** Readable runs, yours and the coordinator's, build against a validated snapshot of all the
  headers and names. While the live headers fail the check, a run waits up to `readable_header_wait_s` (15 minutes
  by default) and then falls back to the last good snapshot. That snapshot does not have your newest names. A
  broken header therefore delays or degrades every run in the project, not just yours. `verify` and `check` refuse
  to judge until the headers pass, so STALE always means "breaks against the current headers".
