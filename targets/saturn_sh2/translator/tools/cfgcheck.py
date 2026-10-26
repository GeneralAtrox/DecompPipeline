"""Does a cfgstruct layout do what its goto form does? Both are run on the same branch outcomes: every condition is
a pure function of the statements run so far (`x_ = k;` lines), so two faithful forms take the same branches at the
same points and run the same statements. symbolize() turns a dumped body into that form.
usage: python tools/cfgcheck.py DUMPDIR   (a folder of SH2_CFG_DUMP=DUMPDIR goto forms: each one's layout is checked)
Read-only: it judges the layout cfgstruct gives, it changes nothing."""
import glob
import hashlib
import os
import re
import sys

LABEL = re.compile(r"^(L_\w+):;?$")
GOTO = re.compile(r"^goto (L_\w+);$")
CGOTO = re.compile(r"^if \((.*)\) goto (L_\w+);$")


def symbolize(lines):
    """Plain statements become x_ = k; each distinct condition becomes cN (a leading ! kept)."""
    out, sym, k = [], {}, 0
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("/*"):
            continue
        m = CGOTO.match(s)
        if m and m.group(1) in ("c_", "!c_"):     # the T-saving slot scope, recognised by its text
            out.append("    " + s)
        elif m:
            c, neg = m.group(1), ""
            while c.startswith("!") and not c.startswith("!="):
                neg, c = "!" if not neg else "", c[1:]
            out.append(f"    if ({neg}{sym.setdefault(c, f'c{len(sym)}')}) goto {m.group(2)};")
        elif LABEL.match(s) or GOTO.match(s) or s.startswith("return") or s == "}" or s == "{ u32 c_ = t;":
            out.append("    " + s if not LABEL.match(s) else s)
        else:
            k += 1
            out.append(f"    x_ = {k}u;")
    return out


class Run:
    def __init__(self, seed, cap=4000):
        self.trace, self.seed, self.steps, self.cap, self.fl = [], seed, 0, cap, 0

    def step(self):
        self.steps += 1
        if self.steps > self.cap:
            raise StopIteration

    def cond(self, name):
        h = hashlib.sha256(f"{self.seed}:{name}:{self.trace}".encode()).digest()
        return h[0] & 1 == 1

    def expr(self, c):
        """A condition of either form: cN, !cN, fl_ == Nu, fl_ != Nu, joined by && / || (no parentheses beyond
        a ! of a whole group, which cfgstruct writes as !(...))."""
        c = c.strip()
        if c.startswith("!(") and c.endswith(")") and balanced(c[2:-1]):
            return not self.expr(c[2:-1])
        if c.startswith("(") and c.endswith(")") and balanced(c[1:-1]):
            return self.expr(c[1:-1])
        for op in ("||", "&&"):
            parts = split_top(c, op)
            if len(parts) > 1:
                vals = (self.expr(p) for p in parts)
                return any(vals) if op == "||" else all(vals)
        m = re.fullmatch(r"fl_ (==|!=) (\d+)u", c)
        if m:
            return (self.fl == int(m.group(2))) == (m.group(1) == "==")
        if c.startswith("!"):
            return not self.expr(c[1:])
        if re.fullmatch(r"c\d+", c):
            return self.cond(c)
        if c in ("c_", "ct_"):     # T saved before a slot that writes it: a condition like any other here
            return self.cond("c_")
        raise ValueError(f"condition not modelled: {c}")

    def ops(self, s):
        m = re.fullmatch(r"x_ = (\d+)u;", s)
        if m:
            self.trace.append(int(m.group(1)))
            return True
        m = re.fullmatch(r"fl_ = (\d+)u;", s)
        if m:
            self.fl = int(m.group(1))
            return True
        return s == "ct_ = t;"


def balanced(s):
    d = 0
    for ch in s:
        d += ch == "("
        d -= ch == ")"
        if d < 0:
            return False
    return d == 0


def split_top(c, op):
    parts, d, cur, i = [], 0, "", 0
    while i < len(c):
        if c[i] == "(":
            d += 1
        elif c[i] == ")":
            d -= 1
        if d == 0 and c.startswith(op, i):
            parts.append(cur)
            cur, i = "", i + len(op)
            continue
        cur += c[i]
        i += 1
    return parts + [cur]


