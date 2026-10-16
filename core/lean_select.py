"""What a lean sandbox shows of each header item it selects (batch 25): whole, projected, or only declared.

Batch 24's selection (readable_lean.select) took whole items, and the code closure through the big shared structs
(an entity, a world, a unit) reached most of the game: TC's median types.h was 112 KB. Here the same selection runs
over views of the items:
- a struct with more than SMALL members is projected (core/lean_layout): it shows the members raw.c's integer
  literals reach (a member whose bytes hold a literal's offset; offset 0 always; for a struct embedded at offset B,
  the literals minus B too), and the members the function's readable file names; padding keeps every shown offset;
- a struct reached only through pointers more than DEPTH hops away from the declarations (game.h's prototypes and
  globals) is only declared (`typedef struct X X;`), unless the readable file names one of its members;
- what a shown member uses by value is shown (its layout is needed); what it uses through a pointer is one hop
  further away;
- the comment rounds of batch 24 (constant groups a comment names, accessor macros raw.c's offsets reach, ...) read
  only the text that is shown, so a dropped member's comment no longer pulls its constant groups in (batch 26:
  except a few constants that match what the function could use: core/lean_hidden).
Without raw.c's literals (evidence None) nothing is projected or cut: batch 24's selection.
"""
import re

from . import lean_groups, lean_hidden, lean_layout, readable_headers
from .readable_headers import comment_names, refs_of

SMALL = 8  # a struct with at most this many members is shown whole
DEPTH = 2  # pointer hops from the declarations whose structs are shown (projected); further ones only declared
ROUND_DEPTH = 1  # the depth of what the comment rounds add (a constant group, an accessor macro's type)
ROUNDS = 2  # comment rounds: what the shown text's comments point at, and what those groups' comments point at
KNOWN_DEPTH = 1  # a group whose comment names a type is included when the type is this close (0: declared)
PART_ACCESSORS = 1  # an accessor group shows only the macros raw.c's offsets reach
FAMILY = 1  # batch 26: constants named after the declarations' words (core/lean_hidden.by_family)
FIELD = re.compile(r"(?:->|\.)\s*([A-Za-z_]\w*)")


def member_names(code):
    """The member names a readable file uses (after -> or .); code: its text with comments and strings masked."""
    return set(FIELD.findall(code))


GLOBAL_SPAN = 0x10000  # how far past a declared global's address a literal address counts as inside it


def global_offsets(global_addrs, literals):
    """Offsets inside the declared globals of the addresses raw.c writes (`wr32(0x8c0c7588u, ...)` is
    g_levelWorld + 0x1b0): evidence for the members of a global's struct, as an offset raw.c adds is."""
    bases = sorted(int(g, 16) for g in global_addrs)
    out = set()
    for v in literals:
        below = [b for b in bases if 0 <= v - b < GLOBAL_SPAN]
        if below:
            out.add(v - below[-1])
    return out


class Views:
    """The selection: items (shown in some form), and for each struct item how it is shown."""

    def __init__(self, hs, evidence, fields=(), offsets=()):
        self.hs, self.fields = hs, set(fields)
        self.base = None if evidence is None else set(evidence) | set(offsets) | {0}
        self.items, self.depth, self.offs = set(), {}, {}
        self.lines = {}  # an accessor group shown in part: {item: the macro names shown} (core/lean_groups)

    # -- the walk --------------------------------------------------------------------------------------------
    def need(self, refs, depth):
        work = [(r, depth, frozenset()) for r in refs]
        while work:
            r, d, o = work.pop()
            self.items.update(self.hs.forward.get(r, ()))
            it = self.hs.by_name.get(r)
            if it is None:
                continue
            old = self.depth.get(it)
            grow = it in self.lines and r not in self.lines[it]  # a macro of a group shown in part is used
            if grow:
                self.lines[it] = self.lines[it] | {r}
            elif it in self.items and old is not None and old <= d and o <= self.offs.get(it, frozenset()):
                continue
            self.items.add(it)
            self.depth[it] = d if old is None else min(old, d)
            self.offs[it] = self.offs.get(it, frozenset()) | o
            work += self._refs(it)

    def _refs(self, it):
        d, lay = self.depth[it], self.layout(it)
        if it in self.lines:
            return [(r, d, frozenset()) for r in lean_groups.code_refs(it, self.lines[it])]
        if lay is None:
            return [(r, d, frozenset()) for r in it.code_refs]
        if self.declared_only(it, lay):
            return []
        out = []
        for i in self.keep(it, lay):
            m = lay.members[i]
            for r in m.value_refs:
                out.append((r, d, self._shifted(it, m, r)))
            out += [(r, d + 1, frozenset()) for r in m.pointer_refs]
            code = readable_headers.split_comments(m.text)[0]
            out += [(r, d, frozenset()) for r in refs_of(code) - m.value_refs - m.pointer_refs]
        return out

    def _shifted(self, it, m, ref):
        """Offsets inside a member embedded by value: the item's literals that fall inside it, minus its offset (and
        modulo the element size for an array of structs)."""
        if self.base is None:
            return frozenset()
        inner = self.layout(self.hs.by_name.get(ref))
        step = inner.size if inner is not None and inner.size else m.size
        evid = self.base | self.offs.get(it, frozenset())
        return frozenset((e - m.offset) % step for e in evid if m.offset <= e < m.offset + m.size)

    # -- what is shown ---------------------------------------------------------------------------------------
    def layout(self, it):
        return lean_layout.layout(self.hs, it) if it is not None and it.kind in ("typedef", "struct") else None

    def projected(self, lay):
        return self.base is not None and not lay.union and len(lay.members) > SMALL

    def declared_only(self, it, lay):
        """A struct only declared: further than DEPTH pointer hops, with a tag to declare, and no member the readable
        file names."""
        if lay is None or self.base is None or self.depth.get(it, 0) <= DEPTH or not _tag(it):
            return False
        return not any(n in self.fields for m in lay.members for n in m.names)

    def keep(self, it, lay):
        if not self.projected(lay):
            return set(range(len(lay.members)))
        evid = self.base | self.offs.get(it, frozenset())
        return lean_layout.kept(lay, {i for i, m in enumerate(lay.members)
                                      if any(m.offset <= e < m.offset + m.size for e in evid)
                                      or self.fields & set(m.names)})

    def shown(self, it):
        """The text types.h shows for an item."""
        if it in self.lines:
            return lean_groups.part(it, self.lines[it])
        lay = self.layout(it)
        if lay is None:
            return it.text
        if self.declared_only(it, lay):
            return forward_text(it, lay)
        if not self.projected(lay):
            return it.text
        return lean_layout.project(lay, self.keep(it, lay))

    def comments(self, it):
        """(names in the shown text's comments, its `PREFIX_*` wildcards)."""
        lay = self.layout(it)
        if it not in self.lines and (lay is None or not self.projected(lay) and not self.declared_only(it, lay)):
            return it.comment_refs, it.wild
        notes = readable_headers.split_comments(self.shown(it))[1]
        return comment_names(notes) - it.names, set(readable_headers.WILDCARD.findall(notes))


