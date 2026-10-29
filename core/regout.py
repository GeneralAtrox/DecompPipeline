"""regout.py: the SH-4 functions whose harness TcOut (the r4..r7 outputs; targets/dreamcast_sh4/ghidra_scripts/TcOut.java)
would write something into, found without Ghidra. selective_restamp --change regout re-prepares exactly these.

TcOut writes into a harness F's meta.json / vectors / expected (r_out, r_out_left, K/A lines, r_exports, O mask, RO
lines) when
  (a) F calls g, g leaves some r4..r7 not as it found it (any path: TcLive.Deltas.outs, g's own calls followed), and F
      reads that register after the call before writing it (TcStrict.walk from call + 4, over F's extent), or
  (b) F itself leaves some r4..r7 changed and some caller reads it after calling F (r_exports).
A call here is also a tail jump (jmp @rN / bra) to a function entry: Ghidra types it as a call (a `bra` mid-body of
PS2 8c077f70 was not, 8c0b106c's were; the order the functions were made in decides, so all count), and the walk starts
at jump + 4, which is past F's code (the literal pool: no instruction, so TcStrict.walk answers "r1..r7 all read"; the
next function's first instruction when one follows). F's own exports are live at its returns (TcStrict.exitLive).

The model is the Ghidra one cut down to "kept or not": r4..r7 are kept at a return when they hold their entry value.
The view is a superset where it must guess (the sample proves the rest, so a miss costs the proof, a spare costs one
prepare): a call target the literals cannot give is every callee the harness meta.json lists; a function whose indirect
jump is unresolved, a cycle, or a flow the walk cannot follow leaves nothing kept.
Code addresses: a literal 0x0cXXXXXX / 0xacXXXXXX is the same RAM word as 0x8cXXXXXX (PS2's pools hold the 0c form).

Nearest existing: coord/agents/sp-regout/tools/regscan.py (outside DP; sp-regout's study) - it lists the triples where a
callee changes r1..r7 and the caller reads them, but resolves only 0x8c literals, ignores tail jumps, exports and
unresolved targets, so it names 7 PS2 callers where TcOut writes into about 17% of the harnesses. This keeps its
analysis and the decoder, and adds those.
"""
import bisect
import json
import pathlib
import struct
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "targets" / "dreamcast_sh4" / "translator"))
from sh4translate.decode import decode  # noqa: E402
from sh4translate.translate import reads, writes  # noqa: E402

R4_7 = 0xf0
R1_7 = 0xfe
CALLS = ("jsr", "bsr", "bsrf")
JUMPS = ("bra", "jmp", "braf")
BRANCHES = ("bt", "bf", "bt/s", "bf/s")
TOP = (-1, 0)
MAX_VISITS = 64
MAX_TAIL = 4000


def p1(v):
    """A code address in the cached P1 form (0x0c / 0xac mirrors map to 0x8c)."""
    return 0x8c000000 | (v & 0xffffff) if v >> 24 in (0x0c, 0xac) else v


def regs(names):
    return {int(r[1:]) for r in names if r.startswith("r") and r[1:].isdigit()}


