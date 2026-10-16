"""Machine names for read-only strings (batch 26): `refine.py strings`.

Readable C may not write a guest address as a number (RR3) and may not define globals or string literals, so a
string the original passes (a sprintf format, a Debug_Alert message) needs a registry name, as TC's game strings
have (`const char g_strNoLimit[10]`). TC's engine strings had none, and 3 of 20 engine pilot functions failed on
them. This module finds them and names them from their content, generically:

- a candidate is an image address raw C uses as a value (passed, stored: core/raw_consts "value") outside every
  function body, or the word a 32-bit literal-pool read (`rd32(POOL)`) loads, when that word is such an address;
- it is a string when the byte before it is NUL (a string starts there, not inside another) and a NUL follows
  within MAX_LEN bytes, with at least `readable_strings_min_chars` (default 1) printable characters before it
  (tab, CR and LF count); a one-character string only among other strings (_in_string_area);
- it is left alone when a registry global's declared extent holds it (already named: core/global_extents), when
  its bytes reach into a named global, or when any matched raw C writes into it by literal address (not
  read-only);
- its name comes from the content: `g_fmt_` + words for a printf format (`%d`, `%s`, ...), else `g_str_` + words
  (CamelCase, at most NAME_WORDS words; an all-caps word as Word), `g_str_ADDR` when it has
  no word; a name the registry or the headers already
  use gets `_2`, `_3`, ... in address order;
- its declaration is `const char NAME[N]` (N counts the NUL), like the designed string globals, with
  `"machine": "strings"` and the text, so designers can rename it (an existing registry entry is never renamed or
  overridden);
- it goes into the names file of the system that owns it (owner): the system (state/systems.json) of most of the
  functions that use it, a tie going to the system of the lowest-addressed user; the file is readable/names/ID.json,
  as for every system (core/names_insert adds the lines without rewriting the designer's file). A string none of whose
  users has a system goes to readable/names/strings.json.

Once written, the lean sandbox declares each one raw.c uses (readable_lean.used_globals: its address is a raw.c
literal, or a pool word), context.md names it under "Addresses raw.c passes", and drafts write its name.
"""
import functools
import pathlib
import re

from . import raw_consts
from .compare import IMAGE_BASE

FILE = "strings.json"
MACHINE = "strings"
MAX_LEN = 256
MIN_CHARS = 1
NAME_WORDS = 4
PRINTABLE = set(range(0x20, 0x7F)) | {9, 10, 13}
POOL_READ = re.compile(r"\brd32\s*\(\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*\)")
WRITE = re.compile(r"\bwr(8|16|32|f32)\s*\(\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*(?:\+\s*(0[xX][0-9a-fA-F]+|\d+)[uU]?\s*)?,")
FORMAT = re.compile(r"%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|L)?([diouxXeEfgGcsp%])")
WIDTH = {"8": 1, "16": 2, "32": 4, "f32": 4}


def string_at(image, v, min_chars=MIN_CHARS):
    """The text of the NUL-terminated printable run starting at image address v, else None (not after a NUL, no NUL
    within MAX_LEN, a non-printable byte, or fewer than min_chars characters)."""
    off = v - IMAGE_BASE
    if not 0 < off < len(image) or image[off - 1] != 0:
        return None
    end = image.find(b"\0", off, off + MAX_LEN + 1)
    if end < 0:
        return None
    run = image[off:end]
    if len(run) < max(min_chars, 1) or any(c not in PRINTABLE for c in run):
        return None
    if len(run) == 1 and not _in_string_area(image, off, end):
        return None
    return run.decode("ascii")


def _in_string_area(image, off, end):
    """A one-character run is a string only among strings: NUL padding follows it to a 4-byte boundary, and the
    nearest non-NUL byte before it (at most 4 back) is printable, the end of the string before (TC `"*.*" "*" "."`).
    A small first member of a data struct (0x2a 00 00 00 after a pointer or a word) mostly fails one of these."""
    pad = image[end:(end + 3) & ~3]
    before = image[max(0, off - 4):off].rstrip(b"\x00")
    return not pad.strip(b"\x00") and bool(before) and before[-1] in PRINTABLE


def pool_words(raw_text, image):
    """{word: pool address}: the image addresses that raw C's 32-bit literal-pool reads load."""
    from .lint import strip_comments
    out = {}
    for m in POOL_READ.finditer(strip_comments(raw_text)):
        off = int(m.group(1), 16) - IMAGE_BASE
        if 0 <= off <= len(image) - 4:
            w = int.from_bytes(image[off:off + 4], "little")
            if IMAGE_BASE <= w < IMAGE_BASE + len(image):
                out.setdefault(w, int(m.group(1), 16))
    return out


