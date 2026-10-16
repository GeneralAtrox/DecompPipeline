"""Constants a projection hides, brought back cheaply (batch 26).

Batch 25's comment rounds read only the shown text of a projected struct, so a constant group named only in a dropped
member's comment (`s32 level; /* 0x10: 0..WEAPON_LEVEL_MAX */`) was no longer shown: 139 names in 90 TC files, and a
fresh agent wrote `level > 3` where WEAPON_LEVEL_MAX existed. Here a dropped member's comment may still pull in the
constants it names, when:
- they are few: at most `readable_lean_comment_group_max` (default 2) names of one group. A #define group is shown in
  part (only the names the comment gives, as core/lean_groups shows accessor groups); an enum only when the whole
  enum has at most that many names;
- and they match something the function could use: one of their values is a literal raw.c writes, or their name
  family (the words of the name, `WEAPON_LEVEL_MAX`: weapon, level) shares a word with a shown member of the struct
  whose dropped member names them (its name or its type: `s32 weapon;`, `WeaponDef *enabled`).
Accessor groups keep their own offset rule (core/lean_groups); nothing here is project-specific.
"""
import re

from . import readable_headers

GROUP_MAX = 2
GENERIC = {"min", "max", "count", "none", "first", "last", "size", "num", "flag", "flags", "mask", "bit", "bits",
           "id", "max", "base", "end", "start", "limit", "the", "of", "a", "b"}


def words(name):
    """Lowercase words of an identifier (`WEAPON_LEVEL_MAX`, `WeaponDef`, `init_level`), generic words dropped."""
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)
    return {p.lower() for p in parts if len(p) > 1} - GENERIC


def dropped_names(v, it):
    """(names, wildcards) in the comments of what a projected struct does not show: its full comments minus the shown
    ones; empty for an item shown whole."""
    lay = v.layout(it)
    if lay is None or it in v.lines or not (v.projected(lay) or v.declared_only(it, lay)):
        return set(), set()
    shown, wild = v.comments(it)
    return it.comment_refs - shown, it.wild - wild


def shown_words(v, it):
    """The words of the shown members' names and types of a struct (padding, the struct's own names and C keywords
    left out: a group is mostly named after its struct, so that word says nothing)."""
    code = readable_headers.split_comments(v.shown(it))[0]
    own = {n.split(":", 1)[-1] for n in it.names}
    out = set()
    for ident in set(readable_headers.IDENT.findall(code)) - own - readable_headers.C_WORDS:
        if not ident.startswith("_pad_"):
            out |= words(ident)
    return out - {w for n in own for w in words(n)}


def pulled(v, rl, shown, evidence, group_max=GROUP_MAX):
    """{constant item: the names to show (None: whole)} that dropped members' comments of the shown items name (the
    module doc). rl: core/readable_lean (int_literals, CONSTANT_KINDS)."""
    if evidence is None or not group_max:
        return {}
    by_name = {}  # constant name -> the shown items whose dropped comments name it
    wilds = []
    for it in shown:
        names, wild = dropped_names(v, it)
        for n in names:
            by_name.setdefault(n, set()).add(it)
        wilds += [(p, it) for p in wild]
    if not by_name and not wilds:
        return {}
    out, cache = {}, {}
    for g in v.hs.items:
        if g in v.items or g.kind not in rl.CONSTANT_KINDS or rl._accessors(g):
            continue
        plain = {n.split(":", 1)[-1] for n in g.names}
        hit = {n: set(by_name.get(n, ())) | {it for p, it in wilds if n.startswith(p)} for n in plain}
        hit = {n: its for n, its in hit.items() if its}
        if not hit or len(hit) > group_max:
            continue
        if g.kind == "enum" and len(plain) > group_max:
            continue
        keep = set()
        for n, its in hit.items():
            vals = rl.int_literals(_value_text(g, n))
            fam = words(n)
            if vals & evidence or any(fam & cache.setdefault(it, shown_words(v, it)) for it in its):
                keep.add(n)
        if keep:
            out[g] = None if g.kind == "enum" or keep == plain else keep
    return out


FAMILY_WORDS = 2  # a constant named after the declarations needs at least this many (non-generic) words


def by_family(v, rl, decl_text, group_max=GROUP_MAX):
    """{constant item: names to show (None: whole)}: constants whose name words all come from the declarations'
    identifiers (`u32 WeaponDef_GetAimIcon(const WeaponDef *def, s32 level)` -> WEAPON_LEVEL_MIN, WEAPON_LEVEL_MAX),
    at most group_max names of one group, at least FAMILY_WORDS words each. Batch 24 reached such constants through
    whole structs; the projection and the pointer depth no longer do."""
    if not group_max:
        return {}
    have = set()
    for ident in set(readable_headers.IDENT.findall(readable_headers.split_comments(decl_text)[0])):
        have |= words(ident)
    out = {}
    for g in v.hs.items:
        if g in v.items or g.kind not in rl.CONSTANT_KINDS or rl._accessors(g):
            continue
        plain = {n.split(":", 1)[-1] for n in g.names}
        keep = {n for n in plain if len(words(n)) >= FAMILY_WORDS and words(n) <= have}
        if not keep or len(keep) > group_max or g.kind == "enum" and len(plain) > group_max:
            continue
        out[g] = None if g.kind == "enum" or keep == plain else keep
    return out


def _value_text(g, name):
    """The code (comments dropped) of name's own #define line in group g, or of the whole group (an enum)."""
    code = readable_headers.split_comments(g.text)[0]
    m = re.search(r"^\s*#\s*define\s+" + re.escape(name) + r"\b[^\n]*", code, re.M)
    return m.group(0).split(name, 1)[1] if m else code