class Code:
    """The image, the functions' bodies (state/function_bodies.txt: entry lo hi, hi exclusive) and per-function results."""

    def __init__(self, image, base, bodies, callees=None):
        self.image, self.base = image, base
        self.bodies = bodies                    # entry -> [(lo, hi)]
        self.callees = callees or {}            # entry -> [callee entry]: the harness meta.json callee records
        self.cover = sorted((lo, hi) for r in bodies.values() for lo, hi in r)
        self.ins, self.ext, self.memo, self.open, self.cl = {}, {}, {}, set(), {}

    def covered(self, a):
        """Whether Ghidra has an instruction at a: inside some function's body, or a run that decodes cleanly up to a
        return or jump (code no function claimed); else the pool data or the alignment nops after a function."""
        if a not in self.cl:
            k = bisect.bisect_right(self.cover, (a, 1 << 40))
            ok = any(a < hi for _, hi in self.cover[max(0, k - 8):k])
            x = a if self.insn(a) is not None and self.insn(a).op != "nop" else a + 1000  # padding is not disassembled
            while not ok and x < a + 48 and x >= a:
                i = self.insn(x)
                if i is None or i.op == "illegal":
                    break
                if i.op in ("rts", "rte", "jmp", "bra", "braf"):
                    ok = self.insn(x + 2) is not None and self.insn(x + 2).op != "illegal"
                    break
                x += 4 if i.delayed else 2
            self.cl[a] = ok
        return self.cl[a]

    def insn(self, a):
        if a not in self.ins:
            o = a - self.base
            self.ins[a] = decode(struct.unpack_from("<H", self.image, o)[0], a) if 0 <= o <= len(self.image) - 2 else None
        return self.ins[a]

    def lit(self, a):
        o = a - self.base
        return p1(struct.unpack_from("<I", self.image, o)[0]) if 0 <= o <= len(self.image) - 4 else None

    def target(self, a, limit=24):
        """The call or jump target of the instruction at a: bsr/bra's own, or the literal loaded into Rn (through
        register copies) in the instructions just before a jsr/jmp @Rn. None: unresolved."""
        i = self.insn(a)
        if i.op in ("bsr", "bra"):
            return i.ea
        if i.op not in ("jsr", "jmp"):
            return None
        want = i.m
        for b in range(a - 2, a - 2 * limit, -2):
            j = self.insn(b)
            if j is None:
                return None
            if b != a - 2 and j.op in ("rts", "bra", "jmp", "braf", "rte"):
                return None
            if want in regs(writes(j)):
                if j.op == "mov.l@pc":
                    return self.lit(j.ea)
                if j.op == "mov" and j.n == want:
                    want = j.m
                    continue
                return None
        return None

    def extent(self, f):
        """The addresses f runs: its body, plus the code its jumps and local calls reach outside it that is no
        function's entry (TcTail.extent)."""
        if f in self.ext:
            return self.ext[f]
        ext = {a for lo, hi in self.bodies[f] for a in range(lo, hi, 2)}
        work = [a for a in ext]
        added = 0
        while work:
            a = work.pop()
            i = self.insn(a)
            if i is None or i.op not in CALLS + JUMPS + BRANCHES:
                continue
            t = self.target(a) if i.op in ("bsr", "bra", "jsr", "jmp") else i.ea if i.op in BRANCHES else None
            if t is None or t in ext or t in self.bodies or self.insn(t) is None:
                continue
            x = t
            while added < MAX_TAIL and x not in ext and (x == t or x not in self.bodies) and self.insn(x) is not None:
                ext.add(x)
                work.append(x)
                added += 1
                j = self.insn(x)
                if j.delayed:
                    ext.add(x + 2)
                if j.op in ("rts", "rte", "bra", "jmp", "braf"):
                    break
                x += 4 if j.delayed else 2
        self.ext[f] = ext
        return ext

    def succ(self, f, a, ext):
        """(addresses the flow reaches inside f's extent, whether control leaves: rts, or a jump out)."""
        i = self.insn(a)
        nxt = a + 4 if i.delayed else a + 2
        if i.op in ("rts", "rte"):
            return [], True
        if i.op in JUMPS:
            t = self.target(a) if i.op != "braf" else None
            return ([t], False) if t is not None and t in ext and t not in self.bodies else ([], True)
        if i.op in BRANCHES:
            return [x for x in (i.ea, nxt) if x in ext], False
        return ([nxt] if nxt in ext else []), False

    # ---- callee side: are r4..r7 kept at every return? ----
    def kept(self, f):
        """Bits (4..7 -> 0x10..0x80) of the registers f leaves as it found them on every path; 0 for a function the
        scan cannot follow (a cycle, an unresolved indirect jump, a flow past MAX_VISITS)."""
        if f in self.memo:
            return self.memo[f]
        if f in self.open or f not in self.bodies:
            return 0
        self.open.add(f)
        r = self.solve(f)
        self.open.discard(f)
        self.memo[f] = r
        return r

    def step(self, s, i):
        o, n, m = i.op, i.n, i.m
        if o == "mov" and n != m:
            s[n] = s[m]
            return
        if o == "add#":
            s[n] = (s[n][0], s[n][1] + i.imm) if s[n][0] != -1 else s[n]
            return
        if o == "mov.l@-_st":
            if s[n][0] == 15:
                s[n] = (15, s[n][1] - 4)
                s[16] = {**s[16], s[n][1]: s[m]}
            else:
                s[n] = TOP
            return
        if o == "mov.l@disp_st" and s[n][0] == 15:
            s[16] = {**s[16], s[n][1] + i.imm: s[m]}
            return
        if o == "mov.l@_st" and s[n][0] == 15:
            s[16] = {**s[16], s[n][1]: s[m]}
            return
        if o == "mov.l@+_ld" and m != n:
            v = s[16].get(s[m][1]) if s[m][0] == 15 else None
            s[m] = (s[m][0], s[m][1] + 4) if s[m][0] != -1 else s[m]
            s[n] = v or TOP
            return
        if o == "mov.l@disp_ld" and s[m][0] == 15:
            s[n] = s[16].get(s[m][1] + i.imm, TOP)
            return
        if o == "mov.l@_ld" and s[m][0] == 15:
            s[n] = s[16].get(s[m][1], TOP)
            return
        if o in ("mov.b@+_ld", "mov.w@+_ld") and m != n:
            s[m] = (s[m][0], s[m][1] + (1 if ".b" in o else 2)) if s[m][0] != -1 else s[m]
            s[n] = TOP
            return
        if o in ("mov.b@-_st", "mov.w@-_st"):
            s[n] = (s[n][0], s[n][1] - (1 if ".b" in o else 2)) if s[n][0] != -1 else s[n]
            return
        if o.endswith("@-_st") or o.endswith("@+_ld"):  # fmov and the rest: the pointer moves by an unknown size
            for r in regs(writes(i)) | ({n} if o.endswith("@-_st") else {m}):
                s[r] = TOP
            return
        for r in regs(writes(i)):
            s[r] = TOP

    def call(self, s, t):
        """After a call to t: r0..r7 are the callee's; a callee that keeps rK hands it back as it was."""
        old = list(s[:8])
        for k in range(8):
            s[k] = TOP
        if t is not None and t in self.bodies:
            keep = self.kept(t)
            for k in range(4, 8):
                if keep >> k & 1:
                    s[k] = old[k]

    def solve(self, f):
        ext = self.extent(f)
        inn, visits, work = {f: [(k, 0) for k in range(16)] + [{}]}, defaultdict(int), [f]
        kept = R4_7
        switch = any(self.insn(a).op == "mova" for a in ext if self.insn(a) is not None)
        while work:
            a = work.pop()
            visits[a] += 1
            i = self.insn(a)
            if visits[a] > MAX_VISITS or i is None:
                return 0
            s = list(inn[a])
            slot = self.insn(a + 2) if i.delayed else None
            if i.delayed and slot is not None:
                self.step(s, slot)                          # the slot runs before the branch takes effect
            if i.op in CALLS:
                self.call(s, self.target(a) if i.op != "bsrf" else None)
            elif not i.delayed:
                self.step(s, i)
            nx, ends = self.succ(f, a, ext)
            if ends:
                if i.op in JUMPS:
                    t = self.target(a) if i.op != "braf" else None
                    if t is None and (i.op == "braf" or switch):
                        return 0                            # a jump table: its successors are unknown
                    if t is None:
                        continue                            # a thunk through a pointer: Ghidra sees no exit here
                    self.call(s, t)                         # a tail call: the callee's outs
                for k in range(4, 8):
                    if s[k] != (k, 0):
                        kept &= ~(1 << k)
                continue
            for x in nx:
                if x not in inn:
                    inn[x] = s
                    work.append(x)
                    continue
                old, ch = inn[x], False
                new = list(old)
                for k in range(16):
                    if old[k] != s[k] and old[k] != TOP:
                        new[k], ch = TOP, True
                if old[16] != s[16]:
                    new[16] = {y: v for y, v in old[16].items() if s[16].get(y) == v}
                    ch = ch or new[16] != old[16]
                if ch:
                    inn[x] = new
                    work.append(x)
        return kept

    # ---- caller side: which of r1..r7 are read after a call, before they are written ----
    def walk(self, f, start, exit_live=0):
        """TcStrict.walk: the r1..r7 bits read from start before written on some path in f's extent. A start with no
        instruction answers all of them."""
        ext = self.extent(f)
        if start not in ext and not self.covered(start):
            return R1_7
        seen, visits, live = {}, defaultdict(int), 0
        work = [(start, 0)]
        while work:
            a, w = work.pop()
            if a in seen and seen[a] & w == seen[a]:
                continue
            seen[a] = w & seen[a] if a in seen else w
            visits[a] += 1
            i = self.insn(a)
            if i is None or visits[a] > MAX_VISITS:
                return R1_7
            seq = [i] + ([self.insn(a + 2)] if i.delayed and self.insn(a + 2) else [])
            w = seen[a]
            for j in seq:
                live |= sum(1 << r for r in regs(reads(j)) if 1 <= r <= 7) & ~w
            for j in seq:
                if j.op not in CALLS:
                    w |= sum(1 << r for r in regs(writes(j)) if 1 <= r <= 7)
            nx, ends = self.succ(f, a, ext)
            if ends:
                live |= exit_live & ~w
            if a in ext or start == a:
                for x in nx:
                    work.append((x, w))
        return live

    def sites(self, f):
        """f's call sites: (address, target or None, is a tail jump). Tail jumps are the jumps out of f's extent into
        a function entry, or unresolved."""
        ext, out = self.extent(f), []
        for a in sorted(ext):
            i = self.insn(a)
            if i is None or i.op not in CALLS + JUMPS or a - 2 in ext and self.insn(a - 2).delayed:
                continue
            t = self.target(a) if i.op != "bsrf" and i.op != "braf" else None
            if i.op in JUMPS and (t in ext and t not in self.bodies):
                continue
            out.append((a, t, i.op in JUMPS))
        return out


