"""What translate_function needs of a built function: the arguments of a call whose callee is not listed, the type of each variable and a generic function's result
type, in their own file (the size cap on translate.py).

Nearest existing: translate.py - the function it serves, over the size cap.
"""
import re

from .translate import ARG_REGS, CallS, Clobber, F, R, Switch, Var, all_stmts, vtype


def vector_abi(blocks):
    """Port form: the whole vector register file v0..v127 lives in the thread's tc_vr (runtime/xb_call.c), as the processor's does;
    each function is its own C function, so its v-registers are locals. At entry it reads every register it uses, after a call it
    reads them again, and before a call and before it returns it stores every register its own code writes (a read the code
    overwrites before using it is dropped). Compiled code passes vectors in v1..v13 and a result in v1 (Too Human's look-at
    8296fea8 takes the eye, direction and up in v1..v3: without them every camera matrix was NaN) and keeps v14..v31 and v64..v127
    across a call, so for it the extra reads and stores give back the values it holds. Hand-written code passes more: Too Human's
    movie decoder loads its constants into v64..v77 once per picture (8316d0c0) and its 8x8 block transform (83149a38) takes v69
    and v72 as the permutes of its transpose. With v1..v13 alone they were 0 and every decoded movie picture was garbage."""
    from .expr import Call, u
    from .ir import Assign, Ret, Store
    from .passes import children
    used, written = set(), set()

    def reg(name):
        m = re.fullmatch(r"v(\d+)", name)
        return int(m.group(1)) if m else None

    def walk(e):
        if isinstance(e, Var) and reg(e.name) is not None:
            used.add(reg(e.name))
        for c in children(e):
            walk(c)
    for s_ in all_stmts(blocks):
        for w in s_.writes():
            if reg(w) is not None:
                used.add(reg(w))
                if isinstance(s_, Assign) and not isinstance(s_, Clobber):
                    written.add(reg(w))
        for e in s_.exprs():
            walk(e)
    for b in blocks:
        for e in b.term.exprs():
            walk(e)
    used.add(1)   # a callee's v1 result, for a caller that hands it back unread (as before)

    def ld(k):
        return Call("xv_ldv", [u(k)], "xv", load=True)

    def stores():
        return [Store("xv_stv", u(k), Var(f"v{k}", "xv")) for k in sorted(written)]

    def calls(sts):
        out = []
        for s_ in sts:
            if isinstance(s_, CallS):
                out += stores() + [s_] + [Assign(f"v{k}", ld(k)) for k in sorted(used)]
            elif isinstance(s_, Ret):   # a jump table's tail call ends in one
                out += stores() + [s_]
            else:
                out.append(s_)
        return out
    blocks[0].stmts += [Assign(f"v{k}", ld(k)) for k in sorted(used)]
    for b in blocks:
        b.stmts = calls(b.stmts)
        if isinstance(b.term, Switch):
            b.term.default = calls(b.term.default)
        if isinstance(b.term, Ret):
            b.stmts += stores()


REG = re.compile(r"r\d+|f\d+|v\d+|c\d(lt|gt|eq|so)|ca|so|ov|ctr|lr|fpscr|vrsave|xbc")
FRAME_BASES = {"r1", "r11", "r12"}   # a save of a register's entry value: into the frame, the address built on these


def uncarried(blocks, params):
    """The registers the function reads before it sets them, other than its parameters (and, in the port form, what the
    caller hands over: tcr12, tcr13, sp0, a funclet's tcnv*, the vector file through tc_vr). The optimiser takes such a
    read as 0, so the C silently differs from the code wherever the caller set that register: Too Human's movie decoder
    passes its constants in v64..v77 (8316d0c0 to 83149a38), which the port form read as 0 until vector_abi carried the
    whole vector file. Faint liveness: an assignment counts only when what it sets is used, so a compare's copy of XER[SO]
    or a CR save (mfcr into the frame) that nothing reads is no read. Not a read either: a store of the register as it
    stands into the frame (a non-volatile register's save, its address built on r1, r11 or r12 and not on the register
    itself), a funclet's parent registers and the stack pointer and r12 a port-form call hands on. Such a store is a save
    only of the entry value: it leaves a mark ("!f0") that the entry drops, and an assignment to the register above it
    keeps it live, so `fctiwz f0,f14; stfd f0,-8(r1); lwz r3,-4(r1)` reads f14 (it hid that read, and the port form took
    the caller's f14 as 0). Before the optimiser, on the built blocks: a sorted list of names."""
    from .ir import Assign, Store

    def reads(s_):
        if isinstance(s_, CallS):
            out = s_.target.vars()
            for f_, _, e in s_.args:
                if f_ not in ("nv", "sp", "r12"):
                    out |= e.vars()
            return out
        if isinstance(s_, Store) and isinstance(s_.val, Var) and s_.addr.vars() & FRAME_BASES and s_.val.name not in s_.addr.vars():
            return s_.addr.vars() | {"!" + s_.val.name}   # a save, if the register still holds its entry value
        return s_.reads()

    def through(sts, live):
        for s_ in reversed(sts):
            w = s_.writes()
            if isinstance(s_, Assign) and not (w & live) and not any("!" + n in live for n in w):
                continue   # sets nothing used later: its reads do not count
            live = (live - w - {"!" + n for n in w}) | reads(s_)
        return live
    live_in = {b.label: set() for b in blocks}
    for _ in range(10000):
        changed = False
        for b in reversed(blocks):
            if isinstance(b.term, Switch):
                # emit.py: `switch (g_) { case V: goto L; ... default: break; } <default>`: the default (a tail call ending
                # in a Ret) runs only when no case does, so it is a path of its own beside the cases, not a prefix of
                # them. Run after the cases' live sets, a call in it took r6 out of what 82cc6bc8's case arms read.
                out = b.term.e.vars() | (through(b.term.default, set()) - {"g_"})
            else:
                out = set(b.term.reads())
            for s in b.term.succs():
                out |= live_in[s]
            live = through(b.stmts, out)
            if live != live_in[b.label]:
                live_in[b.label], changed = live, True
        if not changed:
            return sorted(n for n in live_in[blocks[0].label] if REG.fullmatch(n) and n not in params)
    raise RuntimeError("read-before-set liveness does not settle")


