Ghidra scripts of the Xbox 360 target. Written and compiled; never run in Ghidra yet (PROOF_PLAN.md).

- Locked (target.json `locked_scripts`; TcPrepare builds on them): TcBase, TcClass, TcFacts, TcFixups, TcFlow, TcAsm,
  TcBank, TcOracle, TcVectors, TcPrepare. The project's `oracle/TcInputs.java` (from `template/`) sits between TcBase
  and TcOracle.
- Whole-program tools: TcSetup (creates the functions of `ghidra_setup.txt`, no auto-analysis), TcCatalog,
  TcExport, TcDecodeAudit (gate G2, compared by `tools/decode_audit.py`).

tests/test_xbox360_scripts.py compiles them all and checks TcClass against tools/ppcscan.py and TcFixups against
the Python model the C runtime is tested against.
