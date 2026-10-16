"""Raw identifiers in readable C: fn_ADDR (a function) and g_ADDR / DAT_ADDR (a global) that the names registry may
have named since the file was written, and old names a designer renamed (a names entry's "was": ["g_oldName"]).
Pure (no core.paths): the consistency check (core/doctor.py) uses it too.

The C text is masked (comments, string and character literals blanked, same length), so a match can be told apart as
code or comment, and offsets stay valid for edits (core/sync_names.py).
"""
import json
import pathlib
import re

RAW_FN = re.compile(r"\bfn_(8c[0-9a-f]{6})\b")
RAW_GLOBAL = re.compile(r"\b(?:g|DAT)_(8c[0-9a-fA-F]{6})\b")
_PROTO = re.compile(r"\s*(.+?)\s*\b([A-Za-z_]\w*)\s*\((.*)\)\s*;?\s*", re.S)  # readable.parse_proto's pattern
_DECL = re.compile(r".*?\b([A-Za-z_]\w*)\s*(\[[^\]]*\]\s*)*$")  # readable.global_name's pattern


def proto_name(proto):
    m = _PROTO.fullmatch(proto)
    return m.group(2) if m else None


def decl_name(decl):
    m = _DECL.match(decl.strip().rstrip(";"))
    return m.group(1) if m else None


def mask(text):
    """(masked text, [(start, end) of each comment]): comments and string/char literals become spaces (newlines kept)."""
    out, spans, i, n = list(text), [], 0, len(text)
    while i < n:
        c, nxt = text[i], text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt in "*/":
            end = text.find("*/", i + 2) + 2 if nxt == "*" else text.find("\n", i)
            end = n if end < 2 or end == -1 else end
            spans.append((i, end))
        elif c in "\"'":
            end = i + 1
            while end < n and text[end] != c and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            end = min(end + 1, n)
        else:
            i += 1
            continue
        for k in range(i, end):
            if out[k] != "\n":
                out[k] = " "
        i = end
    return "".join(out), spans


def load_names(names_dir):
    """({addr: registry name} of functions, {addr: name} of globals) from readable/names/*.json (unreadable files and
    entries are skipped: the registry check reports them)."""
    fns, globs = {}, {}
    for f in sorted(pathlib.Path(names_dir).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for a, e in (data.get("functions") or {}).items():
            name = proto_name(str((e or {}).get("proto", "")))
            if name and name != f"fn_{a.lower()}":
                fns[a.lower()] = name
        for a, e in (data.get("globals") or {}).items():
            name = decl_name(str((e or {}).get("decl", "")))
            if name and name.lower() not in (f"g_{a.lower()}", f"dat_{a.lower()}"):
                globs[a.lower()] = name
    return fns, globs


def was_map(entries, current):
    """{old name: current name} from names entries' "was" lists. entries: (entry, current name) pairs; current: every
    current name (an old name that is some entry's current name again is skipped: renaming it would be wrong)."""
    out = {}
    for e, name in entries:
        for old in (e or {}).get("was") or ():
            if isinstance(old, str) and re.fullmatch(r"[A-Za-z_]\w*", old) and old != name and old not in current:
                out[old] = name
    return out


def load_was(names_dir):
    """{old name: current name} of a readable/names directory (a designer's renames, the entries' "was" keys)."""
    entries = []
    for f in sorted(pathlib.Path(names_dir).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries += [(e, proto_name(str((e or {}).get("proto", "")))) for e in (data.get("functions") or {}).values()]
        entries += [(e, decl_name(str((e or {}).get("decl", "")))) for e in (data.get("globals") or {}).values()]
    return was_map(entries, {n for _, n in entries if n})


def find(text, fns, globs, was=None):
    """Raw identifiers in text: ({raw: name} of named ones, {raw: count in code} of functions without a name). raw is
    the identifier as written (fn_8c012345, g_8c0a1234). was: {old name: current name} (load_was): an old name still
    in the text counts as a named raw identifier too."""
    code, comments = mask(text)
    named, unnamed = {}, {}
    if was:  # in code or a comment (sync renames both), never inside a string literal
        for m in re.finditer(r"(?<![.>\w])([A-Za-z_]\w*)\b", text):
            if m.group(1) in was and (code[m.start()] != " " or any(s <= m.start() < e for s, e in comments)):
                named[m.group(1)] = was[m.group(1)]
    for m in RAW_FN.finditer(text):
        if m.group(1) in fns:
            named[m.group(0)] = fns[m.group(1)]
        elif code[m.start()] != " ":
            unnamed[m.group(0)] = unnamed.get(m.group(0), 0) + 1
    for m in RAW_GLOBAL.finditer(text):
        if m.group(1).lower() in globs:
            named[m.group(0)] = globs[m.group(1).lower()]
    return named, unnamed


def scan(ws, addrs=None):
    """{file addr: {raw: name}} of readable files using a raw name the registry has named, and (raw names of unnamed
    functions in code: occurrences, distinct functions, files). addrs: only these files."""
    ws = pathlib.Path(ws)
    fns, globs = load_names(ws / "readable" / "names")
    was = load_was(ws / "readable" / "names")
    bad, occ, distinct, files = {}, 0, set(), 0
    for p in sorted((ws / "readable" / "src").glob("8c*.c")):
        if addrs is not None and p.stem not in addrs:
            continue
        named, unnamed = find(p.read_text(encoding="utf-8", errors="replace"), fns, globs, was)
        if named:
            bad[p.stem] = named
        if unnamed:
            occ += sum(unnamed.values())
            distinct |= set(unnamed)
            files += 1
    return bad, (occ, len(distinct), files)


def unnamed_line(counts):
    occ, distinct, files = counts
    return (f"raw names left for functions without a registry name: {occ} use(s) of {distinct} function(s) in "
            f"{files} readable file(s)")
