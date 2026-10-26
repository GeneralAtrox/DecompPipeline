# xenon-translate: origin

The Xbox 360 (Xenon PowerPC, with AltiVec and VMX128) translator, the user's own code, moved into DecompPipeline on
2026-10-23 from its own repository:

- repository: `<projects>/External/xenon-translate`, branch `master`
- commit: `66b6b4a` ("Return kinds: cache only the per-function code summaries, so new harness signatures are always
  used"), 4 commits

It started from ppc-translate (`targets/gamecube_ppc/translator/`); XenonRecomp and xboxrecomp were reference only,
and no code was copied or ported from them (REFERENCES.md).

Taken as committed (`git archive 66b6b4a`), less its `.gitignore` (DP's covers `out/`, `__pycache__/` and `*.pyc`).
Changes on import, with the same output:

- Line endings are LF, as the rest of `targets/xbox360_ppc/`.
- The tools and `xenontranslate/workspace.py` found DecompPipeline and the Too Human workspace at fixed machine
  paths. They now use the checkout this folder is part of, and the workspace is a required `--ws` (`--workspace`).
  `tools/compile_all.py` takes `vcvars` from DP's `config.local.json`, as `core/check.py` does.
- REFERENCES.md names its folders as `<projects>/...` instead of this machine's paths.
- New: `tools/regress.py` and `regress/toohuman_set.json`, the identical-C baseline (below).

The translator stage runs it through target.json `translator` and the adapter `targets/xbox360_ppc/translate.py`.
`tests/test_own_translators.py` runs it inside DP's test suite: a two-function workspace through the adapter, and
the regress check. The baseline holds Too Human's 500 translator-set functions (the workspace's
`translator_set.json`), each translated with a generic signature so the C depends on the translator and the
executable only. All 500 build with DP's lint and MSVC flags. The baseline was recorded after the import, and is the
same C as commit `66b6b4a` gives.
