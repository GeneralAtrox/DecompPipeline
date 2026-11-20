"""The exit-state form of a translation (Facts.exit_state; the judge form only, never the port's): the same C, plus
before each return a record of the final value of every register the function writes, into the judge runtime's exit
record (tc_rt.h tcx_r, tcx_f, tcx_cr, tcx_xer, tcx_ctr, tcx_lr, tcx_fpscr, tcx_v). The runner prints it with the entry
value of every other register, and x360_compare.compare_exit holds it against the original's registers at exit
(TcExit). So the judge sees what the normal form drops: CR, XER, CTR and LR at exit, the callee-saved r14..r31,
f14..f31 and CR2..CR4, the vector registers and the stack bytes written (the passes keep stack stores here: no
stack_dce).

A register the function writes on some path but not on every one keeps its entry value on the others: here that is
the runtime's (tcx_in_r / tcx_in_f / tcx_in_cr: the vector's argument registers, its entry seeds of r12 and CR, the
seeds of r14..r31, f14..f31 and CR2..CR4), read at entry, where the normal form takes 0 (translate.py entry_vals), which the original's caller never
sees. The normal form's text does not change: nothing here runs with the flag off (tests/test_x360_judge_exit.py).

Facts.exit_seed (tools/x360_vote.py only, never the judge): every r, f and v register the function uses and does not set
at entry is read there from the vote harness's runtime (tcv_in_r / tcv_in_f / tcv_in_v: an instruction test case's
REGISTER_IN values), not only the judge's seeded set; CR, XER and CTR stay 0 at entry (the cases' all-zero state).

Nearest existing: fn_parts.vector_abi - adds statements at entry and before each return, for the port's vector file.
"""
import re

from .expr import Call, u
from .ir import Assign, Ret, Switch

CRB = ("lt", "gt", "eq", "so")
XER = ("ca", "so", "ov")
# The registers whose entry value may be other than 0 (TcOracle.setEntry; the seeds: TcExit), read at entry from the
# runtime when the function writes them. r1, r13 and LR the entry block sets itself.
ENTRY_GPR = {*range(3, 13), *range(14, 32)}   # r11 and r12: tcx_in_r gives 0 where the vector seeds none
ENTRY_FPR = set(range(1, 32))
ENTRY_CR = set(range(32))               # CR2..CR4 seeded, any field the function reads first (vectors.txt A), else 0
# per class: the runtime's record call, the C type of the value, and whether it takes the register's number
RECORD = {"r": ("tcx_r", "u64", True), "f": ("tcx_f", "f64", True), "c": ("tcx_cr", "u32", True),
          "x": ("tcx_xer", "u32", True), "ctr": ("tcx_ctr", "u64", False), "lr": ("tcx_lr", "u64", False),
          "fpscr": ("tcx_fpscr", "u32", False)}


def key(name):
    """(class, number) of a register variable, or None for anything else (temps, parameters, g_)."""
    m = re.fullmatch(r"([rfv])(\d+)", name)
    if m:
        n = int(m.group(2))
        return (m.group(1), n) if n < (128 if m.group(1) == "v" else 32) else None
    m = re.fullmatch(r"c([0-7])(lt|gt|eq|so)", name)
    if m:
        return "c", 4 * int(m.group(1)) + CRB.index(m.group(2))
    if name in XER:
        return "x", XER.index(name)
    return (name, 0) if name in ("ctr", "lr", "fpscr") else None


def order(name):
    k = key(name)
    return ("rfvcx".find(k[0]) if k[0] in "rfvcx" else 9, k[1], name)


class ExitRec:
    """tcx_* of each written register before a return: reads them all, so no pass drops their final values."""
    __slots__ = ("regs",)

    def __init__(self, names):
        from .expr import Var
        from .translate import vtype
        # a vector register appears twice: passes.substitute moves a value into a single use only, and tcx_v needs
        # a variable
        self.regs = [(n, Var(n, vtype(n))) for n in names for _ in range(2 if key(n)[0] == "v" else 1)]

    def reads(self):
        out = set()
        for _, e in self.regs:
            out |= e.vars()
        return out

    def writes(self):
        return set()

    def exprs(self):
        return [e for _, e in self.regs]

    def set_exprs(self, es):
        self.regs = [(n, e) for (n, _), e in zip(self.regs, es)]

    def c_lines(self, em, ind):
        from .emit import coerce
        from .expr import Var
        out, seen = [], set()
        for n, e in self.regs:
            if n in seen:
                continue
            seen.add(n)
            k, i = key(n)
            if k == "v":
                if not isinstance(e, Var):
                    from .translate import TranslateError
                    raise TranslateError(f"exit-state form: {n} at a return is no variable")
                out.append(f"{ind}tcx_v({i}u, {e.name}.w);")
                continue
            fn, ty, numbered = RECORD[k]
            out.append(f"{ind}{fn}({f'{i}u, ' if numbered else ''}{em.r(coerce(e, ty))});")
        return out


