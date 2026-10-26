# The readable stage on the Saturn and the GameCube

The Dreamcast's readable stage (core/readable_check, runtime_rd, core/refine) turns proven raw C into readable C and
judges it on the same test vectors as the raw C. This note says what the Saturn (SH-2) and GameCube (PowerPC) versions
keep, what they change, and why. The judge is not weakened anywhere: every difference below either keeps a Dreamcast
rule or adds a stricter one.

## What stays the same

- The verdict: the readable function runs on the original's dev and hidden vectors, every call to another original
  function is caught and compared (target, order, arguments), and the final memory, the result and the call records
  must equal the original's. A readable build of the raw C itself must give the raw judge's verdict (tools/
  readable_parity.py; the parity numbers are in the branch's hand-over note).
- The agent's flow: refine.preflight on a do-nothing stub, the lean sandbox (context.md, raw.c, game.h, index.txt),
  check.py, the official verdict after the session.

## Big-endian guest memory (both)

Both consoles store values most significant byte first; the host (x86) stores them least significant byte first.

- The readable runtime maps guest RAM at its own address with the guest's bytes unchanged, so a guest pointer is a
  host pointer and memory compares byte for byte with the original's.
- Readable C reads and writes every multi-byte value in guest memory through rd_be.h's be_ types (be_u16 ... be_f64,
  BE_PTR(T), be_fnptr) with be_get / be_set. Each is its own struct, so a plain use does not compile.
- Lints (core/readable_be): BE1, the headers keep no plain multi-byte member, global or pointer-to-plain prototype;
  BE2, readable C declares or casts no pointer to a plain multi-byte value. The judge does not depend on them (a value
  read the wrong way round changes behaviour and fails); they turn the mistake into a clear message and keep the C
  portable to the native port.
- rd_be.h and rd_be.c are identical in both targets (tests/test_readable_be.py checks it).

## Saturn (SH-2)

- The SH-2 has no FPU: an f32 parameter or result goes where an int goes, as its bits (judge_memory "float_regs":
  false; core/readable_abi places it in r4..r7 / r0, and the call trap does the same).
- Memory: one 2 MB section holds both work RAM banks. The high bank is mapped at 0x06000000 and its cache-through
  view 0x26000000. The low bank (0x00200000) cannot be mapped at its own address because Windows keeps that range for
  itself, so it is mapped at its cache-through view only, and a function whose raw C or vectors touch it is refused
  with BE3 (judge_memory "unmapped") instead of being judged. Grandia's matched functions touch none.
- The core readable judge, sandbox and refine flow are used as they are, with the Dreamcast constants read from
  target.json judge_memory: the stack end ("stack_hi"), main RAM ("ram", for RR3 and the literal-pool constants) and
  "float_regs". Without these keys every value is the Dreamcast's.
- The agent's TASK is targets/saturn_sh2/readable_task.md: SH-2 wording, the big-endian rules, no float rules.

## GameCube (PowerPC)

- Floats: a float register holds a double. f64 is the register; a single-precision result is (f64)(f32)(...);
  be_set_stfs is stfs (the 750CL's store conversion). rd_rt.h keeps the ppc_* helpers.
- Arguments: the PowerPC EABI (ppc_readable_abi): integers r3..r10, floats f1..f8 counted apart, a 64-bit integer in
  an odd-even pair, then the stack words at r1+8. The readable prototype is placed by these rules; a prototype that
  does not name the original's parameters gets other values and fails.
- The judge glue is the target's own (ppc_readable.check_readable, through the hook "readable_check"): its harness,
  call records and comparison are ppc_compare's, not the SH-4's. A pointer a readable function passes into its own
  stack frame is paired with the original's frame slot (as on the Dreamcast).
- Sandbox: the core sandbox with two hooks, "readable_sandbox_decls" (callees keep their raw signatures) and
  "readable_context_md" (context.md maps the parameters by the EABI). The TASK is targets/gamecube_ppc/
  readable_task.md.
- Variadic functions under test are refused for now (none among Skies of Arcadia's matched functions).

## Shared files changed (overlaps for merging)

- core/compare.py: STACK_HI, RAM, FLOAT_REGS, UNMAPPED from judge_memory (Dreamcast defaults).
- core/readable_check.py: the "readable_check" / "readable_check_raw" hooks, RD_SOURCES adds rd_be.c when present,
  STACK_HI from compare, data_functions takes any 8-digit address, readable_be.target_lint (nothing on the Dreamcast).
- core/readable_abi.py, readable_lint.py, readable_stack.py, readable_outptr.py, raw_consts.py,
  readable_context.py: the Dreamcast constants read from compare; no-FPU placement.
- core/readable_sandbox.py: rd_be.h copied when present, the target's readable_task.md, the two GameCube hooks, and
  a do-nothing return of `0` for any result type.