def select(hs, decl_text, extra_names=(), evidence=None, uses=(), fields=(), offsets=(),
           group_max=lean_hidden.GROUP_MAX):
    """readable_lean.select over views: (Views, chosen items). fields: member names the readable file uses;
    offsets: more member offsets raw.c reaches (global_offsets); group_max: how many constants a dropped member's
    comment may still pull in (core/lean_hidden, batch 26; 0: none)."""
    from . import readable_lean as rl
    v = Views(hs, evidence, fields, offsets)
    lean = evidence is not None
    declared = set(extra_names)
    v.need(refs_of(decl_text) | set(uses), 0)
    for rnd in range(4):  # constants named or pointed at by shown comments; then what those use
        shown = {it for it in v.items if not v.declared_only(it, v.layout(it))}
        more = rl._trailing(hs, shown, extra_names, evidence)
        if not lean or rnd < ROUNDS:
            more |= _commented(v, rl, shown, declared, evidence, set(uses))
        more -= v.items
        if not more:
            break
        for it in more:
            v.need(set(v.lines[it]) if it in v.lines else {next(iter(it.names))} | it.code_refs, ROUND_DEPTH)
    if lean:  # batch 26, after the rounds (so a group shown in part never stops a rule above showing it whole)
        shown = {it for it in v.items if not v.declared_only(it, v.layout(it))}
        hidden = lean_hidden.pulled(v, rl, shown, evidence, group_max)
        if FAMILY:
            hidden.update({g: n for g, n in lean_hidden.by_family(v, rl, decl_text, group_max).items()
                           if g not in hidden})
        for g, names in hidden.items():
            if names is not None:
                v.lines[g] = set(names)
            v.need(set(v.lines[g]) if g in v.lines else {next(iter(g.names))} | g.code_refs, ROUND_DEPTH)
    return v, v.items


def _commented(v, rl, shown, declared, evidence, uses):
    """The constant groups and types the shown text's comments point at (batch 24's rules; batch 25: only the
    declarations' own types count as known, an accessor group is shown in part)."""
    hs, mentioned, prefixes = v.hs, set(), set()
    for it in shown:
        refs, wild = v.comments(it)
        mentioned |= refs
        prefixes |= wild
    near = shown if evidence is None else {it for it in shown if v.depth.get(it, 0) <= KNOWN_DEPTH}
    known = rl._plain({n for it in near for n in it.names}) | declared
    more = set()
    for it in hs.items:
        if it in v.items or it.kind not in rl.CONSTANT_KINDS:
            continue
        named = it.names & mentioned or any(n.startswith(p) for n in it.names for p in prefixes) \
            or it.comment_refs & declared
        pointed = named or it.comment_refs & known
        lits = rl.int_literals(readable_headers.split_comments(it.text)[0])
        offsets = {x for x in lits if rl.INDEX_MIN <= x <= 0xFFFFFFFF - rl.INDEX_MIN}
        if rl._accessors(it):
            if evidence is None:
                if pointed:
                    more.add(it)
            elif pointed and not offsets:
                more.add(it)
            elif PART_ACCESSORS:
                names = lean_groups.accessor_names(hs, it, evidence, uses)
                if names:
                    v.lines[it] = names
                    more.add(it)
            elif rl._accessed(hs, it, evidence):
                more.add(it)
        elif named or pointed and (evidence is None or lits & evidence or not offsets):
            more.add(it)
    return more | {hs.by_name[n] for it in shown for n in rl.ELEMENT.findall(v.shown(it)) if n in hs.by_name
                   and hs.by_name[n].kind not in rl.CONSTANT_KINDS}


def _tag(it):
    return next((n for n in it.names if n.startswith(("struct:", "union:"))), None)


def forward_text(it, lay):
    """`typedef struct Tag Name;` (or `struct Tag;`) for a struct shown only as declared, with its size."""
    kind, tag = _tag(it).split(":")
    code = readable_headers.split_comments(lay.tail)[0]
    names = code.strip().lstrip("}").strip().rstrip(";").strip()
    note = f" /* 0x{lay.size:x} bytes; members not shown ({it.header}) */"
    if it.kind == "typedef" and names:
        return f"typedef {kind} {tag} {names};{note}"
    return f"{kind} {tag};{note}"
