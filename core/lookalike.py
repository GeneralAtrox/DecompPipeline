"""Look-alikes across projects (refine.py lookalike, batch 23): readable work another project already paid for.

Find: a target project's matched functions whose machine code is identical to a source project's function that has a
passing readable file. The code is compared with relocations masked (core/xmatch.normalize): call and branch
displacements leaving the function, and literal-pool words holding guest addresses, may differ; every other
instruction word (shapes and offsets) and every non-address constant must be equal, and the address slots must pair
one to one. Near-identical functions (same length, a few differing words or constants) are reported apart and not
used.

Map: the source file's callees, functions used as data and globals go to the target through the paired slots of that
pair (a global through any slot inside its declared extent: core/global_extents). The proposal (functions, globals,
and the header items they need) is data for the target's designer; core/lookalike_adopt applies and adopts it.
Generic: nothing here names a project or a game.
"""
import collections
import json
import re

from . import raw_names, readable, xmatch
from .readable_headers import refs_of

NEAR_MAX = 4  # near-identical: at most this many differing instruction words and constants (same length)


def readable_sources(ws):
    """Source functions with a passing readable file: readable/src/ADDR.c and a `readable` row in state/readable.json."""
    led = json.loads((ws / "state" / "readable.json").read_text(encoding="utf-8")) \
        if (ws / "state" / "readable.json").exists() else {}
    return {p.stem for p in (ws / "readable" / "src").glob("8c*.c") if led.get(p.stem, {}).get("status") == "readable"}


def matched_targets(ws):
    """Target functions with verified raw C: a `matched` ledger row and matched/ADDR.c."""
    led = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
    return {a for a, r in led.items() if r.get("status") == "matched" and (ws / "matched" / f"{a}.c").exists()}


def _differences(a, b, limit):
    """Differing instruction words plus differing constants of two normalized functions of the same length; counting
    stops past limit."""
    n = abs(len(a[1]) - len(b[1]))
    for xs, ys in ((a[0], b[0]), (a[1], b[1])):
        for x, y in zip(xs, ys):
            n += x != y
            if n > limit:
                return n
    return n


def find(src_img, tgt_img, sources, targets, near_max=NEAR_MAX, mirror=None):
    """({target: [{"from": source, "pairs": {src hex: tgt hex}}...]} identical, {target: {"from", "differ"}} near).
    Identical lists every source with the same masked code (a source project can hold copies). mirror: the target's
    address_mirrors ({"0c": "8c"}): a pool word holding a mirror address pairs as its canonical address."""
    from .retire import canonical
    mirror = mirror or {}
    norm = {a: xmatch.normalize(src_img, a) for a in sorted(sources)}
    norm = {a: n for a, n in norm.items() if n and n[0]}
    by_code, by_len = collections.defaultdict(list), collections.defaultdict(list)
    for a, n in norm.items():
        by_code[(n[0], n[1])].append(a)
        by_len[len(n[0])].append(a)
    same, near = {}, {}
    for t in sorted(targets):
        n = xmatch.normalize(tgt_img, t)
        if not n or not n[0]:
            continue
        hits = []
        for s in by_code.get((n[0], n[1]), []):
            pairs = xmatch.pair_slots(norm[s][2], n[2])
            if pairs is not None:
                hits.append({"from": s, "pairs": {canonical(f"{x:08x}", mirror): canonical(f"{y:08x}", mirror)
                                                  for x, y in pairs.items()}})
        if hits:
            same[t] = hits
            continue
        best = min(((_differences(norm[s], n, near_max), s) for s in by_len.get(len(n[0]), [])), default=None)
        if best and best[0] <= near_max:
            near[t] = {"from": best[1], "differ": best[0]}
    return same, near


