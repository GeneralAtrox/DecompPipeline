"""Lean readable sandboxes: only the declarations, types and constants one function needs.

The agent's sandbox used to hold the whole readable/include tree and a game.h declaring every function and global.
Now it holds (build_files):
- game.h: the function itself, its callees, the functions raw.c uses as data, the globals raw.c uses, and the
  functions and globals the function's current readable file (and the work.c an attempt starts from) uses;
- types.h: the types, structs, enums and macros those declarations use, with everything they need in turn (code
  references, transitively), plus (select): the constant groups their comments point at (`/* 0x00: AIM_* */`, a
  group whose comment names a declared function, or an included type when raw.c writes one of its values), the
  accessor macros raw.c's offsets reach, the element types field comments name (`/* 0x12c: EntityEvent */`), the
  uncommented constants under an included type or under a section note naming a declared name, and the constants
  whose distinctive value (0x400 or more, one or two definitions) raw.c writes, a constant whose value (0x10 or
  more) raw.c writes and no other constant has, and what the current readable file uses (core/lean_uses, batch 24);
  in the original order (one header);
- the header notes (free comments) that name something included;
- index.txt: every other type and constant of the project's headers (name, kind, header, value), the safety net
  for a helper type or constant raw.c does not use. The official check builds against the project's full headers,
  so anything listed there can be used by name.
context.md repeats the declarations and type definitions, so an agent needs to open nothing else.

Nothing here is project-specific: the headers are parsed generically (readable_headers).
"""
import pathlib
import re

from . import lean_uses, paths, readable, readable_headers
from .lean_index import index_txt  # noqa: F401 (batch 25: moved, still readable_lean.index_txt)
from .readable_headers import IDENT, comment_names, refs_of

CONSTANT_KINDS = ("define", "enum")


def used_globals(reg, meta, pool=(), raw_text="", ext=None):
    """Registry globals raw.c uses: the one at each referenced address, else the nearest one below it within 0x1000
    (an address inside an array or struct), as context.md names them; every registry global whose address raw.c
    writes as a literal (an address the oracle's list misses: `rdf32(0x8c0fc434)` after a register-indexed load);
    and with ext (core/global_extents.cached) every one whose declared extent holds a literal raw.c writes (a pointer
    constant it passes or stores: a string inside a string table). A literal in an address mirror (target.json
    "address_mirrors": `wr32(0x0cfe0320u + 4u * i, ..)`) counts as its canonical address too."""
    from .compare import IMAGE_BASE
    from .global_extents import containing
    from .readable_stack import frame_lo
    from .retire import mirrors
    lits = int_literals(raw_text) | _pool_words(raw_text)  # batch 26: a pool word naming a string
    fold = {int(k, 16): int(c, 16) for k, c in mirrors(paths.ROOT).items()}  # `0x0cfe0320u`: 8cfe0320 (Spawn)
    lits |= {fold[v >> 24] << 24 | v & 0xFFFFFF for v in lits if v >> 24 in fold}
    out = {a for a in reg.globals if int(a, 16) in lits}  # a named pool constant too (`const f32 g_byteToUnit`)
    lo = frame_lo() if ext else 0  # a global in the guest stack window below the own frame (Spawn's) counts
    for v in (lits if ext else ()):
        hit = IMAGE_BASE <= v < lo and containing(ext, v)
        if hit:
            out.add(f"{hit[0]:08x}")
    by_int = sorted((int(a, 16), a) for a in reg.globals)
    for g in meta["globals"]:
        if g in pool:
            continue
        v = int(g, 16)
        below = [a for ai, a in by_int if 0 <= v - ai < 0x1000]
        hit = containing(ext, v) if ext else None
        if hit and f"{hit[0]:08x}" in reg.globals:  # however far into its extent (g_fighters + 0x380c)
            out.add(f"{hit[0]:08x}")
        elif below:
            out.add(below[-1])
    return out


