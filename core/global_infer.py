"""Machine names for the data globals raw C uses and the registry has not named: `refine.py globals`.

Readable C may not write a guest address as a number (RR3), so a table or variable the original reads, writes or
passes needs a registry name. Designers named them by hand; a game with a thousand such addresses (the Spawn
overlays: 34 of 57 EFFMAIN readable failures were RR3 on 91 unnamed addresses) needs a pass that does it from the
code, with no model and no game knowledge:

- the addresses are the ones `refine.py coverage` lists: harness-meta globals and pointer constants no named
  global's extent holds, minus function bodies and literal pools;
- how each is used comes from the targets' matched raw C: `rd8/16/32/f32(ADDR + K)`, `wrN(...)`, and the same
  through a local that was assigned the address (`u32 GD = 0x0c94d0c4u; ... rd32(GD + 0x34u)`); a term that is not a
  constant (`+ (i << 2)`, `+ i * 40`) makes the access indexed;
- the extent of a global is what the accesses touch (offset + width); an address inside another's extent joins it;
  an indexed one runs to the next boundary (the next unnamed address, named global, function or the data's end), at
  most MAX_OPEN bytes; an address only passed as a value gets MAX_VALUE bytes;
- the type is the access width (u32, u16, u8, f32; an f32-only table is f32); mixed widths are a u8 blob;
  a guest pointer is a u32 like every guest word in the registry (no pointer types);
- the name is neutral and plain: `g_words_94b108`, `g_u32_94b10c`. A word of evidence is used when there is one: the
  address passed in argument N of a call to a named function, whose prototype names that parameter
  (`g_effect_94b108`). The count of each kind is reported, so a renamed number is never counted as progress;
- the entry carries `"machine": "globals"`, the evidence and the accesses, so a designer (or a later pass) can
  replace it; it goes to readable/names/machine_globals.json; an address or name the registry holds is never
  touched (core/names_insert, rodata_strings.write_entries).

RR3 is not relaxed: readable C still names the global, it just has a name to use.

Nearest existing: rodata_strings - it names read-only strings from their bytes; this names data from how the code accesses it.
"""
import re

from . import global_extents, paths, raw_consts, readable

FILE = "machine_globals.json"
MACHINE = "globals"
MAX_OPEN = 0x400    # an indexed table without a known end
MAX_VALUE = 0x40    # an address only passed as a value
ELEM = {"u8": 1, "u16": 2, "u32": 4, "f32": 4}
NAMES = {"u8": "bytes", "u16": "halves", "u32": "words", "f32": "floats"}
HEXLIT = r"0[xX]([0-9a-fA-F]{8})[uU]?"
ACCESS = re.compile(r"\b(rd|wr)(8|16|32|f32)\s*\(")
ASSIGN = re.compile(r"\b([A-Za-z_]\w*)\s*((?:<<|>>|[-+|&^*])?)=(?!=)\s*([^;]*);")
LITERAL = re.compile(rf"\s*{HEXLIT}\s*")
MAX_OFFSET = 0x10000  # a larger offset from an address is another pointer
ARG = re.compile(rf"\ba\.r\[(\d)\]\s*=\s*(?:{HEXLIT}|([A-Za-z_]\w*))\s*;")
CALLTO = re.compile(r"\btc_callf?\s*\(\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*,")
SETR4 = re.compile(rf"\bset_r4_only\s*\(\s*&a\s*,\s*{HEXLIT}\s*\)")
TERM = re.compile(r"\s*([+-])?\s*(0[xX][0-9a-fA-F]+|\d+)[uU]?\s*$")
BAD_PARAM = re.compile(r"^(a|b|c|p|n|v|x|arg\d*|param_?\d*|ptr|data|value|buf|buffer)$")


def _balanced(code, i):
    """(text, end) of the first call argument starting at code[i] (after the opening paren): up to the top-level ','
    or the closing ')'."""
    depth, j = 0, i
    while j < len(code):
        c = code[j]
        if c in "([":
            depth += 1
        elif c in ")]":
            if depth == 0:
                return code[i:j], j
            depth -= 1
        elif c == "," and depth == 0:
            return code[i:j], j
        j += 1
    return code[i:], len(code)


