"""The readable stage's agent sandbox (split from core/refine.py in batch 25): TASK.md, check.py, the lean headers
and context.md of one function, and the read-only tree hash that the tamper check compares.

The sandbox is lean (core/readable_lean): context.md holds every declaration and type the function needs (struct
projections and forward declarations included, core/lean_select); index.txt lists the rest. Everything else in
core/refine.py uses these through refine (refine.build_sandbox, refine.TASK, ...).
"""
import os
import shutil
import stat

from . import agent_cli, asm_pool, check, global_extents, header_snapshot, paths, readable, readable_abi, readable_lean
from . import readable_stack
from . import readable_check as rcheck
from .readable_context import context_md

TYPES_STUB = ("/* The types and constants of this function are in context.md (\"Types and constants\").\n"
              " * The check builds work.c with the project's full headers. */\n")

CHECK_LAUNCHER = """# Checks work.c on the dev test set. The official verdict (dev + hidden) is computed after your session.
import subprocess, sys
sys.exit(subprocess.call([sys.executable, r"{tool}", "--project", r"{project}", "check", "{addr}", "--agent",
                          "--work", r"{work}"{headers}]))
"""


def check_launcher(addr, work):
    """The sandbox's check.py: the official check, against this run's header snapshot (core/header_snapshot)."""
    snap = header_snapshot.ACTIVE
    return CHECK_LAUNCHER.format(tool=paths.TOOL / "refine.py", project=paths.ROOT, addr=addr, work=work,
                                 headers=f', "--headers", r"{snap.dir}"' if snap else "")

TASK = """# TASK: rewrite {name} (0x{addr}) as readable C with exactly the same behaviour

A machine judges your work; only the check result counts. Working directory: {box}

`raw.c` is a verified, exact translation of the original machine code, written against a raw API (memory through
rd32/wr32 on u32 guest addresses, calls through tc_call). Rewrite it in `work.c` as normal, readable C, the way the
original programmer would have written it. Define exactly:

    {proto}

It must behave exactly like raw.c on the same tests: the same return value, final memory contents, calls (targets,
order, arguments) and xf0-xf15.

## Files (everything you need is here; never read, list or search outside this directory)

- `context.md`: read it first. The parameter mapping, every declaration raw.c needs (callees, globals, constants),
  and the definitions of the types and constants they use. A big struct shows only the members raw.c reaches:
  `u8 _pad_0x40[0x20];` stands for members not shown (never use a `_pad_` name; the check builds with the full
  definitions).
- `raw.c`: the verified raw version. Its behaviour is the specification.
- `work.c`: your file, and the only file you may change.
- `index.txt`: only if you need a type or constant that context.md lacks; everything listed can be used by name.
- `game.h`: the declarations of context.md, for the build (its types are in context.md only). `rd_rt.h`: u8..s32,
  u64, f32 and the sh4_* helpers.

Bash runs one command only, exactly `python check.py` (no cd, flags, pipes or redirection; anything else is refused).
It prints DEV PASS or DEV FAIL with the differences, and always exits 0.

## Rules (checked automatically)

- R1. Edit only work.c. Its only preprocessor line is `#include "game.h"`.
- R2. Define exactly the one function above (that exact prototype) plus `static` helper functions.
- R3. No rd8/rd16/rd32/rdf32, wr8/wr16/wr32/wrf32, tc_call, TcArgs. Access memory through typed pointers, struct
  fields, arrays and the named globals. Never write a guest address (0x8c000000-0x8cffffff) as a number.
- R4. Call other functions by name, with the arguments raw.c passes (the check compares them); keep every call, in
  order. A function still named fn_8c...... has raw u32 parameters: pass pointers as `(u32)(uintptr_t)ptr`, convert
  a returned address with `(Type *)(uintptr_t)`; its parameters are named by where they go (param_r4; param_fr4
  takes a float's bits; param_s2 is a stack word). Keep raw.c's sh4_r/sh4_set_r and sh4_fr/sh4_set_fr/sh4_xf calls
  where it has them: a callee that reads r8..r14 or fr12..fr15 (context.md lists them) gets them from
  sh4_set_r(N, v) / sh4_set_fr(N, v) right before the call. A variadic callee (`...`) takes its extra arguments as
  in C; pass each as the 32-bit value raw.c passes. When your own function is variadic, read its variable arguments
  with va_list/va_start/va_arg(ap, u32)/va_end (one 32-bit word each) and pass a va_list on as raw.c passes its
  stack pointer: the check passes the entry stack words and requires the va_list to point at the first of them.
  Never use a result of a function declared void (no cast to a value-returning type): a register such a callee
  does not write still holds what the caller put there. When raw.c calls through a value a void callee "returned"
  (the same function address loaded once and called twice), call that function by name each time.
- R5. Floats: f32 only (no double). Keep every float operation and its order, so results stay bit-identical.
  Integer code may be restructured freely. Keep the sh4_* helpers raw.c uses (sh4_fsqrt_rm, sh4_fsrra, ...). The
  FPU runs with FPSCR.DN=1 (a denormal operand counts as a signed zero, a denormal result becomes one); plain C
  float arithmetic and the sh4_* helpers already behave so, and follow FPSCR.RM. fabs, fneg and moves only touch
  bits. Code doing arithmetic on float bits must treat a denormal input as zero itself.
- R6. No global or static variables, no string literals, no inline assembly, no volatile, no extern, no initializer
  list with more than 32 elements. Zero compiler warnings (MSVC /W4, every warning is an error).
- R7. Never special-case test inputs: a hidden test set is checked after your session.

## Style (this is the point of the task)

- Use the struct types, field names, constants and global names. Pointer parameters are typed.
- Name locals by meaning (count, header, quad), never by register (r13, fr4) or offset.
- Loops as for/while, early returns where natural, one statement per line, no gotos.
- Load a pointer from a global once into a local instead of on every line.
- A short comment above the function says what it does. Comment only what is not obvious.
- If a struct lacks a field you need, access it as `*(u32 *)((u8 *)p + 0x10)` and name it in your final message.

## Procedure

1. Read context.md and raw.c. 2. Write work.c. 3. Run `python check.py`. 4. On DEV FAIL fix the cause, run again.
5. Stop at DEV PASS or after 10 runs. Final message, one line: `RESULT: PASS` (add `MISSING FIELDS: ...` if any)
   or `RESULT: FAIL <the remaining difference>`.
{previous}"""


