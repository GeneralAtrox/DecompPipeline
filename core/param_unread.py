"""Readable parameters that are never read while the original reads their storage (r15, 2026-10-19).

What slipped through: TC 8c0117f4 Camera_UpdateKind10 takes the mount transform as 12 f32 arguments
(stack+0 .. stack+44) and readable C discarded all of them (`(void)cam_r0x; ...`, `Transform cam = {0}`). The
kind-10 follow camera was wrong from the first mission frame, yet the judge passed it 96/96:
- the harness models 8 stack words, so stack+32 .. +44 (rotation z, position) were 0 in every vector;
- the rotation reached only mocked callees through pointers to host locals, whose contents the judge does not
  compare.

The check, static and text only (readable/src, readable/names, harness meta, packet asm.txt):
- readable side: each parameter of the file's own function (the registry's name, or fn_ADDR) and its storage
  (core/readable_abi.own_storages: names "storage", the convention, or the raw order). A parameter is unread when
  the body never names it except in `(void)name;`.
- original side, from the asm (core/sh4_asm.Flow): a register storage (r4..r7, fr4..fr11) or stack word
  (entry r15 + N) is read when its value reaches a use on some path. The value is followed through moves, frame
  spills, FPUL and arithmetic into a register (a counter stepped and stored back, never read otherwise, is dead:
  TC fn_8c070bbe's r5); a use is an address, a store outside the frame, a compare or arithmetic whose T is read,
  any other instruction reading it, a call whose record (meta "regs") reads it, or a return in r0/fr0. A call
  writes the caller-saved registers; frchg swaps the banks. A stack word whose address (or one below it) is passed
  to a call is read.
- a hit: an unread parameter whose storage the original reads. Its argument value is lost in the port.
Correlated tests: paths are kept apart by the zero tests of memory words they took (`tst rX,rX` on a word loaded
from outside the frame), so a branch on a word this path already tested takes only its side, until a store outside
the frame, a call or a write to the base register (TC Anim_SeekTime's r6: replaced when +0x930 is set, read only
when it is set). Other correlations are not followed; project.json "param_unread" "waive" takes such a hit by hand.
"""
import re

from . import sh4_asm, stack_uninit
from .asm_paths import (COND, MAX_PATHS, T_USERS, T_WRITERS, cond_key as _cond_key, facts as _facts,
                        slot as _slot, written as _written)

ARG_REGS = [f"r{i}" for i in range(4, 8)] + [f"fr{i}" for i in range(4, 12)]
CLOBBER = {f"r{i}" for i in range(8)} | {f"fr{i}" for i in range(12)} | {"fpul"}
MAX_VISITS = 50000


MOVES = re.compile(r"^(mov(\.[bwl])?|fmov(\.s)?)$")
COMPARES = re.compile(r"^(cmp/\w+|tst(\.b)?|fcmp/\w+)$")
# Arithmetic into a register: the result carries the value on (a dead counter that only goes back to its slot is
# no use). Its T, when one of these writes it and something reads it, is a use.
ALU = re.compile(r"^(add[cv]?|sub[cv]?|and|or|xor|not|negc?|ext[us]\.[bw]|shl[lr](2|8|16)?|sha[lr]|sh[al]d|"
                 r"rotc?[lr]|swap\.[bw]|xtrct|dt|fadd|fsub|fmul|fdiv|fneg|fabs|fsqrt|fcnvsd|fcnvds)$")
FPUL = "fpul"


def _t_dead(insns, i):
    """Whether the T bit set at i is replaced or dropped before anything reads it, on the straight line after i."""
    for k in range(i + 1, len(insns)):
        x = insns[k]
        if x.mn in T_USERS:
            return False
        if T_WRITERS.match(x.mn):
            return True
        if x.mn in ("rts", "rte"):
            return k + 1 >= len(insns) or insns[k + 1].mn not in T_USERS
        if x.mn in sh4_asm.DELAYED:
            return False
    return False


