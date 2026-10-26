"""The worker's sandbox for the Xbox 360 target: TASK.md, context.md, the runtime header and the packet (the
counterpart of targets/gamecube_ppc/ppc_agent.py, installed by hooks.build_sandbox)."""
import os
import shutil
import stat

from core import agent as core_agent
from core import paths
from core.packet_split import packet_files

import x360_compare as cmp

TASK = """# TASK: decompile fn_{addr} into C that behaves exactly like the original PowerPC code

A machine judges your work: explanations and claims are ignored, only the official check's PASS/FAIL counts.
Working directory: {box}

## Goal

Write ONE C function in `work.c`:

    {signature}

For every test input it must produce exactly the same observable effects as the original
machine code at 0x{addr} (Xbox 360, Xenon PowerPC, 64-bit registers, big endian), compared bit for bit:

1. the same return value ({ret_reg}),
2. the same final bytes at every guest memory address the original writes (outside its stack),
3. the same calls: same targets, same order, same argument registers (context.md lists which are compared).

## Files in this directory

- `asm.txt`: the original disassembly. This is the authority. A long function is split into `asm_1.txt`,
  `asm_2.txt`, ... (at most 400 lines each). Read every part, in order.
- `ghidra.c` (or `ghidra_1.c`, ...): Ghidra's decompiler output. It is only a guide and it can be wrong. When it
  disagrees with asm.txt, follow asm.txt.
- `context.md`: parameter registers, callees, globals, and test coverage.
- `tc_rt.h`: the runtime API. It is the only header you may use.
- `work.c`: your file, and the only file you may change.

Bash runs one command only, exactly `python check.py` (no cd, flags, pipes or redirection; anything else is refused).
It runs the dev test set, prints DEV PASS or DEV FAIL with the differences, and always exits 0.

## Rules

Every rule is enforced automatically. Breaking one fails the attempt.

- R1. Edit only work.c. Do not create, rename or delete any file.
- R2. work.c contains `#include "tc_rt.h"` once and no other preprocessor lines (#define, #if, #pragma, other #include).
- R3. Define exactly one non-static function, fn_{addr}, with exactly the signature above. Every helper function must be `static`.
- R4. All guest memory goes through rd8/rd16/rd32/rd64/rdf32/rdf64/rd16r/rd32r and wr8/wr16/wr32/wr64/wrf32/wrf32d/
  wrf64/wr16r/wr32r. Guest pointers are u32 values (the low word of the address register), never C pointers.
  Memory is big endian; the rd/wr functions already handle that.
- R5. Make every call the original makes with tc_call or tc_callf: every `bl` to a function listed in context.md
  (kernel and XAM imports included), every call through a register (`bctrl`), and every `b` to another function
  (a tail call: `return tc_call(...)`). Use the same target, the same order and the same argument registers. Never
  implement a callee and never skip a call. A `bl` to `__savegprlr_N`, `__restgprlr_N`, `__savefpr_N` or
  `__restfpr_N` (context.md names them) is part of the prologue or epilogue, not a call. Skip it.
- R6. No global or static variables, no static locals, no string literals, no initializer list with more than 32
  elements, no inline assembly, no extern, no volatile.
- R7. Never special-case test inputs. After your session, a hidden test set with different random inputs is run.
  Code that only fits the visible tests fails there.
- R8. Never read machine-code bytes, and never interpret or emulate instructions. Reading any instruction address
  fails the check.
- R9. Floats: a float register always holds a double, so use f64 for float registers and values (write `f64`, never
  `double`). Write one C operation per original float instruction, in the original order. A single-precision
  instruction (fadds, fsubs, fmuls, fdivs, frsp) rounds its double result to single: `(f64)(f32)(a + b)`. lfs is
  `(f64)rdf32(addr)`, lfd is `rdf64(addr)`, stfs is `wrf32d(addr, x)`, stfd is `wrf64(addr, x)`. Use the ppc_
  helpers in tc_rt.h for every multiply-add (fmadd, fmsub, fnmadd, fnmsub and their single forms), fctiw/fctiwz/
  fctid/fctidz, fcfid, fsel, fabs, fnabs, fneg, fsqrt, never your own version.
- R10. Never try to read hidden tests, expected outputs or files outside this directory. The file tools are confined
  to this directory, and every attempt is logged.
- R11. work.c must build with zero compiler warnings (MSVC /W4; every warning is an error). Typical fixes: an
  explicit cast for a narrowing conversion such as `(u32)`, `(u16)` or `(s32)`; `(void)param_1;` for a parameter the
  original never reads; removing unused locals.
- R12. When the original passes an address in its own stack frame (`addi r3,r1,0x50` before the `bl`), pass that
  same guest address (r1 at that point plus the offset), never 0, and keep the local in guest memory there.

## Xenon PowerPC reminders

- Registers are 64-bit. `add`, `subf`, `neg`, `mullw`, `ld`, `std` work on all 64 bits; `lwz`/`lhz`/`lbz` zero-extend
  into the full register, `lwa`/`lha`/`extsw`/`extsh`/`extsb` sign-extend, `clrldi rA,rS,32` clears the upper word.
  `cmpw`/`cmplw` compare the low words, `cmpd`/`cmpld` all 64 bits. A dotted instruction (`add.`, `rlwinm.`) sets
  cr0 from the whole 64-bit result, signed. The return value r3 is compared in all 64 bits: if the original leaves
  a value whose upper word is not the zero- or sign-extension of the low word, the signature returns u64.
- Arguments: r3-r10 (integers, pointers; 64-bit) and f1-f13 (floats). Results: r3 (integers), f1 (floats). Set
  args.r[0..7] = r3..r10 and args.f[0..12] = f1..f13 before tc_call/tc_callf; stack arguments are doublewords at
  r1+0x50, r1+0x58, ... (args.s[0..7]).
- r1 (the stack pointer) is 0x{entry_sp:08x} at entry. `stwu r1,-N(r1)` makes the frame: r1 is then
  0x{entry_sp:08x} - N, and a local at d(r1) is the guest address r1 + d. Saved registers, the saved LR
  (`mflr r12` / `stw r12,-8(r1)`) and the `__savegprlr`/`__restgprlr` helpers are bookkeeping: the stack is not
  compared.
- r13 is the processor block (0x7fff0000) for the whole program: `lwz r11,0(r13)` reads the global at 0x7fff0000.
  Read globals with rd32/rd64 of their address, never a constant copy: tests may change them.
- `rlwinm`, `rlwimi`, `rlwnm` and the `rld*` rotates: use the ppc_ helpers (they also give the 64-bit upper word).
  `slw`/`srw`/`sraw`/`srawi` and `sld`/`srd`/`srad`/`sradi` likewise; `srawi` + `addze` (signed divide by a power of
  two) needs the carry: `ppc_srawi(x, n, &ca)` then `ppc_adde(r, 0, ca, NULL)`.
- `twi`/`tw` (traps, often `twllei rX,0` before a divide) never fire on the test inputs: skip them.
- `fctiwz f0,f1; stfd f0,d(r1); lwz r3,d+4(r1)` is `r3 = (u32)ppc_fctiwz(f1)` (zero-extended by lwz).
  `fcmpu`/`fcmpo` then a branch: a NaN compares unordered (every one of <, >, == is false).
- A callee's results: tc_call returns its r3 (64-bit), tc_callf its f1 (both scripted by the test harness).

## Procedure

1. Read context.md, then every asm part and ghidra part listed in context.md.
2. Write fn_{addr} in work.c by translating asm.txt block by block. Keep the original control flow and the original
   order of memory writes and calls.
3. Run `python check.py`.
4. If it prints DEV FAIL, read the listed differences, fix the cause in work.c, and run it again.
5. Stop as soon as it prints DEV PASS, or after you have run check.py 12 times.
6. Your final message must be exactly one line: `RESULT: PASS`, or `RESULT: FAIL <one sentence naming the remaining
   difference>`.
{previous}"""


