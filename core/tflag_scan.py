"""Which functions the T-across-a-call change (ghidra_scripts/TcTflag) can alter, found from the packet asm alone
(tools/selective_restamp.py --change tflag). Pure text: no Ghidra, no core.paths.

TcTflag.exportedFn decides, per function, whether some caller reads its T after calling it. This scan must list at
least every function the oracle exports (a superset costs a re-prepare; a miss leaves a stale harness, as 8c055e10,
8c085d90 and 8c178500 did in rs32). What it follows, case by case against exportedFn:
- a caller that reads T after the call before writing it, or reaches its own return / a tail call with T unwritten
  while that caller is exported itself (to a fixed point): the oracle's walk and exportedFn; `stc SR` reads T and
  `ldc ..,SR` writes it, whatever the case the asm prints the register in;
- a cycle in the callers' chain counts as exported in the oracle (expMemo.put(entry, true) before the walk): every
  function whose chain of hand-on callers reaches a cycle is listed (`risky`);
- a chain deeper than MAX_DEPTH counts: every function with a hand-on chain that long above it is listed;
- walk's MAX_VISITS 64 cannot be reached (a node is walked at most twice: first with T unwritten, then as written),
  so it needs no rule;
- callers the packets do not show (Ghidra's call references from a function without a packet): only with outside=True
  (--change tflag+outside) and state/graph.tsv beside the packets, a callee called from such a function counts as
  exported (its asm is not here to prove it does not read T). Off by default: it lists about 100 more functions per
  copy (PS2 6,610 packets, Spawn main 1,866) and none of the 12 exported functions seen so far needed it.
Nearest existing: tools/selective_restamp.py's own tflag_scan - moved here, not copied (that file may only shrink under
the 400-line ratchet), with the oracle's cases it missed added.
"""
import pathlib
import re

ASM = re.compile(r"^\s*[0-9a-f]{8}\s+_?(\S+)\s+(.*?)\s*(;.*)?$")
T_READS = {"bt", "bf", "bt/s", "bf/s", "addc", "subc", "negc", "rotcl", "rotcr", "movt", "div1"}  # T read first
T_WRITES = {"clrt", "sett", "dt", "shll", "shlr", "shal", "shar", "rotl", "rotr", "addv", "subv", "div0s", "div0u",
            "tas.b", "tst", "tst.b"}  # (and cmp/..., fcmp/..., ldc/ldc.l to sr)
CALLS = {"jsr", "bsr", "bsrf"}
CALLED = re.compile(r";\s*call ([0-9a-f]{8})")
TAILS = {"bra", "braf", "jmp"}  # with a "; call" note: a tail call (no return to the caller)
MAX_DEPTH = 32  # TcTflag.MAX_DEPTH
INF = 1 << 30


def t_use(mn, ops):
    """1: the instruction reads T before writing it, 2: it writes T, 0: neither (asm mnemonic, operands in lower
    case)."""
    if mn in T_READS or (mn in ("stc", "stc.l") and ops[:1] == ["sr"]):
        return 1
    if mn in T_WRITES or mn.startswith(("cmp/", "fcmp/")) or (mn in ("ldc", "ldc.l") and ops[-1:] == ["sr"]):
        return 2
    return 0


def parse(d):
    """A packet's asm rows: (address, mnemonic, operands lower-cased, comment)."""
    rows = []
    for f in sorted(pathlib.Path(d).glob("asm*.txt")):
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ASM.match(ln)
            if m:
                rows.append((int(ln.split()[0], 16), m.group(1).lower(),
                             [o.strip().lower() for o in m.group(2).split(",")], m.group(3) or ""))
    return rows


def t_after(rows, at, start):
    """From rows[start]: 1 when T is read on some path before it is written, else 2 when some path reaches a return
    (rts, rte, jmp, or a tail call) with T unwritten, else 0. A path running off the listing reads."""
    work, seen, ret = [start], set(), 0
    while work:
        i = work.pop()
        while i not in seen:
            if i >= len(rows):
                return 1
            seen.add(i)
            _, mn, ops, note = rows[i]
            u = t_use(mn, ops)
            if u == 1:
                return 1
            if u == 2:
                break
            if mn in ("rts", "rte", "jmp", "braf") or mn == "bra" and CALLED.search(note):
                s = t_use(*rows[i + 1][1:3]) if i + 1 < len(rows) else 0  # its delay slot runs first
                if s == 1:
                    return 1
                ret = ret or (2 if s != 2 else 0)
                break
            if mn == "bra":  # (bt, bf, bt/s, bf/s read T: returned above)
                s = t_use(*rows[i + 1][1:3]) if i + 1 < len(rows) else 0
                if s == 1:
                    return 1
                m = re.match(r"0x([0-9a-f]{8})", ops[0])
                t = at.get(int(m.group(1), 16)) if m else None
                if t is not None and s != 2:
                    work.append(t)
                break
            i += 1
    return ret