def scan(image, base, bodies, callees=None):
    """(a, b, why): the functions TcOut writes into as callers (a) and as callees (b); why: {function: [reasons]}."""
    c = Code(image, base, bodies, callees)
    sites = {f: c.sites(f) for f in bodies}
    ret = lambda a: a + (4 if c.insn(a).delayed else 2)  # noqa: E731
    exports = {}
    for f, ss in sites.items():                              # (b) first: exit_live of (a) is the exports
        for a, t, tail in ss:
            for g in ([t] if t is not None else c.callees.get(f, [])):
                if g not in bodies:
                    continue
                want = ~c.kept(g) & R4_7
                if want:
                    exports[g] = exports.get(g, 0) | (want & c.walk(f, ret(a) if not tail else a + 4))
    a_set, why = set(), defaultdict(list)
    for f, ss in sites.items():
        live = exports.get(f, 0)
        for a, t, tail in ss:
            gs = [t] if t is not None else c.callees.get(f, [])
            gs = [g for g in gs if g in bodies and ~c.kept(g) & R4_7]
            if not gs:
                continue
            read = c.walk(f, a + 4 if tail else ret(a), live) & R4_7
            for g in gs:
                if read & ~c.kept(g):
                    a_set.add(f)
                    why[f].append(f"calls {g:08x} at {a:08x}")
    b_set = {g for g, x in exports.items() if x}
    for g in b_set:
        why[g].append("exports")
    return a_set, b_set, why


