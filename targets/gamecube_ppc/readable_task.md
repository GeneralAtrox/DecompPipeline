# TASK: rewrite {name} (0x{addr}) as readable C with exactly the same behaviour

A machine judges your work; only the check result counts. Working directory: {box}

`raw.c` is a verified, exact translation of the original GameCube (PowerPC) machine code, written against a raw API
(memory through rd32/wr32 on u32 guest addresses, calls through tc_call). Rewrite it in `work.c` as normal, readable
C, the way the original programmer would have written it. Define exactly:

    {proto}

It must behave exactly like raw.c on the same tests: the same return value, final memory contents and calls
(targets, order, arguments).

## Files (everything you need is here; never read, list or search outside this directory)

- `context.md`: read it first. The parameter mapping, every declaration raw.c needs (callees, globals, constants),
  and the definitions of the types and constants they use. A big struct shows only the members raw.c reaches:
  `u8 _pad_0x40[0x20];` stands for members not shown (never use a `_pad_` name; the check builds with the full
  definitions).
- `raw.c`: the verified raw version. Its behaviour is the specification.
- `work.c`: your file, and the only file you may change.
- `index.txt`: only if you need a type or constant that context.md lacks; everything listed can be used by name.
- `game.h`: the declarations of context.md, for the build (its types are in context.md only). `rd_rt.h`: u8..s64,
  f32, f64 and the ppc_* helpers. `rd_be.h`: the big-endian memory types (read its top comment).

Bash runs one command only, exactly `python check.py` (no cd, flags, pipes or redirection; anything else is refused).
It prints DEV PASS or DEV FAIL with the differences, and always exits 0.

## Big-endian memory (the GameCube's byte order)

The check maps MEM1 at its own address (0x80000000), in the GameCube's byte order: the most significant byte first.
Every value in guest memory wider than a byte has a be_ type (be_u16, be_s16, be_u32, be_s32, be_f32, be_f64,
be_u64, BE_PTR(T) for a pointer, be_fnptr for a function pointer): struct fields, globals and what a pointer
parameter points at. Read one with `be_get(x)` and write one with `be_set(x, value)`; using it as a plain number does
not compile. u8/s8 fields and locals, parameters and results are plain values. Never point a plain `u16 *`, `u32 *`,
`f32 *`, `f64 *` or `T **` at guest memory (checked: BE2): point at the be_ type. `BE_PTR(T)` gives a `void *` from
be_get: assign it to a `T *` local.

## Rules (checked automatically)

- R1. Edit only work.c. Its only preprocessor line is `#include "game.h"`.
- R2. Define exactly the one function above (that exact prototype) plus `static` helper functions.
- R3. No rd8/rd16/rd32/rd64/rdf32/rdf64, wr8/wr16/wr32/wr64/wrf32/wrf32d/wrf64, tc_call, tc_callf, TcArgs. Access
  memory through typed pointers, struct fields, arrays and the named globals. Never write a guest address
  (0x80000000-0x817fffff) as a number.
- R4. Call other functions by name, with the arguments raw.c passes (the check compares them); keep every call, in
  order. A function still named fn_80...... has raw parameters: pass pointers as `(u32)(uintptr_t)ptr`, convert a
  returned address with `(Type *)(uintptr_t)`. A pointer raw.c passes into its own stack frame is the address of a
  local of yours (the check pairs the two). Never use a result of a function declared void.
- R5. Floats: a PowerPC float register holds a double. Keep every float operation and its order, so results stay
  bit-identical: f64 for register values; a single-precision instruction's result is `(f64)(f32)(a + b)`; lfs is
  `(f64)be_get(x)` of a be_f32; stfs is `be_set_stfs(&x, value)` (the 750CL's store conversion: never a plain
  `be_set` of a double into a be_f32). Keep raw.c's ppc_* helpers (ppc_fctiwz, ppc_fmadds, ppc_fsel, ppc_rlwimi,
  ...): they are exact where plain C is not. Integer code may be restructured freely.
- R6. No global or static variables, no string literals, no inline assembly, no volatile, no extern, no initializer
  list with more than 32 elements. Zero compiler warnings (MSVC /W4, every warning is an error).
- R7. Never special-case test inputs: a hidden test set is checked after your session.

## Style (this is the point of the task)

- Use the struct types, field names, constants and global names. Pointer parameters are typed.
- Name locals by meaning (count, header, quad), never by register (r31, f1) or offset.
- Loops as for/while, early returns where natural, one statement per line, no gotos.
- Load a pointer from a global once into a local instead of on every line.
- A short comment above the function says what it does. Comment only what is not obvious.
- If a struct lacks a field you need, access it as `be_get(*(be_u32 *)((u8 *)p + 0x10))` and name it in your final
  message.

## Procedure

1. Read context.md and raw.c. 2. Write work.c. 3. Run `python check.py`. 4. On DEV FAIL fix the cause, run again.
5. Stop at DEV PASS or after 10 runs. Final message, one line: `RESULT: PASS` (add `MISSING FIELDS: ...` if any)
   or `RESULT: FAIL <the remaining difference>`.
{previous}
