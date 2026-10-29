"""Code addresses that function code uses as values but that are no function (core/data_funcs.py), checked by Ghidra.

usage: python tools/data_funcs.py --project DIR [--ghidra-project DIR] [--no-ghidra] [--json FILE]
       python tools/data_funcs.py --project DIR --add @FILE
  (list)   candidates from the packets' listings, then a read-only Ghidra check of each (TcEntryCheck.java on the
           project's master Ghidra project, or --ghidra-project: a copy, e.g. tests/ghidra_work/<key>/master): valid
           (decodes as a subroutine that terminates), invalid, data, code (decoded, no function), inside a function.
           Writes nothing in the workspace. Servers stopped (Ghidra locks the project it opens, even read-only).
  --add    the coordinator's choice, checked first by TcEntryCheck (nothing is written when one address is not
           "valid" or "code": it does not disassemble as a subroutine, is data or is already a function): each
           listed address goes into project.json "catalog_overrides" "entry" (TcOverride creates the function at the
           next catalog run) and, outside the game ranges, into "extra_functions" (a list, or the file it names).
           Then, servers stopped: `decomp.py --project DIR catalog`, tools/assign_systems.py, and prepare the new
           functions.
"""
import argparse
import json
import pathlib
import sys
import tempfile

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import expand_addrs, use_project, write_project_json  # noqa: E402


def ghidra_check(paths, ghidra, cfg, addrs, project):
    """{addr: (verdict, detail)} from TcEntryCheck.java, read-only."""
    with tempfile.TemporaryDirectory() as td:
        inp, out = pathlib.Path(td) / "in.txt", pathlib.Path(td) / "out.tsv"
        inp.write_text("".join(f"{a}\n" for a in addrs), encoding="utf-8")
        ghidra._headless(cfg, ["-process", paths.config()["binary_path"].name, "-noanalysis", "-readOnly",
                               "-scriptPath", str(TOOL / "tools" / "ghidra_tools"), "-postScript", "TcEntryCheck.java",
                               str(inp), str(out)], "ghidra_entry_check.log", project=project)
        rows = {}
        for line in out.read_text(encoding="utf-8").splitlines():
            t = line.split("\t")
            rows[t[0]] = (t[1], t[2] if len(t) > 2 else "")
        return rows


def add(paths, addrs):
    """Write the chosen entries into project.json (key order, indent and line endings kept)."""
    pj = paths.ROOT / "project.json"
    raw = pj.read_bytes().decode("utf-8")
    data = json.loads(raw)
    over = data.setdefault("catalog_overrides", {})
    entries = over.setdefault("entry", [])
    sc = paths.scope()
    extra, new_extra = data.get("extra_functions"), []
    for a in addrs:
        if a not in entries:
            entries.append(a)
        if not sc.in_ranges(a) and a not in sc.extras:
            new_extra.append(a)
    if isinstance(extra, str) and new_extra:
        f = paths.ROOT / extra
        f.write_text(f.read_text(encoding="utf-8").rstrip("\n") + "\n" + "".join(f"{a}\n" for a in new_extra),
                     encoding="utf-8")
    elif new_extra:
        data["extra_functions"] = (extra or []) + new_extra
    write_project_json(pj, raw, data)
    print(f"project.json: {len(addrs)} catalog_overrides entr(ies), {len(new_extra)} new extra_functions; now run "
          "`decomp.py --project DIR catalog`, tools/assign_systems.py and prepare them (servers stopped)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--ghidra-project")
    ap.add_argument("--no-ghidra", action="store_true")
    ap.add_argument("--add")
    ap.add_argument("--json")
    a = ap.parse_args()
    use_project(a.project)
    from core import data_funcs, ghidra, paths
    cfg = paths.config()
    if a.add:
        addrs = expand_addrs([a.add])
        gp = pathlib.Path(a.ghidra_project) if a.ghidra_project else None
        bad = data_funcs.refused(ghidra_check(paths, ghidra, cfg, addrs, gp), addrs)
        for x, why in bad:
            print(f"refused {x}: {why} (only an address that disassembles as a subroutine is added)")
        if bad:
            return 1
        add(paths, addrs)
        return 0
    lo = int(str(cfg["load_address"]), 16)
    hi = lo + cfg["binary_path"].stat().st_size
    funcs = {int(f["addr"], 16) for f in paths.read_json(paths.CATALOG)["functions"]}
    cands = data_funcs.candidates(paths.PACKETS, lo, hi, data_funcs.bodies(paths.FUNCTION_BODIES), funcs)
    checked = {} if a.no_ghidra or not cands else ghidra_check(
        paths, ghidra, cfg, sorted(cands), pathlib.Path(a.ghidra_project) if a.ghidra_project else None)
    rows = []
    for x in sorted(cands):
        v, d = checked.get(x, ("unchecked", ""))
        rows.append({"addr": x, "verdict": v, "detail": d, "users": cands[x]})
    by = {}
    for r in rows:
        by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    print(f"{len(rows)} value-used addresses outside every function body: "
          + ", ".join(f"{k} {n}" for k, n in sorted(by.items())))
    for r in rows:
        if r["verdict"] in ("valid", "code", "unchecked"):
            users = ", ".join(f"{u}@{at}" for u, at in r["users"][:3]) + (" ..." if len(r["users"]) > 3 else "")
            print(f"  {r['addr']} {r['verdict']}: {r['detail']} (used by {users})")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