def _pool_words(raw_text):
    """The image addresses raw.c's 32-bit literal-pool reads load (core/rodata_strings.pool_words); none without
    the project's image."""
    from . import rodata_strings
    try:
        return set(rodata_strings.pool_words(raw_text or "", rodata_strings.image(paths.config())))
    except (OSError, KeyError):
        return set()


INT_LIT = re.compile(r"(?<![\w.])(0[xX][0-9a-fA-F]+|\d+)[uUlL]*(?![\w.])")  # not part of a float literal
INDEX_MIN = 0x10  # index.txt: a value this small (2, 0x8) is in too many unrelated constants to point anywhere
TRIVIAL = {0, 1, 0xFFFFFFFF}


def int_literals(text):
    """The integer values written in C text (32-bit; -N also as its two's complement), without 0, 1 and -1."""
    vals = set()
    for m in INT_LIT.finditer(text):
        v = int(m.group(1), 0) & 0xFFFFFFFF if m.group(1)[:2].lower() == "0x" else int(m.group(1)) & 0xFFFFFFFF
        vals |= {v, (-v) & 0xFFFFFFFF}
    return vals - TRIVIAL


ADDED_LIT = re.compile(r"\+\s*(0[xX][0-9a-fA-F]+|\d+)[uUlL]*(?![\w.])")


def added_offsets(text):
    """The integer values raw.c adds to an address (`wr8(param_1 + 0x1, ...)`), 0 and 1 included: member offsets.
    int_literals drops 0, 1 and -1, so a projected struct's member at +1 was hidden (core/lean_select: offset 0 is
    always kept, 2 and up are literals) and Haiku wrote `*((u8 *)out + 0x1)` (Spawn 8c05c10c)."""
    return {int(m.group(1), 16) if m.group(1)[:2].lower() == "0x" else int(m.group(1))
            for m in ADDED_LIT.finditer(text)}


def _closure(hs, refs, chosen):
    """Add the items defining refs, and what those use in their code, transitively (with the forward declarations
    of every struct/union/enum tag they name)."""
    work = list(refs)
    while work:
        r = work.pop()
        chosen.update(hs.forward.get(r, ()))
        it = hs.by_name.get(r)
        if it is None or it in chosen:
            continue
        chosen.add(it)
        work += it.code_refs


def select(hs, decl_text, extra_names=(), evidence=None, uses=()):
    """The items a set of declarations needs (see the module comment). extra_names: function and global names in
    those declarations. evidence: the integer literals of raw.c; a constant group only a comment points at (a field's
    `/* FLAG_* */`, a group whose comment names an included type or a declared function) is included when one of
    its values is written in raw.c. An accessor group (function-like macros) is included when raw.c writes one of
    its offsets (0x10 or more), or, having no such offset, when a comment points at it. None: no such filter.
    uses: header names the function's readable file uses (lean_uses.uses), included with what they need."""
    chosen, declared = set(), set(extra_names)
    _closure(hs, refs_of(decl_text) | set(uses), chosen)
    for _ in range(4):  # constants named or pointed at by included comments; then what those use
        mentioned, prefixes = set(), set()
        for it in chosen:
            mentioned |= it.comment_refs
            prefixes |= it.wild
        known = _plain({n for it in chosen for n in it.names}) | set(extra_names)
        more = _trailing(hs, chosen, extra_names, evidence)
        for it in hs.items:
            if it in chosen or it.kind not in CONSTANT_KINDS:
                continue
            named = it.names & mentioned or any(n.startswith(p) for n in it.names for p in prefixes) \
                or it.comment_refs & declared
            pointed = named or it.comment_refs & known
            lits = int_literals(readable_headers.split_comments(it.text)[0])
            offsets = {v for v in lits if INDEX_MIN <= v <= 0xFFFFFFFF - INDEX_MIN}
            if _accessors(it):  # `#define WORLD_POOL(w) ((Pool *)((u8 *)(w) + 0x1b0))`: an offset raw.c adds
                if evidence is None and pointed or evidence is not None and (
                        _accessed(hs, it, evidence) or pointed and not offsets):
                    more.add(it)
            elif named or pointed and (evidence is None or lits & evidence or not offsets):
                more.add(it)
        more |= {hs.by_name[n] for it in chosen for n in ELEMENT.findall(it.text) if n in hs.by_name
                 and hs.by_name[n].kind not in CONSTANT_KINDS}
        more -= chosen
        if not more:
            break
        for it in more:
            _closure(hs, {next(iter(it.names))} | it.code_refs, chosen)
    return chosen