def call_target(insns, k):
    """The function a call or tail call at insns[k] reaches: its `; call X` note, else the pool word last loaded
    into its jsr/jmp register (`mov.l 0x.., r3 ; = 0x8c0612e4 (function ..)`) in the block; None: a pointer."""
    x = insns[k]
    if x.call_target():
        return x.call_target()
    regs = sh4_asm.regs_in(x.ops[0]) if x.ops and x.mn in ("jsr", "jmp") else []
    for j in range(k - 1, max(-1, k - 16), -1):
        y = insns[j]
        if y.mn in sh4_asm.DELAYED or y.mn in ("bt", "bf"):
            return None
        if regs and y.ops and regs[0] in sh4_asm.regs_in(y.ops[-1]) and sh4_asm.uses(y, regs[0])[1]:
            pv = y.pool_value()
            return f"{pv[0]:08x}" if pv and pv[1] == "function" else None
    return None


def _call_uses(insns, k, taint, callee_regs, sp):
    """Whether the call at insns[k] reads a tainted register or stack word, by the callee's record (meta "regs":
    r0..r7, frN, sN). A callee without a record reads every argument register; a call through a pointer counts
    as reading none (its callee is unknown)."""
    target = call_target(insns, k)
    if target is None:
        return False
    regs = callee_regs.get(target)
    for r in ARG_REGS if regs is None else regs:
        m = re.fullmatch(r"s(\d+)", r)
        if (("s", sp + 4 * int(m.group(1))) if m else r) in taint:
            return f"the call to {target} at {insns[k].addr:08x} (argument {r})"
    return False


def _local_escapes(insns, k, i, taint, sp_states):
    """Whether the call at insns[k] is passed (r4..r7, after its delay slot insns[i]) the address of a tainted frame
    slot: a value copied into a local whose address goes to a call (even a pointer call; TC Draw_TexturedRect's u1,
    the vertex UV). Only the slot itself: a slot above the address may be a dead spill of an unused argument (TC
    Screen_SaveCapture's r5/r6 above the buffer word at r15+0); a value deeper in a passed struct is missed."""
    st = sp_states.get(i)
    if st is not None and i != k:
        try:
            st = stack_uninit._transfer(insns[i], st, set())
        except stack_uninit._Abort:
            st = None
    slots = [t[1] for t in taint if isinstance(t, tuple) and t[1] < 0]  # locals (the argument area: _stack_reads)
    if st is None or not slots:
        return False
    for r in ("r4", "r5", "r6", "r7"):
        v = st[1].get(r)
        if v is not None and v in slots:
            return f"the call at {insns[k].addr:08x} (a local at entry{v:+d} in {r})"
    return False


def _fpul(x, taint):
    """Taint after an instruction that moves a value through FPUL (lds rN,FPUL / float / ftrc / sts / flds / fsts),
    else None."""
    ops = [o.lower() for o in x.ops]
    if FPUL not in ops or x.mn not in ("lds", "sts", "float", "ftrc", "flds", "fsts"):
        return None
    src, dst = ops[0], ops[-1]
    tainted = any(sh4_asm.covers(r, t) for r in sh4_asm.regs_in(src) for t in taint if isinstance(t, str)) \
        if src != FPUL else FPUL in taint
    out = {t for t in taint if not (t == dst or (isinstance(t, str) and dst != FPUL and sh4_asm.uses(x, t)[1]))}
    if tainted:
        out.update([FPUL] if dst == FPUL else sh4_asm.regs_in(x.ops[-1]))
    return out


