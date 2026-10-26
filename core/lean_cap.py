"""The cap on a lean sandbox's types (context.md "Types and constants"), batch 33.

Batch 25's projections and depth bound the structs, but a big function (raw.c with hundreds of small literals) still
showed 117 KB of types (TC 8c012a6e: 63 KB of constant groups whose comments a shown member names and whose small
values raw.c happens to write, 33 KB of structs one pointer away). Haiku paged through it until each attempt ran out
and never wrote work.c. Over the cap, the shown items are ranked by how directly the function reaches them:
0. what the declarations name (the prototypes, the globals, the names the readable file uses) and what that embeds,
   the structs whose members the readable file names: never cut;
1. structs (and other types) one pointer away;
2. constant groups holding a distinctive value raw.c writes (0x10 or more), accessor macros raw.c's offsets reach;
3. types further away;
4. constant groups holding another value raw.c writes (2, 7: in many groups);
5. the rest (a group only a comment points at).
They are taken in that order (smaller first within a rank) while they fit. A struct that does not fit is only
declared (`typedef struct X X; /* members not shown */`), anything else is left out: index.txt lists every type left
out and every constant whose value raw.c writes, and context.md names what was cut (summary). Under the cap nothing
changes. Nothing here is project-specific.
"""
from . import readable_headers

CAP = 60000  # bytes of shown types: with the rest of context.md, one Read of the agent's (about 25k tokens)
DISTINCT = 0x10  # a value this small is in too many groups to point at one (index.txt's INDEX_MIN)
CONSTANT_KINDS = ("define", "enum")
LISTED = 40  # names the summary lists


def budget():
    """config `readable_lean_types_cap`: the cap in bytes (default CAP; 0 turns it off)."""
    from . import paths
    try:
        return int(paths.config().get("readable_lean_types_cap", CAP))
    except (OSError, ValueError, KeyError):
        return CAP


def size(v, items):
    """About the length of readable_lean.types_body for items (the notes aside): each shown text, a blank line, and
    a header line where the header changes."""
    n, header = 0, None
    for it in sorted(items, key=lambda i: i.order):
        if it.header != header:
            header = it.header
            n += len(header) + 15
        n += len(v.shown(it)) + 2
    return n


def notes_size(notes):
    """The bytes readable_lean.types_body gives the header notes ({header: [note, ...]})."""
    return sum(len(f"/* ---- notes from {h} ---- */") + 2 + sum(len(n) + 1 for n in ns) for h, ns in notes.items())


def rank(v, it, evidence):
    if it in v.lines:
        return 2
    if it.kind == "forward" or v.depth.get(it, 0) == 0:
        return 0
    lay = v.layout(it)
    if lay is not None and any(n in v.fields for m in lay.members for n in m.names):
        return 0
    if it.kind not in CONSTANT_KINDS:
        return 1 if v.depth.get(it, 0) <= 1 else 3
    from .readable_lean import int_literals
    hit = int_literals(readable_headers.split_comments(v.shown(it))[0]) & set(evidence or ())
    return 2 if any(DISTINCT <= x <= 0xFFFFFFFF - DISTINCT for x in hit) else 4 if hit else 5


def cap(v, evidence, limit, notes=0):
    """Bring the shown items under limit bytes (v.items and v.capped change); notes: the bytes of the header notes
    shown before them. The cut: {"declared": [names], "dropped": [names]} (empty when it fits)."""
    v.capped, v.cut = set(), {"declared": [], "dropped": []}
    limit = limit and max(limit - notes, 1)
    if not limit or size(v, v.items) <= limit:
        return v.cut
    order = sorted(v.items, key=lambda it: (rank(v, it, evidence), len(v.shown(it)), it.order))
    used, keep = size(v, v.items) - sum(len(v.shown(it)) + 2 for it in v.items), set()  # the header lines
    for it in order:
        n = len(v.shown(it)) + 2
        if used + n <= limit or rank(v, it, evidence) == 0:
            keep.add(it)
            used += n
            continue
        lay = v.layout(it)
        if lay is not None and _tag(it):
            v.capped.add(it)  # Views.declared_only: one line
            keep.add(it)
            used += len(v.shown(it)) + 2
            v.cut["declared"].append(_name(it))
        else:
            v.cut["dropped"].append(_name(it))
    v.items.intersection_update(keep)
    return v.cut


def summary(cut, limit):
    """context.md's line on what the cap left out; "" when nothing."""
    parts = []
    for key, what in (("declared", "shown only as declared"), ("dropped", "left out")):
        names = sorted(set(cut.get(key) or ()))
        if names:
            more = f" and {len(names) - LISTED} more" if len(names) > LISTED else ""
            parts.append(f"{what}: {', '.join(names[:LISTED])}{more}")
    if not parts:
        return ""
    return (f"Over this section's cap ({limit // 1000} KB), the types and constants the function reaches least "
            f"directly are cut ({'; '.join(parts)}). index.txt lists every type left out and every constant whose "
            "value raw.c writes; the check builds work.c with the full headers, so all of them can be used by name.")


def _tag(it):
    return next((n for n in it.names if n.startswith(("struct:", "union:"))), None)


def _name(it):
    """An item's name for the summary: a group's first name and its prefix (`PROJECTILE_*`)."""
    plain = sorted(n.split(":", 1)[-1] for n in it.names)
    if it.kind in CONSTANT_KINDS and len(plain) > 1:
        head = plain[0].rsplit("_", 1)[0] if "_" in plain[0] else plain[0]
        return f"{head}_*" if all(p.startswith(head + "_") for p in plain) else f"{plain[0]} (+{len(plain) - 1})"
    return next((p for p in plain if p), "?")