ELEMENT = re.compile(r"/\*\s*(?:0[xX][0-9a-fA-F]+\s*:\s*)?([A-Za-z_]\w*)\s*\*/")  # `/* 0x12c: EntityEvent */`


def _accessors(it):
    """A #define group with a function-like macro (an accessor: a field at an offset, a cast of a member)."""
    return it.kind == "define" and re.search(r"^\s*#\s*define\s+[A-Za-z_]\w*\(", it.text, re.M) is not None


ACCESSOR = re.compile(r"\(\s*(?:struct\s+)?([A-Za-z_]\w*)\s*\*+\s*\)\s*\(\s*\(\s*u8\s*\*\s*\)\s*\(\s*\w+\s*\)\s*\+\s*"
                      r"(0[xX][0-9a-fA-F]+|\d+)\s*\)")


def _accessed(hs, it, evidence):
    """raw.c writes an offset of the accessor group: a macro's own offset (0x10 or more), or, for a macro giving a
    pointer to a type of known size (`(T *)((u8 *)(w) + 0xac)`, `/* 0x98 bytes */`), an offset inside that span."""
    code = readable_headers.split_comments(it.text)[0]
    if {v for v in int_literals(code) if INDEX_MIN <= v <= 0xFFFFFFFF - INDEX_MIN} & evidence:
        return True
    for t, off in ACCESSOR.findall(code):
        base, size = int(off, 0), _size_of(hs.by_name.get(t))
        if base >= INDEX_MIN and size and any(base <= e < base + size for e in evidence):
            return True
    return False


def _size_of(it):
    m = it and re.search(r"\}\s*\w*\s*;\s*/\*\s*(0x[0-9a-fA-F]+|\d+)\s*bytes?", it.text)
    return int(m.group(1), 0) if m else None


DISTINCT_MIN = 0x400  # a constant this large that raw.c writes, and at most one other group defines, is its value


def _by_value(hs):
    """{value: constant groups whose code writes it} (cached on the headers object)."""
    if getattr(hs, "_by_value", None) is None:
        idx = {}
        for it in hs.items:
            if it.kind in CONSTANT_KINDS:
                for v in int_literals(readable_headers.split_comments(it.text)[0]):
                    if v >= DISTINCT_MIN and v <= 0xFFFFFFFF - DISTINCT_MIN:
                        idx.setdefault(v, []).append(it)
        hs._by_value = idx
    return hs._by_value


def _run(hs, j, header):
    """The #define groups from item j on with no comment of their own, in one header."""
    out = set()
    while j < len(hs.items) and hs.items[j].header == header and hs.items[j].kind == "define" \
            and not hs.items[j].text.lstrip().startswith(("/*", "//")):
        out.add(hs.items[j])
        j += 1
    return out


def _trailing(hs, chosen, declared=(), evidence=None):
    """Uncommented #define groups that belong to something included: the ones right after an included type
    (`#define PVR_TSP_X` lines under the struct whose words they are), and the ones after a section note naming a
    declared function or global (`/* ---- Overlay elements ---- ... g_overlayLastKind ... */`) when raw.c writes one
    of their values, or they have no value of 0x10 or more (sizes, sentinels, float constants)."""
    out = set()
    for it in chosen:
        if it.kind not in ("define", "forward", "other"):
            out |= _run(hs, it.order + 1, it.header)
    if evidence is not None:
        out |= {it for v in evidence if v >= DISTINCT_MIN for it in _by_value(hs).get(v, ())
                if len(_by_value(hs)[v]) <= 2}
        out |= lean_uses.unique(hs, evidence, INDEX_MIN)  # batch 24: a value only one constant has
    for header, note, j in hs.note_at:
        if comment_names(note) & set(declared):
            for g in _run(hs, j, header):
                lits = int_literals(readable_headers.split_comments(g.text)[0])
                if evidence is None or lits & evidence or not {v for v in lits if INDEX_MIN <= v}:
                    out.add(g)
    return out


