# Store queues: a window pointer inside its line

Config `"sq_unaligned": true` (Dreamcast only, off by default). `core/sq_unaligned.py`, `tools/sq_unaligned.py`.

## Correction: the console never has this state

The gap below came from a port bug, not from the game. On the console every list pointer is on a line; the port started its lists
off a line (`docs/proofs/kamui2-list-line.md`, fixed there). Leave this pass off: it asks readable C to reproduce a state that
never occurs, and C that passes it can be wrong in play (round 5's 8c0757c8 tinted the truck on an off-line r14 in plain RAM). The
text below is the pass as built.

## The gap

The oracle only draws a window pointer on a 32-byte line (TcSq.draw). In play, Toy Commander leaves r14 inside a line
(e0bc6044 after a clip block). 13 functions exit that way (8c0755aa, 8c07564c, 8c09ce6c, 8c09cebc, 8c09cf44, 8c09d008,
8c09d080, 8c09d1bc, 8c09d288, 8c09d3d4, 8c09d474, 8c09d55c, 8c09d9dc), and the offset stays for every later block of
that stream. Readable C that takes its line from the pointer (`p & ~31`) passes every aligned vector and overwrites the
stream's earlier stores: 8c07557c drew the movie quad white because it replaced the TCW with a stale word.

## The pass

With the flag on, readable C that passes today's comparison runs a second time on the same vectors with rN moved
4..28 bytes into its line. The function needs both passes. Raw C is not judged on these: readable C and the translator
are the play path.

- Entry model: the bytes below rN in its line are earlier stores still in the queue. The translator's `sq_start`
  (sqmodel.py) and xemu (`xe_main.c`) both hold them.
- `tools/sq_unaligned.py` makes `state/sq_unaligned/<addr>/{vectors,expected,info.json}`. xemu (`XEMU_MODEL=sqwt,sqlog`)
  answers. A vector is kept only where xemu's run on the unmoved vector equals expected.txt, the moved run ends without
  a fault, and the translator's C of the function agrees with xemu. These are two separate store-queue models.
- A mocked callee that moves rN has no defined answer off the line, and that is the usual vector dropped.
- Every moved vector is labelled dev. An agent must see the answers (8c07086c failed on one hidden moved vector with
  nothing to work from); the aligned pass keeps its hidden set.
- The verdict reads stage, failing and run_error, not min_valid_vectors: the kept vectors may be few, and the pass sits
  on top of a whole check.

The pass can only fail C that passes today's judge, so it can't lose a real catch. The oracle, its stamps and the
harnesses stay as they are.

## Proof on Toy Commander (50 window functions)

- The candidate fill for 8c07557c passes; the 1dcbe7c version (`p & ~31`) fails with 51 differences of 96.
- Readable C at the start: 21 of 50 passed. The rest differed on 11 to 51 vectors, or had no usable answers yet.
- After redos through the pipeline, all 50 pass both passes (agents saw only these diffs).
- Translator C with the new `sq_start`: 50 of 50 pass aligned, 38 of 47 pass moved. The 9 partial failures are a mocked
  callee moving rN or one fault, and those vectors are dropped by the agreement filter.

## What it does not judge

- 8c09ce6c, 8c09cebc, 8c09cf44: xemu disagrees with expected.txt on the aligned vectors (an FPSCR mode argument), so
  no vector is kept and this pass says nothing about them.
- Raw C on moved vectors.

## Note for a landing

The `sq_start` text changes the translator's C for the window functions, so their translated shas change. A port takes
only judged text, so it re-translates; aligned results are unchanged (50 of 50 above).

Nearest existing: `core/junk_ram.py` (a second pass with xemu as oracle, on the same vectors: it never moves the window
pointer, and the raw judge runs it) and `tools/xemu_check.py` (xemu against expected.txt, a stamp-time cross-check).

## With the flag off and the pointer on its line

The new code does nothing there: `p & 31` is 0 in `sq_start` and in xemu's `xe_sq_entry`, and `sqlog` is only set by this
pass. `sqlog` also exports r4..r7 at a call and at exit: a harness with an RO line compares them (8c07557c), and without
that its zero run disagrees on every vector, so no moved answer is kept. Measured, master against this branch, on Toy Commander (130 functions: all 50 window functions and 80 others),
Power Stone 2 (83: 3 and 80) and Spawn main (88: 8 and 80):

- xemu's output files are byte-identical on the unmoved vectors, three ways: the zero image (`core/junk_ram.py`'s and
  `tools/xemu_check.py`'s default run), the junk image, and `sqwt`. 0 differences in 301 functions.
- The translator's C of all 61 window functions gets the same verdict and failing count from the judge (all PASS).
- No vector of those 61 has its pointer off its line.
