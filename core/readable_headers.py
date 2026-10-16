"""The project's readable headers (readable/include/types.h and what it includes), split into items.

An item is one top-level definition with the comment right above it: a struct, union or enum, a typedef, a forward
declaration, or a run of #define lines (one group). Each item knows the names it defines and the names it uses, in
its code and in its comments, so the readable stage can give each function only the types and constants it needs
(readable_lean). Free comments (notes not attached to a definition) are kept per header.

The parser handles the C that game headers are written in: no conditional compilation besides the include guard,
no function bodies. Anything else is kept as an item of kind "other" that defines nothing.
"""
import re
import threading

C_WORDS = frozenset(
    "auto break case char const continue default do double else enum extern float for goto if inline int long "
    "register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while "
    "_Bool define include ifndef endif".split())
IDENT = re.compile(r"[A-Za-z_]\w*")
TAGGED = re.compile(r"\b(struct|union|enum)\s+([A-Za-z_]\w*)")
WILDCARD = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*_)\*")


class Item:
    __slots__ = ("order", "header", "line", "text", "kind", "names", "code_refs", "comment_refs", "wild")

    def __init__(self, order, header, line, text):
        self.order, self.header, self.line, self.text, self.kind = order, header, line, text, "other"
        self.names, self.code_refs, self.comment_refs, self.wild = set(), set(), set(), set()


def split_comments(text):
    """(code with comments blanked, comment text)."""
    code, notes, i = [], [], 0
    while i < len(text):
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = len(text) if j < 0 else j + 2
            notes.append(text[i:j])
            code.append(" ")
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = len(text) if j < 0 else j
            notes.append(text[i:j])
            i = j
        else:
            code.append(text[i])
            i += 1
    return "".join(code), " ".join(notes)


def _open_comment(lines):
    """Whether the text ends inside a /* comment (batch 23: `#define X 5 /* ...` continued on the next line made that
    line, and everything up to the next `;`, one broken item)."""
    text = "\n".join(lines)
    return text.rfind("/*") > text.rfind("*/")


def _code_of(line, in_comment):
    """(the line's code with comments removed, whether a /* comment is still open at its end); in_comment: one was
    open at its start."""
    code, i = [], 0
    while i < len(line):
        if in_comment:
            j = line.find("*/", i)
            if j < 0:
                return "".join(code), True
            in_comment, i = False, j + 2
        elif line.startswith("/*", i):
            in_comment, i = True, i + 2
        elif line.startswith("//", i):
            break
        else:
            code.append(line[i])
            i += 1
    return "".join(code), in_comment


def _decl_end(lines, i):
    """The last line of the declaration starting at line i: the one with its ';' at brace depth 0, or, when a comment
    that line opens goes on, the line closing it (batch 24: `typedef struct A A; /* ... (a` continued on the next
    line made that line, and everything up to the next `;`, one broken item, and lost the typedef after it)."""
    depth, in_comment = 0, False
    for j in range(i, len(lines)):
        code, in_comment = _code_of(lines[j], in_comment)
        depth += code.count("{") - code.count("}")
        if depth <= 0 and ";" in code:
            while in_comment and j + 1 < len(lines):
                j += 1
                in_comment = _code_of(lines[j], True)[1]
            return j
    return len(lines)


def refs_of(code):
    """Names a piece of code uses: identifiers, and struct/union/enum tags as 'struct:Tag'."""
    refs = {f"{k}:{t}" for k, t in TAGGED.findall(code)}
    return refs | {w for w in IDENT.findall(TAGGED.sub(" ", code)) if w not in C_WORDS}


def comment_names(text):
    return {w for w in IDENT.findall(text) if w not in C_WORDS}


