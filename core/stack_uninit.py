"""Frame words a function reads before anything writes them (batch 27), from its packet's asm.txt.

A must-written analysis over the listing's control flow (core/sh4_asm.Flow): the bytes of the own frame (below the
entry r15) that every path has written so far. A read of a frame byte not in that set is uninitialized on some path
(a slot written on one path but not on another counts). r15 is followed through pushes, pops and `add #imm,r15`;
`mov r15,rN` / `add #imm,rN` make rN an alias of a frame address. The analysis is conservative, never reporting a
word some code could have written:
- a frame address that escapes (passed to a call in r4..r7 or in the callee's own argument registers from the meta,
  such as Shc_StrCpyUnaligned's r0 / r1; stored; or used in other arithmetic) counts as writing the whole frame from
  then on (a callee out-pointer);
- a store at an unknown frame offset (@(r0,r15)) likewise;
- a callee writes no other frame word: its stack parameters are words the caller wrote before the call.
A listing it cannot follow (a computed jump, braf, r15 set from a register, unbalanced r15 at a join, fschg: fmov
sizes unknown) gives status other than "ok" and no reads.

The rule (README, batch 27): such a word holds whatever the caller left there, so the harness pins it to 0 in every
vector (an oracle hint `genrange 0xADDR 0 0`, written by tools/stack_uninit.py --pin), the judge stays strict, and
readable C uses 0 there with a comment saying the original reads an uninitialized stack word.
"""
import json
import re
from pathlib import Path

from core import sh4_asm

ENTRY_SP = 0x8CFFE000
MAX_VISITS = 50000
MEM = re.compile(r"^@(?:(-)?(r\d+)(\+)?|\((?:(0x[0-9a-f]+|\d+),(r\d+)|(r0),(r\d+))\))$")


def _size(x, op_regs):
    m = re.search(r"\.([bwl])$", x.mn)
    if m:
        return {"b": 1, "w": 2, "l": 4}[m.group(1)]
    return 8 if any(r.startswith(("dr", "xd")) for r in op_regs) else 4


class _Abort(Exception):
    pass


def _transfer(x, st, reads):
    sp, alias, written, allw = st
    alias = dict(alias)
    written = set(written)
    regs = [r for o in x.ops for r in sh4_asm.regs_in(o) if not o.startswith("@")]
    size = _size(x, regs)

    def base(reg):
        return sp if reg == "r15" else alias.get(reg)

    for k, op in enumerate(x.ops):
        m = MEM.match(op) if x.mn != "mova" else None
        if not m:
            continue
        is_dst = k == len(x.ops) - 1 and not sh4_asm.NO_WRITE.match(x.mn) and len(x.ops) > 1
        pre, reg, post, disp, ireg = m.group(1), m.group(2) or m.group(5), m.group(3), m.group(4), m.group(7)
        if ireg:  # @(r0,rN): unknown offset
            if base(ireg) is not None and is_dst:
                allw = True
            continue
        b = base(reg)
        if b is None:
            continue
        if pre:
            b -= size
            sp, alias = (b, alias) if reg == "r15" else (sp, {**alias, reg: b})
        off = b + (int(disp, 0) if disp else 0)
        if is_dst:
            written.update(range(off, off + size))
        elif off < 0 and not allw and any(o not in written for o in range(off, off + size)):
            reads.add((x.addr, off, size))
        if post:
            if reg == "r15":
                sp += size
            else:
                alias[reg] = b + size
    dst = x.ops[-1] if x.ops else ""
    src = x.ops[:-1]
    if x.mn == "add" and len(x.ops) == 2 and src[0].startswith("#") and dst in alias | {"r15": 0}:
        if dst == "r15":
            sp += int(src[0][1:], 0)
        else:
            alias[dst] += int(src[0][1:], 0)
        return sp, alias, written, allw
    if x.mn == "mov" and len(x.ops) == 2 and (src[0] == "r15" or src[0] in alias):
        if dst == "r15":  # r15 restored from a frame pointer
            return base(src[0]), alias, written, allw
        alias[dst] = base(src[0])
        return sp, alias, written, allw
    if x.mn in ("add", "sub") and dst == "r15" and len(src) == 1 and "c:" + src[0] in alias:
        k = alias["c:" + src[0]]  # a large frame: `mov.w #-N,r0; add r0,r15`
        return sp + (k if x.mn == "add" else -k), alias, written, allw
    for o in src:  # a frame address used as a value: it escapes
        if not o.startswith("@") and any(r == "r15" or r in alias for r in sh4_asm.regs_in(o)):
            allw = True
    for r in list(alias):
        reg = r[2:] if r.startswith("c:") else r
        if any(MEM.match(o) and reg in sh4_asm.regs_in(o) for o in x.ops):
            continue  # an address use (post-increment / pre-decrement handled above)
        rd, wr = sh4_asm.uses(x, reg)
        if wr:
            allw = allw or (rd and reg == r)  # changed by unknown arithmetic: where it points is no longer known
            del alias[r]
    pv = x.pool_value()
    m = re.fullmatch(r"#(-?(?:0x)?[0-9a-f]+)", src[0]) if x.mn == "mov" and len(src) == 1 else None
    if re.fullmatch(r"r\d+", dst) and (m or (pv and x.mn in ("mov.w", "mov.l"))):
        v = int(m.group(1), 0) if m else pv[0]
        alias["c:" + dst] = v - (1 << 32) if v >= 1 << 31 else v
    if not dst.startswith("@") and "r15" in sh4_asm.regs_in(dst) and sh4_asm.uses(x, "r15")[1]:
        raise _Abort(f"r15 changed by `{x.mn} {','.join(x.ops)}` at {x.addr:08x}")
    return sp, alias, written, allw