def _step(insns, i, taint, st):
    """(used, taint after insns[i]). A move copies taint (register, or a frame slot it can place); a compare whose
    T nobody reads is no use; anything else reading a tainted register or slot is a use."""
    x = insns[i]
    if x.mn == "frchg":  # the banks swap: fr4.. now name other registers
        return False, {t for t in taint if not (isinstance(t, str) and t.startswith("fr"))}
    if not x.ops:
        return False, taint
    fpul = _fpul(x, taint)
    if fpul is not None:
        return False, fpul
    *src, dst = x.ops
    reads_src = any(("s", _slot(x, o, st)) in taint if o.startswith("@") else
                    any(sh4_asm.covers(r, t) for r in sh4_asm.regs_in(o) for t in taint if isinstance(t, str))
                    for o in src)
    reads_addr = dst.startswith("@") and any(sh4_asm.covers(r, t) for r in sh4_asm.regs_in(dst) for t in taint
                                             if isinstance(t, str))
    out = set(taint)
    dslot = _slot(x, dst, st) if dst.startswith("@") else None
    where = f"{x.addr:08x} `{x.mn} {','.join(x.ops)}`"
    if reads_addr:
        return where, out
    if not dst.startswith("@"):
        for t in list(out):
            if isinstance(t, str) and sh4_asm.uses(x, t)[1]:
                out.discard(t)
    elif dslot is not None:
        out.discard(("s", dslot))
    rmw = not dst.startswith("@") and any(isinstance(t, str) and sh4_asm.uses(x, t)[0] for t in taint)
    if (reads_src or rmw) and ALU.match(x.mn) and not dst.startswith("@"):
        if T_WRITERS.match(x.mn) and not _t_dead(insns, i):
            return where, out  # its T carries the value into a branch
        out.update(sh4_asm.regs_in(dst))
        return False, out
    if not reads_src:
        if rmw:
            return where, out  # a read-modify-write of a tainted register (add r5,r1 reads r1 only: not this)
        return False, out
    if MOVES.match(x.mn) and len(x.ops) == 2:
        if not dst.startswith("@"):
            out.update(r for r in sh4_asm.regs_in(dst))
            return False, out
        if dslot is not None:
            out.add(("s", dslot))
            return False, out
        return where, out  # stored outside the frame
    if COMPARES.match(x.mn) and _t_dead(insns, i):
        return False, out
    return where, out


def _value_used(insns, flow, start, sp_states, callee_regs):
    """Where the value in `start` (a register, or ("s", entry offset)) reaches a use on some path (False: none;
    None: flow unknown). Paths are kept apart by what they learned from zero tests of memory words: a branch that
    tests a word already known on this path takes only its side (TC Anim_SeekTime tests +0x930 twice; r6 is read
    only when it is set, and replaced on that same condition)."""
    states, todo, visits = {(0, frozenset()): (frozenset({start}), frozenset())}, [(0, frozenset())], 0
    per = {0: 1}
    while todo:
        i, vals = node = todo.pop()
        visits += 1
        if visits > MAX_VISITS:
            return None
        taint, binds = states[node]
        st = sp_states.get(i)
        used, out = _step(insns, i, taint, st)
        if used:
            return used
        nxt = flow.succ(i)
        if nxt is None:
            return None
        x = insns[i]
        k = i - 1 if x.slot and i > 0 else i
        prev = insns[k]
        sp = (sp_states.get(i - 1 if x.slot else i) or (0,))[0]
        ckey = _cond_key(insns, k, binds) if prev.mn in COND else None
        nbinds, nvals = _facts(x, st, binds, vals)
        if ckey and ckey[0] in _written(x):
            ckey = None
        for j, kind in nxt:
            if kind == "end":
                if prev.mn in ("rts", "rte"):
                    if out & {"r0", "r1", "fr0", "fr1", "dr0"}:
                        return f"the return at {prev.addr:08x}"
                else:
                    use = _call_uses(insns, k, out, callee_regs, sp)
                    if use:
                        return use  # a tail call reads it
                continue
            jb, jv = nbinds, nvals
            if kind == "call":
                use = _call_uses(insns, k, out, callee_regs, sp) or _local_escapes(insns, k, i, out, sp_states)
                if use:
                    return use
                nxt_taint = frozenset(t for t in out if not (isinstance(t, str) and t in CLOBBER))
                jb, jv = frozenset(), frozenset()  # the callee may write memory
            else:
                nxt_taint = frozenset(out)
            if ckey:
                zero = COND[prev.mn] == (kind == "branch")
                if (ckey, not zero) in jv:
                    continue  # this path already knows the word is the other way
                jv = jv | {(ckey, zero)}
            if not nxt_taint:
                continue
            if (j, jv) not in states and per.get(j, 0) >= MAX_PATHS:
                jv = frozenset()
            key = (j, jv)
            old = states.get(key)
            if old is None:
                per[j] = per.get(j, 0) + 1
                new = (nxt_taint, jb)
            else:
                new = (old[0] | nxt_taint, old[1] & jb)
            if new != old:
                states[key] = new
                todo.append(key)
    return False