def _classify(item, code):
    """Fill item.kind and item.names from the item's code (comments blanked)."""
    if item.kind == "define":
        item.names = set(re.findall(r"^\s*#\s*define\s+([A-Za-z_]\w*)", code, re.M))
        return
    flat = " ".join(code.split())
    body = re.search(r"\{(.*)\}", flat)
    head = flat[:body.start()] if body else flat
    tag = re.match(r"\s*(?:typedef\s+)?(?:const\s+)?(struct|union|enum)\s+([A-Za-z_]\w*)", head)
    if body and tag:
        item.names.add(f"{tag.group(1)}:{tag.group(2)}")
    if body and re.match(r"\s*(?:typedef\s+)?enum\b", head):
        for part in body.group(1).split(","):
            m = IDENT.match(part.strip())
            if m:
                item.names.add(m.group(0))
    if flat.startswith("typedef"):
        tail = flat[body.end():] if body else flat[len("typedef"):]
        fp = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(", tail)
        if fp:
            item.names.add(fp.group(1))
        else:
            item.names |= set(re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?=[,;])", tail))
        item.kind = "typedef"
    elif body:
        m = re.match(r"\s*(struct|union|enum)\b", head)
        item.kind = m.group(1) if m else "other"
    elif re.fullmatch(r"\s*(struct|union|enum)\s+\w+\s*;\s*", flat):
        item.kind = "forward"


class Headers:
    """Items of the include tree rooted at include_dir/types.h, in the order the compiler sees them."""

    def __init__(self, include_dir, root="types.h"):
        self.dir = include_dir
        self.items, self.notes, self.external, self.seen = [], {}, [], []
        self.note_at = []  # (header, note text, order of the next item): the constants a section note introduces
        self.by_name, self.forward = {}, {}
        if root and (include_dir / root).exists():
            self._parse(root)

    def _parse(self, rel):
        if rel in self.seen:
            return
        self.seen.append(rel)
        lines = (self.dir / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        state = {"guard": None, "pending": [], "start": 0, "group": None}
        i = 0
        while i < len(lines):
            i = self._step(rel, lines, i, state)
        self._note(rel, state)

    def _note(self, rel, state):
        if state["pending"]:
            self.notes.setdefault(rel, []).append("\n".join(state["pending"]))
            self.note_at.append((rel, self.notes[rel][-1], len(self.items)))
        state["pending"] = []

    def _step(self, rel, lines, i, st):
        """Consume the construct starting at line i; return the next line index."""
        s = lines[i].strip()
        if not s:
            self._note(rel, st)
            st["group"] = None
            return i + 1
        if s.startswith(("/*", "//")):
            j = i
            if s.startswith("/*"):
                while _open_comment(lines[i:j + 1]) and j + 1 < len(lines):
                    j += 1
            if st["group"] is not None:  # a comment inside a run of #defines belongs to the group
                st["group"].text += "\n" + "\n".join(lines[i:j + 1])
            else:
                if not st["pending"]:
                    st["start"] = i + 1
                st["pending"] += lines[i:j + 1]
            return j + 1
        if s.startswith("#"):
            j = i
            while j + 1 < len(lines) and (lines[j].rstrip().endswith("\\") or _open_comment(lines[i:j + 1])):
                j += 1  # a continued line, or a comment the directive's line opens and a later line closes
            directive = "\n".join(lines[i:j + 1])
            m = re.match(r"#\s*(\w+)\s*(.*)", s)
            word, rest = (m.group(1), m.group(2).split()) if m else ("", [])
            if word == "ifndef" and st["guard"] is None and rest:
                st["guard"] = rest[0]
                self._note(rel, st)
            elif word == "define" and rest == [st["guard"]]:
                pass
            elif word == "endif":
                pass
            elif word == "include":
                self._note(rel, st)
                st["group"] = None
                inc = re.match(r'"([^"]+)"', " ".join(rest))
                found = None
                for base in ((self.dir / rel).parent, self.dir) if inc else ():  # as the compiler searches
                    if (base / inc.group(1)).is_file():
                        found = (base / inc.group(1)).resolve().relative_to(self.dir.resolve()).as_posix()
                        break
                if found:
                    self._parse(found)
                elif directive not in self.external:
                    self.external.append(directive)
            elif word == "define" and st["group"] is not None:
                st["group"].text += "\n" + directive
            elif word == "define":
                st["group"] = self._new(rel, st["start"] if st["pending"] else i + 1, st["pending"] + [directive],
                                        "define")
                st["pending"] = []
            else:
                self._new(rel, st["start"] if st["pending"] else i + 1, st["pending"] + [directive], "other")
                st["pending"], st["group"] = [], None
            return j + 1
        st["group"] = None
        j = _decl_end(lines, i)  # a declaration: up to the ';' at brace depth 0 (its trailing comment included)
        self._new(rel, st["start"] if st["pending"] else i + 1, st["pending"] + lines[i:j + 1], None)
        st["pending"] = []
        return j + 1

    def _new(self, rel, line, text_lines, kind):
        it = Item(len(self.items), rel, line, "\n".join(text_lines))
        it.kind = kind or "other"
        self.items.append(it)
        return it

    def finish(self):
        """Compute names and references (after parsing, when #define groups are complete)."""
        for it in self.items:
            code, comments = split_comments(it.text)
            _classify(it, code)
            if it.kind == "define":
                body = re.sub(r"^\s*#\s*define\s+[A-Za-z_]\w*(\([^)]*\))?", "", code, flags=re.M)
                it.code_refs = refs_of(body) - it.names
            else:
                it.code_refs = refs_of(code) - it.names
            it.comment_refs = comment_names(comments) - it.names
            it.wild = set(WILDCARD.findall(comments))
            for n in it.names:
                self.by_name.setdefault(n, it)
            if it.kind == "forward":
                for k, t in TAGGED.findall(code):
                    self.forward.setdefault(f"{k}:{t}", []).append(it)
        return self


def parse_all(include_dir):
    """Every header under include_dir, each parsed once, also those no #include reaches (the header lint)."""
    hs = Headers(include_dir, root=None)
    for f in sorted(include_dir.rglob("*.h")) if include_dir.exists() else []:
        hs._parse(f.relative_to(include_dir).as_posix())
    return hs.finish()


_CACHE = {}
_LOCK = threading.Lock()


def load(include_dir, root="types.h"):
    """Parsed headers, cached while no file of the include tree changes."""
    files = sorted(include_dir.rglob("*.h")) if include_dir.exists() else []
    key = (str(include_dir), root, tuple((str(f), f.stat().st_mtime_ns) for f in files))
    with _LOCK:
        if key not in _CACHE:
            _CACHE.clear()
            _CACHE[key] = Headers(include_dir, root).finish()
        return _CACHE[key]
