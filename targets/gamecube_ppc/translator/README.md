# ppc-translate

GameCube (Gekko / PowerPC 750CL) machine code to C. It writes one C function per original function, in
DecompPipeline's GameCube raw-C form (`tc_rt.h`), so DP's own judge can check the C against the original. Our
own code throughout: see [REFERENCES.md](REFERENCES.md).

## Results on Skies of Arcadia (`main.dol`)

| | ppc-translate | DolRecomp |
|---|---|---|
| The 83 matched harnesses | 81 PASS | 81 PASS |
| All 100 harnesses | 89 PASS | — |
| Functions in main.dol that build under DP's lint + MSVC | 6748 of 6751 (98.7% of instructions) | — |

- **The 2 failures among the 83** (800cf988, 800d1a34) are the oracle's, not the translation's. When a
  single-precision result is smaller than the smallest single subnormal, the judge's oracle rounds it to 0,
  while IEEE rounding and the 750CL give 0x00000001. Both pass under `--oracle-flush`, a diagnostic that copies
  the oracle's rounding. The agent-written matched C passes only because it copies that flush too.
- **The 11 failures among the 100:**
  - 6 are the same oracle rounding (4 more: 800cf554, 800d0e78, 800dbb4c, 801dd5e8).
  - 801f7500's expected results predate DP commit 6f43d0a, which made mocked callees clobber the volatile
    registers, so its harness is stale.
  - 800c24cc reads both r3 and f1 after one call, which one `tc_call` / `tc_callf` cannot return.
  - 801d72a0, 801d732c and 80291a5c have no vectors.
- Every function judged is written without gotos.
- **The 3 functions that do not build** (8013ad9c, 801a6bdc, 801ad1d4) are over DP's 60 KB file cap even in
  compact form (65 KB, 130 KB and 74 KB).

## Layout

- `ppctranslate/decode.py`: the instruction decoder. `image.py` reads the DOL.
- `ppctranslate/translate.py`: control flow, jump tables, and lifting each instruction into typed statements.
- `ppctranslate/passes.py`: constant and copy propagation, store-to-load forwarding, dead-code removal,
  substitution, and the tc_call / tc_callf choice.
- `ppctranslate/returns.py`: works out across the whole executable whether each function's result is in r3, in
  f1, or in neither. Call sites use it where DP's meta gives no callee signature.
- `ppctranslate/emit.py`: C text and the helpers. `structure.py` and `cfgstruct.py` lay out loops and ifs (from
  sh2-translate).
- `ppctranslate/workspace.py`: reads a DP GameCube workspace (read only).

## Tools

```bash
python tools/translate_one.py --ws <workspace> 801f6120
python tools/dp_judge.py --ws <workspace> --out out [--all] [--only a,b] [--oracle-flush]
python tools/regress.py --ws <workspace> [--all]
python tools/compile_all.py --ws <workspace>
python tools/check_decoder.py --dol main.dol --listings <dir>
```

- `translate_one.py` prints the C for one function.
- `dp_judge.py` judges with DP's own check: lint, build and run, with no Ghidra.
- `regress.py` checks that verdicts and C are identical to `regress/*.json`.
- `compile_all.py` translates and compiles every function, with no judge, and groups what fails by cause.
- `check_decoder.py` compares the decoder against Ghidra's listings.

Every command is bounded: worker processes and compiler batches have timeouts, and the structurer's layout
search has a fixed work budget.
