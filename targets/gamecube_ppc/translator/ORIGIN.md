# ppc-translate: origin

The GameCube (Gekko) PowerPC translator, the user's own code (every commit by GeneralAtrox), moved into
DecompPipeline on 2026-10-22 from its own repository:

- repository: `<projects>/External/ppc-translate`, branch `master`
- commit: `5575050` ("Whole-game compile, executable-wide return kinds, README and references"), 3 commits

Taken as committed (`git archive 5575050`), less its `.gitignore` (DP's covers `out/`). Two changes on import: the
tools and `ppctranslate/workspace.py` found DecompPipeline at a fixed machine path; they now use the checkout this
folder is part of; REFERENCES.md (what was consulted; no third-party code) names its folders as `<projects>/...`
instead of this machine's paths. README.md is unchanged.

The translator stage runs it (target.json `translator`, adapter `targets/gamecube_ppc/translate.py`) since branch
`gc-rel`, which also changed two files here, with the same output:

- `ppctranslate/returns.py`: the whole-executable pass keeps its slow part (one summary per function of the code
  alone) in its cache, keyed by the executable, the function bodies and the target facts, and reruns only the cheap
  rounds with the current harness signatures. Before, it cached the final answers under a key that ignored the
  signatures, so a cache written before a harness existed stayed in use after it. On Skies of Arcadia the answers for
  all 6751 functions are identical to the old pass run fresh.
- `ppctranslate/workspace.py`: the harness folder is listed once instead of one file check per function.

tests/test_own_translators.py runs it inside DP's test suite, through the adapter too.