def ret_reg(meta):
    return {"void": "none (void)", "f32": "f1", "f64": "f1"}.get(meta["ret"], "r3, all 64 bits")


def context_md(meta, summary):
    files = [p.name for p in packet_files(paths.PACKETS / meta["addr"])]
    lines = [f"# fn_{meta['addr']}", "", f"Signature: `{meta['signature']}`", "",
             "Source files, in reading order: " + ", ".join(f"`{f}`" for f in files), "", "## Parameters", ""]
    if not meta["params"]:
        lines.append("None.")
    for p in meta["params"]:
        where = p["storage"] if p["storage"] != "stack" else f"stack, at r1+0x{p['stack_offset']:x} at entry"
        lines.append(f"- {p['name']} ({p['kind']}): {where}")
    lines += ["", f"r1 is 0x{cmp.SP:08x} at entry: the function's stack frame is guest memory just below it.",
              "", f"Return: {meta['ret']} in {ret_reg(meta)}", "", "## Calls the original makes", ""]
    if not meta["callees"]:
        lines.append("Direct calls: none. Calls through registers (bctrl) or tail jumps may still exist; check asm.txt.")
    for c in meta["callees"]:
        regs = ", ".join(c["regs"]) or "none"
        what = f" ({c['import']})" if c.get("import") else ""
        lines.append(f"- 0x{c['addr']}{what}: Ghidra's guess `{c['signature']}`. Argument registers compared: {regs}")
    if meta.get("frame_helpers"):
        lines += ["", "Frame helpers (skip these bl, R5): " + ", ".join(meta["frame_helpers"])]
    if any(r.startswith("s") for c in meta["callees"] for r in c["regs"]):
        lines += ["", "sN is a stack argument: the doubleword at r1+0x50+8N when the call is made. Set it in args.s[N]."]
    lines += ["", "Every call gets a scripted return value in r3 and f1 from the test harness. Use what tc_call or",
              "tc_callf returns.", "", "## Globals the code names (r13 offsets and lis/addi pairs)", ""]
    lines += [f"- 0x{g}" for g in meta["globals"]] or ["None."]
    if meta.get("ppc_special"):
        lines += ["", "## Instructions with a ppc_ helper", "",
                  ", ".join(meta["ppc_special"]) + ". Use the matching ppc_ helpers in tc_rt.h (R9)."]
    lines += ["", "## Test vectors", "",
              f"Dev vectors: {summary.get('dev_valid')}, hidden vectors: {summary.get('hidden_valid')}.",
              f"Basic blocks reached by the dev vectors: {summary.get('blocks_hit_dev')} of {summary.get('blocks_total')}.",
              "Inputs are random: pointers into a scratch area at 0x40000000 and a heap at 0x40001000, integers whose",
              "upper word is zero, the sign-extension or random, floats, and randomised bytes at the referenced globals.",
              "Unwritten memory keeps the image's contents (or zero)."]
    return "\n".join(lines) + "\n"


