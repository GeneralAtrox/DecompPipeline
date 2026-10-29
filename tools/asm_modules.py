"""Hand-written asm modules (core/asm_modules.py): local subroutines that pass state in registers outside the C ABI.

usage: python tools/asm_modules.py --project DIR [--json FILE] [--all] [--apply ENTRY[,ENTRY...]]

Static (the catalog, state/function_bodies.txt and the binary); writes nothing in the workspace. Lists each module
(its public entries, its local pieces and the evidence for each piece): the local pieces need project.json
catalog_overrides "not_entry" (the unit's body then holds them: the oracle runs them, the translator returns from
them through PR). --all also lists the locals that hand back only r4..r7 (TcOut's class) and the plain private
callees, which need nothing. --apply writes the local pieces of the modules with those entries into project.json
"catalog_overrides" "not_entry" (key order, indent and line endings kept, as tools/data_funcs.py --add; a piece
leaves "entry" and "extra_functions", where it was no longer a function); then
`decomp.py catalog`, tools/assign_systems.py and a prepare of the entries. An entry with no catalog function (a
second entry into a piece) goes in with data_funcs.py --add.

Nearest existing: tools/mid_entries.py - it lists and applies catalog_overrides "entry" (functions to add), not the
"not_entry" pieces of a module.
"""
import argparse
import json
import pathlib
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project, write_project_json  # noqa: E402


def drop_pieces(data, root, pieces):
    """A piece is no function: an "entry" override for it would win (TcOverride applies entry last; Spawn's
    8c0602aa, made an entry for a Ghidra delay-slot gap), and an extra_functions line naming it is a doctor FAIL
    (core/scope). Both leave project.json data (and the extra_functions file it names, under root) with the piece.
    Returns ([out of entry], [out of extra_functions])."""
    over = data.get("catalog_overrides") or {}
    gone = [x for x in over.get("entry", []) if x in pieces]
    if gone:
        over["entry"] = [x for x in over["entry"] if x not in pieces]
    extra, dropped = data.get("extra_functions"), []
    if isinstance(extra, list):
        dropped = [x for x in extra if x in pieces]
        data["extra_functions"] = [x for x in extra if x not in pieces]
    elif isinstance(extra, str):
        f = pathlib.Path(root) / extra
        lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
        keep = [ln for ln in lines if (ln.split() or [""])[0].lower() not in pieces]
        dropped = [ln.split()[0] for ln in lines if ln not in keep]
        if dropped:
            f.write_text("".join(keep), encoding="utf-8")
    return gone, dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--json")
    ap.add_argument("--all", action="store_true", help="also list TcOut-class locals and plain private callees")
    ap.add_argument("--apply", help="entries whose modules' local pieces go into catalog_overrides not_entry")
    a = ap.parse_args()
    use_project(a.project)
    from core import asm_modules, mid_entries, paths
    cfg = paths.config()
    starts = [int(f["addr"], 16) for f in paths.read_json(paths.CATALOG)["functions"]]
    bodies = mid_entries.Bodies(paths.FUNCTION_BODIES.read_text(encoding="utf-8"), starts)
    got = asm_modules.find(cfg["binary_path"].read_bytes(), int(str(cfg["load_address"]), 16), bodies, starts)
    over = set((cfg.get("catalog_overrides") or {}).get("not_entry", []))
    print(f"{len(got['modules'])} module(s); {len(got['tcout'])} local(s) handing back r4..r7 only (TcOut); "
          f"{len(got['plain'])} plain private callee(s)")
    for m in got["modules"]:
        todo = [x for x in m["locals"] if x not in over]
        print(f"  module {', '.join(m['entries'])}: {len(m['locals'])} local piece(s), {len(todo)} not yet not_entry")
        for x in m["locals"]:
            ev = m["evidence"][x]
            more = f" (+{len(ev) - 3} more)" if len(ev) > 3 else ""
            print(f"    {x}: {'; '.join(ev[:3])}{more}")
    if a.all:
        for t in got["tcout"]:
            print(f"  r4..r7 only {t['addr']}: {'; '.join(t['evidence'][:2])}")
        print(f"  plain private callees: {len(got['plain'])} (in --json)")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(got, indent=1), encoding="utf-8")
    if a.apply:
        want = {paths.norm_addr(x) for x in a.apply.split(",")}
        mods = [m for m in got["modules"] if want & set(m["entries"])]
        if {e for m in mods for e in m["entries"]} & want != want:
            print(f"not a module entry: {', '.join(sorted(want - {e for m in mods for e in m['entries']}))}")
            return 1
        pj = paths.ROOT / "project.json"
        raw = pj.read_bytes().decode("utf-8")
        data = json.loads(raw)
        over = data.setdefault("catalog_overrides", {})
        ne = over.setdefault("not_entry", [])
        new = [x for m in mods for x in m["locals"] if x not in ne]
        ne += new
        gone, dropped = drop_pieces(data, paths.ROOT, {x for m in mods for x in m["locals"]})
        write_project_json(pj, raw, data)
        print(f"project.json: {len(new)} catalog_overrides not_entry added"
              + (f"; out of entry: {', '.join(gone)}" if gone else "")
              + (f"; out of extra_functions: {len(dropped)}" if dropped else "")
              + "; now run `decomp.py --project DIR catalog`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