class Source:
    """The source project's registry (names, typedefs) and global extents, read from its workspace."""

    def __init__(self, ws, extents=None):
        self.ws = ws
        self.reg = readable.Registry(names_dir=ws / "readable" / "names", include_dir=ws / "readable" / "include")
        self.fn_by_name = {readable.parse_proto(e["proto"])[1]: a for a, e in self.reg.functions.items()}
        self.gl_by_name = {readable.global_name(e["decl"]): a for a, e in self.reg.globals.items()}
        self.extents = extents or {}  # {global addr: sizeof}

    def text(self, addr):
        return (self.ws / "readable" / "src" / f"{addr}.c").read_text(encoding="utf-8", errors="replace")


def used_names(src, text):
    """{identifier: ("function"|"global", source addr)} of the registry and raw names the file's code uses."""
    code = raw_names.mask(text)[0]
    out = {}
    for ident in set(re.findall(r"[A-Za-z_]\w*", code)):
        m = raw_names.RAW_FN.fullmatch(ident)
        g = raw_names.RAW_GLOBAL.fullmatch(ident)
        if ident in src.fn_by_name:
            out[ident] = ("function", src.fn_by_name[ident])
        elif ident in src.gl_by_name:
            out[ident] = ("global", src.gl_by_name[ident])
        elif m:
            out[ident] = ("function", m.group(1))
        elif g and g.group(1).lower() in src.reg.globals:
            out[ident] = ("global", g.group(1).lower())
    return out


def map_names(src, s_addr, t_addr, pairs, text):
    """{identifier: {"kind", "src", "tgt" (hex or None), "why"}} for every registry or raw name the source file uses:
    the function itself goes to the target function; a callee or function used as data through its slot; a global
    through the slots inside its declared extent, which must all give the same target base."""
    pi = {int(x, 16): int(y, 16) for x, y in pairs.items()}
    out = {}
    for ident, (kind, a) in sorted(used_names(src, text).items()):
        ai, tgt, why = int(a, 16), None, None
        if kind == "function":
            tgt = t_addr if a == s_addr else (f"{pi[ai]:08x}" if ai in pi else None)
            why = None if tgt else "no slot of this pair holds its address"
        else:
            size = max(1, src.extents.get(a, 4))
            bases = {y - (x - ai) for x, y in pi.items() if ai <= x < ai + size}
            if len(bases) == 1:
                tgt = f"{bases.pop():08x}"
            else:
                why = ("no slot of this pair lies in its extent" if not bases else
                       f"its slots give {len(bases)} different target bases")
        out[ident] = {"kind": kind, "src": a, "tgt": tgt, "why": why}
    return out


def entry_name(kind, e):
    return readable.parse_proto(e["proto"])[1] if kind == "functions" else readable.global_name(e["decl"])


def own_names(tgt_reg, mirror=None):
    """{canonical address: (kind, entry)} of the target registry's entries (a key in a mirror form, 0c..., counts as
    its canonical address, 8c...)."""
    from .retire import canonical
    out = {canonical(a, mirror or {}): ("functions", e) for a, e in tgt_reg.functions.items()}
    out |= {canonical(a, mirror or {}): ("globals", e) for a, e in tgt_reg.globals.items()}
    return out