def _split_expr(expr):
    """(base token, constant offset, indexed, stride) of `TOKEN + K + K - K + (non-constant)`; base None when the
    expression does not start with an identifier or a literal."""
    expr = expr.strip()
    m = re.match(rf"\(?\s*(?:{HEXLIT}|([A-Za-z_]\w*))\s*", expr)
    if not m:
        return None, 0, False, 0
    base = ("lit", int(m.group(1), 16)) if m.group(1) else ("var", m.group(2))
    rest = expr[m.end():].rstrip(") \t")
    off, indexed, stride = 0, False, 0
    # split the rest at top-level + and -
    parts, depth, cur = [], 0, ""
    for c in rest:
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c in "+-" and depth == 0:
            if cur.strip():
                parts.append(cur)
            cur = c
        else:
            cur += c
    if cur.strip():
        parts.append(cur)
    for p in parts:
        t = TERM.fullmatch(p)
        if t:
            v = int(t.group(2), 0)
            off += -v if t.group(1) == "-" else v
            continue
        indexed = True
        s = re.search(r"<<\s*(\d+)", p)
        if s:
            stride = 1 << int(s.group(1))
        else:
            s = re.search(r"\*\s*(\d+)|(\d+)[uU]?\s*\*", p)
            if s:
                stride = int(s.group(1) or s.group(2))
    return base, off, indexed, stride


def parse_accesses(text, mirror=None):
    """{address: [(offset, width, kind, indexed, stride)]} of the memory accesses raw C text makes through a literal
    address or a local assigned one; kind is "f32" or "int". Addresses are canonical (mirror folded)."""
    from .lint import strip_comments
    code = strip_comments(text)
    assigns = []   # (position, local, address or None): None when the local is set to anything else
    for m in ASSIGN.finditer(code):
        lit = LITERAL.fullmatch(m.group(3)) if not m.group(2) else None
        assigns.append((m.start(), m.group(1), raw_consts.fold(int(lit.group(1), 16), mirror) if lit else None))
    out = {}
    for m in ACCESS.finditer(code):
        arg, _ = _balanced(code, m.end())
        base, off, indexed, stride = _split_expr(arg)
        if base is None:
            continue
        if base[0] == "lit":
            v = raw_consts.fold(base[1], mirror)
        else:
            prior = [a for a in assigns if a[1] == base[1] and a[0] < m.start()]
            if not prior or prior[-1][2] is None:
                continue
            v = prior[-1][2]
        if not -MAX_OFFSET < off < MAX_OFFSET:
            continue
        width = {"8": 1, "16": 2, "32": 4, "f32": 4}[m.group(2)]
        out.setdefault(v, []).append((off, width, "f32" if m.group(2) == "f32" else "int", indexed, stride))
    return out


def arg_evidence(text, mirror, reg):
    """{address: (callee name, parameter name)}: addresses passed in argument register N (a.r[N] = ADDR, or
    set_r4_only) right before a tc_call to a function the registry names, whose prototype names parameter N."""
    from .lint import strip_comments
    code = strip_comments(text)
    out = {}
    events = [(m.start(), "arg", int(m.group(1)), raw_consts.fold(int(m.group(2), 16), mirror)) for m in ARG.finditer(code)
              if m.group(2)]
    events += [(m.start(), "arg", 0, raw_consts.fold(int(m.group(1), 16), mirror)) for m in SETR4.finditer(code)]
    events += [(m.start(), "call", f"{raw_consts.fold(int(m.group(1), 16), mirror):08x}", None)
               for m in CALLTO.finditer(code)]
    events.sort(key=lambda e: e[0])
    pending = {}
    for _, kind, a, b in events:
        if kind == "arg":
            pending[a] = b
            continue
        e = reg.functions.get(a)
        if e:
            try:
                _, fname, params = readable.parse_proto(e["proto"])
            except readable.RegistryError:
                params = []
            ints = [p for p in params if not re.match(r"\s*(f32|float|double|f64)\b", p[0])]
            for n, addr in pending.items():
                if n < len(ints) and not BAD_PARAM.match(ints[n][1]):
                    out.setdefault(addr, (fname, ints[n][1]))
        pending = {}
    return out