def build_sandbox(cfg, addr, meta, summary, prev_work, previous_text):
    box = paths.SANDBOX / addr
    if box.exists():
        for p in box.iterdir():
            os.chmod(p, stat.S_IWRITE)
        shutil.rmtree(box)
    box.mkdir(parents=True)
    packet = paths.PACKETS / addr
    sources = [p.name for p in packet_files(packet)]
    for name in sources:
        shutil.copyfile(packet / name, box / name)
    shutil.copyfile(paths.RUNTIME / "tc_rt.h", box / "tc_rt.h")
    (box / "context.md").write_text(context_md(meta, summary), encoding="utf-8")
    task = TASK.format(addr=addr, box=box, signature=meta["signature"], ret_reg=ret_reg(meta), entry_sp=cmp.SP,
                       previous=previous_text)
    (box / "TASK.md").write_text(task, encoding="utf-8")
    (box / "check.py").write_text(core_agent.CHECK_LAUNCHER.format(tool=paths.TOOL / "decomp.py", project=paths.ROOT,
                                                                   addr=addr, work=box / "work.c"), encoding="utf-8")
    if prev_work and os.path.exists(prev_work):
        shutil.copyfile(prev_work, box / "work.c")
    else:
        stub = {"void": "", "f32": "    return 0.0;\n", "f64": "    return 0.0;\n"}.get(meta["ret"], "    return 0;\n")
        (box / "work.c").write_text(f'#include "tc_rt.h"\n\n{meta["signature"]}\n{{\n{stub}}}\n', encoding="utf-8")
    for name in core_agent.SANDBOX_FILES + tuple(sources):
        os.chmod(box / name, stat.S_IREAD)
    return box, task
