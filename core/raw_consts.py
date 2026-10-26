"""Address constants in matched raw C, and how raw C uses each one (batch 20).

Coverage counted globals from the harness meta "globals" (the addresses the original reads or writes), so a pointer
the original passes or stores as a value (`args.r[1] = 0x8c09f5ecu;`, a string argument to sprintf) was never
reported, and readable C had no name for it (R3 forbids writing it as a number). Raw C writes each address the
original uses as a constant, so its text says how each one is used:
- access: the address operand of a raw memory access (rd32(0x8c0d6478u), wr32(0x8c0d6478u + 4u, v)): a global the
  original reads or writes (harness meta "globals" lists it), or a literal-pool word it loads (readable C writes the
  loaded value as a constant: no name needed);
- call: a tc_call / tc_callf target (a callee, named through its prototype);
- value: anything else: a pointer the original passes (args.r[..] = C), stores (wr32(p, C)), compares or calls
  through. Readable C needs a name for it: a named function, or a named global whose declared extent holds it.
Constants are 0x... words inside the image [IMAGE_BASE, IMAGE_BASE + image size); comments are ignored. A word in a
mirror of the image (target.json address_mirrors: PS2 raw.c writes 0x0c206130 for 0x8c206130) is its canonical address.
"""
import bisect
import re

from .compare import IMAGE_BASE, WORD

HEX = re.compile(r"\b0[xX]([0-9a-fA-F]{8})[uU]?\b")
ACCESS = re.compile(r"\b(?:rd|wr)(?:8|16|32|f32)\s*\(\s*$")
CALL = re.compile(r"\btc_callf?\s*\(\s*$")


def fold(v, mirror):
    """v (int) in its canonical form: a top byte target.json address_mirrors maps ({"0c": "8c"}) is replaced."""
    c = (mirror or {}).get(f"{v >> 24:02x}")
    return int(c, 16) << 24 | v & 0xFFFFFF if c else v


def uses(text, image_size, mirror=None):
    """{address (int): set of uses ("access", "call", "value")} of the image addresses written in raw C text (mirror:
    target.json address_mirrors, a mirror-form word counted at its canonical address)."""
    from .lint import strip_comments
    code = strip_comments(text)
    out = {}
    for m in HEX.finditer(code):
        v = fold(int(m.group(1), 16), mirror)
        if not IMAGE_BASE <= v < IMAGE_BASE + image_size:
            continue
        before = code[max(0, m.start() - 40):m.start()]
        kind = "access" if ACCESS.search(before) else "call" if CALL.search(before) else "value"
        out.setdefault(v, set()).add(kind)
    return out


def values(text, image_size, own=None):
    """Sorted image addresses raw C uses as values (passed, stored, compared), other than its own address."""
    return sorted(v for v, k in uses(text, image_size).items() if "value" in k and v != own)


def resolve(reg, ext, v):
    """The registry name a value address resolves to: ("function", addr, prototype), ("global", base addr, decl,
    offset) when a named global's declared extent holds it (ext: sorted [(base, size)], core/global_extents), else
    None."""
    from .global_extents import containing
    a = f"{v:08x}"
    if a in reg.functions:
        return ("function", a, reg.functions[a]["proto"])
    if ext is not None:
        hit = containing(ext, v)
        if hit:
            b = f"{hit[0]:08x}"
            return ("global", b, reg.globals[b]["decl"], v - hit[0])
    elif a in reg.globals:  # no extents: a named global's own address only
        return ("global", a, reg.globals[a]["decl"], 0)
    return None


def describe(res):
    """A context.md / coverage phrase for resolve()'s answer."""
    if res[0] == "function":
        return f"the function `{res[2]}` (use its name as the pointer)"
    off = f" + 0x{res[3]:x} bytes" if res[3] else ""
    return f"the global `{res[2]}`" + (f" (its address{off}: `(u8 *)&name{off}`)" if res[3] else
                                         " (its address: `&name`, or `name` for an array)")


def unnamed_values(cfg, reg, ext, targets, matched_dir, catalog, bodies_file):
    """Value addresses of the targets' matched raw C that resolve to no name, by kind: {"data": {addr: users},
    "function": {addr: users} (a catalog function used as data, not in the registry), "code": {addr: users} (inside
    or right after a function body: a literal-pool word or table a register then reads; readable C writes the value
    it loads, so no name is needed; reported, never counted)}."""
    size = len(cfg["binary_path"].read_bytes())
    starts = {e["addr"] for e in (catalog.get("functions", []) if isinstance(catalog, dict) else catalog or [])}
    bodies = body_ranges(catalog, bodies_file)
    out = {"data": {}, "function": {}, "code": {}}
    for a in targets:
        f = matched_dir / f"{a}.c"
        if not f.exists():
            continue
        for v in values(f.read_text(encoding="utf-8", errors="replace"), size, int(a, 16)):
            if resolve(reg, ext, v):
                continue
            k = f"{v:08x}"
            kind = "function" if k in starts else region(v, bodies)
            out[kind].setdefault(k, set()).add(a)
    return out


def pointer_bytes(raw_text, code, image):
    """[(address, word)]: words readable code (comment-free) writes as numbers that are the bytes stored at an address
    raw C passes as a value, where raw C never writes that word itself. Before batch 20 context.md listed the word at
    such an address as a literal-pool constant, and agents passed it in place of the pointer (TC 8c04afe0 passed
    0x4f222fd6, the first instruction word of the callback 8c04a9c4, on a path no vector reaches). Words below
    0x10000 are left out (too likely to coincide with a real constant)."""
    raw_nums = {int(m, 16) for m in re.findall(r"\b0[xX]([0-9a-fA-F]+)", raw_text)}
    raw_nums |= {int(m) for m in re.findall(r"\b(\d+)[uU]?\b", raw_text)}
    nums = {int(m, 16) for m in re.findall(r"\b0[xX]([0-9a-fA-F]+)", code)}
    out = []
    for a, how in sorted(uses(raw_text, len(image)).items()):
        off = a - IMAGE_BASE
        if "value" not in how or off + 4 > len(image):
            continue
        word = int.from_bytes(image[off:off + 4], "big" if WORD == ">I" else "little")
        if word >= 0x10000 and word in nums and word not in raw_nums:
            out.append((a, word))
    return out


def region(v, bodies):
    """"code" when v lies inside a function body or right after one before the next function (its literal pool or a
    table), else "data". bodies: sorted [(lo, hi, next start)] (body_ranges)."""
    for lo, hi, nxt in bodies:
        if lo > v:
            break
        if v < max(hi, nxt):
            return "code"
    return "data"


def body_ranges(catalog, bodies_file):
    """Sorted [(lo, hi, next function start)] from state/function_bodies.txt ("entry lo hi") and the catalog."""
    funcs = catalog.get("functions", []) if isinstance(catalog, dict) else catalog or []
    starts = sorted(int(e["addr"], 16) for e in funcs)
    rows = []
    if bodies_file.exists():
        for line in bodies_file.read_text(encoding="utf-8").splitlines():
            t = line.split()
            if len(t) == 3:
                rows.append((int(t[1], 16), int(t[2], 16)))
    else:
        rows = [(int(e["addr"], 16), int(e["addr"], 16) + int(e.get("bytes", 0))) for e in funcs]
    rows.sort()
    out = []
    for lo, hi in rows:
        k = bisect.bisect_right(starts, lo)
        out.append((lo, hi, starts[k] if k < len(starts) else hi))
    return out
