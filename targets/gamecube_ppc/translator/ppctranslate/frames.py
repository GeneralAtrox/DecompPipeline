"""The exit frame and self-recursion of a translated function (translate.translate_function): the generic return
kind, the callee-saved registers each return reports, and the f1 an inner px_rec frame starts with. Kept out of
translate.py, which is over the 400-line cap (tools/sizes.py)."""
import re

from .expr import Call, Var
from .ir import CallS, Ret, Switch

CALLEE_SAVED = re.compile(r"([rf])(1[4-9]|2[0-9]|3[01])")


def generic_ret(blocks):
    from .translate import Clobber, all_stmts
    w = set()
    for s in all_stmts(blocks[1:]):
        if not isinstance(s, Clobber):
            w |= s.writes() if not isinstance(s, CallS) else {"r3"}
    return "u32" if "r3" in w else "f64" if "f1" in w else "void"


def attach_exit_regs(blocks, types):
    """Every return reports the callee-saved registers (r14..r31, f14..f31) the function writes: their values at exit
    are part of its result (a restore it gets wrong, a stack overrun into a save slot)."""
    from .translate import Clobber, all_stmts
    w = set()
    for s in all_stmts(blocks):
        if not isinstance(s, Clobber):
            w |= s.writes()
    xr = []
    for n in w & set(types):
        m = CALLEE_SAVED.fullmatch(n)
        if m:
            xr.append((int(m.group(2)), m.group(1), Var(n, types[n])))
    xr.sort(key=lambda t: (t[1] != "r", t[0]))
    for b in blocks:
        rets = [b.term] if isinstance(b.term, Ret) else []
        if isinstance(b.term, Switch):
            rets += [s for s in b.term.default if isinstance(s, Ret)]
        for r in rets:
            r.xr = list(xr)


def recursive_f1(ft, blocks, entry_vals, has_last):
    """px_rec runs each frame of a self-call as a C call. A body whose f1 only a call sets enters an inner frame with
    the f1 the last mocked call returned (the oracle's callee sets r3 and f1 and the code never touches f1), and a
    pointer call there passes it on: 80211298's hidden vector 96 passed 0.0 where the original passes the previous
    call's f1 (expected.txt "CA ... f1"). The outermost frame reads tc_lastf()'s 0 before any call."""
    if ft.recursive and has_last and "f1" in entry_vals and not any(
            "f1" in s.writes() for b in blocks for s in b.stmts if not isinstance(s, CallS)):
        entry_vals["f1"] = Call("tc_lastf", [], "f64")
