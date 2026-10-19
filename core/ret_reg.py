"""Which result register a function's own listing leaves its result in (docs/PROOF_GATES.md, float gaps).

The judge compares the result register the signature names (core/compare.compare_return): r0 for u32/s32, fr0 for
f32. A signature Ghidra typed u32 or void for a function that returns a float was never compared on fr0: TC 8c01becc
is `rts; fldi0 fr0` (it returns 0.0f) under `u32 fn_8c01becc(void)`, and any C passed. This follows every path from
the entry to each `rts` (its delay slot included) and notes which of r0 and fr0 was written last:
- 'fr0': fr0 was written after r0 and not read again before the return (a value left for the caller);
- 'fr0_read': fr0 was written after r0 but read since (a scratch value, or a clamp that compares it first);
- 'r0', 'call' (a call's result: neither register can be told from here), 'none' (neither written), 'unknown' (a
  path the listing cannot follow, or a return with the banks swapped).
After an odd number of `frchg` fr0 names XF0 (TC 8c0613cc loads XMTRX with `fmov.s @r4,fr0; rts; frchg`): it is
not followed there.
fr0_result() is true when some exit is 'fr0' and none is 'r0': the original returns in fr0 whatever its signature
says. A callee whose result register is known (ret_of) counts as a write of that register.
The listing alone is not enough: TC 8c04ea84 returns r0 (set first) and loads fr0 for a loop its early exit skips,
so that exit has fr0 "written last". The judge compares fr0 only for the functions in the workspace's
state/fr0_results.txt, where tools/float_gaps.py --write puts those whose callers agree (core/ret_use.fr0_confirmed:
a call site reads fr0, none reads r0).
"""
import pathlib

from core import sh4_asm

MAX_STEPS = 20000


def _step(x, state, bank):
    """(state, bank) after x; bank 1: frchg swapped the banks, so fr0 names XF0 (an XMTRX load, TC 8c0613cc)."""
    if x.mn == "frchg":
        return state, bank ^ 1
    w0 = sh4_asm.uses(x, "r0")[1]
    rf, wf = sh4_asm.uses(x, "fr0") if not bank else (False, False)
    if wf:
        return "fr0", bank
    if w0:
        return "r0", bank
    if state == "fr0" and rf:
        return "fr0_read", bank
    return state, bank


def exits(insns, ret_of=None):
    """The set of exit states (module docstring) over every path of a parsed listing."""
    if not insns:
        return {"unknown"}
    flow, out = sh4_asm.Flow(insns), set()
    todo, seen = [(0, "none", 0)], set()
    while todo:
        if len(seen) > MAX_STEPS:
            out.add("unknown")
            break
        i, state, bank = todo.pop()
        if (i, state, bank) in seen:
            continue
        seen.add((i, state, bank))
        state, bank = _step(insns[i], state, bank)
        nxt = flow.succ(i)
        if nxt is None:
            out.add("unknown")
            continue
        for k, kind in nxt:
            if kind == "end":
                b = insns[i - 1] if i > 0 else insns[i]
                if b.mn not in ("rts", "rte"):
                    out.add("call")  # a tail call: the callee's result
                else:
                    out.add(state if not bank else "unknown")  # returns with the banks swapped
            elif kind == "call":
                t = insns[i - 1].call_target() if i > 0 else None
                todo.append((k, (ret_of(t) if ret_of and t else None) or "call", bank))
            else:
                todo.append((k, state, bank))
    return out


def fr0_result(insns, ret_of=None):
    """True when the listing returns in fr0 (module docstring)."""
    e = exits(insns, ret_of)
    return "fr0" in e and "r0" not in e


LIST = "fr0_results.txt"  # in the workspace's state/: written by tools/float_gaps.py --write
_listed = {}


def listed(state_dir):
    """The addresses in state_dir/fr0_results.txt (a function the listing and its callers both show returning in
    fr0; one per line, `#` comments), read once per file version."""
    f = pathlib.Path(state_dir) / LIST
    try:
        key = (str(f), f.stat().st_mtime_ns)
    except OSError:
        return set()
    if key not in _listed:
        text = f.read_text(encoding="utf-8")
        _listed[key] = {w for ln in text.splitlines() for w in ln.split("#")[0].split()[:1]}
    return _listed[key]


def judged(meta, addrs, addr=None):
    """meta for the judge: fr0_result set when the function is in addrs (listed()) but its signature names another
    register than fr0 (core/compare.compare_return then compares fr0 too)."""
    a = addr or meta.get("addr")
    if meta.get("ret") == "f32" or meta.get("ret_reg") or a not in addrs:
        return meta
    return dict(meta, fr0_result=True)