def _plain(names):
    return {n.split(":", 1)[1] if ":" in n else n for n in names}


def _patterns(note):
    """Names a note mentions; `unused_frN` (an identifier ending in N after a lowercase part) also as a pattern."""
    names = comment_names(note)
    pats = [re.compile(re.escape(n[:-1]) + r"\d+$") for n in names if re.fullmatch(r"[a-z_][a-z0-9_]*[a-z_]N", n)]
    return names, pats


def notes_for(hs, decl_text, extra_names=()):
    """Header notes (free comments) that name a declared function or global, or a parameter naming pattern the
    declarations use (`unused_frN`): {header: [note, ...]}."""
    idents = set(IDENT.findall(decl_text))
    out = {}
    for h, notes in hs.notes.items():
        keep = []
        for n in notes:
            names, pats = _patterns(n)
            if names & set(extra_names) or any(p.match(i) for p in pats for i in idents):
                keep.append(n)
        if keep:
            out[h] = keep
    return out


def types_body(hs, chosen, notes=None, views=None):
    """The chosen items in the order the project's headers define them, after the notes that apply; views
    (core/lean_select): how each is shown (a projected or only declared struct)."""
    lines = []
    for h, ns in sorted((notes or {}).items(), key=lambda kv: hs.seen.index(kv[0]) if kv[0] in hs.seen else 0):
        lines += [f"/* ---- notes from {h} ---- */", *ns, ""]
    header, declared = None, set()
    for it in sorted(chosen, key=lambda i: i.order):
        if it.kind == "forward":  # one forward declaration per tag, and none after its definition
            tags = {f"{k}:{t}" for k, t in readable_headers.TAGGED.findall(it.text)}
            if tags <= declared:
                continue
            declared |= tags
        declared |= it.names
        if it.header != header:
            header = it.header
            lines.append(f"/* ---- {header} ---- */")
        lines += [views.shown(it) if views else it.text, ""]
    return "\n".join(lines).strip()


def types_h(hs, chosen, notes=None, views=None):
    """One header: the external includes (rd_rt.h), then types_body."""
    lines = ["/* Generated: the types and constants this function's declarations use (from readable/include). */",
             "#ifndef TYPES_H", "#define TYPES_H", "", *hs.external, "", types_body(hs, chosen, notes, views), "",
             "#endif", ""]
    return "\n".join(lines)


def declared_names(text):
    """Function and global names declared in a game.h text (prototypes and extern/#define lines)."""
    names = set()
    for ln in text.splitlines():
        m = re.match(r"\s*(?:extern\s+)?[^#(]*?\b([A-Za-z_]\w*)\s*(\(|\[|;)", ln)
        if m and not ln.lstrip().startswith(("#", "/*", "typedef")):
            names.add(m.group(1))
    return names


def project_headers(reg):
    return readable_headers.load(readable.INCLUDE if not reg.isolated else readable.INCLUDE / "(isolated: none)")


