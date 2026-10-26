"""Where the game's readable files name constants: the callee argument or the member a header name is written at
(readable/src). A literal at the same place takes the name of that value in use there (or in the family those names
share: XMTRX_STACK_ for XMTRX_STACK_SCENE). Only when exactly one name fits, and never from the function's own file:
a function's readable file is the agent's answer for it, and before it exists only the other files can say."""
from __future__ import annotations

import re

from .. import readable
from ..draft import close_paren, strip_comments

NAME = r"[A-Z_][A-Z0-9_]*"
CAST = r"(?:\(\s*(?:const\s+)?[A-Za-z_]\w*\s*\**\s*\)\s*)*"  # casts before a name: (u32)NAME
KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "defined"}
SRC = None  # the readable files to read when not the project's (tools/readable_tx.py: the game's, not its mirror's)
_CACHE = {}


def _args(code, i):
    """The top-level arguments of the call whose '(' is at code[i]."""
    end = close_paren(code, i)
    out, depth, start = [], 0, i + 1
    for k in range(i + 1, end):
        c = code[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(code[start:k])
            start = k + 1
    out.append(code[start:end])
    return [a.strip() for a in out] if any(a.strip() for a in out) else []


def sites(text, names):
    """[(site, name)]: ("arg", callee, k) and ("member", m) places where text writes a header name (in names)."""
    code = strip_comments(text)
    out = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", code):
        if m.group(1) in KEYWORDS or m.group(1) in names:
            continue
        for k, a in enumerate(_args(code, m.end() - 1)):
            got = re.fullmatch(rf"{CAST}\(?({NAME})\)?", a)
            if got and got.group(1) in names:
                out.append((("arg", m.group(1), k), got.group(1)))
    op = r"(?:==|!=|(?<![=!<>+\-*/&|^])=(?!=))"
    for m in re.finditer(rf"(?:\.|->)\s*(\w+)\s*(?:\[[^\]]*\]\s*)?{op}\s*{CAST}({NAME})\b", code):
        if m.group(2) in names:
            out.append((("member", m.group(1)), m.group(2)))
    for m in re.finditer(rf"\b({NAME})\s*(?:==|!=)\s*[\w.\->\[\]()]*?(?:\.|->)\s*(\w+)\b(?!\s*[.\[(]|\s*->)", code):
        if m.group(1) in names:
            out.append((("member", m.group(2)), m.group(1)))
    return out


class Usage:
    def __init__(self, named_values, src=None):
        self.values = named_values  # {value: [names]}
        self.value_of = {n: v for v, ns in named_values.items() for n in ns}
        self.at = {}  # site: {name: {addr}}
        src = src or readable.SRC
        for p in sorted(src.glob("*.c")) if src.exists() else []:
            for site, n in sites(p.read_text(encoding="utf-8", errors="replace"), self.value_of):
                self.at.setdefault(site, {}).setdefault(n, set()).add(p.stem)

    def name(self, site, v, addr):
        """The one header name of value v that the other files' use at site gives, else None."""
        seen = [n for n, where in self.at.get(site, {}).items() if where - {addr}]
        fams = {n.rsplit("_", 1)[0] + "_" if "_" in n.strip("_") else n for n in seen}  # ONE: a family of its own
        if site[0] == "member" and len(fams) != 1:  # a member name many structs share: one family only
            return None
        exact = sorted({n for n in seen if self.value_of.get(n) == v})
        if len(exact) == 1:
            return exact[0]
        if exact or len(fams) != 1:
            return None
        fam = sorted({n for n in self.values.get(v, ()) if n.startswith(next(iter(fams)))})
        return fam[0] if len(fam) == 1 else None


def usage(named_values, src=None):
    """The Usage of the readable files now (rebuilt when a file is added or changes)."""
    src = src or SRC or readable.SRC
    files = sorted(src.glob("*.c")) if src.exists() else []
    key = (str(src), len(files), max((p.stat().st_mtime_ns for p in files), default=0), len(named_values))
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = Usage(named_values, src)
    return _CACHE[key]
