"""Reuse an earlier project's work in a new one (same CPU, same developer or shared libraries).

  python xproject.py --project NEW --from OLD match     byte-match functions (addresses ignored) -> state/import.json
  python xproject.py --project NEW --from OLD report    counts: exact/near x game/extra/library
  python xproject.py --project NEW --from OLD stage [--near R]
        write OLD's matched C, relocated to NEW's addresses, to NEW/import/<addr>.c for exact matches (and near
        matches with ratio >= R) of functions NEW decompiles (game ranges + extra_functions,
        core/scope.py). Workers judge an imported file first: a pass is matched with no agent run, a fail
        becomes the agent's starting point.
  python xproject.py --project NEW --from OLD names     carry readable names (functions, globals) over to
        NEW/readable/names/imported.json, through the address pairs of exact matches
"""
import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from core import xmatch  # noqa: E402

STACK_LO = 0x8CFF0000
ADDR_RE = re.compile(r"0x(8c[0-9a-fA-F]{6})(u?)\b")


def relocate(text, amap, own_old, own_new):
    """Rewrite OLD guest addresses in C source to NEW ones. Returns (text, unmapped addresses)."""
    missing = set()

    def sub(m):
        a = m.group(1).lower()
        if int(a, 16) >= STACK_LO:
            return m.group(0)
        if a in amap:
            return f"0x{amap[a]}{m.group(2)}"
        missing.add(a)
        return m.group(0)
    text = ADDR_RE.sub(sub, text)
    text = re.sub(rf"\bfn_{own_old}\b", f"fn_{own_new}", text, flags=re.I)
    return text, sorted(missing)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--from", dest="old", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("match")
    sub.add_parser("report")
    st = sub.add_parser("stage")
    st.add_argument("--near", type=float, default=0.85)
    sub.add_parser("names")
    a = ap.parse_args()
    new_ws, old_ws = pathlib.Path(a.project).resolve(), pathlib.Path(a.old).resolve()
    out = new_ws / "state" / "import.json"

    if a.cmd == "match":
        old, new = xmatch.Image(old_ws), xmatch.Image(new_ws)
        matches = xmatch.match(old, new)
        amap = xmatch.address_map(matches)
        out.write_text(json.dumps({"from": str(old_ws), "matches": matches, "address_map": amap}, indent=0),
                       encoding="utf-8")
        print(f"{len(matches)} matched functions, {len(amap)} address pairs -> {out}")
        return
    data = json.loads(out.read_text(encoding="utf-8"))
    matches, amap = data["matches"], data["address_map"]
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
        staged = clean = 0
        for addr, m in sorted(matches.items()):
            src = old_ws / "matched" / f"{m['from']}.c"
            if not new.in_scope(addr) or not src.exists():
                continue
            if m["kind"] != "exact" and m["ratio"] < a.near:
                continue
            text, missing = relocate(src.read_text(encoding="utf-8"), amap, m["from"], addr)
            head = f"/* imported from {old_ws.parent.name} {m['from']} ({m['kind']}, {m['ratio']}) */\n"
            if missing:
                head += f"/* unmapped addresses of the old project: {' '.join(missing)} */\n"
            (dst / f"{addr}.c").write_text(head + text, encoding="utf-8")
            staged += 1
            clean += not missing
        print(f"staged {staged} file(s) in {dst} ({clean} with every address relocated)")
    elif a.cmd == "names":
        names = {"functions": {}, "globals": {}}
        for f in sorted((old_ws / "readable" / "names").glob("*.json")):
            reg = json.loads(f.read_text(encoding="utf-8"))
            for kind in ("functions", "globals"):
                for addr, e in reg.get(kind, {}).items():
                    if addr in amap:
                        names[kind][amap[addr]] = dict(e, imported_from=addr)
        d = new_ws / "readable" / "names"
        d.mkdir(parents=True, exist_ok=True)
        (d / "imported.json").write_text(json.dumps(names, indent=1) + "\n", encoding="utf-8")
        print(f"carried over {len(names['functions'])} function and {len(names['globals'])} global name(s)")


if __name__ == "__main__":
    main()
