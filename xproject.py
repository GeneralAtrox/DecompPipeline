"""Reuse an earlier project's work in a new one (same CPU, same developer or shared libraries).

  python xproject.py --project NEW --from OLD match     byte-match functions (addresses ignored) -> state/import.json
  python xproject.py --project NEW --from OLD report    counts: exact/near x game/extra/library
  python xproject.py --project NEW --from OLD stage [--near R] [--only ADDR ...]
        write OLD's matched C, relocated to NEW's addresses, to NEW/import/<addr>.c for exact matches (and near
        matches with ratio >= R) of functions NEW decompiles (game ranges + extra_functions,
        core/scope.py). Workers judge an imported file first: a pass is matched with no agent run, a fail
        becomes the agent's starting point. --only: just these functions (a twin in a second source, e.g.
        Spawn gend 8c564b14 from ovl 8c94aeec, without touching the imports staged from the first one).
        Of several exact twins, match tries OLD's matched ones first; against itself it never pairs a
        function with itself. A function NEW has matched is never staged (its import is kept as it is).
  python xproject.py --project NEW --from OLD restage [--dry]
        after a relocation fix: rewrite the staged imports today's relocation maps better (restage())
  python xproject.py --project NEW --from OLD names     carry readable names (functions, globals) over to
        NEW/readable/names/imported.json, through the address pairs of exact matches; run match + names once
        per earlier project, best first: a name an earlier --from gave an address is kept. A name on two
        addresses (or one the workspace's own names files define) keeps only the clearly stronger match (the
        old function's own exact match over a pool address pair); otherwise every one is skipped, with a note
"""
import argparse
import collections
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from core import xmatch  # noqa: E402

STACK_LO = 0x8CFF0000
ADDR_RE = re.compile(r"0x([08]c[0-9a-fA-F]{6})(u?)\b")  # RAM at 0x8c... or its P0 mirror 0x0c... (Spawn)


def relocate(text, amap, own_old, own_new):
    """Rewrite OLD guest addresses in C source to NEW ones: a literal recorded as a pair becomes the new image's
    word verbatim; one recorded only in the other form (0x8c... vs 0x0c...) keeps its own form.
    Returns (text, unmapped addresses)."""
    missing = set()

    def sub(m):
        a = m.group(1).lower()
        if int("8c" + a[2:], 16) >= STACK_LO:
            return m.group(0)
        if a in amap:  # the new image's own word, in its own form (TC's 0x8c... pool word is Spawn's 0x0c...)
            return f"0x{amap[a]}{m.group(2)}"
        other = ("0c" if a[0] == "8" else "8c") + a[2:]  # the pair recorded in the other form: keep this one's
        if other in amap:
            return f"0x{a[:2]}{amap[other][2:]}{m.group(2)}"
        missing.add(a)
        return m.group(0)
    text = ADDR_RE.sub(sub, text)
    text = re.sub(rf"\bfn_{own_old}\b", f"fn_{own_new}", text, flags=re.I)
    return text, sorted(missing)


def relocate_match(text, amap, m, addr):
    """relocate() for one match: its own pairs first. The global map drops a pair two matches disagree on (every
    `bra X; nop` thunk matches every other one), but within this function's bytes the pair is certain."""
    return relocate(text, {**amap, **m["pairs"]}, m["from"], addr)


HEADER = re.compile(r"/\* imported from .* ([0-9a-f]{8}) \((?:exact|near), [\d.]+\) \*/\n"
                    r"(?:/\* unmapped addresses of the old project: (.*) \*/\n)?")


