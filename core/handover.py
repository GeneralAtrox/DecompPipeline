"""Register hand-over across every call edge of a translated game, static and console-free (docs/HANDOVER_CHECK.md).

Each translated function is its own C function, so a register only travels between two of them through a channel the
translator builds: an argument field, the stack pointer, a thread register file. The judge runs one function alone and
supplies its inputs, so a register that is lost between two functions passes it. This compares, for every call edge,
what the callee's code reads before setting it with what the caller's emitted C hands over, and what the caller reads
after the call with what the callee's code can have written.

Input: one record per function, made by the console's translator from its own lifted blocks (the Xbox 360's is
targets/xbox360_ppc/port/tools/handover.py):
    {"b": [[stmt, ...], ...], "s": [[successor block, ...], ...], "c": [{"at": pc, "to": target or None, "tail": bool}]}
    A call may carry "sw": true - the fallback of a jump table (an index past the table): it is no write of the function.
    stmt: ["a", dst, [uses]]  an assignment of the function's own code (counts only when dst is read later)
          ["e", dst, [uses]]  the same, set up at entry by the translator (not a write of the function's code)
          ["u", [uses]]       a read (a store, a branch condition)
          ["c", index, [uses]] the call c[index]; uses are what the call itself reads (a pointer call's target)
Block 0 is the entry. Registers the caller always provides are left undefined there, so they show as needs.

    need(F)  = live-in of F's entry, where a call to G reads need(G) and ends the life of what G may write
    writes(G) = the volatile and status registers G's code, or anything it calls, assigns; everything for a pointer call

Nearest existing: targets/xbox360_ppc/translator/xenontranslate/fn_parts.py uncarried / unhanded - one function at a
time, against the callee map of the translation before, constant targets only, r and f registers only, nothing after the
call; xenontranslate/keeps.py solves writes over the image but reports no edge; tools/vabi_flow.py is vector-only on raw
decodes and assumes the ABI it then checks.
"""
import collections
import json

EMPTY = frozenset()
FWD_KINDS = ("fwd-unstored", "fwd-unloaded", "vec-unstored", "vec-unloaded", "fwd-nochannel")
RET_KINDS = ("ret-set", "ret-zeroed", "ret-kind", "ret-status", "ret-ptr", "ret-unknown")
FN_KINDS = ("entry-uncalled", "ptr-target")


class Abi:
    """What a console says about its registers. channel(reg): 'arg' / 'abi' (always handed over), 'file' (handed over
    only when the caller stores it and the callee loads it), 'vector' (the same, through the vector file), 'none'."""

    def __init__(self, is_reg, channel, clobber, status, result):
        self.is_reg, self.channel = is_reg, channel
        self.clobber, self.status, self.result = frozenset(clobber), frozenset(status), frozenset(result)
        self.all_kill = self.clobber | self.status


class Evidence:
    """What the emitted C does, per function; a console's tool subclasses it. None = not known (no C text)."""

    def stores(self, caller, callee):
        """Registers the caller's C stores into the file before every call to callee; None when its C has no such call."""
        return None

    def loads(self, fn):
        """Registers fn's C loads from the register or vector file."""
        return EMPTY

    def vstores(self, fn):
        """Vector registers fn's C stores into the vector file."""
        return EMPTY

    def nv(self, fn):
        """True when fn receives the caller's non-volatile registers as arguments (a funclet)."""
        return False

    def keeps(self, fn):
        """The volatile registers the emitted C leaves alone across a call to fn."""
        return EMPTY

    def kind(self, fn):
        """'r' / 'f' / 'v': where the emitted C takes fn's result from; None when not known."""
        return None

    def pointed(self):
        """Addresses a data word of the image holds (possible pointer-call targets)."""
        return EMPTY


