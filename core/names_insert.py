"""Add entries to a readable/names file without rewriting it (batch 26).

Designers write the names files by hand, in several layouts (one entry per line, indent 1 or 2, CRLF or LF). A
tool that adds entries (core/rodata_strings) inserts them as text into the "functions" or "globals" object, one
compact line each, in the file's own indent and line ending, so a designer's diff shows only the new lines. The result
is checked: it parses, and it equals the old data plus the new entries.
"""
import json
import re


def _close(text, open_at):
    """Index of the '}' closing the object whose '{' is at text[open_at] (strings skipped)."""
    depth, i, in_str = 0, open_at, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces")


def insert(text, kind, entries):
    """text with entries {key: value} added at the end of the top-level object `kind` ("globals"); the object is
    created when missing. A key already there raises ValueError (never an override)."""
    old = json.loads(text) if text.strip() else {}
    if any(k in old.get(kind, {}) for k in entries):
        raise ValueError(f"{kind}: an entry already exists: {sorted(set(entries) & set(old[kind]))}")
    if not entries:
        return text
    nl = "\r\n" if "\r\n" in text else "\n"
    m = re.match(r"\{\s*?\r?\n([ \t]+)", text)
    unit = m.group(1) if m else "  "
    lines = [f'{json.dumps(k)}: {json.dumps(v)}' for k, v in entries.items()]
    if not text.strip():
        text = "{}"
    km = re.search(r'"' + re.escape(kind) + r'"\s*:\s*\{', text)
    if km and _depth(text, km.start()) == 1:
        o = km.end() - 1
        c = _close(text, o)
        body = text[o + 1:c]
        prev = re.findall(r"\n([ \t]*)\"", body)
        ind = prev[-1] if prev else unit * 2
        if body.strip():
            end = o + 1 + len(body.rstrip())
            new = text[:end] + "," + "".join(f"{nl}{ind}{ln}" + ("," if i < len(lines) - 1 else "")
                                              for i, ln in enumerate(lines)) + text[end:]
        else:
            new = text[:o + 1] + "".join(f"{nl}{ind}{ln}" + ("," if i < len(lines) - 1 else "")
                                         for i, ln in enumerate(lines)) + nl + unit + text[c:]
    else:
        top = text.rstrip()
        assert top.endswith("}")
        inner = top[:-1].rstrip()
        sep = "" if inner.endswith("{") else ","
        obj = (f'{unit}{json.dumps(kind)}: {{' + "".join(f"{nl}{unit * 2}{ln}" + ("," if i < len(lines) - 1 else "")
                                                         for i, ln in enumerate(lines)) + f"{nl}{unit}}}")
        new = inner + sep + nl + obj + nl + "}" + text[len(top):]
    data = json.loads(new)
    want = dict(old)
    want[kind] = {**old.get(kind, {}), **entries}
    if data != want:
        raise ValueError("the inserted text does not parse to the old data plus the new entries")
    return new


def _depth(text, pos):
    """Brace depth of text at pos (strings skipped): 1 for a key of the top-level object."""
    depth, i, in_str = 0, 0, False
    while i < pos:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return depth