def clusters(unnamed, acc, bounds, limit):
    """[(lo, hi, addresses)]: the unnamed addresses grouped into globals. unnamed: sorted addresses; acc: {address:
    [(offset, width, kind, indexed, stride)]}; bounds: sorted starts that end an open extent (named globals,
    functions); limit: the end of the data."""
    import bisect

    def reach(a):
        use = acc.get(a)
        if not use:
            return a + 4, False       # only passed as a value
        end = max(a + o + w for o, w, *_ in use)
        return end, any(u[3] for u in use)

    def next_bound(a, others):
        k = bisect.bisect_right(bounds, a)
        cand = [bounds[k]] if k < len(bounds) else []
        cand += [x for x in others if x > a]
        return min(cand + [limit])

    out, i = [], 0
    while i < len(unnamed):
        lo = unnamed[i]
        hi, open_ = reach(lo)
        members = [lo]
        j = i + 1
        while j < len(unnamed) and unnamed[j] < hi:
            e, o = reach(unnamed[j])
            hi, open_ = max(hi, e), open_ or o
            members.append(unnamed[j])
            j += 1
        nxt = next_bound(lo, unnamed[j:j + 1])
        if open_:
            hi = max(hi, min(nxt, lo + MAX_OPEN))
        elif all(not acc.get(m) for m in members):
            hi = max(hi, min(nxt, lo + MAX_VALUE))
        # a named global or function starting inside the extent ends it there
        k = bisect.bisect_right(bounds, lo)
        if k < len(bounds) and bounds[k] < hi:
            hi = max(bounds[k], lo + 1)
        out.append((lo, hi, members))
        i = j
    return out


def element(uses):
    """(type, known) of the accesses [(offset, width, kind, ...)]: the one width they share, else a byte blob."""
    widths = {u[1] for u in uses}
    if not uses:
        return "u8", False
    if widths == {4}:
        return ("f32" if all(u[2] == "f32" for u in uses) else "u32"), True
    if len(widths) == 1:
        return f"u{widths.pop() * 8}", True
    return "u8", True


