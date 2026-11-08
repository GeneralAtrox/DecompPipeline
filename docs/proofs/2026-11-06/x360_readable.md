# Xbox 360 readable C: parity, a Too Human sample, planted wrong functions (2026-11-06)

The readable stage for `targets/xbox360_ppc` (commits 6eb93d95, 3ed46305, d7af53e2, f9b3b4f; design in
docs/READABLE_SATURN_GAMECUBE.md, "Xbox 360") run on real Too Human functions. Everything ran on build box A through
`tools/jobreg.py` and `tools/bounded.py`, in a private clone and a private 22-function workspace under
a scratch folder (deleted afterwards). No game data, generated C or readable C is kept.

## Sample

22 functions of the Too Human workspace that the translator passes (state/translator_verdicts.json), picked for variety
(40 to 244 bytes; 0 to 5 parameters; integer, s64, f64 and void results; direct calls, globals, ppc_ helpers,
float arguments, calls through vtables): 82a4be14 82549490 82d3b4a8 82d5cae0 82f03090 823b50c8 82c48438 82cf5eb0
82ef2610 824e7a88 82d99880 82505c78 82a800d8 82334de0 8236a1d0 82411b98 82395e40 82801900 82bf1188 82d471c0
8260f510 82deef28. Each harness has 48 dev and 48 hidden vectors.

## What was run, and the numbers

| Step | Command (in the clone, workspace `..\ws`) | Result |
|---|---|---|
| Unit tests | `tests/test_x360_readable.py`, `tests/test_readable_be.py` | 17 and 17 OK |
| Raw C | `tools/translate_backlog.py --only <22> --jobs 4 --keep` (harness stamps predate d1cc5d66, so `--record` refuses; the PASS files were copied to matched/) | 22/22 PASS, 182 s |
| Runtime parity | `tools/readable_parity.py <22>`: each raw C by the raw judge and by the 64-bit readable runtime | 22/22 agree (22 PASS both), 42.9 s |
| Readable C | `refine.py run <22> --draft off --workers 4` (Opus, effort high) | 22/22 readable |
| Official re-judge | `refine.py check ADDR` for each, after lint_wide | 20 PASS (96/96 vectors), 2 FAIL at lint (RR3, below) |
| Planted wrong functions | `refine.py check ADDR --work PLANT` | 4 of 5 FAIL; the 5th is a harness limit, below |

Global names: `refine.py globals --write` named 8 data globals of the 5 functions that use any (machine names); they
were changed to be_ types by hand (core/global_infer writes plain types, which BE1 refuses on a big-endian target).

## Tokens and cost per function (state/readable.json, every attempt counted)

31 attempts for 22 functions: 2,096,776 input tokens, 93,192 output, 53,555 thinking; $3.72 in all, $0.169 and about
99,500 tokens per function. 18 functions passed at the first attempt (median $0.053, 22,900 input tokens).

- 82549490 cost $0.60 over 7 attempts, of which $0.58 went on two set-up faults found by this run, not on the
  function: build box A had no `python` on the agent's PATH (4 attempts, check.py exit 127), then the callee's raw
  signature `fn_825494c0(s32, u64)` made the original's 64-bit r3 unmatchable (2 attempts; fixed by
  x360_readable_abi.register_sig, commit 3ed46305). After the fix it passed in one attempt at $0.023.
- Second attempts: 82411b98 $0.86, 8260f510 $0.97 (calls through vtables that take a callee's f1 and r3 together),
  82395e40 $0.19.
- Without the set-up faults: about $3.14, $0.143 per function.

## Planted wrong readable functions

Each plant is one change to a passing readable file. The judge must FAIL it.

| Plant | Change | Verdict |
|---|---|---|
| p1 82549490 | the call argument's offset 0x40 made 0x48 | FAIL 96/96 (call #1, r3) |
| p2 82549490 | the same argument truncated to 32 bits | FAIL 56/96 (r3's upper word) |
| p3 82bf1188 | a float subtraction's operands swapped | FAIL 1/96 (return f1) |
| p5 82d3b4a8 | the sub-object offset 0x16c made 0x168 | FAIL 96/96 (call #1, r3) |
| p4 82d3b4a8 | the value stored in a local and passed by pointer to a callee, xored with 1 | PASS |
| control 82d3b4a8 | the unchanged file | PASS |

p4 is a limit of these harnesses, not of the readable judge: the same change in the raw C (a store into the caller's
parameter area at r1+0x1c, which the stack comparison skips, read only by the mocked callee) also passes the raw
judge (job 20261106-171641). These harnesses were made before d1cc5d66, whose stricter judge records the memory a
pointer argument points at when the call is made (CS lines); their expected.txt has none. A harness re-prepared by the
current oracle should catch it.

## Found and fixed while running

- 64-bit call arguments: a callee known only by its raw signature now takes u64 for every narrower integer
  (register_sig), as the raw judge compares all 64 bits.
- RR3 missed image addresses written as sign-extended 64-bit literals (0xffffffff82150af4): lint_wide (f9b3b4f).
  82f03090 and 82334de0 now fail at lint: each passes a function or data address that has no name in the
  registry, so it waits for a name.

## Open

- Readability gaps the judge does not catch: 82395e40 reaches an unnamed function address as an offset from a named
  global (`&g - 0x19cf70`), which RR3 does not see; three agents reported struct fields they had to reach by offset.
- core/global_infer writes plain types for globals; on the big-endian targets they need be_ types.
- `python` must be on the PATH of the machine that runs readable agents (targets/xbox360_ppc/README.md).
- Not carried over from the SH-4 judge (each fails a correct function, never passes a wrong one): variadic callees,
  vector arguments across a call, out-pointers into the caller's frame, stack addresses stored to memory, lean
  sandboxes.

## Scorecard, Xbox 360 gate 1 (`tools/scorecard.py --console xbox360`)

Before (e85901e4):

```
  RED    1 Full toolset  (open: readable C)
         fail       readable C
                    open: missing targets/xbox360_ppc/runtime_rd/rd_runner.c
```

After (6eb93d95 and later):

```
  GREEN  1 Full toolset
         pass       readable C
                    source: checkout
```
