# Sonic Adventure's SDK as a speed-up for Dreamcast work (2026-11-01)

Source: https://github.com/reshaped/sonic-adventure at 25a3d31, cloned on build box B outside every repository. It has no
licence, so it is reference and answer key only. Names, addresses, call shapes and understanding were used. No code,
data or text was copied here; the scripts in this folder are ours.

**Plain answer: it adds little for names, about 2 to 4% of each game's unnamed library functions. Its use is as a
readable explanation of the SDK and as an independent check.**

## Build

No download was needed. Our r9 SHC built 538 of 550 sources, giving 527 objects and 2,650 functions after cutting.
The 12 failures need disc-generated data or hit an assembler error.
Only 228 of 524 objects have the section size Sonic's image declares (83,923 bytes), so our compiler differs from
Sonic's R10 and R32 for many of them. R10.1 added nothing: 2,555 of 2,559 symbols came out the same as r9.

The not-approved compiler downloads would at most turn some near matches into exact ones. That ceiling is small
(see below), so they are not requested.

## What it adds per game

Method for every exact match: bytes with relocations masked. Near: xmatch shingles at 0.8 or above. "New" means the
function had no name from the registry or FID. Toy Commander has no FID baseline, so its unnamed count is overstated.

| Game | Library functions | Unnamed before | Exact candidates | Refused | Clean | New | Applied | Near, unnamed (refused) |
|---|---|---|---|---|---|---|---|---|
| Vigilante 8 | 3,073 | 843 | 1,044 | 930 | 114 | 29 | 27 | 47 |
| Spawn | 3,677 | 2,275 | 1,644 | 1,491 | 153 | 70 | 68 | 120 |
| Power Stone 2 | 3,052 | 1,954 | 2,532 | 2,456 | 76 | 36 | 35 | 49 |
| Toy Commander | 1,441 | 1,278 | 575 | 511 | 64 | 55 | 43 | 182 |

All near matches refused, including already named functions: V8 380, Spawn 404, PS2 261, TC 208.
Unapplied new names had no call shape to give the registry (asm or missing source). The registry needs a prototype.

Ceiling even with Sonic's exact compiler (new + unnamed near): V8 76, PS2 85, Spawn 190, TC 237.

### Per SDK library (exact candidates / clean / new; near / near and unnamed)

| Library | V8 | Spawn | PS2 | TC |
|---|---|---|---|---|
| kamui | 632/0/0; 5/1 | 1229/45/28; 30/27 | 2204/0/0; 5/4 | 335/0/0; 5/5 |
| ninja | 21/0/0; 2/1 | 15/0/0; 5/0 | 22/0/0; 4/0 | 7/0/0; 2/1 |
| shinobi | 108/61/28; 169/11 | 109/55/32; 172/20 | 105/53/32; 166/18 | 88/51/44; 169/156 |
| shinobi-sound | 2/0/0; 1/0 | none | none | none |
| runtime | 61/18/0; 6/2 | 47/13/0; 6/2 | 50/16/0; 4/1 | 73/10/8; 6/6 |
| sofdec | 83/6/0; 40/1 | 90/4/1; 40/8 | 84/4/1; 39/10 | 6/0/0; 1/0 |
| movie-sdk | 120/29/1; 132/22 | 129/31/4; 105/35 | 43/0/0; 20/7 | 48/0/0; 2/2 |
| loader-sdk | 17/0/0; 25/9 | 25/5/5; 46/28 | 24/3/3; 23/9 | 18/3/3; 23/12 |

Kamui's huge candidate counts are false: tiny functions that match anywhere. Only Spawn gets clean Kamui matches, so
the others use a different Kamui version.

## Guards and their planted tests

A clean match needs at least 16 unmasked bytes, one Sonic name for those bytes, no conflict in a symbol's relocated
word, a library address, and either 64 unmasked bytes or another function of the same object at the right offset.

| Planted wrong answer | Rejected |
|---|---|
| One byte flipped in 30 clean matches of TC's image | 30 of 30 |
| 10 clean functions copied to game-range addresses | 10 of 10 |
| PS2 8c06c038, the real look-alike that disabled idle animations | Refused by four guards (short, ambiguous, game-range, no-object-run) |

The flipped run also lost one unflipped match, because its neighbour no longer confirmed it. That is the guard being
strict. Against FID, clean matches agree 177 times and differ 8; the differences checked are same-bytes twins such as
`ovLockCs` and `mwlMngLockCs`.

No new name is one the kit defines, so applying them swaps no code.

## Names applied

Through `xproject.py names`, on sparse clones, on branch `sonic-sdk-names`. Nothing was pushed or landed.

| Game | Base | Commit | Bundle in the notes folder |
|---|---|---|---|
| Toy Commander | 4f8b58b | 918e5cb | `sonic-names-tc.bundle` |
| Power Stone 2 | 29f0a2c | d5c40f8 | `sonic-names-ps2.bundle` |
| Spawn | f1d530c | 34768d7 | `sonic-names-spawn.bundle` |
| Vigilante 8 | no game repository here | none | `sonic-names-v8-imported.json` |

