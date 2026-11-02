# Mega Man 10: the first whole-game run (the rest of `DESIGN.md` section 10)

Mega Man 10 (XBLA, an STFS package, `default.xex` unpacked by `tools/stfs.py`) is the whole game the translator and
judge are measured on, since Too Human's 86,565 judgeable functions cost about 100 hours to prepare. Workspace
the workspace `mm10ws` on MAGNUS. Figures are of 2026-10-31 08:40; the run goes on (status below).

**The game.** 9,963 catalog functions, 9,759 judgeable. 204 are refused by the catalog: atomic 118, vector estimate 37,
cache 21, system 11, embedded data 8, vector ABI 6, FPSCR 3. By instruction count the judgeable ones fall into 1,530
(1 to 10), 2,543 (11 to 30), 4,104 (31 to 100), 1,571 (101 to 333), 206 (334 to 1,000) and 9 (over 1,000). 160 of them
use VMX128: 154 opcode forms, 1,932 vector instructions (155 forms and 9,073 instructions over everything in the
catalog that uses VMX128, refused functions included).

**What has been judged** (3,771 of the 9,759 functions have a harness; 2,508 have a verdict, from 12 chunks of 400):

| | Functions | PASS | FAIL | NOVECTORS |
|---|---|---|---|---|
| Scalar | 2,453 | 2,161 | 16 | 276 |
| Vector | 55 | 27 | 1 | 27 |
| All | 2,508 | 2,188 | 17 | 303 |

NOVECTORS means the prepare found no valid input (or the translator refused the function), so nothing was judged: the
reasons seen are `executes code outside its body` (a tail-call thunk: about a quarter of the small functions),
Direct3D pack/unpack (refused, no ground truth; about 43% of the vector functions) and the oracle's time budget
(`vector generation did not finish in time`).

**Failures found, and two translator causes fixed** (commit f29cc3e). The first 2,411 judged functions had 43 failures.
Two causes were found by reading the translated C of six of them, then confirmed on all 43 in a private copy of the
tree: 29 of the 43 pass with the two fixes and nothing else changed.

- *A float condition was turned around.* The structurer wrote `if (!(f0 < c)) goto L` as `f0 >= c`, which is false for a
  NaN where the machine falls through. `expr.render` now records the text of every relational float comparison and
  `structure.neg` keeps those as `!(...)`. Equality turns around exactly and still does.
- *Every volatile register was zeroed after a call.* The judge's mock keeps the ones the callee never writes (meta
  `"keeps"`, section 3): under LTCG a caller reads `r5` after calling a leaf that never touches it. The translator
  ignored `keeps`; `Signature.keeps` now carries them and `call()` skips their clobber. This removed the whole class
  `argument r8 expected <input value>, got 0` and `invalid guest memory access: read 00000000`.

The same 2,508 functions judged again with the fixes: 17 failures, none of them lost from the first run's passes.
The 17: 9 read their own machine code (`R8: read machine code at ...`; functions of 203 to 489 instructions; the cause
is not examined), 3 pass a pointer-valued argument register that the C leaves 0 (821ba7d8, 8225fcc0, 822fbd38), 2
write a different value to memory (82200ca8, 82222f90), 1 returns f1 from a function that only sets r3's bits (821c7bc8:
the return-kind guess), 1 vector function whose hidden set fails on a NaN sign (822513b8, open question 11) and 1
scalar function that fails 2 of 48 hidden vectors (82294260). The last two groups are not yet diagnosed.

**Vector coverage.** The 27 passing vector functions exercise 52 of the 154 forms, 23 of them in two or more functions.
That is enough to say the phase works on game code (27 functions with no fault found, 1 failure that is the scalar NaN
rule), and not enough to say the whole instruction set is right: the other 102 forms are in functions not judged yet,
refused (Direct3D pack/unpack), or out of time. 114 Too Human forms do not occur in Mega Man 10 at all, so Too Human
stays the stress test for vector code and for large functions (the mutation check above ran on its slice).

**How long.** Prepare is the cost, and it grows with function size. Chunks 0 to 8 (functions up to 30 instructions and
the vector functions) took 15 to 55 minutes each (4.4 hours for about 3,700 functions, 2 Ghidra slots). Chunks 9 to 24
(31 instructions and up, and the largest) all hit their 55-minute bound: 25 to 60 functions per chunk-hour per driver, so
about 2 minutes of one slot per function. About 6,000 functions are left; at the present pace (4 slots, with the Too Human
proof and the saturn-tx job on the same machine) that is **60 to 120 hours**; the judge adds about 5 minutes per
400 functions, 2 to 4 hours in all. My earlier figure of 15 to 20 hours for prepare was taken from the small functions
and is wrong for the whole game. Levers, not yet used: more slots (the machine was at 25% CPU and 10 GB free RAM),
and not running the oracle for a function the translator will refuse anyway (a tail-call thunk): about a quarter of
the small functions are of that kind and each still costs a prepare.

`mm10_super.ps1` carries the run on without anyone: prepare, then judge, chunk after chunk in rounds (each step bounded to
3,300 s, resumable, 4 slots), up to 120 hours (the deadline is kept in a file, so a restart does not restart the clock), then
judges every chunk once more with the final translator and writes `mm10_final.txt`. It keeps no state but the harness
directories and per-chunk markers, so `mm10_start.ps1` (which starts it only if it is not running) brings it back after a
restart of the machine; `mm10_status.ps1` shows where it is. Nothing in it needs the user.