def candidates(raw_text, image, bodies):
    """{address: "value" | "pool"}: the addresses outside function bodies that raw C passes or stores, and the pool
    words it loads that point there (bodies: raw_consts.body_ranges)."""
    out = {v: "value" for v, kinds in raw_consts.uses(raw_text, len(image)).items()
           if "value" in kinds and not in_body(v, bodies)}
    for w in pool_words(raw_text, image):
        if not in_body(w, bodies):
            out.setdefault(w, "pool")
    return out


def in_body(v, bodies):
    """v lies inside a function body (bodies: raw_consts.body_ranges). Not raw_consts.region: that counts everything
    up to the next function start as code, and the next start after the last text function can lie past the whole
    read-only data (TC: 0x8c09f5b6 to 0x8c0b0242 holds every string)."""
    return any(lo <= v < hi for lo, hi, _ in bodies if lo <= v)


def literal_writes(texts):
    """Sorted [(lo, hi)] byte spans raw C texts write at literal addresses (wr32(0x8c0a2710u + 4u, v))."""
    from .lint import strip_comments
    spans = set()
    for t in texts:
        for m in WRITE.finditer(strip_comments(t)):
            lo = int(m.group(2), 16) + (int(m.group(3), 0) if m.group(3) else 0)
            spans.add((lo, lo + WIDTH[m.group(1)]))
    return sorted(spans)


def _overlaps(spans, lo, hi):
    return any(a < hi and lo < b for a, b in spans)


def base_name(text, addr):
    """The content name of a string: g_fmt_Words for a printf format, else g_str_Words (g_str_ADDR: no word)."""
    specs = FORMAT.findall(text)
    is_fmt = any(s != "%" for s in specs)
    words = re.findall(r"[A-Za-z0-9]+", FORMAT.sub(" ", text) if is_fmt else text)
    if not words and is_fmt:
        words = [s for s in specs if s != "%"]
    words = [w.capitalize() if w.isupper() and len(w) > 1 else w[:1].upper() + w[1:] for w in words[:NAME_WORDS]]
    body = "".join(words)[:40] or f"{addr:08x}"
    return ("g_fmt_" if is_fmt else "g_str_") + body


def propose(found, taken):
    """{address: (name, text)} for found {address: text}, in address order; a name in `taken` (or given earlier)
    gets _2, _3, ... (deterministic)."""
    out, used = {}, set(taken)
    for v in sorted(found):
        base = base_name(found[v], v)
        name, k = base, 2
        while name in used:
            name, k = f"{base}_{k}", k + 1
        used.add(name)
        out[v] = (name, found[v])
    return out


def entry(name, text):
    """The registry entry of a machine-named string (readable/names/strings.json)."""
    return {"decl": f"const char {name}[{len(text.encode('ascii')) + 1}]", "machine": MACHINE, "text": text}


def scan(cfg, reg, ext, targets, matched_dir, bodies, all_raw=None, taken=()):
    """(proposal {address: (name, text)}, report {"named": {addr: users}, "skipped": {why: count}}) for the
    targets' matched raw C. all_raw: every matched raw C text (for the read-only test; default the targets'),
    taken: names already in use (registry and headers)."""
    image = _image(str(cfg["binary_path"]))
    texts = {a: (matched_dir / f"{a}.c").read_text(encoding="utf-8", errors="replace")
             for a in targets if (matched_dir / f"{a}.c").exists()}
    writes = literal_writes(all_raw if all_raw is not None else texts.values())
    min_chars = int(cfg.get("readable_strings_min_chars", MIN_CHARS))
    found, users, skipped = {}, {}, {}
    for a, text in texts.items():
        for v in candidates(text, image, bodies):
            s = string_at(image, v, min_chars)
            if s is None:
                continue
            why = _why_not(reg, ext, writes, v, len(s) + 1)
            if why:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            found[v] = s
            users.setdefault(v, set()).add(a)
    prop = propose(found, taken)
    return prop, {"named": {f"{v:08x}": sorted(u) for v, u in sorted(users.items())}, "skipped": skipped}


