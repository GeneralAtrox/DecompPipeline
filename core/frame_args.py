"""R12 (port phase C): raw C that passes 0 where the original passes an address in its own stack frame.

The original computes such an argument as `mov r15,rN` (plus `add #imm,rN`) before a call: a local whose address
the callee reads or fills (a cursor, an out vector). When no test vector reaches the call, the judge never compares
it, and TC's matched raw C had dropped the address to 0 in four functions (8c043cb4, 8c048ad8, 8c03ec88,
8c01af38): readable C copied it, and the native port read or wrote address 0.
1929 of TC's 8814 call sites were unreached at the time.

The rule, from the packet's asm alone: when every call to callee C passes an r15-derived value in rN, a tc_call of C
in work.c whose args.r word for rN is a literal 0, or a variable that is only ever assigned 0, fails the lint.
"""
import re

from . import lint, sh4_asm

CALL = re.compile(r"\btc_callf?\(\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*,")
ASSIGN = re.compile(r"\b(?:a|args)\.r\[([0-3])\]\s*=\s*([^;]+);")
ZERO = re.compile(r"\(?\s*(?:\(\s*u32\s*\)\s*)?0(?:[xX]0+)?[uU]?\s*\)?")
NO_WRITE = ("cmp/", "tst")
WINDOW = 10


def _seq(insns, i):
    """The straight-line instructions that run before the call at i: back to the previous call or branch (at most
    WINDOW), then the delay slot."""
    j = i
    while j > 0 and i - j < WINDOW:
        y = insns[j - 1]
        if y.call_target() or y.slot or y.mn.startswith(("b", "j", "rts")):
            break
        j -= 1
    return insns[j:i] + ([insns[i + 1]] if i + 1 < len(insns) and insns[i + 1].slot else [])


def frame_args(insns):
    """{callee: {arg index 0..3: [(call address, r15 offset)]}} for argument registers holding r15 + k at a call,
    and {callee: number of calls}."""
    found, calls = {}, {}
    for i, x in enumerate(insns):
        callee = x.call_target()
        if not callee:
            continue
        calls[callee] = calls.get(callee, 0) + 1
        off = {}
        for y in _seq(insns, i):
            if len(y.ops) != 2 or not re.fullmatch(r"r[4-7]", y.ops[1]):
                continue
            k = int(y.ops[1][1:]) - 4
            imm = re.fullmatch(r"#(-?0x[0-9a-fA-F]+|-?\d+)", y.ops[0])
            if y.mn == "mov" and y.ops[0].lower() == "r15":
                off[k] = 0
            elif y.mn == "add" and imm and k in off:
                off[k] += int(imm.group(1), 0)
            elif not y.mn.startswith(NO_WRITE):
                off.pop(k, None)
        for k, o in off.items():
            found.setdefault(callee, {}).setdefault(k, []).append((x.addr, o))
    return found, calls


def _zero_vars(code):
    names = set(re.findall(r"\b[us]32\s+(\w+)\s*=\s*0[uU]?\s*;", code))
    return {n for n in names if len(re.findall(rf"\b{n}\s*(?:[-+*/|&^]|<<|>>)?=(?!=)", code)) == 1}


def lint_frame_args(src, asm_text):
    """R12 errors for work.c (src) against the function's asm listing."""
    if not asm_text:
        return []
    found, calls = frame_args(sh4_asm.parse(asm_text))
    always = {c: {k: v for k, v in regs.items() if len(v) == calls[c]} for c, regs in found.items()}
    always = {c: r for c, r in always.items() if r}
    if not always:
        return []
    code = lint.strip_comments(src)
    zero = _zero_vars(code)
    errors, pending = [], {}
    for line in code.splitlines():
        for m in ASSIGN.finditer(line):
            pending[int(m.group(1))] = m.group(2).strip()
        m = CALL.search(line)
        if not m:
            continue
        callee = m.group(1).lower()
        for k, sites in sorted(always.get(callee, {}).items()):
            e = pending.get(k, "")
            if ZERO.fullmatch(e) or e in zero:
                at, o = sites[0]
                errors.append(f"R12: at the call of {callee}, the original passes r{k + 4} = r15 + 0x{o:x} (an "
                              f"address in its own stack frame, asm {at:08x}), but work.c passes `{e}`: pass that "
                              "frame address, the local whose address the callee reads or fills")
        pending = {}
    return sorted(set(errors))
