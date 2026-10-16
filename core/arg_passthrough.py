"""Argument registers a function passes on unwritten to a callee (a read-only asm view; no Ghidra).

SHC leaves an argument in its register when the function only forwards it: `sts.l pr; mov.l @(lit),r5; jsr @r2`
calls the callee with the caller's own r4. Ghidra's prototype misses such a parameter when the callee's view of it is
missing too (an opaque thunk that jumps through a table, like a library's syscall vector): the harness then neither
gives the function that parameter nor compares that register at the call, and raw C passes 0 there. A designer who
names the parameter (from the asm) makes the readable function take it, and context.md says where it goes
(context_lines), since the judge cannot tell.

A register counts when it is unwritten on some path from the entry to a call (a bsr/jsr, a tail call, or an
unresolved jmp in its delay slot's flow) and the callee's names prototype takes it (a parameter not named unused*).
After a call the argument registers count as written (caller-saved). Float registers are skipped in a function that
switches FPU banks or modes.
"""
from .readable import parse_proto  # first: core.readable imports readable_abi back
from . import readable_abi, sh4_asm  # noqa: E402

ARGS = tuple([f"r{i}" for i in range(4, 8)] + [f"fr{i}" for i in range(4, 12)])
MODE = ("frchg", "fschg", "fpchg")


def takes(reg, addr, reads_only=False):
    """Argument registers of addr's names prototype ("storage" when given), or None when it has none. reads_only:
    without the placeholder parameters named unused* (the callee does not read them)."""
    e = reg.functions.get(addr)
    if not e:
        return None
    try:
        st = readable_abi.override(reg, addr, e["proto"]) or readable_abi.storages(reg, e["proto"])
        params = parse_proto(e["proto"])[2]
    except Exception:  # a bad prototype is the registry's error, reported elsewhere
        return None
    return {s for s, (_, n) in zip(st, params) if s in ARGS and not (reads_only and n.lower().startswith("unused"))}


def scan(insns, reg, entry):
    """{register: ("read", None) | ("to", callee addr)}: argument registers some path from entry reads before writing,
    or passes unwritten to a callee that takes them (the first finding per register)."""
    flow = sh4_asm.Flow(insns)
    floats = not any(x.mn in MODE for x in insns)
    found, seen = {}, {}
    work = [(flow.at.get(entry), frozenset(ARGS))]
    while work:
        i, state = work.pop()
        if i is None or i >= len(insns):
            continue
        old = seen.get(i)
        if old is not None and state <= old:
            continue
        state = state | old if old is not None else state
        seen[i] = state
        x, cur = insns[i], set(state)
        for r in state:
            rd, wr = sh4_asm.uses(x, r)
            if rd and (floats or r.startswith("r")):
                found.setdefault(r, ("read", None))
            if wr:
                cur.discard(r)
        succ = flow.succ(i)
        br = insns[i - 1] if x.slot and i > 0 and insns[i - 1].mn in sh4_asm.DELAYED else None
        if br is not None and (br.mn in sh4_asm.CALLS or br.call_target() or (br.mn == "jmp" and succ is None)):
            tgt = br.call_target()
            for r in cur & ((takes(reg, tgt, True) if tgt else None) or set()):
                if floats or r.startswith("r"):
                    found.setdefault(r, ("to", tgt))
            if br.mn in sh4_asm.CALLS:
                cur = set()
        for j, _ in succ or ():
            if j is not None:
                work.append((j, frozenset(cur)))
    return found


def for_function(reg, addr, packets_dir):
    """scan() of addr's packet asm, or {} without one."""
    text = sh4_asm.read_packet(packets_dir / addr)
    return scan(sh4_asm.parse(text), reg, int(addr, 16)) if text else {}


def context_lines(reg, addr, params, storages, raw_regs, packets_dir):
    """context.md notes: a parameter raw.c does not use (raw_regs: the storages of raw.c's parameters) that the
    original passes on unchanged to a callee. The judge does not compare that register, so only this note says it."""
    found = None
    out = []
    for (_, n), s in zip(params, storages):
        if s in raw_regs or s not in ARGS:
            continue
        if found is None:
            found = for_function(reg, addr, packets_dir)
        how = found.get(s)
        if how and how[0] == "to":
            callee = parse_proto(reg.functions[how[1]]["proto"])[1]
            out.append(f"  NOTE: the original never writes {s} before calling {callee}: it passes `{n}` on unchanged "
                       f"there. Pass `{n}` as that argument of {callee} (raw.c's 0 or nothing in {s} is a gap of its "
                       f"harness, which does not compare that register), never `(void){n}` with 0 or NULL in its place.")
    return out