class Image:
    def __init__(self, lines, abi, noret=EMPTY):
        """lines: {address: the record's JSON text}. Kept as text: a function is parsed only while it is analysed.
        noret: addresses known never to return although their records may show a return (a console's fatal and raise
        imports, outside the records; a game's longjmp and exit, whose records show a return); a function
        whose every path calls one never returns either."""
        self.raw, self.abi, self.seeds = lines, abi, frozenset(noret)
        self.own, self.calls, self.callers = {}, {}, collections.defaultdict(set)
        kill_all = abi.all_kill
        for a, ln in lines.items():
            rec = json.loads(ln)
            self.own[a] = frozenset(st[1] for b in rec["b"] for st in b if st[0] == "a")
            self.calls[a] = rec["c"]
            for c in rec["c"]:
                if c["to"] is not None:
                    self.callers[c["to"]].add(a)
        self.kill = {a: set(w & kill_all) for a, w in self.own.items()}     # writes(G), solved below
        work = collections.deque(self.raw)
        queued = set(work)
        while work:
            a = work.popleft()
            queued.discard(a)
            w, n = self.kill[a], len(self.kill[a])
            for c in self.calls[a]:
                if not c.get("sw"):          # as keeps.own_writes: a jump table's fallback writes nothing the code relies on
                    w |= self.kill.get(c["to"], kill_all)
            if len(w) != n:
                for p in self.callers.get(a, ()):
                    if p in self.raw and p not in queued:
                        queued.add(p)
                        work.append(p)
        self.noret = self._never_return() | self.seeds
        self.need = {a: EMPTY for a in self.raw}
        self.own_need = {a: self.flow(a, own=True) for a in self.raw}     # what its own code reads, callees apart
        self._solve()

    def kills(self, target):
        return self.kill.get(target, self.abi.all_kill)

    def flow(self, addr, record=None, own=False):
        """Live-in of addr's entry with today's needs. record: {call index: what is live right after that call}.
        own: a call reads nothing for its callee (the reads of the function's own code only)."""
        rec = json.loads(self.raw[addr])
        blocks, succ, calls = rec["b"], rec["s"], rec["c"]
        lin = [EMPTY] * len(blocks)

        def sweep(rec_into):
            changed = False
            for i in range(len(blocks) - 1, -1, -1):
                live = set()
                for j in succ[i]:
                    live |= lin[j]
                for st in reversed(blocks[i]):
                    k = st[0]
                    if k in ("u", "r"):
                        live.update(st[1])
                    elif k == "c":
                        to = calls[st[1]]["to"]
                        if to in self.noret:     # nothing after a call that never returns runs: no read there is live
                            live = set()
                        if rec_into is not None:
                            rec_into[st[1]] = frozenset(live)
                        live -= self.kills(to)
                        live.update(st[2])
                        if not own:
                            live |= self.need.get(to, EMPTY)
                    elif st[1] in live:          # an assignment nothing reads is no read of its operands
                        live.discard(st[1])
                        live.update(st[2])
                if live != lin[i]:
                    lin[i], changed = frozenset(live), True
            return changed
        for _ in range(100000):
            if not sweep(None):
                break
        else:
            raise RuntimeError(f"liveness of {addr:08x} does not settle")
        if record is not None:
            sweep(record)
        return frozenset(x for x in lin[0] if self.abi.is_reg(x)) if blocks else EMPTY

    def _never_return(self):
        """The functions no path of which reaches a return ("r"): each path ends in a loop, a trap or a call to such a function.
        A pointer call or a call to code not analysed is taken as returning, unless it is one of the seeds. Least fixpoint of "returns", so a cycle of calls
        with no return in it never returns. Too Human's fatal-error calls (82b2b250 under an assert) were followed by code
        that belongs to the next path: 632 ret-set rows read registers after them."""
        rets = set()

        def returns(a):
            rec = json.loads(self.raw[a])
            blocks, succ, calls = rec["b"], rec["s"], rec["c"]
            if not blocks:
                return True
            seen, work = {0}, [0]
            while work:
                i = work.pop()
                for st in blocks[i]:
                    if st[0] == "r":
                        return True
                    if st[0] == "c":
                        to = calls[st[1]]["to"]
                        if (to in self.raw and to not in rets) or to in self.seeds:
                            break
                        if calls[st[1]].get("tail"):     # a tail call to code that returns returns for it
                            return True
                else:
                    for j in succ[i]:
                        if j not in seen:
                            seen.add(j)
                            work.append(j)
            return False
        work = collections.deque(sorted(self.raw))
        queued = set(work)
        while work:
            a = work.popleft()
            queued.discard(a)
            if a not in rets and returns(a):
                rets.add(a)
                for p in self.callers.get(a, ()):
                    if p in self.raw and p not in rets and p not in queued:
                        queued.add(p)
                        work.append(p)
        return frozenset(self.raw) - rets

    def _solve(self):
        work = collections.deque(sorted(self.raw, reverse=True))
        queued = set(work)
        while work:
            a = work.popleft()
            queued.discard(a)
            n = self.flow(a)
            if n != self.need[a]:
                self.need[a] = n | self.need[a]
                for p in self.callers.get(a, ()):
                    if p in self.raw and p not in queued:
                        queued.add(p)
                        work.append(p)

    def lost(self, addr):
        """The needs of addr that only a file store or nothing at all can hand over."""
        ch = self.abi.channel
        return sorted(x for x in self.need[addr] if ch(x) in ("file", "none"))