SEEDED_R = {*range(3, 11), 12}         # TcOracle.setEntry: vectors.txt R, every vector, in both runs; r12 A (entry seeds)
SEEDED_F = set(range(1, 14))            # vectors.txt F (TcOracle.CALL_FLOATS)
SEEDED_C = set(range(32))               # vectors.txt A: the CR fields the function reads before setting them


def seed_judge(ft, blocks, names):
    """The judge form (not the port's, not the vote's): a register read before it is set that the vector seeds
    (TcOracle.setEntry: r3..r10 and f1..f13, vectors.txt R and F; r12 and the CR fields the function reads first, its
    entry seeds, vectors.txt A; the same in the normal and the exit-state run but for CR2..CR4, which the exit-state run
    reseeds from K, here and in the runner) is read at entry from the runner (tcx_in_r / tcx_in_f / tcx_in_cr; tc_exit.c
    tcx_begin takes them from the same lines), so the C starts from exactly the original's entry value, whatever the
    signature says. r12 is 32 bits there, as the port's TcArgs.r12. Where the oracle seeds none (its dataflow, TcFlow
    entryReads, saw no read), both start at 0. Any other register is still refused: r14..r31 and f14..f31 are seeded
    only in the exit-state run (TcExit; the normal lines are the unseeded run's), and the vector, XER, CTR and
    r0/r2/r11 values the oracle never varies. Returns [] (nothing uncarried) or raises."""
    from .expr import Call
    from .translate import TranslateError
    seeded, rest = [], []
    for n in names:
        k = key(n)
        ok = k is not None and (k[0] == "r" and k[1] in SEEDED_R or k[0] == "f" and k[1] in SEEDED_F
                                or k[0] == "c" and k[1] in SEEDED_C)
        (seeded if ok else rest).append(n)
    if rest:
        raise TranslateError(f"reads {', '.join(rest)} before setting them, and the vector does not seed them")
    ty = {"r": ("tcx_in_r", "u64"), "f": ("tcx_in_f", "f64"), "c": ("tcx_in_cr", "u32")}
    blocks[0].stmts[:0] = [Assign(n, Call(ty[key(n)[0]][0], [u(key(n)[1])], ty[key(n)[0]][1])) for n in seeded]
    ft.notes.append(f"reads {', '.join(seeded)} before setting them: the vector's entry values (tcx_in_r / tcx_in_f / tcx_in_cr)")
    return []


def apply(ft, blocks, params):
    """The exit-state form of the built blocks, in place: entry reads of the registers whose entry value may be other
    than 0, then an ExitRec before each return (a block's, and a jump table's tail call's)."""
    from .translate import TranslateError
    if ft.facts.port:
        raise TranslateError("the exit-state form is the judge's: not with the port form")
    entry = blocks[0]
    at_entry = {s.dst for s in entry.stmts if isinstance(s, Assign)}
    used, written = set(), set()
    for b in blocks:
        for s in [*b.stmts, *(b.term.default if isinstance(b.term, Switch) else ())]:
            written |= {w for w in s.writes() if key(w)}
            for e in s.exprs():
                used |= {v for v in e.vars() if key(v)}
        for e in b.term.exprs():
            used |= {v for v in e.vars() if key(v)}
    used |= written
    ins = []
    seed = getattr(ft.facts, "exit_seed", False)
    for n in sorted(used - at_entry - set(params), key=order):
        k, i = key(n)
        if seed and k in "rfv":         # the vote harness (tools/x360_vote.py): any register, from its own runtime
            ins.append(Assign(n, Call(f"tcv_in_{k}", [u(i)], {"r": "u64", "f": "f64", "v": "xv"}[k])))
        elif k == "r" and i in ENTRY_GPR:
            ins.append(Assign(n, Call("tcx_in_r", [u(i)], "u64")))
        elif k == "f" and i in ENTRY_FPR:
            ins.append(Assign(n, Call("tcx_in_f", [u(i)], "f64")))
        elif k == "c" and i in ENTRY_CR:
            ins.append(Assign(n, Call("tcx_in_cr", [u(i)], "u32")))
    entry.stmts[:0] = ins
    regs = sorted(written | {s.dst for s in ins} | {n for n in at_entry if key(n)}, key=order)
    for b in blocks:
        if isinstance(b.term, Ret):
            b.stmts.append(ExitRec(regs))
        elif isinstance(b.term, Switch):
            d = b.term.default
            b.term.default = [x for s in d for x in ((ExitRec(regs), s) if isinstance(s, Ret) else (s,))]
