# Xbox 360 (Xenon PowerPC) target

DecompPipeline's Xbox 360 target. The baseline title is Too Human. It is built like `targets/gamecube_ppc/`, with
64-bit registers, the XEX2 container and the Xbox 360 memory map.

- **DESIGN.md:** how it works and the open questions.
- **PROOF_PLAN.md:** the first Ghidra run (not started; it waits for the go).
- **REFERENCES.md:** what was consulted.

## Status

| Part | State |
|---|---|
| Disc → `default.xex` → loaded image (`tools/xiso.py`, `xex.py`, `aes.py`) | done, offline, tested on the real disc |
| Catalog inputs (`tools/ppcscan.py`, `catalog_inputs.py`) | done: 88,193 functions, 86,559 in phase 1 |
| C runtime and runner (`runtime/`) | done, tested bit for bit in both fma modes |
| Judge glue (`x360_check.py`, `x360_compare.py`, `x360_agent.py`, `hooks.py`) | done, tested |
| Ghidra scripts (`ghidra_scripts/`, `template/`, `tools/ghidra_setup.py`, `decode_audit.py`) | written; they compile and their pure parts are tested; **never run in Ghidra yet** (PROOF_PLAN.md) |
| VMX128 (vector) functions | **refused in phase 1** (1,403 of Too Human's); phase 2 designed in DESIGN.md §5 |

## Commands

Retail titles are encrypted with the console's key. It is never committed: put it in DecompPipeline's git-ignored
`config.local.json` as `"xex_retail_key": "<32 hex digits>"`, or set `DP_XEX_RETAIL_KEY`.

Make the catalog inputs from the disc or from `default.xex` (about 8 s):

```
python targets/xbox360_ppc/tools/catalog_inputs.py DISC.iso OUT_DIR
```

List the disc, or pull one file out of it:

```
python targets/xbox360_ppc/tools/xiso.py DISC.iso list
python targets/xbox360_ppc/tools/xiso.py DISC.iso extract default.xex OUT.xex
```

Pick the proof's 50 functions again (the same inputs give the same list):

```
python targets/xbox360_ppc/tools/proof_pick.py OUT_DIR targets/xbox360_ppc/proof_set.json
```

Write Ghidra's setup list (the functions TcSetup creates; `--proof` keeps the proof's 50 and what they call):

```
python targets/xbox360_ppc/tools/ghidra_setup.py OUT_DIR/catalog_inputs.json WORKSPACE/ghidra_setup.txt --proof targets/xbox360_ppc/proof_set.json
```

Run the tests (about 40 s; the MSVC parts need `vcvars` and the script parts `ghidra_home` and `java_home` in
`config.local.json`; the real-disc test runs when it also names the disc, `"xbox360_disc"`, and has the key):

```
python tests/test_xbox360_ppc.py
python tests/test_xbox360_glue.py
python tests/test_xbox360_helpers.py
python tests/test_xbox360_scripts.py
```