def _why_not(reg, ext, writes, v, size):
    if raw_consts.resolve(reg, ext, v):
        return "already named"
    bases = [b for b, _ in ext] if ext is not None else [int(a, 16) for a in reg.globals]
    if any(v <= b < v + size for b in bases):
        return "reaches into a named global"
    if _overlaps(writes, v, v + size):
        return "written by raw C (not read-only)"
    return None


def command(cfg, targets, write=False, log=print):
    """`refine.py strings`: report (and with write, add to the owner systems' names files) the machine names of the
    targets' unnamed read-only strings. Returns the proposal {address: (name, text)}."""
    from . import global_extents, paths, readable, readable_headers
    reg = readable.Registry()
    ext = global_extents.for_process(cfg, reg)
    hs = readable_headers.load(readable.INCLUDE)
    taken = {readable.parse_proto(e["proto"])[1] for e in reg.functions.values()}
    taken |= {readable.global_name(e["decl"]) for e in reg.globals.values()}
    taken |= {n.split(":", 1)[-1] for it in hs.items for n in it.names}
    bodies = raw_consts.body_ranges(paths.read_json(paths.CATALOG) or {}, paths.FUNCTION_BODIES)
    all_raw = [f.read_text(encoding="utf-8", errors="replace") for f in sorted(paths.MATCHED.glob("*.c"))]
    prop, rep = scan(cfg, reg, ext, targets, paths.MATCHED, bodies, all_raw, taken)
    for v, (name, text) in sorted(prop.items()):
        users = rep["named"][f"{v:08x}"]
        log(f"0x{v:08x}  {entry(name, text)['decl']:<48} {len(users)} user(s), e.g. {users[0]}  {text!r}")
    log(f"strings: {len(prop)} unnamed read-only string(s) in {len(targets)} function(s); skipped: "
        f"{rep['skipped'] or 'none'}")
    if not write:
        log("dry run: nothing written (--write adds each to its owner system's readable/names file)")
        return prop
    from . import systems
    sys_of, _ = systems.load()
    by_file = {}
    for v, (name, text) in sorted(prop.items()):
        sid = owner(rep["named"][f"{v:08x}"], sys_of)
        by_file.setdefault(f"{sid}.json" if sid else FILE, {})[f"{v:08x}"] = entry(name, text)
    for fname, entries in sorted(by_file.items()):
        added = write_entries(readable.NAMES / fname, entries)
        log(f"wrote {len(added)} machine-named string(s) to {paths.show(readable.NAMES / fname)}")
    return prop


def owner(users, sys_of):
    """The system that owns a string: the one holding most of its users (functions), a tie going to the system of
    the lowest-addressed user; None when no user has a system."""
    counts = {}
    for a in sorted(users, key=lambda x: int(x, 16)):
        if sys_of.get(a):
            counts.setdefault(sys_of[a], [0, int(a, 16)])[0] += 1
    if not counts:
        return None
    return min(counts, key=lambda s: (-counts[s][0], counts[s][1]))


def write_entries(path, entries):
    """Add the entries {addr: entry} to the names file at path (created when missing), under its lock, as text lines
    (core/names_insert: the designer's layout stays). An address the registry holds by now (a designer named it
    meanwhile) or a name it uses is left out; when the registry no longer loads afterwards, the file is restored.
    Returns the addresses added."""
    import os
    from . import filelock, names_insert, readable
    with filelock.locked(path):
        reg = readable.Registry()
        names = {readable.global_name(e["decl"]) for e in reg.globals.values()}
        names |= {readable.parse_proto(e["proto"])[1] for e in reg.functions.values()}
        todo = {a: e for a, e in entries.items()
                if a not in reg.globals and readable.global_name(e["decl"]) not in names}
        old = path.read_bytes() if path.exists() else None
        if not todo:
            return []
        text = names_insert.insert(old.decode("utf-8") if old else "", "globals", todo)
        _write_bytes(path, text.encode("utf-8"))
        try:
            readable.Registry()
        except readable.RegistryError:
            if old is None:
                os.remove(path)
            else:
                _write_bytes(path, old)
            raise
        return sorted(todo)


def _write_bytes(path, data):
    """Atomic, byte for byte (the file's own line endings)."""
    import os
    import threading
    from .filelock import retry_refused
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    try:
        retry_refused(lambda: os.replace(tmp, path), path, "replacing")
    finally:
        if tmp.exists():
            tmp.unlink()


@functools.lru_cache(maxsize=4)
def _image(path):
    return pathlib.Path(path).read_bytes()


def image(cfg):
    return _image(str(cfg["binary_path"]))
