"""Tidier C: simplification passes over the translator's emitted lines, before the r8..r14 hand-over analysis.

Each pass is switchable (FunctionTranslator.simplify, a set of names) and keeps what the function does:
- shift:  `r0 <<= 2; r0 <<= 8;` (consecutive logical shifts of one register, no label between) -> `r0 <<= 10;`
- const:  a register known to hold a constant (within a block) is written as that constant where it is read in a
          plain statement (`rd32(r3)` with r3 = 0x0608223c -> `rd32(0x0608223cu)`), and literal arithmetic folds
- copy:   after `r1 = r12;`, reads of r1 in plain statements read r12 (until either is written, a call or a label)
- dce:    an instruction whose only effect is registers or flags no later code reads is dropped; memory reads and
          writes, calls, hardware and runtime accesses, and anything that writes other state are never dropped
- inline: `X = E;` followed by a statement that reads X once, after which X is dead (or rewritten by it), becomes
          that one statement with E in place of X
Never r15 as a constant (the stack stays relative). Liveness here covers every register variable and T, MACH, MACL
and PR: a call reads its arguments (its text), the hand-over of r8..r14 at calls and exits reads them all, a
return reads the result register(s), and a halt reads everything.
"""
from __future__ import annotations

import re

PASSES = ("shift", "const", "copy", "dce", "inline", "params", "struct")
U32 = 0xFFFFFFFF
VAR = r"(?:r1[0-5]|r[0-9]|fr1[0-5]|fr[0-9]|fpul|t|mach|macl|pr)"
VARS = re.compile(rf"\b{VAR}\b")
VARSET = {f"r{k}" for k in range(16)} | {f"fr{k}" for k in range(16)} | {"fpul", "t", "mach", "macl", "pr"}
STATE = {f"r{k}" for k in range(8, 15)}
SIDE = re.compile(r"\b(?:rd\w*|wr\w*|sq_\w+|tx_call|body|tc_\w+|sh4_\w+|sx8|sx16|CV)\s*\(|@|\bgoto\b|\breturn\b|\bswitch\b|"
                  r"\bif\b")
PLAIN = re.compile(rf"^({VAR}) = (.*);$")
COMPOUND = re.compile(rf"^({VAR}) (\+|-|&|\||\^|<<|>>)= (.*);$")
WRITE = re.compile(r"^(wr(?:8|16|32))\((.*)\);$")
ARG = re.compile(r"^(a\.(?:r|fr|s|rlo)\[\d\]) = (.*);$")      # a call's argument (TcArgs field)
SHIFT = re.compile(rf"^({VAR}) (<<|>>)= (\d+);$")
LIT = r"(?:0x[0-9a-f]+u|\d+u)"
FOLD = re.compile(rf"(^|[(,=]\s*)({LIT}) (\+|-|&|\||\^|<<|>>) ({LIT})(?=\s*(?:[),;]|$))")
CALLS = ("bsr", "bsrf", "jsr")
BRANCHES = ("bt", "bf", "bt/s", "bf/s", "bra", "rts", "bsr", "bsrf", "jsr", "braf", "jmp", "bsrl")


def lit(v):
    from .translate import h
    return h(v)


def litval(s):
    return int(s[:-1], 0)


CASTS = [("(u8)(u32)(s32)(s8)", "(u8)"), ("(u16)(u32)(s32)(s16)", "(u16)")]
LITFIRST = re.compile(rf"([(,]\s*)({LIT}) \+ ({VAR})(?=\s*[),])")


SEXT_LIT = re.compile(rf"\(u32\)\(s32\)\((s8|s16)\)({LIT})(?![\w.(\[])")
TRUNC_LIT = re.compile(rf"\((u8|u16)\)({LIT})(?![\w.(\[])")


def sext(v, bits):
    v &= (1 << bits) - 1
    return (v - (1 << bits) if v >> (bits - 1) else v) & 0xFFFFFFFF


def tidy(text):
    """Equal forms that read better: a truncation of a sign extension is the truncation alone (the low bits are the
    same), and in a sum of a literal and a register the register comes first."""
    for a, b in CASTS:
        text = text.replace(a, b)
    # a cast of a literal is the literal it gives (MSVC /W4 rejects a cast that truncates a constant: C4310)
    text = SEXT_LIT.sub(lambda m: lit(sext(litval(m.group(2)), 8 if m.group(1) == "s8" else 16)), text)
    text = TRUNC_LIT.sub(lambda m: f"({m.group(1)})" + lit(litval(m.group(2)) & (0xFF if m.group(1) == "u8" else 0xFFFF)), text)
    return LITFIRST.sub(lambda m: f"{m.group(1)}{m.group(3)} + {m.group(2)}", text)


