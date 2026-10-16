"""Part of a #define group for a lean sandbox (batch 25): an accessor group shows only the macros whose offset raw.c
writes (or that the readable file names), not every macro of the group.

An accessor group (`#define WORLD_POOL(w) ((SlotPool *)((u8 *)(w) + 0x1b0))` and its siblings) describes many
words of a big struct; batch 24 included the whole group, and what its casts name, when raw.c wrote any one of its
offsets. Nothing here is project-specific.
"""
import re

from . import readable_headers

DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)")


def chunks(text):
    """(the group's leading lines, [(macro name, its text: the comment lines right above it, the directive and its
    continuation lines)]). Comment lines after the last directive stay with it."""
    head, out, pending, cur = [], [], [], None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = DEFINE.match(ln)
        if m:
            j = i
            while j + 1 < len(lines) and (lines[j].rstrip().endswith("\\") or _open("\n".join(lines[i:j + 1]))):
                j += 1
            cur = [m.group(1), pending + lines[i:j + 1]]
            out.append(cur)
            pending, i = [], j + 1
            continue
        if cur is None:
            head.append(ln)
        else:
            pending.append(ln)
        i += 1
    if pending and out:
        out[-1][1] += pending
    return head, [(n, "\n".join(t)) for n, t in out]


def _open(text):
    return text.rfind("/*") > text.rfind("*/")


def accessor_names(hs, it, evidence, uses=()):
    """The macros of an accessor group that raw.c reaches: one whose own offset (0x10 or more) raw.c writes, or that
    gives a pointer to a type of known size (`(T *)((u8 *)(w) + 0xac)`) with an offset raw.c writes inside that span;
    and every macro the readable file names (uses)."""
    from .readable_lean import ACCESSOR, INDEX_MIN, _size_of, int_literals
    out, refs = set(), {}
    for name, text in chunks(it.text)[1]:
        code = readable_headers.split_comments(text)[0]
        refs[name] = set(readable_headers.IDENT.findall(code))
        if name in uses or {v for v in int_literals(code) if INDEX_MIN <= v <= 0xFFFFFFFF - INDEX_MIN} & evidence:
            out.add(name)
            continue
        for t, off in ACCESSOR.findall(code):
            base, size = int(off, 0), _size_of(hs.by_name.get(t))
            if base >= INDEX_MIN and size and any(base <= e < base + size for e in evidence):
                out.add(name)
    grown = bool(out)
    while grown:  # a macro built on a kept one (`ANIM_CTRL_POSES(c)` on `ANIM_CTRL_POSE_BLOCK(c)`) is kept too
        more = {n for n, r in refs.items() if n not in out and r & out}
        out |= more
        grown = bool(more)
    return out


def part(it, names):
    """The group's text with only the macros in names (its leading comment kept)."""
    head, parts = chunks(it.text)
    kept = [t for n, t in parts if n in names]
    left = len(parts) - len(kept)
    tail = [f"/* ... {left} more macro(s) of this group not shown */"] if left else []
    return "\n".join(head + kept + tail)


def code_refs(it, names):
    """What the kept macros' bodies use (readable_headers.Headers.finish's rule, on the kept macros only); a sibling
    macro they use is a ref too, so the caller shows it as well."""
    parts = chunks(it.text)[1]
    code = readable_headers.split_comments("\n".join(t for n, t in parts if n in names))[0]
    body = re.sub(r"^\s*#\s*define\s+[A-Za-z_]\w*(\([^)]*\))?", "", code, flags=re.M)
    return readable_headers.refs_of(body) - set(names)
