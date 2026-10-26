# sh4-translate: origin

The Dreamcast SH-4 translator, the user's own code (every commit by GeneralAtrox), moved into DecompPipeline on
2026-10-22 from its own repository:

- repository: `<projects>/External/sh4-translate`, branch `main`
- commit: `5a51194` ("SH-4 translation that passes DP's judge on TC, PS2 and Spawn as dcrecomp does"), 11 commits

Taken as committed (`git archive 5a51194`), less its `.gitignore` (DP's covers `out*/`). Changes on import, all
paths: the tools find DecompPipeline from their own location; `tools/regress.py` finds the three games under the
projects folder (config `projects_dir`, else DP's parent folder, as the scorecard does); README.md and REFERENCES.md
name folders as `<projects>/...` instead of this machine's paths. REFERENCES.md lists what was read: dcrecomp (GPL)
only to compare pass rates, none of its code here.

`results/verdicts_<game>.json` are its judge results at that commit (Toy Commander 2,635 of 2,698 pass, Power
Stone 2 6,444 of 6,541, Spawn 1,811 of 1,875; every other function's cause recorded there).

`regress/toycommander_sample.json` (new here): the C of every 6th function with a harness in Toy Commander's
workspace (451 of 2,702), as sh4-translate 5a51194 wrote it in its own repository before the import (sha256, first
16 hex digits). The copy here wrote the same C for all 2,702 on import; tests/test_own_translators.py keeps the
sample so. `../translate.py` is DP's adapter for the translator stage (docs/TRANSLATOR_STAGE.md).
