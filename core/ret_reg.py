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
so that exit has fr0 "written last". The judge compares a register the signature does not name only for the functions
in the workspace's state/fr0_results.txt, where tools/float_gaps.py --write puts those whose callers agree
(core/ret_use.fr0_confirmed: a call site reads fr0, none reads r0) and those whose callers really read a register the
signature leaves out (core/ret_use.narrowed: r0 too). A stale list passes what it does not name: TC 8c093e94 was
prepared after the list was written, so core/doctor fails a workspace whose list misses one.
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


def may_leave(insns, reg, callee_leaves=None):
    """Can a call to this listing change reg (r0 or fr0)? False only when no path from the entry to an exit writes it
    and no path calls something that may: a caller reading reg afterwards then reads its own value, which a hand-written
    routine keeps on purpose (TC 8c061520 loads fr1..fr15 and leaves fr0 alone), not a result. A call or tail call
    counts unless callee_leaves(target, reg) says otherwise; a path the listing cannot follow counts."""
    if not insns:
        return True
    flow, todo, seen = sh4_asm.Flow(insns), [(0, 0)], set()
    while todo:
        if len(seen) > MAX_STEPS:
            return True
        i, bank = todo.pop()
        if (i, bank) in seen:
            continue
        seen.add((i, bank))
        x = insns[i]
        if x.mn == "frchg":
            bank ^= 1
        elif (reg != "fr0" or not bank) and sh4_asm.uses(x, reg)[1]:
            return True
        nxt = flow.succ(i)
        if nxt is None:
            return True
        for k, kind in nxt:
            if kind == "call" or (kind == "end" and insns[i - 1].mn not in ("rts", "rte")):
                t = insns[i - 1].call_target() if i > 0 else None
                if not callee_leaves or not t or callee_leaves(t, reg):
                    return True
            if kind != "end":
                todo.append((k, bank))
    return False


def fr0_result(insns, ret_of=None):
    """True when the listing returns in fr0 (module docstring)."""
    e = exits(insns, ret_of)
    return "fr0" in e and "r0" not in e


LIST = "fr0_results.txt"  # in the workspace's state/: written by tools/float_gaps.py --write; `addr [r0] [fr0]`
_listed = {}


def listed(state_dir):
    """{addr: registers} from state_dir/fr0_results.txt (a function whose result registers go beyond its signature;
    one per line, `#` comments, an address alone means fr0), read once per file version."""
    f = pathlib.Path(state_dir) / LIST
    try:
        key = (str(f), f.stat().st_mtime_ns)
    except OSError:
        return {}
    if key not in _listed:
        rows = (ln.split("#")[0].split() for ln in f.read_text(encoding="utf-8").splitlines())
        _listed[key] = {w[0]: set(w[1:]) or {"fr0"} for w in rows if w}
    return _listed[key]


FRET = "fret_callers.txt"  # in state/: `caller callee...`, written by tools/float_gaps.py --write
_fret = {}


def fret_callers(state_dir):
    """{caller: [callee]}: the callees whose mocked result must be a float for this caller's vectors, though Ghidra's
    prototype does not say f32 (core/ret_use.Project.fret_callers), read once per file version."""
    f = pathlib.Path(state_dir) / FRET
    try:
        key = (str(f), f.stat().st_mtime_ns)
    except OSError:
        return {}
    if key not in _fret:
        rows = (ln.split() for ln in f.read_text(encoding="utf-8").splitlines())
        _fret[key] = {w[0]: w[1:] for w in rows if w}
    return _fret[key]


def fret_arg(state_dir, addr):
    """TcPrepare's script arg 14 and the stamp's line for addr: its float callees joined by `_`, or "-"."""
    return "_".join(fret_callers(state_dir).get(addr) or ()) or "-"


def judged(meta, addrs, addr=None):
    """meta for the judge: fr0_result, r0_result, r3_result and f1_result set for the registers addrs (listed()) names that
    the function's signature does not (core/compare.compare_return, ppc_compare.compare_return then compare them too)."""
    regs = addrs.get(addr or meta.get("addr")) or ()
    if meta.get("ret_reg"):
        return meta
    out = dict(meta)
    if "fr0" in regs and meta.get("ret") != "f32":
        out["fr0_result"] = True
    if "r0" in regs and meta.get("ret") not in ("u32", "s32", "u64"):
        out["r0_result"] = True
    out.update({f"{r}_result": True for r in regs if r in ("r3", "f1")})  # PowerPC; the compare skips what the signature names
    return out
