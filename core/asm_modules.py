"""Hand-written asm modules: functions whose local subroutines pass state in registers outside the C ABI (D2; PS2
Lib_RenderModelA 8c188420..8c189530).

Why one function at a time cannot judge them: DP judges a function with its callees mocked (a mocked callee keeps
every register but r0/fr0, TcLive's r8..r14 deltas and TcOut's r4..r7 results). A local subroutine of hand-written
asm reads its inputs from whatever registers its caller left (r11/r3 set in a call's delay slot, r12 a flag word, r4
a cursor) and hands results back in T, r9's bits or fr registers. Its own harness cannot set those inputs (they are
no arguments), and its caller's mock cannot give those results back: the caller's harness hits the call limit, the
subroutine's runs code it cannot reach (PS2 8c188b60, 8c189000).

The rule, from the machine code alone (no game, no address). A catalog function L is LOCAL when
- no word of the image holds its address (P0/P1/P2 form: no pointer, table or literal-pool word: nothing calls it by
  address), and
- every branch to it is a bsr, bra, bt or bf (no jsr) from another function's code.
A local L SHARES REGISTERS with its referrer when
- input: on some path from its entry L reads a register before writing it that is no argument of the C ABI: r0..r3,
  r8..r14 (a push of a callee-saved register to @-r15 is no read), fr0..fr3, fr12..fr15, T, FPUL, MACH/MACL; or
- result: after a bsr to L the caller reads, before writing it, a register L writes other than r0, fr0 and PR.
A local whose only sharing is results in r4..r7 is TcOut's class (sp-regout: a mocked callee hands those back
exactly); a local sharing nothing is a plain private callee. Every other sharing local joins its referrers: the
connected functions form one MODULE. Its public entries are the members something outside it enters (a word holding
the address, a branch from outside the module); the rest are its local pieces, part of the unit's body: the oracle
runs them, the translator gives each local bsr a PR return (no call), and the catalog marks them not_entry.

Evidence comes with every result; tools/asm_modules.py lists them. SH-4 only (the translator's decoder).

Nearest existing: core/mid_entries.py - it finds code entered from outside a function (an entry to add), not the
local subroutines that share registers with their callers (pieces to fold into one unit); TcOut (sp-regout) hands
back r4..r7 only.
"""
import pathlib
import struct
import sys

from . import mid_entries

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "targets" / "dreamcast_sh4" / "translator"))
from sh4translate.decode import decode  # noqa: E402
from sh4translate.translate import R, reads, writes  # noqa: E402

BRANCH = ("bsr", "bra", "bt", "bf", "bt/s", "bf/s")
ABI_IN = {f"r{k}" for k in range(4, 8)} | {f"fr{k}" for k in range(4, 12)} | {"r15", "pr", "fpscr", "sr", "gbr"}
ABI_OUT = {"r0", "fr0", "pr", "r15"}
TCOUT = {f"r{k}" for k in range(4, 8)}
T_READS = ("bt", "bf", "bt/s", "bf/s", "rotcl", "rotcr", "addc", "subc", "negc", "movt", "div1")
AFTER_LIMIT = 64  # instructions followed after a call's return for the caller's reads


def regs_read(i):
    """The registers and flags i reads (the translator's reads() plus T), without the save of a register by a push."""
    if i.op in ("mov.l@-_st", "fmov.s@-_st") and R(i.n) == "r15":
        return set()
    if i.op == "sts.l":
        return set()
    r = set(reads(i))
    if i.op in T_READS:
        r.add("t")
    return r


class Code:
    """The image's instructions and each catalog function's code (its body ranges plus what its flow reaches)."""

    def __init__(self, image, base, bodies, starts):
        self.image, self.base, self.bodies, self.starts = image, base, bodies, set(starts)
        self.ins = {}

    def insn(self, a):
        if a not in self.ins:
            o = a - self.base
            self.ins[a] = decode(struct.unpack_from("<H", self.image, o)[0], a) if 0 <= o <= len(self.image) - 2 \
                else None
        return self.ins[a]

    def succ(self, a):
        """Where control goes after the instruction at a, inside its function (calls return; rts/braf/jmp end)."""
        i = self.insn(a)
        if i is None or i.op in ("rts", "rte", "braf", "jmp", "illegal"):
            return []
        if i.op == "bra":
            return [i.ea]
        if i.op in ("bt", "bf"):
            return [a + 2, i.ea]
        if i.op in ("bt/s", "bf/s"):
            return [a + 4, i.ea]
        return [a + 4] if i.delayed else [a + 2]

    def code(self, f):
        """f's instruction addresses: its body ranges and every address its flow reaches before another entry."""
        out = {a for lo, hi in self.bodies.chunks(f) for a in range(lo, hi, 2)}
        work = [f]
        seen = set()
        while work:
            a = work.pop()
            if a in seen or (a != f and a in self.starts) or self.insn(a) is None:
                continue
            seen.add(a)
            out.add(a)
            if self.insn(a).delayed:
                out.add(a + 2)
            work += self.succ(a)
        return out


def pointer_words(image, base, targets):
    """{target: [word address]}: aligned image words holding a target's address (P0/P1/P2 form)."""
    out = {}
    for k, w in enumerate(struct.unpack_from(f"<{len(image) // 4}I", image, 0)):
        a = mid_entries.p1(w)
        if a in targets:
            out.setdefault(a, []).append(base + 4 * k)
    return out