def fold(text):
    """Literal arithmetic as one literal, where the two literals are a whole operand list or right-hand side."""
    while True:
        m = FOLD.search(text)
        if not m:
            return tidy(text)
        a, op, b = litval(m.group(2)), m.group(3), litval(m.group(4))
        if op in ("<<", ">>") and b >= 32:
            return text
        v = {"+": a + b, "-": a - b, "&": a & b, "|": a | b, "^": a ^ b,
             "<<": a << b if op == "<<" else 0, ">>": a >> b}[op] & U32
        text = text[:m.start(2)] + lit(v) + text[m.end(4):]


def text_reads(lines):
    """Variables a node's C reads: every variable token except the target of a plain assignment."""
    out = set()
    for ln in lines:
        for piece in ln.split(";"):
            p = piece.strip().lstrip("{").strip()
            if p.startswith(("u32 ", "f32 ")):
                p = p[4:]
            m = re.match(rf"^({VAR}) = ", p)
            if m:
                p = p[m.end():]
            out |= set(VARS.findall(p))
    return out


def atomic(e):
    if re.fullmatch(rf"{VAR}|{LIT}|\w+", e):
        return True
    m = re.fullmatch(r"(?:\((?:u|s)(?:8|16|32)\))*\w+\((.*)\)", e)
    if m:  # one call (with casts before it) whose parentheses close at the end
        depth = 0
        body = e[e.index("(", len(re.match(r"(?:\((?:u|s)(?:8|16|32)\))*", e).group(0))):]
        for k, ch in enumerate(body):
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if depth == 0 and k != len(body) - 1:
                return False
        return True
    return False


def is_bool(e):
    """e is a comparison or logical expression at its top level (C gives it type int, not u32)."""
    while e.startswith("(") and e.endswith(")") and atomic_parens(e):
        e = e[1:-1]
    if e.startswith("!"):
        return True
    depth = 0
    for k, ch in enumerate(e):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0 and (e.startswith(("==", "!=", "<=", ">=", "&&", "||"), k)
                           or (ch in "<>" and e[k + 1:k + 2] not in "<>=" and e[k - 1:k] not in "<>")):
            return True
    return False


def atomic_parens(e):
    """e's first parenthesis closes at its end."""
    depth = 0
    for k, ch in enumerate(e):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0:
            return k == len(e) - 1
    return False


def subst(line, x, repl):
    """line with reads of x replaced by repl, in a plain statement only (None when x is not read there)."""
    if not VARS.search(line) or ";" in line[:-1] or "{" in line:
        return None
    pat = re.compile(rf"\b{x}\b")
    m = PLAIN.match(line)
    if m:
        if not pat.search(m.group(2)):
            return None
        return fold(f"{m.group(1)} = {pat.sub(lambda _m: repl, m.group(2))};")
    m = COMPOUND.match(line)
    if m:
        lhs, op, rhs = m.groups()
        if lhs == x:
            r = pat.sub(lambda _m: repl, rhs)
            return fold(f"{lhs} = {repl} {op} {r if atomic(r) else '(' + r + ')'};")
        if not pat.search(rhs):
            return None
        return fold(f"{lhs} {op}= {pat.sub(lambda _m: repl, rhs)};")
    m = ARG.match(line)
    if m:
        if not pat.search(m.group(2)):
            return None
        return fold(f"{m.group(1)} = {pat.sub(lambda _m: repl, m.group(2))};")
    m = WRITE.match(line)
    if m:
        if not pat.search(m.group(2)):
            return None
        return fold(f"{m.group(1)}({pat.sub(lambda _m: repl, m.group(2))});")
    return None