The route dropped none. Parameter names are generic (`p1`) and types are basic. The call shapes have not been through a port
build, so each game's owner should build before taking its bundle.

## V8's 1,451 functions outside the game range

1,130 identified: FID 1,091, registry 114, Sonic clean 54, of which only 8 are new to FID. 321 have nothing.
The kit replaces 56 now and 79 if the known names were in the registry. V8's boot stubs were sent to its card:
`memcmp` (8c0f6bf0) and `amFileLoad` (8c0fdc40) stubbed were the likeliest faults, by name and return value.

## Stubs

`sonic-sdk-stub-identities.md` in the notes folder covers Spawn's 20 and PS2's 25. Verdicts come from reading callers,
not from swapping a stub in a run. Main faults: `syCblCheck` in both games returning 0 where the kit's returns VGA,
PS2's Kamui 2 render-state calls, and two PS2 game functions stubbed.

## Independent check of the translator and judge

Sample: Toy Commander's copies of SDK runtime functions, the only ones with both a Sonic exact match and a harness.
Everything ran read-only (`mutation_check --report --planted`, `dp_judge` in a mirror). Sources were written by us from
what the SDK's source says each function does, and stayed in a work folder.

- **Translator:** 8 of 8 PASS (8c0827f4 floor, 8c082794 ceil, 8c09396c and 8c09398c series, 8c067d18 divlu,
  8c067c64 divls, 8c067dc0 modls, 8c067ee0 strcmp).
- **Correct versions:** 3 of 3 pass the judge.
- **Wrong versions:** 11 of 14 rejected.

| Function | Wrong versions | Rejected | Survived |
|---|---|---|---|
| floor | 6 | 4 | threshold at 2^31 instead of 2^32; result's sign not put back |
| ceil | 4 | 3 | threshold at 2^31 |
| cosine series | 4 | 4 | none |

**The survivors are real judge blind spots.** Those vectors hold no value from 2^31 to 2^32, and floor's hold no -0.0 where it
matters, so a wrong C passes. The judge was not changed. The translator's output was not run on those inputs.

The readable C of 8c0827f4 is called `Lib_F32Truncate`, but the SDK's source shows it is floor, and its
`g_strImageDecodeNoName + 0x94` is the library's -1.0. Passing the judge proved behaviour, not those names.

## Float traps from the repo's notes, tested here

Confirmed:

- **Our SHC truncates decimal constants.** Compiled on build box B: `0.1F` gives 3dcccccc, `1.0F/9.0F` gives 3de38e38.
- **Our MSVC rounds them to nearest.** `0.1f` gives 3dcccccd, also under `_RC_CHOP`.
- **Discs differ by game.** Truncated and nearest words for common constants:
  TC 64 and 2, PS2 0 and 1,044, V8 0 and 56, Spawn 37 and 223. None of Spawn's 37 lies in a function with readable or
  matched C. TC is the game the trap applies to.
- **Real example:** TC `readable/src/8c01af38.c` writes `0.6f` and `0.1f`; the disc has 3f199999 and 3dcccccc. x86 gets
  each one step higher. `tools/float_gaps.py` already lists it as `readable_lost`, with 5 other functions on TC's list,
  and `core/float_lits.py` rejected the planted forms: `0.1f` against 3dcccccc is refused, the bits form is accepted, a
  wrong `0.2f` is refused.
- **One step in a constant is visible to the judge when the run reaches it.** PS2 8c06af9e: disc word passes, one step
  lower is rejected.
- **Compile flags change a constant quotient under round-toward-zero.** `1.0f / 9.0f` gives 3de38e38 at run time with
  `/fp:strict` and 3de38e39 folded with `/fp:precise`.

**Gap found, not fixed:** all 400 sampled harnesses of PS2 and of Spawn have no `P` line, so the readable runtime judges them
rounding to nearest (`rd_mem.c`, `g_mxcsr_rc` stays 0). All 400 of TC's have `P 00040001`. The port starts in round-toward-zero
(`sh4_rt_set_fpu_mode`). Whether those two games really play with RM=1 at each function was not checked. If they do, their readable C
was judged in a different mode from the one it plays in. Their constant quotients are where it shows: PS2 8c06af9e,
8c0f9df2, 8c103be6.

## Time saved

Estimate, not a measurement.

- **Names:** hours. 173 applied across four games, and our own FID already had most of what this could give.
- **Understanding SDK functions:** the real gain, days across the ports, because the commented source answers what a
  function does and what it returns. It also says where the judge's vectors are thin.
- **Our SDK copy:** `SegaSDK/Dreamcast Shinobi development library` already holds 67 real `.c` sources, so check it first.
- **Nothing for game code or the 144 fps work.**

## Reproduce

`sbuild.py` builds the objects, `stable.py` makes the table, `smatch.py` matches a game, `protos.py` gets call shapes,
and `mkapply.py` builds the workspaces for `xproject.py names`. Paths are given as arguments.