def propose(unnamed, acc, bounds, limit, evidence, taken):
    """{address: (decl, name, why, evidence word or None)} for the unnamed addresses in address order. taken: names in
    use (registry, headers); a clash gets _2, _3, ..."""
    out, used = {}, set(taken)
    for lo, hi, members in clusters(sorted(unnamed), acc, bounds, limit):
        uses = [(o + m - lo, w, k, ix, st) for m in members for (o, w, k, ix, st) in acc.get(m, ())]
        typ, known = element(uses)
        size = max(hi - lo, ELEM[typ])
        n = -(-size // ELEM[typ])
        word = next((evidence[m] for m in members if m in evidence), None)
        tag = f"{lo & 0xFFFFFF:06x}"
        if word:
            base = f"g_{word[1]}_{tag}"
        elif n == 1:
            base = f"g_{typ}_{tag}"
        else:
            base = f"g_{NAMES[typ]}_{tag}"
        name, k = base, 2
        while name in used:
            name, k = f"{base}_{k}", k + 1
        used.add(name)
        decl = f"{typ} {name}" + (f"[{n}]" if n > 1 else "")
        why = "accessed " + ", ".join(sorted({f"+0x{o:x} u{w * 8}" if kd == "int" else f"+0x{o:x} f32"
                                              for o, w, kd, *_ in uses})[:8]) if known else "address passed only"
        out[lo] = (decl, name, why, word)
    return out


def entry(decl, why, word):
    e = {"decl": decl, "machine": MACHINE, "why": why}
    if word:
        e["evidence"] = f"argument `{word[1]}` of {word[0]}"
    return e


def unnamed_addresses(cfg, reg, ext, targets):
    """(sorted data addresses, {address: [users]}): harness-meta globals and pointer constants of the targets that no
    named global's extent holds (the lists refine.py coverage prints; code and literal-pool words are left out)."""
    users = {}
    for a in targets:
        meta = paths.read_json(paths.HARNESS / a / "meta.json")
        if meta is None:
            continue
        for g in meta.get("globals") or []:
            v = int(g, 16)
            if not global_extents.containing(ext, v):
                users.setdefault(v, set()).add(a)
    vals = raw_consts.unnamed_values(cfg, reg, ext, targets, paths.MATCHED, paths.read_json(paths.CATALOG) or {},
                                     paths.FUNCTION_BODIES)
    for g, us in vals["data"].items():
        users.setdefault(int(g, 16), set()).update(us)
    return sorted(users), {v: sorted(u) for v, u in users.items()}


def name_data(cfg, targets):
    """Machine names for the data these functions use, before a readable run: a string or global with no name is what
    failed 11 of 13 failed Vigilante 8 functions, and an agent cannot add a name. A registry problem is reported, not fatal."""
    from . import rodata_strings
    for namer in (rodata_strings.command, command):
        try:
            namer(cfg, targets, write=True, log=lambda m: print(m, flush=True))
        except readable.RegistryError as e:
            print(f"data names: not written: {e}", flush=True)


def command(cfg, targets, write=False, log=print):
    """`refine.py globals`: report (and with write, add to readable/names/machine_globals.json) machine names for the
    targets' unnamed data globals. Returns the proposal {address: (decl, name, why, evidence)}."""
    from . import readable_headers, retire, rodata_strings
    reg = readable.Registry()
    ext_by, unknown = global_extents.extents(cfg, reg, paths.BUILD / "rd_extents")
    ext = sorted((int(a, 16), s) for a, s in ext_by.items()) + sorted((int(a, 16), 1) for a in unknown)
    ext.sort()
    unnamed, users = unnamed_addresses(cfg, reg, ext, targets)
    mirror = retire.mirrors(paths.ROOT)
    size = len(rodata_strings.image(cfg))
    acc, ev = {}, {}
    for f in sorted(paths.MATCHED.glob("*.c")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for v, rows in parse_accesses(text, mirror).items():
            if v in users:
                acc.setdefault(v, []).extend(rows)
        for v, w in arg_evidence(text, mirror, reg).items():
            if v in users:
                ev.setdefault(v, w)
    catalog = paths.read_json(paths.CATALOG) or {}
    funcs = [int(e["addr"], 16) for e in (catalog.get("functions", []) if isinstance(catalog, dict) else catalog)]
    bounds = sorted({b for b, _ in ext} | set(funcs))
    from .compare import IMAGE_BASE
    limit = IMAGE_BASE + size
    hs = readable_headers.load(readable.INCLUDE)
    taken = {readable.parse_proto(e["proto"])[1] for e in reg.functions.values()}
    taken |= {readable.global_name(e["decl"]) for e in reg.globals.values()}
    taken |= {n.split(":", 1)[-1] for it in hs.items for n in it.names}
    prop = propose(unnamed, acc, bounds, limit, ev, taken)
    kinds = {"evidence": 0, "accessed": 0, "value": 0}
    for lo, (decl, name, why, word) in sorted(prop.items()):
        kinds["evidence" if word else "accessed" if why.startswith("accessed") else "value"] += 1
        log(f"0x{lo:08x}  {decl:<40} {why}" + (f"  [{word[0]}({word[1]})]" if word else ""))
    log(f"globals: {len(unnamed)} unnamed data address(es) -> {len(prop)} machine-named global(s) in {len(targets)} "
        f"function(s): {kinds['evidence']} with a callee-parameter name, {kinds['accessed']} named only by type and "
        f"address from the accesses, {kinds['value']} passed only (blob of unknown layout)")
    if not write:
        log("dry run: nothing written (--write adds them to readable/names/" + FILE + ")")
        return prop
    added = rodata_strings.write_entries(readable.NAMES / FILE,
                                         {f"{lo:08x}": entry(d, y, w) for lo, (d, n, y, w) in sorted(prop.items())})
    log(f"wrote {len(added)} machine-named global(s) to {paths.show(readable.NAMES / FILE)}")
    return prop