def check(img, ev):
    """Every mismatch, as dicts {kind, caller, at, callee, reg}; and the counts of what was looked at.

    A forward finding is reported where the value is made and where it is read, not on the functions it only passes
    through (the files are the thread's, so a function that never touches the register need do nothing):
    fwd-unstored   the caller's code writes a register the callee needs (itself, or for something it calls), and the
                   caller's C does not store it into the file before the call
    fwd-unloaded   a function's own code reads a register before setting it and its C never loads it from the file
                   (one row a function: caller = callee = the function)
    vec-unstored / vec-unloaded   the same for the vector file
    fwd-nochannel  the caller's code writes a register the callee needs and no channel carries (a condition field,
                   carry, the count register)
    ret-set        the caller reads, after the call, a volatile register the callee's code writes: the C has 0 there
                   (or the caller's old value, when the C wrongly keeps it: reg is then marked "kept")
    ret-zeroed     the caller reads a volatile register the callee leaves alone, and the C zeroes it
    ret-kind       the caller reads r3 / f1 the callee writes, and the C takes the callee as returning nothing
    ret-status     the caller reads a condition field or carry the callee's code writes: the C keeps its own old value
    ret-ptr / ret-unknown   as ret-set, after a pointer call / a call to code that was not analysed (lower confidence)
    entry-uncalled a function needs a file or no-channel register and no constant call reaches it
    ptr-target     a function needs one and a data word holds its address (it can be called through a pointer)
    """
    abi, out = img.abi, []
    seen = collections.Counter()

    def add(kind, f, c, reg):
        out.append({"kind": kind, "caller": f, "at": c["at"] if c else 0, "callee": (c["to"] or 0) if c else f, "reg": reg})   # 0: a pointer call
    for f in sorted(img.raw):
        after = {}
        img.flow(f, after)
        for idx, c in enumerate(img.calls[f]):
            g, live = c["to"], after.get(idx)
            if live is None:
                seen["calls not reached in the blocks"] += 1
                continue
            known = g in img.raw
            seen["edges"] += 1
            seen["pointer calls" if g is None else "constant calls" if known else "calls to code not analysed"] += 1
            kill = img.kills(g)
            for x in sorted(live & kill):
                if not abi.is_reg(x):
                    continue
                if x in abi.result:
                    if known and ev.kind(g) == "v":
                        add("ret-kind", f, c, x)
                elif x in abi.status:
                    if known:
                        add("ret-status", f, c, x)
                elif known:
                    add("ret-set", f, c, x + (" kept" if x in ev.keeps(g) else ""))
                else:
                    add("ret-ptr" if g is None else "ret-unknown", f, c, x)
            if not known:
                continue
            for x in sorted((live & abi.clobber) - kill - abi.result - ev.keeps(g)):
                add("ret-zeroed", f, c, x)
            stores = ev.stores(f, g)
            for x in sorted(img.need[g] & img.own[f]):
                ch = abi.channel(x)
                if ch in ("arg", "abi") or (ch == "file" and ev.nv(g) and nonvolatile(x)):   # a funclet gets r14..r31 as arguments
                    continue
                if ch == "none":
                    add("fwd-nochannel", f, c, x)
                elif stores is None:
                    seen["needs not checked: the caller's C shows no such call"] += 1
                elif ch == "file" and x not in stores:
                    add("fwd-unstored", f, c, x)
                elif ch == "vector" and x not in ev.vstores(f):
                    add("vec-unstored", f, c, x)
    pointed = ev.pointed()
    for g in sorted(img.raw):
        loads = ev.loads(g)
        for x in sorted(img.own_need[g] - loads):
            ch = abi.channel(x)
            if ch == "vector" or (ch == "file" and not (ev.nv(g) and nonvolatile(x))):
                out.append({"kind": "vec-unloaded" if ch == "vector" else "fwd-unloaded", "caller": g, "at": 0, "callee": g, "reg": x})
        lost = img.lost(g)
        if not lost:
            continue
        seen["functions needing a file or no-channel register"] += 1
        direct = [p for p in img.callers.get(g, ()) if p in img.raw]
        for x in lost:
            if not direct:
                add("entry-uncalled", g, None, x)
            if g in pointed:
                add("ptr-target", g, None, x)
    return out, seen