class Simplifier:
    def __init__(self, ft, items):
        self.ft, self.items = ft, items
        self.order = sorted(ft.primary)
        self.pos = {x: k for k, x in enumerate(self.order)}
        self.dropped: set[int] = set()
        self.unwritten: dict[int, set] = {}   # node -> variables whose assignment in it was dropped

    # -------------------------------------------------------------------------------------------- helpers
    def lines(self, a):
        s, e = self.ft.span[a]
        return [self.items[k][1] for k in range(s, e) if self.items[k][1]]

    def set_lines(self, a, new):
        s, e = self.ft.span[a]
        for k in range(s, e):
            self.items[k] = ("line", "")
        for k, x in enumerate(new):
            self.items[s + k] = ("line", x)

    def code_lines(self, a):
        """The node's own statements (not markers or labels): what a non-branch node may have rewritten."""
        return [x.strip() for x in self.lines(a)]

    def is_branch(self, a):
        return self.ft.insns[a].op in BRANCHES

    def node_writes(self, a):
        from .translate import writes
        i = self.ft.insns[a]
        w = set(writes(i))
        if i.delayed and a + 2 in self.ft.insns:
            w |= writes(self.ft.insns[a + 2])
        return w

    def next_in_block(self, a):
        k = self.pos[a]
        if k + 1 >= len(self.order):
            return None
        b = self.order[k + 1]
        if b in self.ft.leaders or b != a + 2:
            return None
        return b

    # -------------------------------------------------------------------------------------------- passes
    def shift(self):
        for a in self.order:
            if a in self.dropped or self.is_branch(a):
                continue
            while True:
                b = self.next_in_block(a)
                while b is not None and b in self.dropped:
                    b = self.next_in_block(b)
                if b is None or self.is_branch(b) or b in self.dropped:
                    break
                la, lb = self.code_lines(a), self.code_lines(b)
                if len(la) != 1 or len(lb) != 1:
                    break
                ma, mb = SHIFT.match(la[0]), SHIFT.match(lb[0])
                if not (ma and mb and ma.group(1) == mb.group(1) and ma.group(2) == mb.group(2)):
                    break
                n = int(ma.group(3)) + int(mb.group(3))
                if n >= 32:
                    break
                self.set_lines(a, [f"{ma.group(1)} {ma.group(2)}= {n};"])
                self.set_lines(b, [])
                self.dropped.add(b)

    def propagate(self, use_const, use_copy):
        from .translate import writes
        ft = self.ft
        consts, copies = {}, {}
        for a in self.order:
            i = ft.insns[a]
            if a in ft.leaders:
                consts, copies = {}, {}
            if not self.is_branch(a) and a not in self.dropped:
                new = []
                gone = set()    # written by the node's earlier statements: the state before it no longer holds
                for x in self.code_lines(a):
                    y = x
                    for r in sorted(VARS.findall(x), key=len, reverse=True):
                        rep = None
                        if r in gone:
                            pass
                        elif use_const and r in consts and r != "r15":
                            rep = lit(consts[r])
                        elif use_copy and r in copies and copies[r] not in gone:
                            rep = copies[r]
                        if rep is not None:
                            z = subst(y, r, rep)
                            if z is not None:
                                y = z
                    new.append(y)
                    m = PLAIN.match(y) or COMPOUND.match(y)
                    if m:
                        gone.add(m.group(1))
                if new != self.code_lines(a):
                    self.set_lines(a, new)
            # the state after the node, from the instructions (their meaning is unchanged by any rewrite)
            seq = [i] + ([ft.insns[a + 2]] if i.delayed and a + 2 in ft.insns else [])
            for x in seq:
                consts = ft.const_step(x, consts)
                w = writes(x)
                copies = {k: v for k, v in copies.items() if k not in w and v not in w}
                if x.op == "mov" and x.n != x.m and f"r{x.n}" != "r15" and f"r{x.m}" not in consts:
                    copies[f"r{x.n}"] = f"r{x.m}"
            if self.is_branch(a) and a not in self.dropped:
                # a call's (or tail call's) arguments are read after the delay slot: the state just computed. Only
                # up to the node's first call: a fall-through tail call after it reads registers the call changed.
                new, before = [], True
                for raw in self.lines(a):
                    x = y = raw.strip()
                    if before and ARG.match(x):
                        for r in sorted(VARS.findall(x), key=len, reverse=True):
                            rep = None
                            if use_const and r in consts and r != "r15":
                                rep = lit(consts[r])
                            elif use_copy and r in copies:
                                rep = copies[r]
                            if rep is not None:
                                z = subst(y, r, rep)
                                if z is not None:
                                    y = z
                    if "tc_call(" in x:
                        before = False
                    new.append(raw[:len(raw) - len(raw.lstrip())] + y)
                if new != self.lines(a):
                    self.set_lines(a, new)
            if ft.mutation and ft.mutation[0] == a:
                # a mutation test changed this node's C: what it writes is no longer what the instruction says
                for x in seq:
                    w = writes(x)
                    consts = {k: v for k, v in consts.items() if k not in w}
                    copies = {k: v for k, v in copies.items() if k not in w and v not in w}
            if i.op in CALLS:
                copies = {}
                consts = {k: v for k, v in consts.items() if k == "r15"}

    def liveness(self):
        """live_out per node, over every variable (module doc)."""
        ft = self.ft
        sig = ft.sig
        if sig.ret == "void":
            ret = {"r0"} if ft.sp_input else set()     # the port form's body hands back r0 (translate.render)
        elif sig.ret_reg:
            ret = {sig.ret_reg}
        elif sig.ret == "u64":
            ret = {"r0", "r1"}
        elif sig.ret == "f32":
            ret = {"fr0", "r0"}     # r0: a tail call's result is its bits
        else:
            ret = {"r0"}
        if sig.fr0_result:
            ret = ret | {"fr0"}
        gen, kill = {}, {}
        for a in self.order:
            ls = self.lines(a)
            g = text_reads(ls) & VARSET
            if any("@SYNC_OUT" in x or "@RETURN" in x or "tc_halt" in x for x in ls):
                g |= ft.state
            if any("@RETURN" in x for x in ls):
                tail = any("@RETURN_CLEAN" in x for x in ls)     # after a tail call: the callee's r0
                g |= (ret - {"fr0"} if tail else ret) | ({"pr"} if ft.pr_targets else set())
            if any("tc_halt" in x for x in ls):
                g |= VARSET
            gen[a] = g
            mutated = ft.mutation and ft.mutation[0] == a     # a mutation test's C: assume it writes nothing
            kill[a] = (set() if a in self.dropped or mutated
                       else (self.node_writes(a) & VARSET) - self.unwritten.get(a, set()))
        succ = {a: list(ft.succ.get(a, ())) for a in self.order}
        if ft.pr_targets:
            for a in self.order:
                if any("@RETURN" in x for x in self.lines(a)):
                    succ[a] += sorted(ft.pr_targets)
        live_in = {a: set() for a in self.order}
        live_out = {a: set() for a in self.order}
        changed = True
        while changed:
            changed = False
            for a in reversed(self.order):
                out = set()
                for b in succ[a]:
                    out |= live_in.get(b, set())
                inn = gen[a] | (out - kill[a])
                if out != live_out[a] or inn != live_in[a]:
                    live_out[a], live_in[a] = out, inn
                    changed = True
        self.live_in = live_in
        return live_out

    def dce(self):
        changed_any = False
        while True:
            live_out = self.liveness()
            changed = False
            for a in self.order:
                if a in self.dropped or self.is_branch(a):
                    continue
                ls = self.code_lines(a)
                if not ls or any(SIDE.search(x) for x in ls):
                    continue
                w = self.node_writes(a)
                if not w or not w <= VARSET or w & live_out[a]:
                    if len(ls) == 1 and "{" not in ls[0] and ls[0].count(";") > 1:
                        # several statements: drop the assignments of dead variables no later one reads
                        st = [x.strip() + ";" for x in ls[0].split(";") if x.strip()]
                        keep = []
                        for k, x in enumerate(st):
                            m = PLAIN.match(x)
                            later = " ".join(st[k + 1:])
                            if (m and not SIDE.search(m.group(2)) and m.group(1) not in live_out[a]
                                    and not re.search(rf"\b{m.group(1)}\b", later)):
                                self.unwritten.setdefault(a, set()).add(m.group(1))
                                continue
                            keep.append(x)
                        if len(keep) < len(st):
                            self.set_lines(a, [" ".join(keep)] if len(keep) > 1 else keep)
                            changed = changed_any = True
                    continue
                self.set_lines(a, [])
                self.dropped.add(a)
                changed = changed_any = True
            if not changed:
                return changed_any

    def fold_flag(self, j, k, live_out):
        """Node j sets T alone (`t = E;`) and node k (bt/bf, no delay slot) branches on it with one line: the
        branch tests E itself, when T is dead after k."""
        from .structure import neg
        lj, lk = self.code_lines(j), self.code_lines(k)
        if len(lj) != 1 or len(lk) != 1 or "t" in live_out[k]:
            return False
        mj = PLAIN.match(lj[0])
        mb = re.match(r"^if \((!?)t\) (goto L_\w+;)$", lk[0])
        if not mj or not mb or mj.group(1) != "t" or (self.node_writes(j) & VARSET) != {"t"}:
            return False
        e = mj.group(2)
        if re.search(r"\b(?:tx_call|body|tc_\w+|sh4_\w+|CV)\s*\(|@", e) or len(e) > 100:
            return False
        if not re.search(r"\b(?!(?:[us](?:8|16|32)|0x[0-9a-f]+u?|\d+u?)\b)[A-Za-z_]\w*", e):
            return False    # a constant test stays on t: a constant if/while condition is warning C4127
        self.set_lines(k, [f"if ({neg(e) if mb.group(1) else e}) {mb.group(2)}"])
        self.set_lines(j, [])
        self.dropped.add(j)
        return True

    def inline(self):
        changed_any = False
        while True:
            # one liveness per sweep: an inline changes the live sets at its own pair only (x is dead after k, the
            # reads of e move from j to k), so pairs it does not touch keep valid sets; touched ones wait a sweep
            live_out = self.liveness()
            changed = False
            touched = set()
            for j in self.order:
                if j in self.dropped or j in touched or self.is_branch(j):
                    continue
                k = self.next_in_block(j)
                while k is not None and k in self.dropped:
                    k = self.next_in_block(k)
                if k is not None and k not in touched and self.ft.insns[k].op in ("bt", "bf"):
                    # `t = E;` and the branch on it: `if (E) goto L;` when nothing after reads T
                    if self.fold_flag(j, k, live_out):
                        touched |= {j, k}
                        changed = changed_any = True
                    continue
                if k is None or k in touched or self.is_branch(k):
                    continue
                lj, lk = self.code_lines(j), self.code_lines(k)
                if len(lj) != 1 or len(lk) != 1:
                    continue
                mj = PLAIN.match(lj[0])
                if not mj:
                    continue
                x, e = mj.groups()
                if re.search(r"\b(?:tx_call|body|tc_\w+|sh4_\w+|CV)\s*\(|@", e) or "{" in lk[0] or ";" in lk[0][:-1]:
                    continue
                if (self.node_writes(j) & VARSET) != {x}:
                    continue
                y = lk[0]
                mc = COMPOUND.match(y)
                if mc and mc.group(1) == x:
                    r = mc.group(3)
                    y = f"{x} = {x} {mc.group(2)} {r if atomic(r) else '(' + r + ')'};"
                mp = PLAIN.match(y)
                rhs_reads = VARS.findall(mp.group(2)) if mp else None
                if mp:
                    if rhs_reads.count(x) != 1:
                        continue
                    if mp.group(1) != x and x in live_out[k]:
                        continue
                    put = e if atomic(e) else "(" + e + ")"
                    if is_bool(e) and mp.group(2).strip() != x:
                        put = "(u32)" + put     # an int 0/1 inside a u32 expression (C4389 on == against a u32)
                    new = mp.group(1) + " = " + re.sub(rf"\b{x}\b", lambda _m: put, mp.group(2)) + ";"
                elif WRITE.match(y):
                    if VARS.findall(y).count(x) != 1 or x in live_out[k]:
                        continue
                    put = e if atomic(e) else "(" + e + ")"
                    new = re.sub(rf"\b{x}\b", lambda _m: put, y)
                else:
                    continue
                if len(new) > 110:
                    continue
                if re.search(r"\b(?:rd\w*)\s*\(", e) and re.search(r"\btc_hw_\w+\s*\(", new):
                    continue
                self.set_lines(k, [fold(new)])
                self.set_lines(j, [])
                self.dropped.add(j)
                touched |= {j, k}
                changed = changed_any = True
            if not changed:
                return changed_any


def run(ft, items):
    """Apply ft.simplify's passes to items (the emitted lines) in place."""
    s = Simplifier(ft, items)
    p = ft.simplify
    if "const" in p or "copy" in p:
        s.propagate("const" in p, "copy" in p)
    for _ in range(20):   # each pass can open the way for another (a dead flag dropped lets two shifts fold)
        before = [x for _, x in items]
        if "shift" in p:
            s.shift()
        if "dce" in p:
            s.dce()
        if "inline" in p:
            s.inline()
        if [x for _, x in items] == before:
            break
    ft.dropped = s.dropped
    s.liveness()
    ft.sim_live_in = s.live_in.get(ft.entry, set()) | {"r15"}   # what the body reads before writing