def restage(new_ws, old_ws, matches, amap, dry=False):
    """Re-relocate the staged imports with today's relocate_match; returns [(addr, unmapped before, after)].
    An import is rewritten only when its function is not matched, its match still names the same old function, the
    old C is the same (the two differ only in address literals) and fewer addresses stay unmapped. Spawn's TC
    division helpers were staged 37 minutes before f6254d7 (a match's own pairs) and kept TC's error global
    0x8c1129f4, so each failed its attempt 0; today's relocation gives the overlay's own 0x0c57a054."""
    led = json.loads((new_ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
    out = []
    for f in sorted((new_ws / "import").glob("*.c")):
        a, text = f.stem, f.read_text(encoding="utf-8")
        h, m = HEADER.match(text), matches.get(f.stem)
        if not h or not m or m["from"] != h.group(1) or (led.get(a) or {}).get("status") == "matched":
            continue
        src = old_ws / "matched" / f"{m['from']}.c"
        if not src.exists():
            continue
        new, missing = relocate_match(src.read_text(encoding="utf-8"), amap, m, a)
        body, was = text[h.end():], (h.group(2) or "").split()
        if ADDR_RE.sub("0x?", new) != ADDR_RE.sub("0x?", body) or len(missing) >= len(was):
            continue
        out.append((a, was, missing))
        if not dry:
            head = text[:h.end()].split("\n")[0] + "\n"
            if missing:
                head += f"/* unmapped addresses of the old project: {' '.join(missing)} */\n"
            f.write_text(head + new, encoding="utf-8")
    return out


MATCH_RANK = {"exact": 2, "pair": 1}  # imported_match: the address is the old function's own exact match, or only
# an address pair from a literal pool (a callee or a data word); an entry from before this field has no rank


def entry_name(kind, e):
    """The C name a names entry defines, or None when it has none the readable registry would parse."""
    if e.get("name"):
        return e["name"]
    from core import readable
    try:
        return readable.parse_proto(e["proto"])[1] if kind == "functions" else readable.global_name(e["decl"])
    except (KeyError, readable.RegistryError):
        return None


def drop_clashes(names, own=frozenset()):
    """Remove imported entries whose name another entry also defines (the readable registry refuses a name used
    twice, and a name on two addresses means at least one is wrong). The clearly stronger match is kept (MATCH_RANK,
    a single top rank); otherwise every one is dropped. A name the workspace's own names files define drops every
    imported entry with it. -> one note per clash."""
    by = {}
    for kind, table in names.items():
        for addr, e in table.items():
            n = entry_name(kind, e)
            if n:
                by.setdefault(n, []).append((kind, addr, e))
    notes = []
    for n, ents in sorted(by.items()):
        if len(ents) < 2 and n not in own:
            continue
        ents.sort(key=lambda t: t[1])
        ranks = [MATCH_RANK.get(e.get("imported_match"), 0) for _, _, e in ents]
        keep = None
        if n not in own and min(ranks) > 0 and ranks.count(max(ranks)) == 1:
            keep = ents[ranks.index(max(ranks))][1]
        desc = ", ".join(f"{a} (from {e.get('imported_from')}, {e.get('imported_match') or 'no score'})"
                         for _, a, e in ents)
        for kind, addr, _ in ents:
            if addr != keep:
                del names[kind][addr]
        why = ("the workspace's own names define it" if n in own else
               f"kept {keep}, the stronger match" if keep else "no clearly stronger match: skipped all")
        notes.append(f"name clash: {n} at {desc}: {why}")
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--from", dest="old", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("match")
    sub.add_parser("report")
    st = sub.add_parser("stage")
    st.add_argument("--near", type=float, default=0.85)
    st.add_argument("--only", nargs="+", metavar="ADDR")
    sub.add_parser("names")
    sub.add_parser("restage").add_argument("--dry", action="store_true")
    a = ap.parse_args()
    new_ws, old_ws = pathlib.Path(a.project).resolve(), pathlib.Path(a.old).resolve()
    out = new_ws / "state" / "import.json"

    if a.cmd == "match":
        old, new = xmatch.Image(old_ws), xmatch.Image(new_ws)
        oldled = old_ws / "state" / "ledger.json"
        done = json.loads(oldled.read_text(encoding="utf-8"))["functions"] if oldled.exists() else {}
        matches = xmatch.match(old, new, prefer={x for x, r in done.items() if r.get("status") == "matched"})
        amap = xmatch.address_map(matches)
        out.write_text(json.dumps({"from": str(old_ws), "matches": matches, "address_map": amap}, indent=0),
                       encoding="utf-8")
        print(f"{len(matches)} matched functions, {len(amap)} address pairs -> {out}")
        return
    data = json.loads(out.read_text(encoding="utf-8"))
    matches, amap = data["matches"], data["address_map"]
    if data.get("from") and pathlib.Path(data["from"]).resolve() != old_ws:  # one source per import.json
        sys.exit(f"{out} was matched against {data['from']}, not {old_ws}: run match with this --from first")
    if a.cmd == "restage":
        done = restage(new_ws, old_ws, matches, amap, a.dry)
        for x, was, now in done:
            print(f"  {x}: unmapped {' '.join(was)} -> {' '.join(now) or 'none'}")
        print(f"{'would rewrite' if a.dry else 'rewrote'} {len(done)} staged import(s) in {new_ws / 'import'}")
        return
    old, new = xmatch.Image(old_ws), xmatch.Image(new_ws)

    if a.cmd == "report":
        c = collections.Counter()
        for addr, m in matches.items():
            region = "game" if new.is_game(addr) else ("extra" if new.in_scope(addr) else "library")
            same = "game" if old.is_game(m["from"]) else ("extra" if old.in_scope(m["from"]) else "library")
            c[(region, m["kind"], same)] += 1
        total_game = sum(1 for f in new.functions if new.is_game(f))
        total_extra = sum(1 for f in new.functions if new.in_scope(f) and not new.is_game(f))
        print(f"new project: {len(new.functions)} functions, {total_game} in the game range"
              + (f", {total_extra} extra (project.json extra_functions)" if total_extra else ""))
        for k, v in sorted(c.items()):
            print(f"  {k[0]:8s} {k[1]:6s} (old {k[2]}): {v}")
        oldled = json.loads((old_ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
        usable = sum(1 for addr, m in matches.items() if new.in_scope(addr) and m["kind"] == "exact"
                     and oldled.get(m["from"], {}).get("status") == "matched")
        print(f"exact in-scope (game range + extra) matches whose old function is matched (C to import): {usable}")
    elif a.cmd == "stage":
        dst = new_ws / "import"
        dst.mkdir(exist_ok=True)
        staged = clean = kept = 0
        label = json.loads((old_ws / "project.json").read_text(encoding="utf-8")).get("name") or old_ws.name
        only = {x.lower().removeprefix("0x").rjust(8, "0") for x in a.only or ()}
        nled = new_ws / "state" / "ledger.json"
        led = json.loads(nled.read_text(encoding="utf-8"))["functions"] if nled.exists() else {}
        for addr, m in sorted(matches.items()):
            if only and addr not in only:
                continue
            if (led.get(addr) or {}).get("status") == "matched":  # as restage: a matched function's import stays
                kept += 1
                continue
            src = old_ws / "matched" / f"{m['from']}.c"
            if not new.in_scope(addr) or not src.exists():
                continue
            if m["kind"] != "exact" and m["ratio"] < a.near:
                continue
            text, missing = relocate_match(src.read_text(encoding="utf-8"), amap, m, addr)
            head = f"/* imported from {label} {m['from']} ({m['kind']}, {m['ratio']}) */\n"
            if missing:
                head += f"/* unmapped addresses of the old project: {' '.join(missing)} */\n"
            (dst / f"{addr}.c").write_text(head + text, encoding="utf-8")
            staged += 1
            clean += not missing
        print(f"staged {staged} file(s) in {dst} ({clean} with every address relocated; {kept} matched, kept)")
    elif a.cmd == "names":
        from core.retire import canonical, mirrors
        os.environ["DECOMP_PROJECT"] = str(new_ws)  # entry_name parses with core.readable, which needs a project
        mirror = mirrors(new_ws)  # a pool word in mirror form (Spawn's 0c... pointers) names the canonical address
        d = new_ws / "readable" / "names"
        path = d / "imported.json"
        prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        names = {"functions": {}, "globals": {}}
        for f in sorted((old_ws / "readable" / "names").glob("*.json")):
            reg = json.loads(f.read_text(encoding="utf-8"))
            for kind in ("functions", "globals"):
                for addr, e in reg.get(kind, {}).items():
                    if addr in amap:
                        new_addr = canonical(amap[addr], mirror)
                        m = matches.get(new_addr) or matches.get(amap[addr]) or {}
                        how = "exact" if m.get("kind") == "exact" and m.get("from") == addr else "pair"
                        names[kind][new_addr] = dict(e, imported_from=addr, imported_ws=str(old_ws),
                                                     imported_match=how)
        # several earlier projects in turn (--from A, then --from B): an address keeps the name an earlier project
        # gave it; this project's own entries from a previous run are replaced
        for kind in names:
            for addr, e in prev.get(kind, {}).items():
                if e.get("imported_ws") not in (None, str(old_ws)):
                    names[kind][addr] = e
        own = set()  # names the workspace's own names files define: they always win over an imported one
        for f in sorted(d.glob("*.json")) if d.exists() else []:
            if f.name != path.name:
                reg = json.loads(f.read_text(encoding="utf-8"))
                own |= {entry_name(k, e) for k in ("functions", "globals") for e in reg.get(k, {}).values()}
        for note in drop_clashes(names, own - {None}):
            print(note)
        kept = sum(e.get("imported_ws") not in (None, str(old_ws)) for t in names.values() for e in t.values())
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(names, indent=1) + "\n", encoding="utf-8")
        print(f"carried over {len(names['functions'])} function and {len(names['globals'])} global name(s)"
              + (f", {kept} of them kept from other projects" if kept else ""))


if __name__ == "__main__":
    main()
