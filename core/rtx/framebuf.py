"""Frame buffers in the readable pass (core/rtx/lift).

A frame address passed as a plain value (`r7 = r15; tc_call(callee, r4, r5, r6, r7)`, the callee's parameter a raw u32)
has no type to make a local of, so core/rtx/lift._frame_locals does not see it. Its words become an array: from the
lowest such address up to the next saved register or typed frame local, so register saves stay slots the exit check
can resolve. Everything inside stays memory: loads and stores the dataflow never moves across a call. The judge pairs
the host array with the guest address (readable_check.map_stack_args) and the vectors decide.

Nearest existing: core/rtx/lift._frame_locals - it needs a pointer type from a cast, and lift.py is over its size cap
(it may only shrink)."""
from . import flow
from .cast import Bin, Call, Cast, Num, Var


def bind(body, job, types, arrays):
    """Turn the frame addresses used as values into array locals. Adds {name: (type, dims)} to arrays."""
    from .lift import every_expr, map_exprs, topdown
    sp = job.spec
    bare, saved = set(), set()

    def at_sp(e):
        b, c = flow.linear(e)
        if isinstance(b, Var) and b.name == "__sp":
            return c - (1 << 32) if c & 0x80000000 else c
        return None

    def see(e):
        """Collects the addresses that are not the address of a load or store; a node it replaces is not descended."""
        if isinstance(e, Call) and isinstance(e.fn, str) and (e.fn in sp.loads or e.fn in sp.stores):
            at = at_sp(e.args[0])
            if at is not None:
                if e.fn in sp.stores:
                    val = e.args[1]
                    if isinstance(val, Var) and val.name.startswith("__in_"):
                        saved.add(at)
                    topdown(val, see)
                return Num(0)
        elif isinstance(e, (Var, Bin)) and at_sp(e) is not None:
            bare.add(at_sp(e))
            return Num(0)
        return e
    for e in every_expr(body.body):
        topdown(e, see)
    starts = sorted(s for s in bare if s < 0)
    if not starts:
        return
    bounds = saved | {0} | {-int(n[7:]) for n in types if n.startswith("__frame")}
    regions = []
    for s in starts:
        if regions and s < regions[-1][1]:
            continue
        hi = min(b for b in bounds if b > s)
        regions.append((s, hi, f"frame_buf{-s}"))
        arrays[regions[-1][2]] = ("u32", f"[{(hi - s + 3) // 4}]")

    def sub(e):
        if isinstance(e, (Var, Bin)):
            s = at_sp(e)
            for lo, hi, name in regions if s is not None else ():
                if lo <= s < hi:
                    base = Cast("u32", Var(name))
                    return base if s == lo else Bin("+", base, Num(s - lo, str(s - lo)))
        return e
    map_exprs(body.body, lambda e: topdown(e, sub))