LARGE_NOTE = """
## A long function

raw.c is long. Work top-down in raw.c's order and keep its structure. Write work.c as you go instead of planning
the whole function first; run `python check.py` as soon as it builds and fix what it reports with small edits. Where a record has no struct, use the byte offsets and list them under MISSING FIELDS.
"""


def sandbox_decls(reg, addr, meta):
    """({addr: signature as game.h declares it}, data function addrs): the function itself, its callees (raw-named
    ones with parameters named by storage) and the functions raw.c uses as data."""
    sigs = {c["addr"]: readable_abi.storage_named(reg, c, rcheck.callee_storage(paths.HARNESS / addr, c))
            for c in meta["callees"]} | {addr: meta["signature"]}
    return sigs, rcheck.data_functions(reg, addr, meta)


def stub_c(reg, proto):
    """A work.c that does nothing but is valid C for the prototype (/W4: every parameter referenced)."""
    _, _, params = readable.parse_proto(proto)
    body = "".join(f"    (void){n};\n" for _, n in params)
    rk = reg.kind(readable.parse_proto(proto)[0])
    body += {"v": "", "f": "    return 0.0f;\n"}.get(rk, "    return 0;\n")
    return f'#include "game.h"\n\n{proto}\n{{\n{body}}}\n'


def build_sandbox(cfg, reg, addr, meta, prev_work, previous_text):
    box = paths.SANDBOX / f"rd_{addr}"
    if box.exists():
        for p in box.rglob("*"):
            os.chmod(p, stat.S_IWRITE)
        shutil.rmtree(box)
    box.mkdir(parents=True)
    sigs, data = sandbox_decls(reg, addr, meta)
    raw_text = (paths.MATCHED / f"{addr}.c").read_text(encoding="utf-8")
    ext = global_extents.for_process(cfg, reg)  # names the pointer constants raw.c passes (context, game.h)
    lean = readable_lean.headers_for(reg, meta, list(sigs) + data, sigs, raw_text, ext,
                                     readable_lean.current_text(readable.SRC / f"{addr}.c", prev_work))
    (box / "game.h").write_text(readable.game_h(reg, addrs=list(sigs) + data + lean["functions"], raw_sigs=sigs,
                                                lean_globals=lean["globals"]), encoding="utf-8")
    (box / "types.h").write_text(TYPES_STUB, encoding="utf-8")  # batch 25: the types are shown once, in context.md
    (box / "index.txt").write_text(lean["index.txt"], encoding="utf-8")
    shutil.copyfile(rcheck.RUNTIME_RD / "rd_rt.h", box / "rd_rt.h")
    raw_box = readable_stack.annotate_raw(raw_text, reg, addr, meta)  # zero pool placeholders: the image's word
    raw_box = asm_pool.fill_raw(raw_box, cfg["binary_path"].read_bytes(), check.IMAGE_BASE, int(addr, 16))
    (box / "raw.c").write_text(raw_box, encoding="utf-8")
    (box / "context.md").write_text(context_md(reg, addr, meta, cfg, raw_text, lean["types_body"], sigs, ext),
                                    encoding="utf-8")
    proto = reg.proto(addr, meta)
    name = readable.parse_proto(proto)[1]
    if agent_cli.is_large(cfg, len(raw_text.encode("utf-8"))):  # batch 26
        previous_text = LARGE_NOTE + (previous_text or "")
    task = TASK.format(addr=addr, name=name, proto=proto, box=box, previous=previous_text)
    (box / "TASK.md").write_text(task, encoding="utf-8")
    (box / "check.py").write_text(check_launcher(addr, box / "work.c"), encoding="utf-8")
    if prev_work and os.path.exists(prev_work):
        shutil.copyfile(prev_work, box / "work.c")
    else:
        rk = reg.kind(readable.parse_proto(proto)[0])
        stub = {"v": "", "f": "    return 0.0f;\n"}.get(rk, "    return 0;\n")
        (box / "work.c").write_text(f'#include "game.h"\n\n{proto}\n{{\n{stub}}}\n', encoding="utf-8")
    for p in box.rglob("*"):
        if p.is_file() and p.name != "work.c":
            os.chmod(p, stat.S_IREAD)
    return box, task


def tree_hashes(box):
    import hashlib
    return {str(p.relative_to(box)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(box.rglob("*")) if p.is_file() and p.name != "work.c"}
