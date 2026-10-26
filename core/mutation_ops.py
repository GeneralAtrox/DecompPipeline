"""The mutation check's operators (core/mutation.py): a small C tokenizer and every candidate edit of a source.

No parser: tokens carry their brace depth, paren depth and whether they lie in initializer braces; the operators
work on that (docs/PROOF_GATES.md lists them). An edit is (start, end, text) on the source, so a mutant keeps the
source's formatting and its diff is the change alone.
"""
import re

TOKEN = re.compile(r"""
  (?P<ws>\s+)
 |(?P<comment>//[^\n]*|/\*.*?\*/)
 |(?P<pp>(?<![^\n])[ \t]*\#(?:\\\n|[^\n])*)
 |(?P<str>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
 |(?P<num>0[xX][0-9a-fA-F]+[uUlL]*|(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?[fFlL]?|\d+[eE][+-]?\d+[fFlL]?|\d+[uUlL]*)
 |(?P<id>[A-Za-z_]\w*)
 |(?P<op><<=|>>=|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|[-+*/%&|^~!<>=?:;,.(){}\[\]])
 |(?P<other>.)
""", re.S | re.X)

KEYWORDS = {"if", "else", "while", "for", "do", "switch", "case", "default", "return", "break", "continue", "goto",
            "sizeof", "typedef", "struct", "union", "enum", "static", "const", "volatile", "extern", "register",
            "inline", "__inline", "__forceinline", "unsigned", "signed"}
TYPES = {"void", "char", "short", "int", "long", "float", "double", "unsigned", "signed", "const", "static",
         "volatile", "struct", "union", "enum", "register", "u8", "u16", "u32", "u64", "s8", "s16", "s32", "s64",
         "f32", "f64", "TcArgs", "size_t", "uintptr_t", "intptr_t"}