def _after_call(st, call, callee_regs=None):
    """callee_regs: {callee addr: its argument registers} (meta callees' "regs"): a frame address in any of them
    escapes too, e.g. a helper that takes its pointers in r0 / r1."""
    sp, alias, written, allw = st
    args = {"r4", "r5", "r6", "r7"} | set((callee_regs or {}).get(call.call_target(), ()))
    if any(r in alias for r in args):
        allw = True
    alias = {r: v for r, v in alias.items() if not re.fullmatch(r"(c:)?r[0-7]", r)}
    return sp, alias, written, allw


def _merge(a, b):
    if a is None:
        return b
    if a[0] != b[0]:
        raise _Abort(f"r15 differs at a join ({a[0]} / {b[0]})")
    alias = {r: v for r, v in a[1].items() if b[1].get(r) == v}
    return a[0], alias, set(a[2]) & set(b[2]), a[3] and b[3]


def _same(a, b):
    return a is not None and a[0] == b[0] and a[1] == b[1] and set(a[2]) == set(b[2]) and a[3] == b[3]


def scan(insns, entry_sp=ENTRY_SP, callees=None):
    """{"status": "ok" | why not, "reads": [{"at", "addr", "off", "size"}]} for a parsed listing. callees: the
    meta's callee records; a call passes a frame address in each callee's own argument registers as well as r4..r7."""
    callee_regs = {c["addr"]: c.get("regs") or () for c in callees or ()}
    if not insns:
        return {"status": "no listing", "reads": []}
    if any(x.mn == "fschg" for x in insns):
        return {"status": "fschg: fmov sizes unknown", "reads": []}
    flow, reads = sh4_asm.Flow(insns), set()
    states, todo, visits = {0: (0, {}, set(), False)}, [0], 0
    try:
        while todo:
            i = todo.pop()
            visits += 1
            if visits > MAX_VISITS:
                raise _Abort("too many paths")
            out = _transfer(insns[i], states[i], reads)
            nxt = flow.succ(i)
            if nxt is None:
                raise _Abort(f"unknown flow after {insns[i].addr:08x}")
            for j, kind in nxt:
                if kind == "end":
                    continue
                s2 = _after_call(out, insns[i - 1], callee_regs) if kind == "call" else out
                m = _merge(states.get(j), s2)
                if not _same(states.get(j), m):
                    states[j] = m
                    todo.append(j)
    except _Abort as e:
        return {"status": str(e), "reads": []}
    out = [{"at": f"{a:08x}", "addr": f"{entry_sp + off:08x}", "off": off, "size": n} for a, off, n in sorted(reads)]
    return {"status": "ok", "reads": out}


def words(result):
    """The distinct 4-byte-aligned guest words of a scan's reads, in address order."""
    return sorted({(int(r["addr"], 16) + k) & ~3 for r in result["reads"] for k in range(r["size"])})


PIN_COMMENT = "# batch 27: an uninitialized stack word the function reads, pinned to 0 in every vector"
PIN = re.compile(r"^genrange 0x([0-9a-f]{8}) 0 0$")


def pinned(hints_text):
    """The guest words a hints file pins to 0 (`genrange 0xADDR 0 0`)."""
    return {int(m.group(1), 16) for ln in (hints_text or "").splitlines() if (m := PIN.match(ln.strip()))}


def pin_text(hints_text, ws):
    """hints_text plus a pin line for every word of ws not pinned yet (None: nothing to add)."""
    new = [w for w in ws if w not in pinned(hints_text)]
    if not new:
        return None
    body = (hints_text or "").rstrip("\n")
    lines = ([body] if body else []) + [PIN_COMMENT] + [f"genrange 0x{w:08x} 0 0" for w in new]
    return "\n".join(lines) + "\n"


def context_lines(meta, hints_text):
    """context.md lines for a readable attempt: each uninitialized frame word raw.c reads, and what to write."""
    res = (meta or {}).get("uninit_stack") or {}
    ws = words(res) if res.get("reads") else []
    if not ws:
        return []
    pins = pinned(hints_text)
    out = ["", "## Uninitialized stack words", ""]
    for w in ws:
        at = ", ".join(sorted({r["at"] for r in res["reads"] if (int(r["addr"], 16) & ~3) == w}))
        how = ("the harness pins it to 0 in every vector: use 0 there, with a comment saying the original reads an "
               "uninitialized stack word" if w in pins else "not pinned yet: report it under MISSING FIELDS")
        out.append(f"- 0x{w:08x} (raw.c's guest stack, read at {at}) is read before anything writes it; {how}.")
    return out


def scan_packet(packet_dir, entry_sp=ENTRY_SP, callees=None):
    """callees: the meta's callee records (default: the packet's meta.json, when it has one)."""
    text = sh4_asm.read_packet(packet_dir)
    if callees is None:
        mp = Path(packet_dir) / "meta.json"
        callees = (json.loads(mp.read_text(encoding="utf-8")).get("callees") if mp.exists() else None) or []
    return scan(sh4_asm.parse(text) if text else [], entry_sp, callees)