def load(ws):
    """(image, base, bodies, callees) of a workspace's decomp folder (project.json, state/function_bodies.txt,
    harness/*/meta.json)."""
    ws = pathlib.Path(ws)
    pj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    image = (ws / pj["binary"]).resolve().read_bytes()
    base = int(pj.get("load_address", "0x8c010000"), 16)
    bodies = defaultdict(list)
    for ln in (ws / "state" / "function_bodies.txt").read_text().split("\n"):
        p = ln.split()
        if len(p) == 3:
            bodies[int(p[0], 16)].append((int(p[1], 16), int(p[2], 16)))
    callees = {}
    for m in (ws / "harness").glob("*/meta.json"):
        try:
            cs = json.loads(m.read_text(encoding="utf-8")).get("callees") or []
        except ValueError:
            continue
        callees[int(m.parent.name, 16)] = [p1(int(str(c["addr"]).removeprefix("0x"), 16)) for c in cs]
    return image, base, dict(bodies), callees


def candidates(ws):
    """The addresses (8 hex digits) of the workspace's functions TcOut would write into."""
    a, b, _ = scan(*load(ws))
    return {f"{x:08x}" for x in a | b}


if __name__ == "__main__":
    for w in sys.argv[1:]:
        a, b, _ = scan(*load(w))
        print(f"{w}: {len(a)} callers (a), {len(b)} exporters (b), {len(a | b)} in all")
