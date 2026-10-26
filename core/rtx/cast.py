"""The C the translators emit, as a tree: a tokenizer, a parser for that subset of C, and a printer.

The subset is what DP's raw stage (and so every translator) writes: declarations of scalars and TcArgs blocks,
assignments (`x = e;`, `x op= e;`), calls, if/else, while, do/while, for, switch with case labels, break, continue,
return, goto and labels; expressions with casts, unary and binary operators, ?:, calls, indexing and members.
Anything else is a ParseError: the function is left to the agents.
"""
from __future__ import annotations

import re

TYPES = {"u8", "u16", "u32", "u64", "s8", "s16", "s32", "s64", "f32", "f64", "int", "unsigned", "char", "short",
         "long", "float", "double", "void", "signed", "uintptr_t", "intptr_t", "size_t", "TcArgs", "const",
         "volatile", "struct", "union", "enum", "SqQueues"}
KEYWORDS = {"if", "else", "while", "do", "for", "switch", "case", "default", "break", "continue", "return", "goto",
            "static", "sizeof"}
TOKEN = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<num>0[xX][0-9a-fA-F]+[uUlL]*|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fFuUlL]*)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<op><<=|>>=|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|[-+*/%&|^~!<>=?:;,.(){}\[\]])
  | (?P<pp>\#[^\n]*)
""", re.S | re.X)


class ParseError(Exception):
    pass


def tokens(text):
    out, i = [], 0
    while i < len(text):
        m = TOKEN.match(text, i)
        if not m:
            raise ParseError(f"unexpected {text[i:i + 20]!r}")
        i = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group()))
    return out


# ---- the tree -------------------------------------------------------------------------------------------------

class Node:
    __slots__ = ()

    def __eq__(self, o):
        return type(self) is type(o) and all(getattr(self, s) == getattr(o, s) for s in self.__slots__)

    def __hash__(self):
        return hash((type(self).__name__,) + tuple(_h(getattr(self, s)) for s in self.__slots__))

    def __repr__(self):
        return f"{type(self).__name__}({', '.join(repr(getattr(self, s)) for s in self.__slots__)})"


def _h(v):
    return tuple(_h(x) for x in v) if isinstance(v, list) else v


class Num(Node):
    __slots__ = ("v", "text")

    def __init__(self, v, text=None):
        self.v, self.text = v, text if text is not None else num_text(v)


class Var(Node):
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


class Call(Node):
    __slots__ = ("fn", "args")

    def __init__(self, fn, args):
        self.fn, self.args = fn, list(args)


class Unary(Node):
    __slots__ = ("op", "e")

    def __init__(self, op, e):
        self.op, self.e = op, e


class Bin(Node):
    __slots__ = ("op", "a", "b")

    def __init__(self, op, a, b):
        self.op, self.a, self.b = op, a, b


class Cast(Node):
    __slots__ = ("type", "e")

    def __init__(self, type_, e):
        self.type, self.e = type_, e


class Cond(Node):
    __slots__ = ("c", "a", "b")

    def __init__(self, c, a, b):
        self.c, self.a, self.b = c, a, b


class Index(Node):
    __slots__ = ("a", "i")

    def __init__(self, a, i):
        self.a, self.i = a, i


class Member(Node):
    __slots__ = ("e", "name", "arrow")

    def __init__(self, e, name, arrow=False):
        self.e, self.name, self.arrow = e, name, arrow


class Raw(Node):
    """Text kept as it is (a brace initializer)."""
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


class Assign(Node):
    __slots__ = ("lhs", "op", "rhs")

    def __init__(self, lhs, op, rhs):
        self.lhs, self.op, self.rhs = lhs, op, rhs


class ExprS(Node):
    __slots__ = ("e",)

    def __init__(self, e):
        self.e = e


class Decl(Node):
    __slots__ = ("type", "items")

    def __init__(self, type_, items):
        self.type, self.items = type_, list(items)   # [(name, init or None, dims text)]


class Block(Node):
    __slots__ = ("body",)

    def __init__(self, body):
        self.body = list(body)


class If(Node):
    __slots__ = ("c", "t", "e")

    def __init__(self, c, t, e=None):
        self.c, self.t, self.e = c, t, e


class While(Node):
    __slots__ = ("c", "body")

    def __init__(self, c, body):
        self.c, self.body = c, body


class DoWhile(Node):
    __slots__ = ("body", "c")

    def __init__(self, body, c):
        self.body, self.c = body, c


class For(Node):
    __slots__ = ("init", "c", "step", "body")

    def __init__(self, init, c, step, body):
        self.init, self.c, self.step, self.body = init, c, step, body


class Switch(Node):
    __slots__ = ("e", "body")

    def __init__(self, e, body):
        self.e, self.body = e, body


class Case(Node):
    __slots__ = ("v",)          # an expression, or None for default

    def __init__(self, v):
        self.v = v


class Break(Node):
    __slots__ = ()


class Continue(Node):
    __slots__ = ()


class Return(Node):
    __slots__ = ("e",)

    def __init__(self, e=None):
        self.e = e


class Goto(Node):
    __slots__ = ("label",)

    def __init__(self, label):
        self.label = label


class Label(Node):
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


def num_text(v):
    if isinstance(v, float):
        r = repr(v)
        return (r if ("." in r or "e" in r or "inf" in r or "nan" in r) else r + ".0") + "f"
    if v < 0:
        return str(v)
    return f"0x{v:x}u" if v >= 10 else f"{v}u" if v else "0"


def num_value(text):
    t = text.rstrip("uUlL")
    if t[:2].lower() == "0x":
        return int(t, 16)
    if re.search(r"[.eEfF]", t) and not t[:2].lower() == "0x":
        return float(t.rstrip("fF"))
    return int(t, 10)


# ---- the parser -----------------------------------------------------------------------------------------------

BINARY = [("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="), ("<", ">", "<=", ">="), ("<<", ">>"), ("+", "-"),
          ("*", "/", "%")]
PREC = {op: i + 1 for i, ops in enumerate(BINARY) for op in ops}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}


class Parser:
    def __init__(self, text, types=()):
        self.t = tokens(text)
        self.i = 0
        self.types = TYPES | set(types)

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", "")

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def accept(self, v):
        if self.peek()[1] == v:
            self.i += 1
            return True
        return False

    def expect(self, v):
        if not self.accept(v):
            raise ParseError(f"expected {v!r}, got {self.peek()[1]!r} at token {self.i}")

    def is_type_start(self, k=0):
        kind, v = self.peek(k)
        return kind == "id" and v in self.types

    def type_name(self):
        parts = []
        while self.is_type_start():
            v = self.next()[1]
            parts.append(v)
            if v in ("struct", "union", "enum"):
                parts.append(self.next()[1])
        while self.peek()[1] == "*":
            self.next()
            parts.append("*")
        if not parts:
            raise ParseError(f"expected a type at {self.peek()[1]!r}")
        return " ".join(parts).replace(" *", " *")

    # expressions
    def expr(self):
        return self.cond()

    def cond(self):
        c = self.binary(1)
        if self.accept("?"):
            a = self.expr()
            self.expect(":")
            b = self.cond()
            return Cond(c, a, b)
        return c

    def binary(self, level):
        if level > len(BINARY):
            return self.unary()
        a = self.binary(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in BINARY[level - 1]:
            op = self.next()[1]
            b = self.binary(level + 1)
            a = Bin(op, a, b)
        return a

    def unary(self):
        kind, v = self.peek()
        if v in ("-", "~", "!", "*", "&", "+"):
            self.next()
            e = self.unary()
            if v == "-" and isinstance(e, Num) and isinstance(e.v, float):
                return Num(-e.v, "-" + e.text)
            return e if v == "+" else Unary(v, e)
        if v == "(" and self.is_type_start(1):
            self.next()
            ty = self.type_name()
            self.expect(")")
            return Cast(ty, self.unary())
        if v == "sizeof":
            raise ParseError("sizeof")
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            v = self.peek()[1]
            if v == "(":
                self.next()
                args = []
                if not self.accept(")"):
                    while True:
                        args.append(self.expr())
                        if self.accept(")"):
                            break
                        self.expect(",")
                e = Call(e.name if isinstance(e, Var) else e, args)
            elif v == "[":
                self.next()
                i = self.expr()
                self.expect("]")
                e = Index(e, i)
            elif v in (".", "->"):
                self.next()
                e = Member(e, self.next()[1], v == "->")
            elif v in ("++", "--"):
                raise ParseError("++/--")
            else:
                return e

    def primary(self):
        kind, v = self.next()
        if kind == "num":
            return Num(num_value(v), v)
        if kind == "id" and v not in KEYWORDS:
            return Var(v)
        if v == "(":
            e = self.expr()
            self.expect(")")
            return e
        raise ParseError(f"unexpected {v!r} in an expression")

    def brace_init(self):
        start, depth = self.i, 0
        while True:
            v = self.next()[1]
            depth += {"{": 1, "}": -1}.get(v, 0)
            if depth == 0:
                break
            if v == "":
                raise ParseError("unterminated initializer")
        return Raw(" ".join(x for _, x in self.t[start:self.i]).replace("{ ", "{").replace(" }", "}")
                   .replace(" ,", ","))

    # statements
    def statement(self):
        kind, v = self.peek()
        if v == "{":
            return self.block()
        if v == ";":
            self.next()
            return Block([])
        if v == "if":
            self.next()
            self.expect("(")
            c = self.expr()
            self.expect(")")
            t = self.body()
            e = self.statement() if self.accept("else") else None
            if e is not None and not isinstance(e, (Block, If)):
                e = Block([e])
            if isinstance(e, Block) and len(e.body) == 1 and isinstance(e.body[0], If):
                e = e.body[0]  # `else { if ... }` is `else if ...`
            return If(c, t, e)
        if v == "while":
            self.next()
            self.expect("(")
            c = self.expr()
            self.expect(")")
            return While(c, self.body())
        if v == "do":
            self.next()
            body = self.body()
            self.expect("while")
            self.expect("(")
            c = self.expr()
            self.expect(")")
            self.expect(";")
            return DoWhile(body, c)
        if v == "for":
            self.next()
            self.expect("(")
            init = None if self.peek()[1] == ";" else self.simple()
            self.expect(";")
            c = None if self.peek()[1] == ";" else self.expr()
            self.expect(";")
            step = None if self.peek()[1] == ")" else self.simple()
            self.expect(")")
            return For(init, c, step, self.body())
        if v == "switch":
            self.next()
            self.expect("(")
            e = self.expr()
            self.expect(")")
            return Switch(e, self.statement())
        if v == "case":
            self.next()
            e = self.expr()
            self.expect(":")
            return Case(e)
        if v == "default":
            self.next()
            self.expect(":")
            return Case(None)
        if v == "break":
            self.next()
            self.expect(";")
            return Break()
        if v == "continue":
            self.next()
            self.expect(";")
            return Continue()
        if v == "return":
            self.next()
            e = None if self.peek()[1] == ";" else self.expr()
            self.expect(";")
            return Return(e)
        if v == "goto":
            self.next()
            lab = self.next()[1]
            self.expect(";")
            return Goto(lab)
        if kind == "id" and self.peek(1)[1] == ":" and v not in KEYWORDS:
            self.next()
            self.next()
            return Label(v)
        if self.is_type_start():
            return self.decl()
        s = self.simple()
        self.expect(";")
        return s

    def body(self):
        s = self.statement()
        return s if isinstance(s, Block) else Block([s])

    def decl(self):
        ty = self.type_name()
        items = []
        while True:
            stars = ""
            while self.accept("*"):
                stars += "*"
            name = self.next()[1]
            dims = ""
            while self.accept("["):
                d = self.expr()
                self.expect("]")
                dims += f"[{pr(d)}]"
            init = None
            if self.accept("="):
                init = self.brace_init() if self.peek()[1] == "{" else self.expr()
            items.append((name, init, dims))
            if stars:
                raise ParseError("a pointer declarator")
            if self.accept(";"):
                return Decl(ty, items)
            self.expect(",")

    def simple(self):
        lhs = self.expr()
        if self.peek()[1] in ASSIGN_OPS:
            op = self.next()[1]
            return Assign(lhs, op, self.expr())
        return ExprS(lhs)

    def block(self):
        self.expect("{")
        body = []
        while not self.accept("}"):
            if self.peek()[0] == "eof":
                raise ParseError("unterminated block")
            body.append(self.statement())
        return Block(body)


def parse_body(text, types=()):
    """The statements of a function body's text (with its braces)."""
    p = Parser(text, types)
    b = p.block()
    if p.peek()[0] != "eof":
        raise ParseError("text after the body")
    return b


def parse_expr(text, types=()):
    p = Parser(text, types)
    e = p.expr()
    if p.peek()[0] != "eof":
        raise ParseError("text after the expression")
    return e


# ---- the printer ----------------------------------------------------------------------------------------------

UNARY_P, POSTFIX_P, COND_P = 11, 12, 0


def prec(e):
    if isinstance(e, Bin):
        return PREC[e.op]
    if isinstance(e, Cond):
        return COND_P
    if isinstance(e, (Unary, Cast)):
        return UNARY_P
    if isinstance(e, Num) and (e.text.startswith("-")):
        return UNARY_P
    return POSTFIX_P


def pr(e, need=0):
    s = _pr(e)
    return f"({s})" if prec(e) < need else s


def _pr(e):
    if isinstance(e, Num):
        return e.text
    if isinstance(e, Var):
        return e.name
    if isinstance(e, Raw):
        return e.text
    if isinstance(e, Call):
        fn = e.fn if isinstance(e.fn, str) else pr(e.fn, POSTFIX_P)
        return f"{fn}({', '.join(pr(a, 1) for a in e.args)})"
    if isinstance(e, Unary):
        inner = pr(e.e, UNARY_P)
        if e.op in ("-", "&", "*") and inner.startswith(e.op):
            inner = f"({inner})"
        return e.op + inner
    if isinstance(e, Cast):
        return f"({e.type}){pr(e.e, UNARY_P)}"
    if isinstance(e, Bin):
        p = PREC[e.op]
        # same-level left associativity; parenthesise the mixes compilers warn about (C4554: shifts, &|^ with others)
        la = pr(e.a, p)
        rb = pr(e.b, p + 1)
        if e.op in ("<<", ">>", "&", "|", "^") or e.op in ("&&", "||"):
            if isinstance(e.a, Bin) and e.a.op != e.op and PREC[e.a.op] != p:
                la = f"({_pr(e.a)})"
            if isinstance(e.b, Bin) and e.b.op != e.op:
                rb = f"({_pr(e.b)})"
        if e.op in ("+", "-") and isinstance(e.a, Bin) and e.a.op in ("<<", ">>"):
            la = f"({_pr(e.a)})"
        return f"{la} {e.op} {rb}"
    if isinstance(e, Cond):
        return f"{pr(e.c, 1)} ? {pr(e.a, 1)} : {pr(e.b, COND_P)}"
    if isinstance(e, Index):
        return f"{pr(e.a, POSTFIX_P)}[{pr(e.i)}]"
    if isinstance(e, Member):
        return f"{pr(e.e, POSTFIX_P)}{'->' if e.arrow else '.'}{e.name}"
    raise TypeError(e)


def simple_text(s):
    if isinstance(s, Assign):
        return f"{pr(s.lhs, UNARY_P)} {s.op} {pr(s.rhs)}"
    if isinstance(s, ExprS):
        return pr(s.e)
    raise TypeError(s)


def stmt_lines(s, ind="    "):
    """The statement's lines, indented by ind."""
    if isinstance(s, (Assign, ExprS)):
        return [f"{ind}{simple_text(s)};"]
    if isinstance(s, Decl):
        items = ", ".join(n + d + (f" = {pr(i, 1)}" if i is not None else "") for n, i, d in s.items)
        return [f"{ind}{s.type} {items};"]
    if isinstance(s, Block):
        if not s.body:
            return [f"{ind};"]
        return [f"{ind}{{"] + body_lines(s.body, ind + "    ") + [f"{ind}}}"]
    if isinstance(s, If):
        out = [f"{ind}if ({pr(s.c)}) {{"] + body_lines(_stmts(s.t), ind + "    ")
        e = s.e
        while e is not None:
            if isinstance(e, If):
                out += [f"{ind}}} else if ({pr(e.c)}) {{"] + body_lines(_stmts(e.t), ind + "    ")
                e = e.e
            elif isinstance(e, Block) and len(e.body) == 1 and isinstance(e.body[0], If):
                e = e.body[0]
            else:
                out += [f"{ind}}} else {{"] + body_lines(_stmts(e), ind + "    ")
                break
        return out + [f"{ind}}}"]
    if isinstance(s, While):
        return [f"{ind}while ({pr(s.c)}) {{"] + body_lines(_stmts(s.body), ind + "    ") + [f"{ind}}}"]
    if isinstance(s, DoWhile):
        return [f"{ind}do {{"] + body_lines(_stmts(s.body), ind + "    ") + [f"{ind}}} while ({pr(s.c)});"]
    if isinstance(s, For):
        head = (f"{simple_text(s.init) if s.init else ''}; {pr(s.c) if s.c is not None else ''}; "
                f"{simple_text(s.step) if s.step else ''}")
        head = "for (;;)" if head.strip() == ";  ;" or head == "; ; " else f"for ({head.rstrip()})"
        return [f"{ind}{head} {{"] + body_lines(_stmts(s.body), ind + "    ") + [f"{ind}}}"]
    if isinstance(s, Switch):
        return [f"{ind}switch ({pr(s.e)}) {{"] + body_lines(_stmts(s.body), ind + "    ", switch=True) + [f"{ind}}}"]
    if isinstance(s, Case):
        return [f"{ind}case {pr(s.v)}:" if s.v is not None else f"{ind}default:"]
    if isinstance(s, Break):
        return [f"{ind}break;"]
    if isinstance(s, Continue):
        return [f"{ind}continue;"]
    if isinstance(s, Return):
        return [f"{ind}return{' ' + pr(s.e) if s.e is not None else ''};"]
    if isinstance(s, Goto):
        return [f"{ind}goto {s.label};"]
    if isinstance(s, Label):
        return [f"{ind[:-4] if len(ind) >= 4 else ''}{s.name}:;"]
    raise TypeError(s)


def _stmts(s):
    return s.body if isinstance(s, Block) else [s]


def body_lines(stmts, ind, switch=False):
    out = []
    for s in stmts:
        if switch and isinstance(s, Case):
            out += stmt_lines(s, ind[:-4] + "    ")
        else:
            out += stmt_lines(s, ind + ("    " if switch else ""))
    return out
