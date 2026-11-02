"""What translate_function needs of a built function: the arguments of a call whose callee is not listed, the type of each variable and a generic function's result
type, in their own file (the size cap on translate.py).

Nearest existing: translate.py - the function it serves, over the size cap.
"""
from .translate import ARG_REGS, CallS, Clobber, F, R, Switch, Var, all_stmts, vtype


def fill_auto_args(ft, blocks):
    """A call whose callee DP's meta does not list (indirect, or no meta) passes every argument register the
    function may have set: written by its own code (not a callee's clobber), a parameter or a call's r3 / f1; and the
    stack argument doublewords when the function writes its outgoing argument area."""
    defined = {stg for _, _, stg, _ in ft.sig.params}
    for s_ in all_stmts(blocks[1:]):
        if not isinstance(s_, Clobber):   # a mocked callee sets r3 and f1 (passes.other_result gives the other one)
            defined |= {"r3", "f1"} if isinstance(s_, CallS) else s_.writes()
    regs = [r for r in ARG_REGS if r in defined]
    for s_ in all_stmts(blocks):
        if isinstance(s_, CallS) and s_.auto:
            s_.args = [("r", int(r[1:]) - 3, R(int(r[1:]))) if r[0] == "r" else ("f", int(r[1:]) - 1, F(int(r[1:])))
                       for r in regs]
            s_.args += [("s", k, ft.stack_arg(k)) for k in range(8)]


def var_types(ft, blocks):
    from .passes import children
    types = {n: k for n, k, _, _ in ft.sig.params}
    types.update(ft.temps)

    def walk(e):
        if isinstance(e, Var):
            types.setdefault(e.name, e.ty)
        for c in children(e):
            walk(c)
    for b in blocks:
        sts = list(b.stmts) + (list(b.term.default) if isinstance(b.term, Switch) else [])
        for s_ in sts:
            for w in s_.writes():
                types.setdefault(w, ft.temps.get(w) or vtype(w))
            for e in s_.exprs():
                walk(e)
        for e in b.term.exprs():
            walk(e)
    types["g_"] = "u32"
    return types


def generic_ret(blocks):
    w = set()
    for s_ in all_stmts(blocks[1:]):
        if not isinstance(s_, Clobber):
            w |= s_.writes() if not isinstance(s_, CallS) else {"r3"}
    return "u64" if "r3" in w else "f64" if "f1" in w else "void"


def is_funclet(dol, addr):
    """A funclet (a __finally or catch block the compiler outlines): it builds its frame pointer from r12, which the code that
    runs it sets to the parent's frame, and may read the parent's non-volatile registers as they stand. Its first instructions
    do `addi rN, r12, -frame` before anything writes r12."""
    for k in range(8):
        a = addr + 4 * k
        if not dol.has(a, 4):
            return False
        i = dol.insn(a)
        if i.op == "addi" and getattr(i, "ra", None) == 12:
            return True
        if i.op in ("b", "bc", "bclr", "bcctr") or (i.op == "mfspr" and getattr(i, "rd", None) == 12):
            return False
    return False
