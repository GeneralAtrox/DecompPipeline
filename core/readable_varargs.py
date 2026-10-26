"""Readable stage: calls to variadic functions (`...` in the registry prototype) and the stack words judged at them.

SHC passes a variadic function's last named parameter and its variable arguments on the stack (readable_abi.storages;
the call trap copies the caller's remaining cdecl words after the named stack words). The original's callee record
compares only what Ghidra's signature names (sprintf: r4 and s0, the format), so a names entry says how many words a
call passes:
  "varargs": "printf"  the last named parameter is a printf format: one word per conversion and per `*` width or
                       precision (SHC: int, pointer, char and float are one word each; %ll.. two)
  "varargs": "null"    the variable words end with the first zero word (included): a NULL-terminated list
At each call the judge then compares the stack words s0..s7 the original's call passes by that rule, as the original
passed them (the named stack parameters and the variable words; never the caller's frame words after them). Without
"varargs", the named stack parameters are compared. A format the judge cannot read from the initial memory (in the
stack, or written by the function under test) makes it compare all of s0..s7: a FAIL, never a false PASS.
"""
import re

from .compare import FRAME_LO, Memory

RULES = ("printf", "null")
STACK_WORD = 13  # REG_INDEX["s0"]
PRINTF_SPEC = re.compile(rb"%([-+ #0]*)(\*|\d*)(?:\.(\*|\d*))?(hh|h|ll|l|L|q|j|z|t)?([A-Za-z%])")


def variadic_info(reg, addr, place):
    """(the format's / last named parameter's stack word, the first variable word, rule) of a variadic callee whose
    named parameters are placed at place (readable_abi.callee_place)."""
    from .readable import RegistryError, parse_proto
    e = reg.functions.get(addr) or {}
    rule = e.get("varargs")
    name = parse_proto(e.get("proto", f"void fn_{addr}(void)"))[1]
    if rule is not None and rule not in RULES:
        raise RegistryError(f"{name}: \"varargs\" must be one of {', '.join(RULES)}")
    stack = [int(s.split("+")[1]) // 4 for s in place if s.startswith("stack+")]
    last = place[-1] if place else ""
    if not last.startswith("stack+"):
        raise RegistryError(f"{name}: a variadic function's last named parameter is on the stack (SHC), not in {last}")
    return int(last.split("+")[1]) // 4, max(stack) + 1, rule


def printf_words(fmt):
    """Words a printf format consumes."""
    n = 0
    for m in PRINTF_SPEC.finditer(fmt):
        if m.group(5) == b"%":
            continue
        n += (m.group(2) == b"*") + (m.group(3) == b"*") + (2 if m.group(4) == b"ll" else 1)
    return n


def _read_cstr(mem, exp, ptr, limit=512, frame_lo=FRAME_LO):
    """The NUL-terminated bytes at ptr in the initial memory, or None (in the stack, written by the function, or
    longer than limit)."""
    if ptr >= frame_lo:  # the own frame: built at run time (a global below it is memory)
        return None
    out = bytearray()
    for a in range(ptr, ptr + limit):
        if a in exp["writes"]:
            return None
        b = mem.initial(a)
        if b == 0:
            return bytes(out)
        out.append(b)
    return None


def words_passed(info, mem, exp, exp_call):
    """How many stack words s0.. the original's call (exp_call, a record of exp) passes (8: all of them)."""
    fmt_word, first, rule = info
    if rule == "null":
        return next((j + 1 for j in range(first, 8) if exp_call[STACK_WORD + j] == 0), 8)
    if rule == "printf":
        fmt = _read_cstr(mem, exp, exp_call[STACK_WORD + fmt_word])
        return 8 if fmt is None else min(8, first + printf_words(fmt))
    return first


def judged_meta(meta, variadic):
    """meta with s0..s7 compared at the calls of the variadic callees (mask_calls keeps the words past each call's
    arguments out of the comparison)."""
    if not variadic:
        return meta
    words = [f"s{j}" for j in range(8)]
    callees = [dict(c, regs=c["regs"] + [w for w in words if w not in c["regs"]]) if int(c["addr"], 16) in variadic
               else c for c in meta["callees"]]
    return dict(meta, callees=callees)


def mask_calls(variadic, image, vec, exp, act):
    """In the readable run's records (act), the stack words past what the original's call passes take the original's
    values: not compared."""
    if not variadic or not act:
        return
    mem = Memory(image, vec)
    for ec, ac in zip(exp["calls"], act["calls"]):
        if ec[0] != ac[0]:
            break
        info = variadic.get(ec[0])
        if info is None:
            continue
        n = words_passed(info, mem, exp, ec)
        for j in range(n, 8):
            ac[STACK_WORD + j] = ec[STACK_WORD + j]
