"""Port readiness map: walk the original's call tree from an entry and say what each function still needs.

    py -3.12 tools/port_map.py tree   --ws WORKSPACE [--port DIR] [ENTRY] [--list STATES] [--out FILE] [--direct-only]
    py -3.12 tools/port_map.py native --ws WORKSPACE --port DIR [--out FILE]
    py -3.12 tools/port_map.py drift  --ws WORKSPACE --port DIR [--baseline FILE] [--out FILE]
    py -3.12 tools/port_map.py record --ws WORKSPACE --port DIR --baseline FILE
    py -3.12 tools/port_map.py judge  --ws WORKSPACE ADDR...

ENTRY is an address or a registry name (default: game.json `entry`). DIR is the folder with game.json. --kit DIR
overrides the kit's folder (default: this repository's port/). States and how edges are read: core/port_map.py;
bindings, citations and drift: core/port_map_native.py.

Every command reads only, except that `record` writes its --baseline file. `judge` is the one that catches a forged
ledger row: it judges readable/src/ADDR.c and matched/ADDR.c again on a temporary copy of what the judge reads, so no
ledger or cache of the workspace is written. It builds, so a long list goes through tools/jobreg.py.

Nearest existing: core/final_audit.py and tools/doctor.py (whole-game verdicts, not per entry, and no native code) and
port/tools/gen_port.py (the build's view of replacements, no states) - this joins them per function from an entry.
"""
import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core import port_map as pm  # noqa: E402
from core import port_map_native as pn  # noqa: E402


def write(data, out):
    if out:
        pathlib.Path(out).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"written: {out}")


def print_tree(ws, tree, wanted):
    print(f"{ws.project.get('name')}: {tree['entry']} {tree['name'] or ''}: {tree['summary']['functions']} functions")
    for state in pm.STATES:
        s = tree["summary"]["states"][state]
        print(f"  {state:15} {s['functions']:6} functions {s['bytes']:9} bytes")
    print(f"  reached by: {tree['summary']['reached']}")
    print(f"  flags: {tree['summary']['flags']}")
    c = tree["completeness"]
    print(f"NOT COMPLETE: {c['why']}. Indirect call sites in the tree: {c['indirect_call_sites']} in "
          f"{len(c['functions_with_indirect_calls'])} functions; jump tables: {c['jump_tables']}; "
          f"stopped at kit or native code: {c['cut_at_replacement']}; stopped at depth: {c['cut_at_depth']}")
    print(f"Edge sources disagree: {c['edge_gaps'] or 'nowhere'}")
    for r in tree["rows"]:
        if r["state"] in wanted:
            print(f"{r['depth']:3} {r['addr']} {r['state']:14} {r['name'] or '':32} {r['why']}"
                  + (f" [{', '.join(r['flags'])}]" if r["flags"] else "") + (f" ({r['reached']} from {r['via']})" if r["via"] else ""))


def print_native(table, binds):
    print(f"{len(table)} bindings of {len({r['addr'] for r in table})} addresses")
    for r in table:
        print(f"{r['addr']} {r['fn']:32} {r['kind']:9} {r['file']}:{r['line']} decomp {r['decomp']}"
              + ("" if r["defined"] else " NOT DEFINED") + ("" if r["built"] else " NOT BUILT"))
    only = binds.citation_only()
    print(f"\nOnly a citation, no binding ({len({c['addr'] for c in only})} addresses, {len(only)} mentions; "
          "a mention is not a proof):")
    for c in only:
        print(f"{c['addr']} {c['file']}:{c['line']} {c['text']}")
    print(f"\nNative functions with no address ({len(binds.unbound)}): {' '.join(binds.unbound[:40])}")
    if binds.not_built:
        print(f"Source files game.json does not build: {' '.join(binds.not_built)}")


def print_drift(d):
    print(f"{d['bound']} bound addresses; baseline: {d['baseline'] or 'none, using git'}")
    print(f"MOVED under native code: {len(d['moved'])}")
    for m in d["moved"]:
        print(f"  {m['addr']} {m['name'] or ''} ({', '.join(m['native'])}; by {m['by']}): "
              + "; ".join(f"{c['what']} {c['was']} -> {c['now']}" for c in m["changes"]))
    print(f"No raw or readable C here, nothing to move: {len(d['no_decomp'])}")
    print(f"No baseline, so not known: {len(d['no_baseline'])}")
    for m in d["no_baseline"]:
        print(f"  {m['addr']} {m['name'] or ''} ({', '.join(m['native'])}): {m['why']}")
    if d["native_changed"]:
        print(f"Native code changed since the baseline: {d['native_changed']}")
    if d["copy_differs"]:
        print(f"The port's decomp copy differs from this workspace: {len(d['copy_differs'])}: "
              + " ".join(f"{c['addr']}({c['what']})" for c in d["copy_differs"][:30]))