OPERATORS = ("relop", "arith", "bitwise", "shift", "const", "negate", "delete", "swap_args", "return0")
SWAP = {"relop": {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="},
        "arith": {"+": "-", "-": "+", "+=": "-=", "-=": "+="},
        "bitwise": {"&": "|", "|": "&", "&=": "|=", "|=": "&="},
        "shift": {"<<": ">>", ">>": "<<", "<<=": ">>=", ">>=": "<<="}}
BINARY_ONLY = {"+", "-", "&"}  # also unary: binary only after an operand
ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
STORE_CALL = re.compile(r"(wr(8|16|32|64|f32|f64)\w*|memcpy|memset|memmove|sh4_st\w*|tc_wr\w*)$")
# Survivor classes (class_of): a change the judge cannot see by design, not a gap more vectors would close.
#   arg_slot  a TcArgs slot assignment (a.r[3] = ..., args.s[i] = ..., tc_args4(...)): a call is compared on the
#             registers the callee's signature declares only, so a slot no callee reads is not behaviour
#   stack     a store to the guest stack (wr32(sp + 4, ...), wr32(0x8cffdff4, ...), wr32(fb + 8, ...) with fb set from
#             a 0x8cffXXXX address: stack_names) or a stack pointer variable:
#             writes in the original's own frame (readable_stack.frame_lo() up) are scratch, never compared
#   logic     everything else: a survivor here is a real harness weakness
ARG_SLOT = re.compile(r"^\s*\w+\s*(\.|->)\s*(r|fr|s|rlo)\s*\[[^\]]*\]\s*=(?!=)|\btc_args\w*\s*\(")
STACK_NAMES = ("sp", "frame", "r15", "fp")
STACK_LIT = re.compile(r"0x8cff[0-9a-fA-F]{4}u?$")
_DEF = re.compile(r"\b(\w+)\s*=(?!=)\s*(?:\(\w+\)\s*)?(\w+)\s*(?:[-+]\s*\w+\s*)?;")


def stack_re(extra=()):
    """A store to the stack: wr*(NAME ..., wr*(0x8cffXXXX ..., or an assignment of NAME, for the stack names."""
    stk = "(?:" + "|".join(map(re.escape, sorted(set(STACK_NAMES) | set(extra)))) + ")"
    return re.compile(r"^\s*(?:wr\w*\s*\(\s*(?:\(\w+\)\s*)?(?:" + stk + r"\b|0x8cff[0-9a-fA-F]{4})"
                      r"|(?:\w+\s+)?" + stk + r"\s*[-+]?=(?!=))")


STACK = stack_re()


def stack_names(src):
    """Variables of src that hold a guest stack address: set from a 0x8cffXXXX literal (u32 fb = 0x8cffdfacu;) or
    from another stack name plus or minus an offset (p = fb + 0x10u;), to a fixpoint."""
    names, defs = set(STACK_NAMES), _DEF.findall(src)
    grew = True
    while grew:
        grew = False
        for name, base in defs:
            if name not in names and (base in names or STACK_LIT.match(base)):
                names.add(name)
                grew = True
    return names - set(STACK_NAMES)
REGARG = re.compile(r"^(\w+)\s*(\.|->)\s*(r|fr|s)\s*\[\s*\d+\s*\]\s*=(?!=)")


class Tok:
    __slots__ = ("kind", "text", "start", "end", "depth", "pdepth", "init")

    def __init__(self, kind, text, start, end):
        self.kind, self.text, self.start, self.end = kind, text, start, end
        self.depth = self.pdepth = 0
        self.init = False

    def __repr__(self):
        return f"{self.kind}:{self.text}"


def tokens(src):
    """Significant tokens (no whitespace, comments or preprocessor lines), each with its brace depth, paren depth
    and whether it lies in an initializer's braces (= { ... })."""
    out, depth, pdepth, init_stack = [], 0, 0, []
    for m in TOKEN.finditer(src):
        kind = m.lastgroup
        if kind in ("ws", "comment", "pp"):
            continue
        t = Tok(kind, m.group(), m.start(), m.end())
        if t.text == "{":
            prev = out[-1].text if out else ""
            init_stack.append(prev in ("=", ",", "{") and (bool(init_stack) and init_stack[-1] or prev == "="))
        t.init = any(init_stack)
        t.depth, t.pdepth = depth, pdepth
        if t.text == "{":
            depth += 1
        elif t.text == "}":
            depth = max(depth - 1, 0)
            if init_stack:
                init_stack.pop()
        elif t.text == "(":
            pdepth += 1
        elif t.text == ")":
            pdepth = max(pdepth - 1, 0)
        out.append(t)
    return out


def _operand_end(t):
    return t.kind in ("id", "num", "str") and t.text not in KEYWORDS or t.text in (")", "]")


def _match(toks, i, open_, close):
    """Index of the token closing toks[i] (an open_), or None."""
    n = 0
    for j in range(i, len(toks)):
        if toks[j].text == open_:
            n += 1
        elif toks[j].text == close:
            n -= 1
            if n == 0:
                return j
    return None


def _statement_end(toks, i):
    """Index of the ';' ending the statement that starts at toks[i] (same braces and parens), or None."""
    d, p = toks[i].depth, toks[i].pdepth
    for j in range(i, len(toks)):
        t = toks[j]
        if t.text in ("{", "}") and t.depth <= d and j > i and t.pdepth == p:
            return None
        if t.text == ";" and t.depth == d and t.pdepth == p:
            return j
    return None


def _is_decl(toks, i):
    t = toks[i]
    if t.text in TYPES:
        return True
    return t.kind == "id" and i + 1 < len(toks) and (toks[i + 1].kind == "id" or
                                                       toks[i + 1].text == "*" and i + 2 < len(toks)
                                                       and toks[i + 2].kind == "id" and i + 3 < len(toks)
                                                       and toks[i + 3].text in ("=", ";", ","))


def _stmt_start(toks, i):
    """toks[i] starts a statement inside a function body (not a block's brace or an empty statement)."""
    if toks[i].depth < 1 or toks[i].pdepth or toks[i].init or toks[i].text in ("{", "}", ";"):
        return False
    prev = toks[i - 1].text if i else ""
    if prev in (";", "{", "}", "else"):
        return True
    if prev == ")":  # the body of if (...) / while (...) / for (...) without braces
        j = _match_back(toks, i - 1)
        return j is not None and j > 0 and toks[j - 1].text in ("if", "while", "for")
    return False


def _match_back(toks, i):
    n = 0
    for j in range(i, -1, -1):
        if toks[j].text == ")":
            n += 1
        elif toks[j].text == "(":
            n -= 1
            if n == 0:
                return j
    return None


def _split_args(toks, lo, hi):
    """Top-level argument token ranges [(a, b)] between toks[lo] '(' and toks[hi] ')'."""
    args, start, n = [], lo + 1, 0
    for j in range(lo + 1, hi):
        t = toks[j].text
        if t in "([{":
            n += 1
        elif t in ")]}":
            n -= 1
        elif t == "," and n == 0:
            args.append((start, j - 1))
            start = j + 1
    if start <= hi - 1:
        args.append((start, hi - 1))
    return args


def _const_text(text, delta):
    m = re.fullmatch(r"(0[xX])?([0-9a-fA-F]+)([uUlL]*)", text)
    if not m or (not m.group(1) and not m.group(2).isdigit()):
        return None
    if not m.group(1) and len(m.group(2)) > 1 and m.group(2).startswith("0"):
        return None  # octal: leave alone
    v = int(m.group(2), 16 if m.group(1) else 10)
    nv = v + delta if v + delta >= 0 else v + 1
    if nv > 0xFFFFFFFF:
        nv = v - 1
    body = (f"{nv:x}" if m.group(2) == m.group(2).lower() else f"{nv:X}") if m.group(1) else str(nv)
    return (m.group(1) or "") + body + m.group(3)


def candidates(src):
    """Every candidate edit, in source order: [{"op", "edits": [(start, end, text)], "line", "what", "cls"}] (cls:
    the class a survivor of it gets, class_of)."""
    toks = tokens(src)
    out = []
    stack = stack_re(stack_names(src))

    def add(op, edits, at, what):
        out.append({"op": op, "edits": edits, "line": src.count("\n", 0, at) + 1, "what": what,
                    "cls": class_of(src, toks, i, stack)})

    for i, t in enumerate(toks):
        if t.depth < 1 or t.init:
            continue
        prev = toks[i - 1] if i else None
        if t.kind == "op":
            for op, table in SWAP.items():
                if t.text in table:
                    if t.text in BINARY_ONLY and not (prev and _operand_end(prev)):
                        continue
                    add(op, [(t.start, t.end, table[t.text])], t.start, f"{t.text} -> {table[t.text]}")
        elif t.kind == "num" and re.fullmatch(r"(0[xX][0-9a-fA-F]+|\d+)[uUlL]*", t.text):
            if prev and prev.text == "[" and _is_decl_bracket(toks, i):
                continue
            if prev and prev.text in ("case",):
                continue
            delta = 1 if (t.start * 7 + len(t.text)) % 2 == 0 else -1  # deterministic direction
            if re.fullmatch(r"0+[uUlL]*|0[xX]0+[uUlL]*", t.text):
                delta = 1
            new = _const_text(t.text, delta)
            if new and new != t.text:
                add("const", [(t.start, t.end, new)], t.start, f"{t.text} -> {new}")
        elif t.text in ("if", "while") and i + 1 < len(toks) and toks[i + 1].text == "(":
            j = _match(toks, i + 1, "(", ")")
            if j is not None and j > i + 2:
                a, b = toks[i + 2].start, toks[j - 1].end
                add("negate", [(a, a, "!("), (b, b, ")")], t.start, f"{t.text} (c) -> {t.text} (!(c))")
        elif t.text == "return" and i + 1 < len(toks) and toks[i + 1].text != ";":
            j = _statement_end(toks, i)
            if j is not None:
                expr = src[toks[i + 1].start:toks[j - 1].end]
                if not re.fullmatch(r"\(?\s*(\(\w+\)\s*)?0+[uU]?\s*\)?|0\.0*f?", expr.strip()):
                    add("return0", [(toks[i + 1].start, toks[j - 1].end, "0")], t.start, f"return {expr} -> return 0")
        if t.kind == "id" and t.text not in KEYWORDS and i + 1 < len(toks) and toks[i + 1].text == "(" \
                and not (prev and (prev.kind == "id" and prev.text not in ("return", "else") or prev.text == "*")):
            j = _match(toks, i + 1, "(", ")")
            if j is not None:
                args = _split_args(toks, i + 1, j)
                for (a0, a1), (b0, b1) in zip(args, args[1:]):
                    x, y = src[toks[a0].start:toks[a1].end], src[toks[b0].start:toks[b1].end]
                    if x.strip() != y.strip() and x.strip().startswith("&") == y.strip().startswith("&"):
                        add("swap_args", [(toks[a0].start, toks[a1].end, y), (toks[b0].start, toks[b1].end, x)],
                            t.start, f"{t.text}(.. {x}, {y} ..) -> {t.text}(.. {y}, {x} ..)")
                        break  # one swap per call
        if _stmt_start(toks, i) and t.text not in KEYWORDS and not _is_decl(toks, i):
            j = _statement_end(toks, i)
            if j is None:
                continue
            top = [k for k in range(i, j) if toks[k].pdepth == t.pdepth and toks[k].text in ASSIGN]
            is_store = t.kind == "id" and i + 1 < len(toks) and toks[i + 1].text == "(" and STORE_CALL.match(t.text)
            if top or is_store:
                text = src[t.start:toks[j].end]
                add("delete", [(t.start, toks[j].end, "{ }")], t.start, f"deleted: {_one_line(text)}")
            # adjacent TcArgs register assignments: swap their values (the raw C's call arguments)
            nxt = j + 1
            if top and nxt < len(toks) and _stmt_start(toks, nxt):
                k = _statement_end(toks, nxt)
                s1, s2 = src[t.start:toks[j].end], (src[toks[nxt].start:toks[k].end] if k is not None else "")
                m1, m2 = REGARG.match(s1), REGARG.match(s2)
                if m1 and m2 and m1.group(1) == m2.group(1) and m1.group(3) == m2.group(3):
                    v1, v2 = s1[m1.end():-1].strip(), s2[m2.end():-1].strip()
                    if v1 != v2:
                        e1 = t.start + m1.end()
                        e2 = toks[nxt].start + m2.end()
                        add("swap_args", [(e1, toks[j].end - 1, " " + v2), (e2, toks[k].end - 1, " " + v1)],
                            t.start, f"swapped values of {_one_line(s1)} and {_one_line(s2)}")
    return out


def _text_class(text, stack=STACK):
    return "arg_slot" if ARG_SLOT.search(text) else "stack" if stack.match(text) else "logic"


def _stmts_class(src, toks, b, stack=STACK):
    """The class of the statement (or braced block of statements) starting at toks[b]: arg_slot / stack when every
    statement in it is one, else logic."""
    if b >= len(toks):
        return "logic"
    if toks[b].text != "{":
        j = _statement_end(toks, b)
        return _text_class(src[toks[b].start:toks[j].end], stack) if j is not None else "logic"
    e = _match(toks, b, "{", "}")
    if e is None or e == b + 1:
        return "logic"
    kinds, k = set(), b + 1
    while k < e:
        j = _statement_end(toks, k)
        if j is None or j >= e or toks[k].text in KEYWORDS:
            return "logic"
        kinds.add(_text_class(src[toks[k].start:toks[j].end], stack))
        k = j + 1
    return kinds.pop() if len(kinds) == 1 else "logic"


def class_of(src, toks, i, stack=None):
    """The survivor class of an edit at toks[i] (see ARG_SLOT, stack_re): by the statement holding it; an edit in a
    for (...) header takes its loop body's class (for (i = 0; i < 8; i++) args.s[i] = ...: arg_slot). stack: the
    stack-store pattern of src (default stack_re(stack_names(src)))."""
    stack = stack or stack_re(stack_names(src))
    k = i
    while k > 0 and not (toks[k - 1].text in (";", "{", "}") and toks[k - 1].pdepth == 0):
        k -= 1
    while toks[k].text in ("if", "while", "for", "else", "do", "switch"):
        if toks[k].text in ("else", "do"):
            k += 1
            continue
        close = _match(toks, k + 1, "(", ")") if k + 1 < len(toks) and toks[k + 1].text == "(" else None
        if close is None:
            return "logic"
        if i <= close:
            return _stmts_class(src, toks, close + 1, stack) if toks[k].text == "for" else "logic"
        k = close + 1
        if k >= len(toks):
            return "logic"
    return _stmts_class(src, toks, k, stack)


def _is_decl_bracket(toks, i):
    """A number in [ ] of a declaration (an array size): changing it is no behaviour change worth judging."""
    j = i - 2
    while j >= 0 and toks[j].kind == "id":
        j -= 1
    return j + 1 < i - 1 and _is_decl(toks, j + 1)


def _one_line(s, n=90):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 3] + "..."


def apply(src, edits):
    for a, b, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        src = src[:a] + text + src[b:]
    return src