GR_HELPERS = {   # the port form's general and floating register files (runtime/xb_call.c), for hand_over
    f"tc_{op}{c}": f"extern __declspec(thread) {t} tc_{c}r[32];\nstatic {sig}\n{{\n    {stmt};\n}}"
    for c, t in (("g", "u64"), ("f", "f64"))
    for op, sig, stmt in (("ld", f"{t} tc_ld{c}(u32 k)", f"return tc_{c}r[k]"), ("st", f"void tc_st{c}(u32 k, {t} v)", f"tc_{c}r[k] = v"))}


def unhanded(blocks, need_of):
    """hand_over's check, apart from it: each register a constant call should hand on (need_of) that no tc_stg / tc_stf
    before the call in its block stores, named "r11>82000000". A dropped hand-over (tests/test_registers.py) shows here."""
    from .expr import Const
    from .ir import Store
    out = []
    def walk(sts):
        put = set()
        for s_ in sts:
            if isinstance(s_, Store) and s_.fn in ("tc_stg", "tc_stf"):
                put.add(("r" if s_.fn == "tc_stg" else "f") + str(s_.addr.v))
            elif isinstance(s_, CallS) and isinstance(s_.target, Const):
                t = s_.target.v & 0xFFFFFFFF
                out.extend(f"{n}>{t:08x}" for n in need_of.get(t, ()) if re.fullmatch(r"[rf]\d+", n) and n not in put)
                put = set()
    for b in blocks:
        walk(b.stmts)
        if isinstance(b.term, Switch): walk(b.term.default)
    return out


def hand_over(blocks, own, need_of, params=()):
    """Port form: the general and floating registers a function reads before it sets them (other than its parameters)
    live in the thread's tc_gr / tc_fr. It loads its own at entry (own: uncarried's names), and before a call to a
    function in need_of ({address: names}, from the last translation's read-before-set lines) it stores what that one
    reads. These are mostly catalogue functions that are really a label of their caller, entered by a branch with
    registers live: Too Human's decoder set-up branches into 82cbd238, which tests the caller's r11, from 82cbd1d0.
    A register it hands on without setting it (its caller's r11 on to the callee) is loaded too and returned: the
    function then reads it first, and its own callers hand it on after the next translation (gen_xbox.read_first_map)."""
    from . import emit
    from .expr import Call, Const, u
    from .ir import Assign, Store
    emit.HELPERS.update(GR_HELPERS)
    emit.ORDER += [h for h in GR_HELPERS if h not in emit.ORDER]   # emitted only when in ORDER
    rf = re.compile(r"[rf]\d+")

    def calls(sts):
        out = []
        for s_ in sts:
            if isinstance(s_, CallS) and isinstance(s_.target, Const):
                out += [Store("tc_stg", u(int(n[1:])), R(int(n[1:]))) if n[0] == "r" else Store("tc_stf", u(int(n[1:])), F(int(n[1:])))
                        for n in need_of.get(s_.target.v & 0xFFFFFFFF, ()) if rf.fullmatch(n)]
            out.append(s_)
        return out
    for b in blocks:
        b.stmts = calls(b.stmts)
        if isinstance(b.term, Switch):
            b.term.default = calls(b.term.default)
    extra = [n for n in uncarried(blocks, set(params)) if rf.fullmatch(n) and n not in own]
    blocks[0].stmts += [Assign(n, Call("tc_ldg" if n[0] == "r" else "tc_ldf", [u(int(n[1:]))], "u64" if n[0] == "r" else "f64",
                                       load=True)) for n in [*own, *extra] if rf.fullmatch(n)]
    return extra


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
    do `addi rN, r12, -frame` before anything writes r12. One that needs no frame of its parent saves a non-volatile register and
    loads through it at once, as it stands (first: Too Human's 82a583a0, `lwz r3, 0x50(r30)`, under 82a58290): no normal function
    reads what it has only saved."""
    saved, r12_written = set(), False
    for k in range(8):
        a = addr + 4 * k
        if not dol.has(a, 4):
            return False
        i = dol.insn(a)
        ra = getattr(i, "ra", None)
        if i.op == "addi" and ra == 12 and not r12_written:
            return True
        if i.op in ("b", "bc", "bclr", "bcctr"):
            return False
        if i.op == "mfspr" and getattr(i, "rd", None) == 12:
            if not saved:
                return False
            r12_written = True
        elif i.op == "std" and ra == 1:
            saved.add(i.rd)
        elif i.op in ("lwz", "ld", "lbz", "lhz") and ra in saved and ra >= 14:
            return True
    return False
