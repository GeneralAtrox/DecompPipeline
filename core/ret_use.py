"""Which result registers callers read after calling a function, and the raw return-type candidates that follow
(batch 27). Static, from the packets' asm.txt and the harness meta.json files; no Ghidra.

Two generic decompiler mistakes are listed as candidates for a names entry's "raw_ret" (core/sig_override.py), never
applied by themselves:
- tail: the raw ret is void, but the function ends in a tail call (bra/jmp to a function) whose callee returns a
  value: the function returns the callee's value (TC 8c075928 Lib_FileBrowserCompareNames).
- f32_unread: the raw ret is f32, and no call site reads fr0 before writing it (the f32 is a leftover of the last
  FPU operation): `void`, or `u32` when a call site reads r0 instead (TC 8c07cbba, 8c07b738).
- fr0_exit: the raw ret is void, u32 or s32, but the function's own listing returns in fr0 (core/ret_reg.py: fr0
  written last and not read again on some path to `rts`, r0 on none) and its callers agree (fr0_confirmed): `f32`
  (TC 8c0611c8). The judge compares fr0 for the ones listed in state/fr0_results.txt meanwhile.
A call site is read on every path from the call's return to the next write of the register, call (a callee whose
record compares that register reads it), return (the caller's own result register reads it) or tail call. A path
the listing cannot follow (a computed jump) makes the site unknown; a function whose address is also loaded as a
value somewhere (a function pointer: callers this cannot see) or that has no call site is never a f32_unread candidate.
"""
import json
import pathlib

from core import ret_reg, sh4_asm

INT_RETS = ("u32", "s32", "u64")
MAX_STEPS = 4000


def result_reg(meta):
    """The register a function's raw ret occupies (r0, fr0, its ret_reg) or None for void."""
    if meta.get("ret_reg"):
        return meta["ret_reg"]
    return "fr0" if meta.get("ret") == "f32" else "r0" if meta.get("ret") in INT_RETS else None


def fr0_confirmed(ev):
    """The callers agree that a function returns in fr0: some call site reads fr0 and none reads r0."""
    return ev["fr0"] > 0 and ev["r0"] == 0