def private_copy(ws_path, addrs, dest):
    """What the judge reads, in a folder of its own: it keeps caches under its project (build/, state/fsca), and a read
    of another card's workspace must not write there."""
    src, dest = pathlib.Path(ws_path).resolve(), pathlib.Path(dest)
    (dest / "state").mkdir(parents=True)
    proj = json.loads((src / "project.json").read_text(encoding="utf-8"))
    for key, value in proj.items():  # the game's image and its like stay where they are, read only
        if isinstance(value, str) and value and (src / value).exists():
            proj[key] = str((src / value).resolve())
    (dest / "project.json").write_text(json.dumps(proj), encoding="utf-8")
    for path in (src / "state").iterdir():
        if path.is_file() and path.suffix != ".lock":
            shutil.copy2(path, dest / "state" / path.name)
    for rel in ("state/fsca", "readable/names", "readable/include"):
        if (src / rel).is_dir():
            shutil.copytree(src / rel, dest / rel, ignore=shutil.ignore_patterns("*.lock"))
    (dest / "readable" / "src").mkdir(parents=True, exist_ok=True)
    (dest / "matched").mkdir()
    for addr in addrs:
        for rel in (f"matched/{addr}.c", f"readable/src/{addr}.c"):
            if (src / rel).is_file():
                shutil.copy2(src / rel, dest / rel)
        if (src / "harness" / addr).is_dir():
            shutil.copytree(src / "harness" / addr, dest / "harness" / addr)


def judge(ws_path, addrs):
    """Judge raw and readable C again on a temporary copy, so no ledger or cache of the workspace is written. {addr: verdicts}."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="port_map_judge_"))
    private_copy(ws_path, addrs, tmp / "ws")
    os.environ["DECOMP_PROJECT"] = str(tmp / "ws")
    from core import check, paths, readable, readable_check  # noqa: PLC0415  (they read the project at import)
    cfg, reg = paths.config(), readable.Registry()
    out = {}
    try:
        for addr in addrs:
            row = {}
            raw = paths.MATCHED / f"{addr}.c"
            if raw.is_file():
                row["raw"] = check.run_check(cfg, addr, str(raw), build_dir=tmp / addr / "raw").get("verdict")
            else:
                row["raw"] = "NO FILE"
            src = readable.SRC / f"{addr}.c"
            if src.is_file():
                row["readable"] = readable_check.check_readable(cfg, addr, str(src), tmp / addr / "rd", reg=reg).get("verdict")
            else:
                row["readable"] = "NO FILE"
            out[addr] = row
            print(f"{addr} raw {row['raw']} readable {row['readable']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("tree", "native", "drift", "record", "judge"))
    ap.add_argument("targets", nargs="*", help="tree: the entry; judge: addresses")
    ap.add_argument("--ws", required=True)
    ap.add_argument("--port", default=None)
    ap.add_argument("--kit", default=None)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--list", default="needs_raw,needs_readable,outside", help="states to list (comma-separated, or all)")
    ap.add_argument("--direct-only", action="store_true", help="tree: follow direct calls only, no pointer references")
    ap.add_argument("--max-depth", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.command == "judge":
        results = judge(a.ws, a.targets)
        write(results, a.out)
        return 0 if all(r == {"raw": "PASS", "readable": "PASS"} for r in results.values()) else 1
    ws = pm.Workspace(a.ws)
    binds = pn.Bindings(ws, a.port, a.kit)
    if a.command == "tree":
        entry = a.targets[0] if a.targets else (binds.game.get("entry") or {}).get("addr")
        if not entry:
            ap.error("give an entry: no --port with a game.json entry")
        tree = pm.walk(ws, entry, binds, pointers=not a.direct_only, max_depth=a.max_depth)
        print_tree(ws, tree, set(pm.STATES) if a.list == "all" else set(a.list.split(",")))
        write(tree, a.out)
        return 0
    if not a.port:
        ap.error(f"{a.command} needs --port")
    if a.command == "native":
        table = pn.native_table(ws, binds)
        print_native(table, binds)
        write({"bindings": table, "citation_only": binds.citation_only(), "unbound": binds.unbound}, a.out)
        return 0
    if a.command == "record":
        if not a.baseline:
            ap.error("record needs --baseline")
        print(f"recorded {pn.record(ws, binds, a.baseline)} addresses in {a.baseline}")
        return 0
    d = pn.drift(ws, binds, a.baseline)
    print_drift(d)
    write(d, a.out)
    return 1 if d["moved"] else 0


if __name__ == "__main__":
    sys.exit(main())