def lean_set(reg, decl_lines, raw_text=None, uses=(), fields=(), global_addrs=()):
    """(headers, chosen items, notes, views) for the declarations decl_lines (game.h's prototype and global lines);
    raw_text: raw.c, whose integer literals decide which comment-pointed constants are included and which members
    a projected struct shows; uses: header names the function's readable file uses; fields: the member names it uses
    (core/lean_select, batch 25); global_addrs: the globals declared (an address inside one is a member offset)."""
    from . import lean_select
    hs = project_headers(reg)
    text = "\n".join(decl_lines)
    names = declared_names(text)
    lits = None if raw_text is None else int_literals(raw_text)
    from .retire import mirrors
    from .reg_offsets import reached, workspace_image
    from .frame_offsets import offsets as frame_offsets
    regs, derived = reached(raw_text or "", workspace_image(raw_text or ""))  # pool-loaded, stepped, derived bases
    offsets = (lean_select.global_offsets(global_addrs, (lits or set()) | derived, mirrors(paths.ROOT))
               | added_offsets(raw_text or "") | regs
               | frame_offsets(raw_text or ""))  # members of a struct local in the original's frame
    views, chosen = lean_select.select(hs, text, names, lits, uses, fields, offsets, group_max())
    return hs, chosen, notes_for(hs, text, names), views


def group_max():
    """config `readable_lean_comment_group_max`: how many constants a dropped member's comment may still pull in
    (core/lean_hidden; default 2, 0 turns it off)."""
    from . import lean_hidden
    try:
        return int(paths.config().get("readable_lean_comment_group_max", lean_hidden.GROUP_MAX))
    except (OSError, ValueError, KeyError):
        return lean_hidden.GROUP_MAX


def decl_lines(reg, fn_addrs, raw_sigs, global_addrs):
    """The port-form declarations (prototypes, extern globals) of a lean game.h."""
    lines = [f"{reg.proto(a, raw_sig=raw_sigs.get(a))};" for a in sorted(fn_addrs)]
    lines += [f"extern {reg.globals[g]['decl']};" for g in sorted(global_addrs)]
    return lines


def own_pool(meta):
    """The "globals" between the function's start and the next function: its own literal pool, not data."""
    cat = paths.read_json(paths.CATALOG)
    starts = sorted(f["addr"] for f in (cat["functions"] if isinstance(cat, dict) else cat))
    nxt = next((s for s in starts if s > meta["addr"]), "8d000000")
    return {g for g in meta["globals"] if meta["addr"] < g < nxt}


def headers_for(reg, meta, fn_addrs, raw_sigs, raw_text, ext=None, readable_text=None):
    """A function's lean headers: {"globals": the registry globals declared, "functions": the functions declared
    besides fn_addrs, "types.h", "index.txt", "types_body": the types.h text without guard and includes (for
    context.md)}. ext: used_globals'. readable_text: the function's current readable file (and the work.c an attempt
    starts from: current_text); the registry functions and globals and the header names it uses are declared too."""
    fns, gls, uses = lean_uses.uses(reg, project_headers(reg), readable_text)
    fns = sorted(a for a in fns - set(fn_addrs) if _declarable(reg, a))
    gl = used_globals(reg, meta, own_pool(meta), raw_text, ext) | gls
    decl = decl_lines(reg, list(fn_addrs) + fns, raw_sigs, gl)
    hs, chosen, notes, views = lean_set(reg, decl, raw_text, uses, member_names(readable_text), gl)
    return {"globals": gl, "functions": fns, "types.h": types_h(hs, chosen, notes, views),
            "types_body": types_body(hs, chosen, notes, views),
            "index.txt": index_txt(hs, chosen, int_literals(raw_text), views.lines), "decl": decl, "views": views}


def member_names(readable_text):
    """The member names a readable text uses (after -> or ., comments and strings masked)."""
    from .lean_select import member_names as names
    from .raw_names import mask
    return names(mask(readable_text)[0]) if readable_text else set()


def _declarable(reg, addr):
    try:
        reg.proto(addr)
        return True
    except readable.RegistryError:  # a prototype the registry refuses: the file's own check reports it
        return False


def current_text(*files):
    """The text of the files given that exist (a function's current readable file, the work.c an attempt starts
    from), each once: what headers_for's readable_text declares."""
    files = dict.fromkeys(pathlib.Path(f).resolve() for f in files if f)
    return "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in files if f.is_file())


def tokens(nbytes):
    """Rough token estimate of text (about 3.5 bytes per token for C and English)."""
    return int(nbytes / 3.5)