def sites_of(packets):
    """{caller: [(callee, call site, 1: T read after it, 2: handed on)]}: the calls whose T the caller reads or hands
    on (forward from the call's delay slot). A call whose target the asm does not name has callee "?"."""
    sites = {}
    for d in pathlib.Path(packets).iterdir():
        rows = parse(d)
        at = {a: i for i, (a, _, _, _) in enumerate(rows)}
        for i, (a, mn, ops, note) in enumerate(rows):
            c = CALLED.search(note)
            if mn in CALLS:
                u = t_after(rows, at, i + 2)
            elif c and mn in TAILS:
                u = 2
            else:
                continue
            if u:
                sites.setdefault(d.name, []).append((c.group(1) if c else "?", f"{a:08x}", u))
    return sites


def risky(sites):
    """Functions TcTflag.exportedFn counts without finding a read: the callers' chain (callers that hand T on, u 2)
    above them reaches a cycle (the oracle's memo is set to true before the walk) or is longer than MAX_DEPTH."""
    up = {}
    for caller, ss in sites.items():
        for g, _, u in ss:
            if g != "?" and u == 2:
                up.setdefault(g, set()).add(caller)
    height, stack = {}, set()

    def h(g, depth):  # the longest chain of hand-on callers above g (INF: a cycle or past the cap)
        if g in stack or depth > MAX_DEPTH + 1:
            return INF
        if g in height:
            return height[g]
        stack.add(g)
        v = max((1 + h(c, depth + 1) for c in up.get(g, ())), default=0)
        stack.discard(g)
        height[g] = min(v, INF)
        return height[g]

    return {g for g in up if h(g, 0) >= MAX_DEPTH}


def outside_callees(packets):
    """Callees called from a function that has no packet (state/graph.tsv beside the packets: addr, calls, fptrs, ...):
    their callers' asm is not here, so they count as exported. Empty without the file."""
    graph = pathlib.Path(packets).parent / "state" / "graph.tsv"
    if not graph.exists():
        return set()
    have = {d.name for d in pathlib.Path(packets).iterdir()}
    out = set()
    for ln in graph.read_text(encoding="utf-8", errors="replace").splitlines():
        p = ln.split("\t") + [""]
        if p[0] not in have:
            out |= {x for x in p[1].split(",") if x}
    return out


def scan(packets, outside=False):
    """({caller: {callee: [call site, ...]}}, the exported functions): the calls whose T the caller reads, before writing it, or hands on by
    reaching its own return or a tail call with T unwritten while the caller is itself exported (some caller reads its
    T, the same way, to a fixed point; or a cycle / deep chain / caller without a packet, see the module header). A
    tail call's callee counts as handed on. A superset of TcTflag's sites (exportedFn, outs): the sample checks it. A
    call whose target the asm does not name counts with callee \"?\"."""
    sites = sites_of(packets)
    exported, grow = risky(sites) | (outside_callees(packets) if outside else set()), True
    while grow:
        grow = False
        for caller, ss in sites.items():
            for g, _, u in ss:
                if g != "?" and g not in exported and (u == 1 or caller in exported):
                    exported.add(g)
                    grow = True
    out = {}
    for caller, ss in sites.items():
        for g, site, u in ss:
            if u == 1 or caller in exported:
                out.setdefault(caller, {}).setdefault(g, []).append(site)
    return out, exported


def tflag_scan(packets, outside=False):
    """scan's calls only: {caller: {callee: [call site, ...]}}."""
    return scan(packets, outside)[0]


def tflag_candidates(packets, outside=False):
    """The functions whose harness the T-across-a-call change (TcTflag) can alter: callers that read or hand on T after
    a call (tflag_scan), the callees of those calls (their T is compared at exit) and the exported functions found
    without a read (cycle, deep chain; with outside, a caller without a packet). Every other harness stays byte for
    byte (no K line for register 16, no line t)."""
    calls, exported = scan(packets, outside)
    have = {d.name for d in pathlib.Path(packets).iterdir()}
    return set(calls) | {c for cs in calls.values() for c in cs if c != "?"} | (exported & have)