def _stack_reads(insns, flow, callees):
    """({entry offsets loaded}, lowest entry offset whose address escapes to a call or None, {index: frame state});
    None: flow unknown."""
    callee_regs = {c["addr"]: c.get("regs") or () for c in callees or ()}
    states, todo, visits, offs, esc = {0: (0, {}, set(), False)}, [0], 0, set(), None
    try:
        while todo:
            i = todo.pop()
            visits += 1
            if visits > MAX_VISITS:
                return None
            x, st = insns[i], states[i]
            sp, alias = st[0], st[1]
            for k, op in enumerate(x.ops):
                m = stack_uninit.MEM.match(op) if x.mn != "mova" else None
                if not m:
                    continue
                reg = m.group(2) or m.group(5) or m.group(7)
                b = sp if reg == "r15" else alias.get(reg)
                is_dst = k == len(x.ops) - 1 and not sh4_asm.NO_WRITE.match(x.mn) and len(x.ops) > 1
                if b is None or is_dst or m.group(1):
                    continue
                if m.group(7):  # @(r0,rN): known when r0 holds a constant (`mov.w #0x118,r0`)
                    if "c:r0" not in alias:
                        continue
                    b += alias["c:r0"]
                off = b + (int(m.group(4), 0) if m.group(4) else 0)
                if off >= 0:
                    offs.add(off - off % 4)
            out = stack_uninit._transfer(x, st, set())
            if x.mn == "add" and len(x.ops) == 2 and x.ops[0] == "r15" and "c:" + x.ops[1] in alias:
                out = (out[0], {**out[1], x.ops[1]: sp + alias["c:" + x.ops[1]]}, out[2], out[3])  # `add r15,rN`
            nxt = flow.succ(i)
            if nxt is None:
                return None
            for j, kind in nxt:
                if kind == "end":
                    continue
                if kind == "call":
                    call = insns[i - 1]
                    args = {"r4", "r5", "r6", "r7"} | set(callee_regs.get(call.call_target(), ()))
                    for r in args:
                        v = out[1].get(r)
                        if v is not None and v >= 0:
                            esc = v if esc is None else min(esc, v)
                    s2 = stack_uninit._after_call(out, call, callee_regs)
                else:
                    s2 = out
                m2 = stack_uninit._merge(states.get(j), s2)
                if not stack_uninit._same(states.get(j), m2):
                    states[j] = m2
                    todo.append(j)
    except stack_uninit._Abort:
        return None
    return offs, esc, states


def original_reads(insns, callees=None):
    """{"status", "regs": set, "stack": set of offsets, "escape": offset or None} of a parsed listing."""
    if not insns:
        return {"status": "no listing"}
    flow = sh4_asm.Flow(insns)
    stack = _stack_reads(insns, flow, callees)
    if stack is None:
        return {"status": "flow not followed"}
    callee_regs = {c["addr"]: c.get("regs") or () for c in callees or ()}
    used = {}
    for start in ARG_REGS + [("s", n) for n in sorted(stack[0])]:
        used[start] = _value_used(insns, flow, start, stack[2], callee_regs)
        if used[start] is None:
            return {"status": "flow not followed"}
    return {"status": "ok", "regs": {r for r in ARG_REGS if used[r]},
            "stack": {s[1] for s in used if isinstance(s, tuple) and used[s]}, "escape": stack[1],
            "where": {(s if isinstance(s, str) else f"stack+{s[1]}"): used[s] for s in used if used[s]}}


def reads_storage(orig, storage):
    if storage.startswith("stack+"):
        n = int(storage.split("+")[1])
        return n in orig["stack"] or (orig["escape"] is not None and orig["escape"] <= n)
    return storage in orig["regs"]


COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def definition(src, names):
    """(prototype text, body text) of the first function defined in src under one of names, else None."""
    text = COMMENT.sub(" ", src)
    for name in names:
        m = re.search(r"^[A-Za-z_][\w \t\*]*?\b" + re.escape(name) + r"\s*\(([^;{}]*)\)\s*\{", text, re.M)
        if not m:
            continue
        depth, i = 0, m.end() - 1
        for j in range(i, len(text)):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                proto = re.sub(r"\s+", " ", text[m.start():m.end() - 1].strip())
                return re.sub(r"^(static|inline|__inline)\s+", "", proto), text[i + 1:j]
    return None


def unread(body, pname):
    """Whether the body never names pname except in `(void)pname;`."""
    rest = re.sub(r"\(\s*void\s*\)\s*" + re.escape(pname) + r"\s*;", " ", body)
    return not re.search(r"\b" + re.escape(pname) + r"\b", rest)