def readers(img, g, reg, limit=8):
    """The functions under g (by constant calls that need reg) whose own code reads reg before setting it."""
    out, seen, work = [], {g}, [g]
    while work and len(out) < limit:
        a = work.pop()
        if reg in img.own_need.get(a, EMPTY):
            out.append(a)
        for c in img.calls.get(a, ()):
            t = c["to"]
            if t not in seen and reg in img.need.get(t, EMPTY):
                seen.add(t)
                work.append(t)
    return sorted(out)


def nonvolatile(reg):
    return reg[0] in "rf" and reg[1:].isdigit() and int(reg[1:]) >= 14


def closure(img, roots, depth=None):
    """roots and everything they reach by constant calls (to depth); {address: distance}."""
    dist = {r: 0 for r in roots}
    work = collections.deque(roots)
    while work:
        a = work.popleft()
        if depth is not None and dist[a] >= depth:
            continue
        for c in img.calls.get(a, ()):
            t = c["to"]
            if t is not None and t not in dist:
                dist[t] = dist[a] + 1
                work.append(t)
    return dist


def table(findings):
    """{kind: (edges, distinct callees, {register class: edges})}."""
    out = {}
    for k in FWD_KINDS + RET_KINDS + FN_KINDS:
        rows = [r for r in findings if r["kind"] == k]
        cls = collections.Counter(reg_class(r["reg"]) for r in rows)
        out[k] = (len(rows), len({r["callee"] for r in rows}), dict(cls))
    return out


def reg_class(reg):
    reg = reg.split()[0]
    if reg[0] in "rfv" and reg[1:].isdigit():
        n = int(reg[1:])
        if reg[0] == "v":
            return "vector"
        return ("int " if reg[0] == "r" else "float ") + ("non-volatile" if n >= 14 else "volatile")
    if reg[0] == "c" and len(reg) >= 4 and reg[1].isdigit():
        return "condition field" + (" non-volatile" if reg[1] in "234" else "")
    return {"ca": "carry", "so": "overflow", "ov": "overflow", "ctr": "count", "lr": "link"}.get(reg, reg)
