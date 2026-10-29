"""Second entry points folded into another function's body (core/mid_entries.py): each needs an entry override.

usage: python tools/mid_entries.py --project DIR [--json FILE] [--merged] [--apply ADDR[,ADDR...]]

Static (the packets' listings, state/function_bodies.txt, the catalog and the binary); writes nothing in the
workspace but with --apply: the chosen addresses, each one listed here as needing an override (in or outside the
scope: a module's second entry, D2's PS2 8c188422), go into project.json catalog_overrides "entry" (key order,
indent and line endings kept, as tools/data_funcs.py --add); then `decomp.py catalog`. Lists, for the functions in the project's scope, every address something outside its function enters
(a call, a function pointer in a literal pool, a branch from another function, a table of functions), with the
evidence: each one needs project.json catalog_overrides "entry" (TcOverride makes the function at the next catalog
run; the port then has a host function there instead of returning 0). Counted apart: the same in code outside the
scope (libraries the port kit replaces), and "merged bodies" (a body range starting with its own prologue, reached
only from its function: --merged lists them; no override needed while nothing else enters them).
"""
import argparse
import json
import pathlib
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project, write_project_json  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--json")
    ap.add_argument("--merged", action="store_true", help="also list the merged bodies")
    ap.add_argument("--apply", help="listed addresses that go into catalog_overrides entry")
    a = ap.parse_args()
    use_project(a.project)
    from core import mid_entries, paths
    cfg = paths.config()
    starts = [int(f["addr"], 16) for f in paths.read_json(paths.CATALOG)["functions"]]
    bodies = mid_entries.Bodies(paths.FUNCTION_BODIES.read_text(encoding="utf-8"), starts)
    over = cfg.get("catalog_overrides") or {}
    decided = set(over.get("entry", [])) | set(over.get("not_entry", []))
    gaps = [tuple(int(x, 16) for x in g.split("-")) for g in cfg.get("image_gaps", [])]
    hits = mid_entries.find(mid_entries.listings_of(paths.PACKETS), bodies, cfg["binary_path"].read_bytes(),
                            int(str(cfg["load_address"]), 16), decided, gaps)
    sc = paths.scope()
    rows = [{"addr": f"{x:08x}", "in": f"{bodies.owner(x):08x}", "evidence": ev,
             "kind": ("needs override" if mid_entries.needs_override(ev) else "merged body"),
             "in_scope": f"{bodies.owner(x):08x}" in sc} for x, ev in hits.items()]
    need = [r for r in rows if r["kind"] == "needs override" and r["in_scope"]]
    print(f"{len(need)} need an entry override; {sum(r['kind'] == 'needs override' and not r['in_scope'] for r in rows)}"
          f" more outside the scope; {sum(r['kind'] == 'merged body' for r in rows)} merged bodies")
    for r in need + ([r for r in rows if r["kind"] == "merged body"] if a.merged else []):
        more = f" (+{len(r['evidence']) - 2} more)" if len(r["evidence"]) > 2 else ""
        print(f"  {r['addr']} in {r['in']} [{r['kind']}]: {'; '.join(r['evidence'][:2])}{more}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if a.apply:
        want = [paths.norm_addr(x) for x in a.apply.split(",")]
        listed = {r["addr"] for r in rows if r["kind"] == "needs override"}
        if set(want) - listed - decided:
            print(f"not listed as needing an override: {', '.join(sorted(set(want) - listed - decided))}")
            return 1
        pj = paths.ROOT / "project.json"
        raw = pj.read_bytes().decode("utf-8")
        data = json.loads(raw)
        entries = data.setdefault("catalog_overrides", {}).setdefault("entry", [])
        new = [x for x in want if x not in entries]
        entries += new
        write_project_json(pj, raw, data)
        print(f"project.json: {len(new)} catalog_overrides entr(ies) added; now run `decomp.py --project DIR catalog`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
