"""The translator's vector temporaries in the readable pass (core/rtx/lift).

An instruction the runtime computes over an array is one block in the raw C: `{ f32 v_[4]; v_[0] = fr4; ...;
sh4_ftrv(v_); fr4 = v_[0]; ... }`. Each block's arrays become locals of their own (vec1, lhs1, rhs1), written and read
only there, so nothing the passes move can cross into another block; the runtime call stays where it is, in order
with every other call. A target names its arrays in spec.vector_locals ({array in the raw C: stem of the local}).

Nearest existing: core/rtx/lift.declarations - it refuses an array local, and lift.py is over its size cap (it may only
shrink)."""
from .cast import Block, Decl, Var


def bind(body, spec):
    """Rename each block's vector arrays apart and drop their declarations. Returns {new name: (type, dims)}."""
    from .lift import all_stmts, map_exprs, rewrite
    stems = getattr(spec, "vector_locals", None) or {}
    arrays, seen = {}, {}
    # last first: splicing a block must not move the statements still to visit in the same list
    for stmts, k, s in reversed(list(all_stmts(body.body))):
        if not isinstance(s, Block):
            continue
        ren = {}
        for d in s.body:
            if isinstance(d, Decl) and d.items and all(dims and n in stems for n, _, dims in d.items):
                for n, _, dims in d.items:
                    seen[n] = seen.get(n, 0) + 1
                    ren[n] = f"{stems[n]}{seen[n]}"
                    arrays[ren[n]] = (d.type, dims)
        if ren:
            s.body = [d for d in s.body if not (isinstance(d, Decl) and all(n in ren for n, _, _ in d.items))]
            map_exprs(s.body, lambda e, ren=ren: rewrite(e, lambda x: Var(ren[x.name]) if isinstance(x, Var) and
                                                         x.name in ren else x))
            stmts[k:k + 1] = s.body
    return arrays


def decls(arrays, used):
    """The declaration lines of the arrays still used."""
    return [f"    {t} {n}{dims};" for n, (t, dims) in sorted(arrays.items()) if n in used]
