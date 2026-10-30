# GameCube test truth: the answer-key games

The five fully matched public decomps (pikmin, melee, ac-decomp, pikmin2, tp; local clones,
each with a `CLEANROOM.md`) are this target's answer key. `tools/gc_truth.py` reads only their `config/` and scores
a DP workspace of the same disc against them. Clean room: no name, type or C of theirs enters DP or reaches an agent;
reports hold addresses and numbers and are refused inside the repository or a workspace.

## What is scored

| Part | main.dol | REL modules |
|---|---|---|
| Catalog boundaries (exact / boundary_differs / split / merged / missed, extra DP functions) | yes | yes, per module |
| Translator pass rate (judged translated attempts, or a standalone verdicts file) | yes | yes |
| Readable naming agreement (numbers only) | yes | yes |

**REL support.** DP's GameCube target loads REL modules through `targets/gamecube_ppc/tools/gcrel.py`: `gcdisc.py
setup` links a disc's RELs onto main.dol at fixed addresses (`linked.dol`) and records where each module's sections
went (`link.json`, beside the workspace's binary). `gc_truth.py score` reads that record (or `--link FILE`) and
places every REL truth function, (module, section, offset) in the decomp's config, at its linked address, then
scores it with main.dol's. A decomp module is matched to a linked one by its disc path. Its code sections are
matched to the module's executable sections in order (the module's `splits.txt` "Sections:" list; a module without
one and with a single executable section maps `.text` to it). A module that is not linked, or whose code sections do
not line up, is listed in the report as counted, not scored, with the reason.

## Discs

The answer-key disc images are dumped by the user (not downloaded). Until a folder holds its image, only `summary`
and the synthetic tests run. For each game:

1. `python tools/gc_truth.py check-disc --decomp <clone> <image.iso>` names the version the dump is (its main.dol's
   SHA-1 against the decomp's `build.sha1` hashes).
2. `python targets/gamecube_ppc/tools/gcdisc.py setup <image.iso> <workspace>` makes the workspace, with the RELs
   linked (default: all).
3. Catalog and judge as usual, then `python tools/gc_truth.py score --decomp <clone> --version <V> --project
   <workspace> --out <folder outside the repo and the workspace>`.

Tests: `python -W error tests/test_gc_truth.py` (synthetic configs and workspaces only, REL scoring included).