def entry_inputs(code, f):
    """The registers outside the C ABI's arguments f reads before writing them on some path from its entry."""
    got = set()
    work = [(f, frozenset())]
    seen = set()
    while work and len(seen) < 4096:
        a, done = work.pop()
        if (a, done) in seen:
            continue
        seen.add((a, done))
        i = code.insn(a)
        if i is None or (a != f and a in code.starts):
            continue
        steps = [i] + ([code.insn(a + 2)] if i.delayed else [])
        w = set(done)
        for x in steps:
            got |= (regs_read(x) - w) - ABI_IN
            w |= writes(x) if x.op not in ("bsr", "bsrf", "jsr") else set()
        if i.op in ("bsr", "bsrf", "jsr"):
            continue  # what the callee reads is its own business (a local callee is checked itself)
        for b in code.succ(a):
            work.append((b, frozenset(w)))
    return got


def reads_after(code, f_code, site):
    """{register: address}: what the code after the call at site reads before writing it (calls are transparent)."""
    got = {}
    work = [(site + 4, frozenset(), 0)]
    seen = set()
    while work:
        a, done, n = work.pop()
        if (a, done) in seen or n > AFTER_LIMIT or a not in f_code:
            continue
        seen.add((a, done))
        i = code.insn(a)
        w = set(done)
        for x in [i] + ([code.insn(a + 2)] if i.delayed else []):
            for r in regs_read(x) - w:
                got.setdefault(r, x.addr)
            w |= writes(x) if x.op not in ("bsr", "bsrf", "jsr") else {"pr"}
        for b in code.succ(a):
            work.append((b, frozenset(w), n + 1))
    return got


def kept_out(code, addrs):
    """The registers the code at addrs writes, less those it saves by a push to @-r15 (restored before it returns)."""
    w, saved = set(), set()
    for a in addrs:
        i = code.insn(a)
        if i is None:
            continue
        w |= writes(i)
        if i.op in ("mov.l@-_st", "fmov.s@-_st") and R(i.n) == "r15":
            saved |= reads(i) - {"r15"}
    return w - saved


def find(image, base, bodies, starts):
    """{"modules": [...], "tcout": [...], "plain": [...]}: each module {entries, locals, evidence}."""
    code = Code(image, base, bodies, starts)
    fcode = {f: code.code(f) for f in sorted(code.starts)}
    refs = {}  # target -> [(kind, from function, site)]
    into = {}  # entry no branch reaches -> [(function whose code runs on into it, site)]
    for f, addrs in fcode.items():
        for a in sorted(addrs):
            i = code.insn(a)
            if i is not None and i.op in BRANCH + ("jsr", "bsrf") and i.ea in code.starts and i.ea != f:
                refs.setdefault(i.ea, []).append((i.op, f, a))
            for b in code.succ(a):
                if b in code.starts and b != f and (i.op not in BRANCH or b != i.ea):
                    into.setdefault(b, []).append((f, a))
    into = {s: v for s, v in into.items() if s not in refs}
    words = pointer_words(image, base, set(refs) | set(into))
    local = [t for t, rs in refs.items() if t not in words and all(k in BRANCH for k, _, _ in rs)]
    writes_of = {t: kept_out(code, fcode[t]) for t in local}
    share, tcout, plain = {}, [], []
    for t in local:
        ev = [f"reads {r} at entry (no argument)" for r in sorted(entry_inputs(code, t))]
        res = {}
        for k, f, site in refs[t]:
            if k == "bsr":
                for r, at in reads_after(code, fcode[f], site).items():
                    if r in writes_of[t] - ABI_OUT:
                        res.setdefault(r, f"{f:08x} reads it at {at:08x} after the bsr at {site:08x}")
        ev += [f"hands back {r}: {why}" for r, why in sorted(res.items())]
        if not ev:
            plain.append(t)
        elif not [e for e in ev if not e.startswith("hands back")] and set(res) <= TCOUT:
            tcout.append((t, ev))
        else:
            share[t] = ev
    parent = {}

    def root(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for t in share:
        for _, f, _ in refs[t]:
            parent[root(t)] = root(f)
    for s, v in sorted(into.items()):  # a piece's tail that is a catalog entry nothing branches to (PS2 8c188b34)
        if not words.get(s) and all(f in share for f, _ in v):
            share[s] = [f"no reference; {f:08x} runs on into it at {a:08x}" for f, a in v]
            refs[s] = []
            parent[root(s)] = root(v[0][0])
    groups = {}
    for x in set(parent) | set(parent.values()):
        groups.setdefault(root(x), set()).add(x)
    modules = []
    for members in groups.values():
        entries = sorted(m for m in members if m not in share or words.get(m)
                         or any(f not in members for _, f, _ in refs.get(m, [])))
        ev = {f"{m:08x}": share[m] + [f"{k} from {f:08x} at {s:08x}" for k, f, s in refs[m]]
              for m in sorted(members) if m in share and m not in entries}
        modules.append({"entries": [f"{m:08x}" for m in entries],
                        "locals": sorted(ev), "evidence": ev})
    return {"modules": sorted(modules, key=lambda m: m["entries"]),
            "tcout": [{"addr": f"{t:08x}", "evidence": ev} for t, ev in sorted(tcout)],
            "plain": [f"{t:08x}" for t in sorted(plain)]}
