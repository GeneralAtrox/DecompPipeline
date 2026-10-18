"""DecompPipeline: behaviour-checked function decompilation with locked-down LLM agents.

Every command works on one project workspace (a folder with project.json):
  python decomp.py --project DIR setup [--force]      build the Ghidra project + catalog (once)
  python decomp.py --project DIR catalog              re-run the function catalog on the existing project
  python decomp.py --project DIR seed                 find more functions (TcSeed) in the existing project + catalog
      setup, catalog and seed then retire the artefacts of every address that is no longer a function (tools/retire.py)
  python decomp.py --project DIR serve                start the monitor UI
  python decomp.py --project DIR prepare ADDR         export packet + vectors + oracle for one function
  python decomp.py --project DIR check ADDR --work F  official verdict for a C file (dev + hidden)
  python decomp.py --project DIR investigate [ADDR]   run one investigation without the UI
  python decomp.py --project DIR systems [--graph] [--extend]  group functions into systems (--graph: re-export; --extend: keep ids)
  python decomp.py --project DIR export OUT ADDR...   disassembly + pseudo-C of any functions (libraries too)
  python decomp.py --project DIR sizes                fail if any pipeline or project source file exceeds 400 lines
  python decomp.py --project DIR audit [--project DIR ...] [--verbose] [--json]
                                                      unmatched functions by blocker kind (exit 2: UNDECLARED kinds)
--project may be omitted when $DECOMP_PROJECT is set or the current folder holds project.json.
"""
import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def retire_removed(ws, note):
    """After a catalog change: move the artefacts of every address that is no longer a function to retired/
    (tools/retire.py). Refused while a writer may run; completeness.py --check and the server say so until done."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "tools"))
    import retire as retire_tool  # tools/retire.py
    refused = retire_tool.run(ws, note=note)[2]
    if refused:
        print(f"NOT RETIRED: {refused}. Run  python tools/retire.py --project {ws}  once idle; the project is not "
              "complete until then (completeness.py --check fails).")
    from core import sysassign
    loose = sysassign.unassigned(ws)[0]
    if loose:  # new functions: placed by hand-checked rules, never silently (designs refer to the systems)
        print(f"{len(loose)} game function(s) in no system: python tools/assign_systems.py --project {ws} --dry, "
              "then without --dry (completeness.py --check fails until then)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="game workspace: a folder with project.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--force", action="store_true")
    sub.add_parser("catalog")
    sub.add_parser("seed")
    sub.add_parser("serve")
    p = sub.add_parser("prepare")
    p.add_argument("addr")
    c = sub.add_parser("check")
    c.add_argument("addr")
    c.add_argument("--work", required=True)
    c.add_argument("--agent", action="store_true", help="dev set only, agent-facing output")
    c.add_argument("--json", action="store_true")
    sub.add_parser("sizes")
    ex = sub.add_parser("export")
    ex.add_argument("out")
    ex.add_argument("addrs", nargs="+")
    sy = sub.add_parser("systems")
    sy.add_argument("--graph", action="store_true")
    sy.add_argument("--extend", action="store_true", help="add new functions to the existing systems (ids kept)")
    i = sub.add_parser("investigate")
    i.add_argument("addr", nargs="?")
    au = sub.add_parser("audit", help="unmatched functions by blocker kind (target.json blocker_kinds)")
    au.add_argument("--project", dest="audit_projects", action="append", default=[],
                    help="more workspaces for one combined table (repeatable)")
    au.add_argument("--json", action="store_true")
    au.add_argument("--verbose", action="store_true", help="list the addresses of every kind")
    a = ap.parse_args()
    if a.cmd == "audit":  # read-only; several workspaces at once
        wss = [str(pathlib.Path(p).resolve()) for p in ([a.project] if a.project else []) + a.audit_projects]
        if wss:
            os.environ["DECOMP_PROJECT"] = wss[0]
        from core import audit, paths
        sys.exit(audit.main(wss or [str(paths.ROOT)], as_json=a.json, verbose=a.verbose))
    if a.project:
        os.environ["DECOMP_PROJECT"] = str(pathlib.Path(a.project).resolve())
    from core import check, ghidra, paths  # paths resolves the project at import
    cfg = paths.config()

    if a.cmd in ("setup", "catalog", "seed"):
        before = {f["addr"] for f in (paths.read_json(paths.CATALOG) or {}).get("functions", [])}
        if a.cmd == "setup":
            ghidra.setup(cfg, force=a.force)
        else:
            (ghidra.catalog if a.cmd == "catalog" else ghidra.seed)(cfg)
        cat = paths.read_json(paths.CATALOG)
        after = {f["addr"] for f in cat["functions"]}
        print(f"catalog: {len(after)} functions ({len(after - before)} new, {len(before - after)} gone)"
              + (f"; FPSCR loads: {cat['fpscr_loads'][:12]}" if a.cmd == "setup" else ""))
        retire_removed(paths.ROOT, f"decomp.py {a.cmd}: no longer in the catalog")
    elif a.cmd == "serve":
        from core import orch, server
        why = server.port_taken(cfg["port"], cfg.get("name"))
        if why:
            sys.exit(f"serve refused: {why}")
        server.serve(orch.Orchestrator(), cfg["port"])
    elif a.cmd == "prepare":
        meta, summary = ghidra.prepare(cfg, paths.norm_addr(a.addr), on_line=None)
        print(json.dumps({"signature": meta["signature"], "unsupported": meta["unsupported"], "oracle": summary}, indent=2))
        if meta.get("stale_callers"):  # ghidra.prepare: the signature changed, so the callers' records are stale
            print(f"the signature changed: {len(meta['stale_callers'])} caller harness(es) now record the old one; "
                  f"re-prepare them (decomp.py prepare ADDR each, or tools/reprepare_stale.py, which repeats until nothing "
                  f"changes): {' '.join(meta['stale_callers'])}")
    elif a.cmd == "check":
        addr = paths.norm_addr(a.addr)
        if a.agent:
            res = check.run_check(cfg, addr, a.work, sets=("dev",), build_dir=paths.BUILD / addr / "agent")
            print(check.format_for_agent(res))
            sys.exit(0 if res["verdict"] == "PASS" else 1)
        res = check.run_check(cfg, addr, a.work, sets=("dev", "hidden"), build_dir=paths.BUILD / addr / "manual")
        print(check.dumps(res) if a.json else f"{res['verdict']}  stage={res['stage']}  failing={res['failing']}/"
              f"{res['total']}\n" + check.format_for_agent({**res, "verdict": res["verdict"]}))
        sys.exit(0 if res["verdict"] == "PASS" else 1)
    elif a.cmd == "systems":
        from core import orch, systems
        if a.graph or not systems.GRAPH.exists():
            ghidra.graph(cfg)
        o = orch.Orchestrator()
        game = {x: f for x, f in o.functions.items() if x not in o.extras}  # extras: their own systems (sysassign)
        if a.extend:
            built, n = systems.extend(game)
            print(f"{n} new function(s) added to existing systems")
        else:
            print(systems.dumps_debug(systems.build(game)))
        if o.extras:
            print(f"{len(o.extras)} extra function(s) (project.json extra_functions) get systems of their own: python "
                  f"tools/assign_systems.py --project {paths.ROOT} --dry, then without --dry")
    elif a.cmd == "export":
        ghidra.export(cfg, [paths.norm_addr(x) for x in a.addrs], pathlib.Path(a.out).resolve())
    elif a.cmd == "sizes":
        exts = {".py", ".java", ".c", ".h", ".html", ".md"}
        dirs = [paths.TOOL, paths.REFERENCE, paths.ROOT / "oracle"]
        scratch = (paths.TOOL / "tests" / ".work", paths.TOOL / "tests" / "ghidra_work")  # regress.py work dirs
        files = [p for d in dirs for p in d.rglob("*") if p.suffix in exts and not any(p.is_relative_to(s) for s in scratch)]
        files += list(paths.ROOT.glob("*.md"))
        over = [(n, p) for p in files if (n := len(p.read_text(encoding="utf-8").splitlines())) > 400]
        for n, p in sorted(over, reverse=True):
            print(f"{n:5d}  {paths.show(p)}")
        print("all files <= 400 lines" if not over else f"{len(over)} file(s) over 400 lines")
        sys.exit(1 if over else 0)
    elif a.cmd == "investigate":
        from core import orch
        o = orch.Orchestrator()
        o.log = lambda m: print(m, flush=True)
        o.run_one(paths.norm_addr(a.addr) if a.addr else None)


if __name__ == "__main__":
    main()
