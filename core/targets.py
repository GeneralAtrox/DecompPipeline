"""Target lists of refine.py commands (batch 21): ADDR, SYSTEM and @FILE, each function once.

TC's prod4 round got a list with 48 addresses twice (a CRLF file merged with LF files), and `run` started two workers
on each of them. tokens() reads @FILE lists (UTF-8 with or without a BOM, CRLF or LF, '#' comments, commas or
whitespace between items) and normalises each item: an address is lowercased without 0x and padded to 8 digits; a
system id is matched without regard to case. addresses() expands systems into their functions and drops every
repeat, keeping the first position. Both say how many duplicates they dropped.
Pure: no core.paths.
"""
import pathlib
import re

HEX = re.compile(r"(?:0x)?[0-9a-f]{1,8}", re.I)


def norm(item):
    item = item.strip()
    return item.lower().removeprefix("0x").rjust(8, "0") if HEX.fullmatch(item) else item


def read_list(path):
    """The items of an @FILE list."""
    text = pathlib.Path(path).read_text(encoding="utf-8-sig")
    return [w for line in text.splitlines() for w in line.split("#", 1)[0].replace(",", " ").split()]


def tokens(items):
    """(the items of the command line, @FILEs expanded, in order with repeats, whether any came from a file)."""
    out, from_file = [], False
    for it in items or []:
        if it.startswith("@"):
            out += read_list(it[1:])
            from_file = True
        elif it.strip():
            out.append(it.strip())
    return out, from_file


def addresses(items, by_id=None, log=print):
    """Function addresses of ADDR/SYSTEM/@FILE items, each once in first-seen order (systems expanded through by_id,
    {id: [addr, ...]}); an item that is neither an address nor a system raises ValueError. Logs the duplicates
    dropped when there are any or a list file was read."""
    toks, from_file = tokens(items)
    systems = {k.upper(): v for k, v in (by_id or {}).items()}
    expanded = []
    for t in toks:
        if t.upper() in systems:
            expanded += [norm(a) for a in systems[t.upper()]]
        elif HEX.fullmatch(t):
            expanded.append(norm(t))
        else:
            raise ValueError(f"not an address or a system id: {t!r}")
    out = list(dict.fromkeys(expanded))
    if log and (from_file or len(out) < len(expanded)):
        log(f"targets: {len(toks)} item(s) listed, {len(expanded)} function(s) after systems, "
            f"{len(expanded) - len(out)} duplicate(s) dropped, {len(out)} unique")
    return out