def run_goto(lines, seed):
    code = [l.strip() for l in lines if l.strip()]
    at = {LABEL.match(s).group(1): i for i, s in enumerate(code) if LABEL.match(s)}
    r, pc = Run(seed), 0
    try:
        while pc < len(code):
            r.step()
            s = code[pc]
            pc += 1
            if LABEL.match(s) or s.startswith("{") or s == "}":
                continue
            m = GOTO.match(s)
            if m:
                pc = at[m.group(1)]
                continue
            m = CGOTO.match(s)
            if m:
                if r.expr(m.group(1)):
                    pc = at[m.group(2)]
                continue
            if s.startswith("return"):
                return r.trace + ["ret"]
            if not r.ops(s):
                raise ValueError(f"statement not modelled: {s}")
        return r.trace + ["end"]
    except StopIteration:
        return r.trace + ["cap"]


# ---------------------------------------------------------------------------------------------- structured C

class Brk(Exception):
    pass


class Cnt(Exception):
    pass


class Ret(Exception):
    pass


def parse(lines):
    """[(kind, ...)] of the structured C: ('s', stmt), ('if', [(cond, body)], else_body), ('loop', head, body, tail)
    with head/tail the loop's conditions (None: none), ('do0', body)."""
    code = [l.strip() for l in lines if l.strip() and not l.strip().startswith("/*")]
    pos = [0]

    def block():
        out = []
        while pos[0] < len(code):
            s = code[pos[0]]
            if s == "}" or s.startswith("} else") or s.startswith("} while"):
                return out
            pos[0] += 1
            m = re.fullmatch(r"if \((.*)\) (break;|continue;|return r0;|fl_ = \d+u;|x_ = \d+u;)", s)
            if m:
                out.append(("if", [(m.group(1), [("s", m.group(2))])], []))
                continue
            m = re.fullmatch(r"if \((.*)\) \{", s)
            if m:
                arms, els = [(m.group(1), block())], []
                while True:
                    e = code[pos[0]]
                    pos[0] += 1
                    m2 = re.fullmatch(r"\} else if \((.*)\) \{", e)
                    if m2:
                        arms.append((m2.group(1), block()))
                        continue
                    if e == "} else {":
                        els = block()
                        pos[0] += 1
                    break
                out.append(("if", arms, els))
                continue
            if s in ("for (;;) {", "do {") or s.startswith("while ("):
                head = None if s in ("for (;;) {", "do {") else re.fullmatch(r"while \((.*)\) \{", s).group(1)
                body = block()
                e = code[pos[0]]
                pos[0] += 1
                if s == "do {":
                    c = re.fullmatch(r"\} while \((.*)\);", e).group(1)
                    out.append(("do0", body) if c == "0" else ("loop", None, body, c))
                else:
                    out.append(("loop", head, body, None))
                continue
            out.append(("s", s))
        return out
    tree = block()
    if pos[0] != len(code):
        raise ValueError(f"unparsed: {code[pos[0]]}")
    return tree


def run_struct(lines, seed):
    tree, r = parse(lines), Run(seed)

    def ex(seq):
        for st in seq:
            r.step()
            k = st[0]
            if k == "s":
                s = st[1]
                if s == "break;":
                    raise Brk
                if s == "continue;":
                    raise Cnt
                if s.startswith("return"):
                    raise Ret
                if not r.ops(s):
                    raise ValueError(f"statement not modelled: {s}")
            elif k == "if":
                for c, body in st[1]:
                    if r.expr(c):
                        ex(body)
                        break
                else:
                    ex(st[2])
            elif k == "do0":
                try:
                    ex(st[1])
                except Brk:
                    pass
            else:
                _, head, body, tail = st
                while True:
                    r.step()
                    if head is not None and not r.expr(head):
                        break
                    try:
                        ex(body)
                    except Brk:
                        break
                    except Cnt:
                        pass
                    if tail is not None and not r.expr(tail):
                        break
    try:
        ex(tree)
        return r.trace + ["end"]
    except Ret:
        return r.trace + ["ret"]
    except StopIteration:
        return r.trace + ["cap"]


def differs(goto_lines, struct_lines, seeds=64):
    """The first seed whose runs differ, as (seed, goto trace, structured trace), or None."""
    for seed in range(seeds):
        a, b = run_goto(goto_lines, seed), run_struct(struct_lines, seed)
        if "cap" in (a[-1], b[-1]):
            n = min(len(a), len(b)) - 1
            if a[:n] != b[:n]:
                return seed, a, b
            continue
        if a != b:
            return seed, a, b
    return None


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sh2translate.cfgstruct import structure_cfg
    bad = 0
    for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.txt"))):
        g = symbolize(open(f).read().splitlines())
        o = structure_cfg(g)
        d = differs(g, o) if o is not None else None
        bad += d is not None
        print(os.path.basename(f)[:-4], "keeps gotos" if o is None else f"DIFFERS (seed {d[0]})" if d else "same")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
