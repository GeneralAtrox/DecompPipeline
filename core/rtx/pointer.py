"""Calls through a computed pointer in the readable pass (core/rtx/lift step 4), SH-4.

The readable judge decodes a call through a function pointer by the registers the original set before it (expected.txt
CA, core/readable_check.pointer_protos): the C call's parameters are those registers, the integer ones first, then the
float ones. So the lifted call is typed by exactly that set (pointer_ca: from the vectors, where they agree), and a call
the vectors do not pin down stays the agent's: nothing is guessed.

Nearest existing: core/rtx/lift.calls - it lifts calls to a literal target by the callee's prototype; a computed target
has none, and lift.py is over its size cap (it may only shrink)."""
from .cast import Call, Cast, Num, Raw, Var
from .flow import Unsupported


def pointer_ca(results):
    """({k: the registers the original set before its k-th call}, the call count) of a function whose every vector makes
    the same number of calls with the same registers at each call through a pointer (expected.txt CA, parsed by
    core/compare.parse_results): the lift can then type the k-th call of its text exactly, as the judge decodes it
    (core/readable_check.pointer_protos). Where every set of every vector is r4..r(3+n) (integers, no floats), r4..r7
    type a call whichever it is (key None: no call index needed, so the call count may differ between the vectors).
    Otherwise a function whose vectors differ in the call count, and a call whose sets differ, are the agent's."""
    runs = list(results.values())
    prefixes = [[f"r{4 + i}" for i in range(n)] for n in range(5)]
    every = [list(regs) for r in runs for regs in r.get("call_args", {}).values()]
    default = {None: prefixes[4]} if runs and all(x in prefixes for x in every) else {}  # r4..r7 serve every prefix
    if not runs or len({len(r["calls"]) for r in runs}) != 1:
        return default, None
    total, out = len(runs[0]["calls"]), dict(default)
    for k in range(total):
        sets = {tuple(r.get("call_args", {}).get(k) or ()) for r in runs}
        if len(sets) == 1:
            out[k] = list(next(iter(sets)))
    return out, total


def call(e, job, types, k, body):
    """tc_call(T, &a) with T computed (a function-table slot: no prototype to place the arguments by) -> a call
    through a function pointer. The readable judge decodes such a call by the registers the original set before it
    (expected.txt CA, core/readable_check.pointer_protos): the C parameters are those registers, the integer ones
    first, then the float ones. So the type is exactly that set, which job.pointer_ca gives for the k-th call when
    every vector agrees on it (core/rtx/job.pointer_ca); anything else is the agent's, nothing is guessed.
    tc_callf returns the float; tc_call, the integer."""
    sp, target = job.spec, e.args[0]
    table = getattr(job, "pointer_ca", None) or {}
    ca = table.get(k, table.get(None))
    if ca is None or not getattr(sp, "pointer_args", None):
        raise Unsupported("a computed call target")
    from .lift import names_used
    used, args, ptypes = names_used(body), [], []
    for st in ca:
        v = sp.arg_var(st)
        if st not in sp.pointer_args or v is None:
            raise Unsupported(f"a call through a pointer that also takes {st}")
        fl = st.startswith("fr")
        if v in used:
            types.setdefault(v, "f32" if fl else "u32")
        # a register the function never sets in the block (the original set it before the call): the block's 0
        args.append(Var(v) if v in types else Num(0.0, "0.0f") if fl else Num(0, "0"))
        ptypes.append("f32" if fl else "u32")
    ret = "f32" if e.fn == sp.call_fns[1] else "u32"
    fnptr = Cast(f"{ret} (*)({', '.join(ptypes) or 'void'})", Cast("uintptr_t", target))
    return Call("__result", [Call(fnptr, args), Num(1 if ret == "f32" else 2), Raw(e.fn)])


def check_count(job, seen):
    """The text's calls must be the vectors' calls when the call index types a call (job.call_total is set)."""
    if getattr(job, "call_total", None) is not None and seen != job.call_total:
        raise Unsupported(f"{seen} calls in the text, {job.call_total} in the vectors")
