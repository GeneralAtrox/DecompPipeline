# sh2-translate: origin

The Saturn SH-2 translator, the user's own code (every commit by GeneralAtrox), moved into DecompPipeline on
2026-10-22 from its own repository:

- repository: `<projects>/External/sh2-translate`, branch `saturn-games`
- commit: `0b28828` ("Saturn games: GCC jump tables in more shapes, low-RAM overlays, two compile fixes"), 10 commits

Taken as committed (`git archive 0b28828`), less its `.gitignore` (DP's covers `out/`). One change on import: the
tools found DecompPipeline at a fixed machine path; they now use the checkout this folder is part of.

`sh2translate/` is the translator; `../translate.py` is DP's adapter (docs/TRANSLATOR_STAGE.md), which the
translator stage runs from here unless config.local.json `translators` names another folder. `tools/` are its
judge, coverage, overlay and decoder-check scripts; tests/test_own_translators.py runs it inside DP's test suite.

`regress/grandia_field.json` (new here): the C of every function with a harness in Grandia's FIELD.BIN workspace
(84), as sh2-translate 0b28828 wrote it in its own repository before the import (sha256, first 16 hex digits).
The copy here writes the same C for all 84; the test keeps it so.