def propose(src, tgt_reg, mapped, project, mirror=None):
    """The names proposal: {"functions": {tgt: entry}, "globals": {tgt: entry}, "kept": {tgt: target's own name},
    "conflicts": [why]} from mapped ({target fn: map_names result}). An address the target registry already names keeps
    its name (never overwritten); a source name the target uses for another address, two source entries for one
    target address, or one source name for several target addresses (identical copies of a stub) is a conflict and is
    not proposed. Entries carry `"lookalike": "PROJECT:ADDR"`."""
    own = own_names(tgt_reg, mirror)
    taken = {entry_name(k, e): a for a, (k, e) in own.items()}
    out = {"functions": {}, "globals": {}, "kept": {}, "conflicts": []}
    seen = {}
    for names in mapped.values():
        for ident, m in names.items():
            if not m["tgt"] or raw_names.RAW_FN.fullmatch(ident) or raw_names.RAW_GLOBAL.fullmatch(ident):
                continue
            kind = "functions" if m["kind"] == "function" else "globals"
            t = m["tgt"]
            if t in own:
                out["kept"][t] = entry_name(*own[t])
                continue
            if seen.setdefault((kind, t), ident) != ident:
                out["conflicts"].append(f"{t}: both {seen[(kind, t)]} and {ident}")
                continue
            if taken.get(ident, t) != t:
                out["conflicts"].append(f"{ident}: the target names {taken[ident]} so, not {t}")
                continue
            table = src.reg.functions if kind == "functions" else src.reg.globals
            out[kind][t] = dict(table[m["src"]], lookalike=f"{project}:{m['src']}")
    bad = {c.split(":", 1)[0] for c in out["conflicts"]}
    count = collections.Counter(entry_name(k, e) for k in ("functions", "globals") for e in out[k].values())
    for name in sorted(n for n, c in count.items() if c > 1):  # one name, one address
        out["conflicts"].append(f"{name}: one source name for {count[name]} target addresses (identical copies)")
        bad.add(name)
    for kind in ("functions", "globals"):
        out[kind] = {t: e for t, e in sorted(out[kind].items()) if t not in bad and entry_name(kind, e) not in bad}
    return out


def target_name(tgt_reg, m, mirror=None):
    """The name a mapped identifier gets in the target: the target registry's (its own, or proposed and applied),
    else the target's raw name (fn_ADDR, g_ADDR). None: unmapped."""
    t = m["tgt"]
    if not t:
        return None
    own = own_names(tgt_reg, mirror)
    if t in own:
        return entry_name(*own[t])
    return f"fn_{t}" if m["kind"] == "function" else f"g_{t}"


def rewrite(text, renames):
    """text with each identifier in renames ({old: new}) replaced at once (code and comments)."""
    renames = {o: n for o, n in renames.items() if o != n}
    if not renames:
        return text
    rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, renames), key=len, reverse=True)) + r")\b")
    return rx.sub(lambda mm: renames[mm.group(1)], text)


def retarget(text, tgt_proto):
    """text with its one exported definition's header replaced by tgt_proto, and the old parameter names renamed by
    position in the body (the target names the function or its parameters otherwise). None when the parameter
    counts differ or there is no single exported definition."""
    from .sync_text import SyncRefused, exported
    code = raw_names.mask(text)[0]
    try:
        found = exported(code)
    except SyncRefused:
        return None
    if not found:
        return None
    start, close, brace, end = found
    old = readable.parse_proto(" ".join(code[start:close + 1].split()))
    new = readable.parse_proto(tgt_proto)
    if len(old[2]) != len(new[2]) or readable.is_variadic(code[start:close + 1]) != readable.is_variadic(tgt_proto):
        return None
    body = rewrite(text[brace:end + 1], {o: n for (_, o), (_, n) in zip(old[2], new[2])})
    return text[:start] + tgt_proto.strip().rstrip(";") + text[close + 1:brace] + body + text[end + 1:]


def header_items(hs, texts, defined=()):
    """The source header items texts need (identifiers, transitively), minus those defining a name in defined (the
    target's own headers): (items in source order, the names skipped as already defined)."""
    from .readable_lean import _closure
    chosen = set()
    refs = set()
    for t in texts:
        refs |= refs_of(raw_names.mask(t)[0])
    _closure(hs, refs, chosen)
    for _ in range(8):  # a name the parser did not file under its item (a #define group broken by a long comment)
        lost = {r for it in chosen for r in it.code_refs if r not in hs.by_name and ":" not in r}
        more = {it for it in hs.items if it not in chosen and any(
            re.search(rf"^\s*#\s*define\s+{re.escape(r)}\b", it.text, re.M) for r in lost)}
        if not more:
            break
        for it in more:
            _closure(hs, it.code_refs, chosen)
            chosen.add(it)
    skipped = {n for it in chosen for n in it.names if n in defined}
    keep = [it for it in sorted(chosen, key=lambda i: i.order) if not it.names & set(defined)]
    return keep, sorted(skipped)