def _read_json(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def reads_after(insns, flow, i, reg, regs_of, own):
    """True when reg is read after the call at i (delay slot i + 1) on some path, False when no path reads it,
    None when a path cannot be followed. regs_of(callee) -> the registers a call to it compares; own: the caller's
    result register (or None)."""
    todo, seen, steps = [i + 2], set(), 0
    while todo:
        j = todo.pop()
        if j in seen:
            continue
        seen.add(j)
        steps += 1
        if j >= len(insns) or steps > MAX_STEPS:
            return None
        r, w = sh4_asm.uses(insns[j], reg)
        if r:
            return True
        if w:
            continue
        nxt = flow.succ(j)
        if nxt is None:
            return None
        for k, kind in nxt:
            if kind == "call" or (kind == "end" and insns[j - 1].mn not in ("rts", "rte")):
                if reg in regs_of(insns[j - 1].call_target()):
                    return True
            elif kind == "end":
                if reg == own:
                    return True
            else:
                todo.append(k)
    return False


class Project:
    """The harness metas and packet listings of one workspace, read once."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.meta, self.asm = {}, {}
        hd = self.root / "harness"
        for d in sorted(hd.iterdir()) if hd.is_dir() else []:
            m = _read_json(d / "meta.json")
            if m:
                self.meta[d.name] = m
        self.callers, self.pool_refs, self.calls = {}, {}, {}
        for a in self.meta:
            ins = self.insns(a)
            for x in ins or []:
                t = x.call_target()
                if t and x.mn in sh4_asm.CALLS | {"bra", "jmp"}:
                    self.callers.setdefault(t, set()).add(a)
                    self.calls[t] = self.calls.get(t, 0) + (x.mn in ("jsr", "jmp"))
                pv = x.pool_value()
                if pv and pv[1] == "function":
                    t = f"{pv[0]:08x}"
                    self.pool_refs[t] = self.pool_refs.get(t, 0) + 1

    def insns(self, a):
        if a not in self.asm:
            text = sh4_asm.read_packet(self.root / "packets" / a)
            self.asm[a] = sh4_asm.parse(text) if text else None
        return self.asm[a]

    def address_taken(self, f):
        """f's address is loaded as a value more often than a jsr/jmp calls it: a function pointer."""
        return self.pool_refs.get(f, 0) > self.calls.get(f, 0)

    def sites(self, f):
        """[(caller, call address, {'r0': bool|None, 'fr0': bool|None})] for every call or tail call of f."""
        out = []
        for g in sorted(self.callers.get(f, ())):
            ins, gm = self.insns(g), self.meta.get(g) or {}
            regs = {c["addr"]: set(c.get("regs") or ()) for c in gm.get("callees") or []}
            flow = sh4_asm.Flow(ins)
            for x in ins:
                if x.call_target() != f or x.mn not in sh4_asm.CALLS | {"bra", "jmp"}:
                    continue
                if x.mn in ("bra", "jmp"):  # a tail call: f's result is g's own
                    own = result_reg(gm)
                    out.append((g, x.addr, {r: r == own for r in ("r0", "fr0")}))
                    continue
                own = result_reg(gm)
                out.append((g, x.addr, {r: reads_after(ins, flow, x.idx, r, lambda c: regs.get(c, set()), own)
                                        for r in ("r0", "fr0")}))
        return out

    def tail_callees(self, f):
        """The functions f's listing tail-calls (bra/jmp to a function)."""
        return sorted({x.call_target() for x in self.insns(f) or [] if x.mn in ("bra", "jmp") and x.call_target()})

    def evidence(self, f):
        """{'sites', 'unknown', 'r0', 'fr0', 'address_taken'}: how f's result registers are used."""
        s = self.sites(f)
        return {"sites": len(s), "unknown": sum(any(v is None for v in u.values()) for _, _, u in s),
                "r0": sum(bool(u["r0"]) for _, _, u in s), "fr0": sum(bool(u["fr0"]) for _, _, u in s),
                "address_taken": self.address_taken(f)}

    def candidates(self):
        """[{addr, case, ret, suggest, why, evidence}] in address order."""
        out = []
        for f, m in sorted(self.meta.items()):
            if m.get("ret_override"):
                continue
            if m.get("ret") in ("void", "u32", "s32") and not m.get("ret_reg") and ret_reg.fr0_result(self.insns(f)):
                ev = self.evidence(f)
                if fr0_confirmed(ev):
                    out.append({"addr": f, "case": "fr0_exit", "ret": m["ret"], "suggest": "f32",
                                "why": f"the listing returns in fr0; {ev['fr0']} call site(s) read fr0",
                                "evidence": ev})
                    continue
            if m.get("ret") == "void" and not m.get("ret_reg"):
                tails = [(c, (self.meta.get(c) or {}).get("ret")) for c in self.tail_callees(f)]
                vals = [(c, r) for c, r in tails if r in INT_RETS + ("f32",)]
                blind = [c for c, r in tails if r is None]  # a callee outside the scope (SDK): no harness
                if vals:
                    out.append({"addr": f, "case": "tail", "ret": "void", "suggest": vals[0][1],
                                "why": "tail call to " + ", ".join(f"{c} ({r})" for c, r in vals),
                                "evidence": self.evidence(f)})
                elif blind:
                    out.append({"addr": f, "case": "tail_unknown", "ret": "void", "suggest": "?",
                                "why": "tail call to " + ", ".join(blind) + " (no harness: its result is unknown)",
                                "evidence": self.evidence(f)})
            elif m.get("ret") == "f32" and not m.get("fpu_state"):
                ev = self.evidence(f)
                if ev["sites"] and not ev["unknown"] and not ev["fr0"] and not ev["address_taken"]:
                    out.append({"addr": f, "case": "f32_unread", "ret": "f32", "suggest": "u32" if ev["r0"] else "void",
                                "why": f"{ev['sites']} call site(s), none reads fr0"
                                       + (f"; {ev['r0']} read r0" if ev["r0"] else ""), "evidence": ev})
        return out
