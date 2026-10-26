"""Overlay views: a struct a header macro lays over another object's bytes at +K (core/lean_select uses them).

`#define MENU_FIGHTER_WORK(f) ((MenuFighterWork *)((u8 *)(f) + 0x3714))` shows a Fighter's bytes from 0x3714 as a
MenuFighterWork, so raw.c reaches the view's member o as f + 0x3714 + o: a projected view shows the members at raw.c's
offsets less K, where raw offsets alone hid most of them and Haiku wrote magic offsets (PS2 S13, S19). The forms:
- a byte-pointer cast plus K, K a literal or a #define of one: `((T *)((u8 *)(p) + K))`;
- the same with the type a macro parameter, and the macros that name it: `#define OBJ_WORK(obj, T) ((T *)((u8 *)(obj)
  + OBJ_WORK_OFFSET))` with `#define MUSIC_TEST(obj) OBJ_WORK(obj, MusicTestWork)`;
- a member's address: `((MobWork *)&(obj)->work)`, K the offsets of the members so named in the headers' structs.
"""
import re

from . import lean_layout, readable_headers

CAST = r"\(\s*([A-Za-z_]\w*)\s*\*\s*\)\s*"
BYTES = r"\(\s*(?:const\s+)?(?:u8|s8|char|unsigned\s+char|uint8_t|u32|uintptr_t)\s*\*?\s*\)\s*"
OPERAND = r"(?:\(\s*\w+\s*\)|\w+)"
ADD = re.compile(CAST + r"\(\s*" + BYTES + OPERAND + r"\s*\+\s*(\w+)\s*\)")
MEMBER = re.compile(CAST + r"&\s*" + OPERAND + r"\s*->\s*([A-Za-z_]\w*)\s*\)")
DEFINE = re.compile(r"#\s*define\s+([A-Za-z_]\w*)\(([^)]*)\)(.*)")
NUMBER = re.compile(r"(0[xX][0-9a-fA-F]+|\d+)[uUlL]*")


def bases(hs):
    """{item order: the K at which a header macro lays that struct over other bytes} (cached on the headers)."""
    cache = hs.__dict__.get("_lean_overlays") if hasattr(hs, "__dict__") else None
    if cache is None:
        cache = _scan(hs)
        if hasattr(hs, "__dict__"):
            hs.__dict__["_lean_overlays"] = cache
    return cache


def _scan(hs):
    out, generic, members, lines = {}, {}, [], []
    for it in hs.items:
        code = readable_headers.split_comments(it.text)[0].replace("\\\n", " ")
        lines += [ln for ln in code.splitlines() if "#" in ln and "define" in ln]
    for ln in lines:
        d = DEFINE.search(ln)
        if d is None:
            continue
        params = [p.strip() for p in d.group(2).split(",")]
        for t, k in ADD.findall(d.group(3)):
            if t in params:
                generic[d.group(1)] = (params.index(t), k)
            else:
                _add(hs, out, t, k)
        members += MEMBER.findall(d.group(3))
    for ln in lines:  # the macros that name a generic macro's type
        for name, (i, k) in generic.items():
            for args in re.findall(r"\b" + name + r"\s*\(([^()]*)\)", ln):
                args = [a.strip() for a in args.split(",")]
                if i < len(args) and args[i] not in generic:
                    _add(hs, out, args[i], k)
    for t, member in members:
        for k in _member_offsets(hs, member):
            _add(hs, out, t, k)
    return {o: frozenset(ks) for o, ks in out.items()}


def _add(hs, out, type_name, k):
    it, k = hs.by_name.get(type_name), _value(hs, k)
    if it is not None and k:
        out.setdefault(it.order, set()).add(k)


def _value(hs, tok):
    """A literal's value, or a #define's literal value; None otherwise."""
    if isinstance(tok, int):
        return tok
    m = NUMBER.fullmatch(tok)
    if m:
        return int(m.group(1), 0)
    it = hs.by_name.get(tok)
    if it is None:
        return None
    code = readable_headers.split_comments(it.text)[0]
    d = re.search(r"#\s*define\s+" + tok + r"\s+\(?\s*" + NUMBER.pattern + r"\s*\)?\s*$", code, re.M)
    return int(d.group(1), 0) if d else None


def _member_offsets(hs, member):
    word, out = re.compile(r"\b" + member + r"\b"), set()
    for it in hs.items:
        if it.kind not in ("typedef", "struct") or not word.search(it.text):
            continue
        lay = lean_layout.layout(hs, it)
        out |= {m.offset for m in (lay.members if lay else ()) if member in m.names}
    return out
