"""index.txt of a lean readable sandbox: the safety net for a type or constant types.h does not show (moved out of
core/readable_lean in batch 25; batch 26: the macros a group shown in part leaves out). Nothing here is
project-specific."""
import re

from . import lean_uses, readable_headers

INDEX_MIN = 0x10  # a value this small (2, 0x8) is in too many unrelated constants to point anywhere


def _plain(names):
    return {n.split(":", 1)[1] if ":" in n else n for n in names}


def _int_literals(text):
    from .readable_lean import int_literals
    return int_literals(text)


def _macro_values(it):
    """{name: value text} of a #define group."""
    out = {}
    for m in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)(\([^)]*\))?(.*)$", it.text, re.M):
        out[m.group(1)] = ((m.group(2) or "") + " " + readable_headers.split_comments(m.group(3))[0]).strip()
    return out


def _enum_values(it):
    body = re.search(r"\{(.*)\}", readable_headers.split_comments(it.text)[0], re.S)
    out = {}
    for part in (body.group(1).split(",") if body else ()):
        m = re.match(r"\s*([A-Za-z_]\w*)\s*(?:=\s*(.+?))?\s*$", part, re.S)
        if m:
            out[m.group(1)] = (m.group(2) or "").strip()
    return out


def _shown(hs, name, text):
    """A constant's value text for index.txt; a counted enum value or an expression with its value."""
    got = lean_uses.values(hs).get(name)
    if got is None or re.fullmatch(r"\(?\s*-?(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\s*\)?", text):
        return text
    value = lean_uses.value_key(got[0])
    return f"{text} (= 0x{value:x})" if text else f"0x{value:x}"


def _size_note(it):
    m = re.search(r"\}\s*\w*\s*;\s*/\*\s*(0x[0-9a-fA-F]+|\d+)\s*bytes?", it.text)
    return f", {m.group(1)} bytes" if m else ""


def index_txt(hs, chosen, evidence=None, parts=None):
    """The safety net: the types not in types.h (name, kind, header), and the constants not in types.h whose value
    raw.c writes as a literal (evidence; None: every constant), with their values. parts: {item: the macro names
    shown} of the #define groups types.h shows in part (batch 26: their other macros count as not shown)."""
    types, consts = [], []
    evaluated = lean_uses.named(hs, evidence, INDEX_MIN) if evidence is not None else {}
    parts = parts or {}
    for it in hs.items:
        if it in chosen and it not in parts or it.kind in ("forward", "other"):
            continue
        if it.kind == "define":
            vals = {n: x for n, x in _macro_values(it).items() if n not in parts.get(it, ())}
        elif it.kind == "enum" or re.match(r"\s*(/\*.*?\*/\s*)*typedef\s+enum\b", it.text, re.S):
            vals = _enum_values(it)
        else:
            vals = {}
        for n, v in vals.items():
            if evidence is None or n in evaluated or {x for x in _int_literals(v) & evidence
                                                      if INDEX_MIN <= x <= 0xFFFFFFFF - INDEX_MIN}:
                consts.append(f"{n} = {_shown(hs, n, v)}  ({it.header})")
        if it.kind != "define":
            kind = re.search(r"\b(struct|union|enum)\b", readable_headers.split_comments(it.text)[0])
            kind = kind.group(1) if kind else "typedef"
            for n in sorted(_plain(it.names) - set(vals)):
                types.append(f"{n}  {kind}{_size_note(it)}  ({it.header})")
    head = ["# Not in types.h, but usable by name (the check builds with the project's full headers).",
            "# Constants whose value raw.c writes as a literal:"]
    mid = ["", "# Other types:"]
    return "\n".join(head + sorted(set(consts), key=str.lower) + mid + sorted(set(types), key=str.lower)) + "\n"
