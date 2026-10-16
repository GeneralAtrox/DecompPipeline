"""Classify a game's library functions as SDK, engine or unknown from FID results (tools/sdk_fid.py apply).

usage: python tools/sdk_classify.py --project WS --fid DIR [--out DIR] [--watch RX]

DIR holds tc_fid.tsv and graph.tsv (both written by `sdk_fid.py apply`). Writes, into --out (default DIR):
  classify.tsv          one row per library function (addr >= project.json game_end): class, reason, evidence
  engine_functions.txt  the candidate engine list (one address per line): not SDK, reached from the game's calls into
                        the library (callees and pointers, stopping at SDK functions), not a hardware function
  validate.tsv          every registry name of the SDK file (readable/names/SDK.json) that is not Lib_*, beside FID
  summary.json          the counts
Rules, in order (the reason column names the one that decided):
  1. fid: FID names it (score >= 14.6), unique or not: every candidate is an SDK function.
  2. object: no FID name, but the nearest FID-named functions before and after it come from the same object file
     (a linker places an object's functions together), or `library`: from two objects of the same SDK library.
  3. named: the SDK registry names it and it is not a Lib_* (designers' engine guess) name.
  4. callers: every function that calls it or takes its address is SDK (repeated until nothing changes).
  Then engine: reached from the game without passing an SDK function, and not hardware (catalog hw_refs/sys_ops);
  unknown: everything else (hardware ones reached from the game say so).
--watch RX: registry names to report one by one (e.g. the Lib_* functions next to Kamui's code).
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sdk_fid_lib as L  # noqa: E402
from sdk_fid import project_names  # noqa: E402

HEADER = ["addr", "our_name", "class", "rule", "reason", "fid_names", "library_object", "score", "status",
          "reached", "lib_reach", "hw", "bytes"]


def read_graph(path):
    """{addr: set(callees and pointer targets)} from TcGraph's TSV (addr, calls, fptrs, globals, strings)."""
    edges = {}
    for ln in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        p = ln.split("\t")
        if len(p) >= 3:
            edges[p[0]] = {x for x in (p[1].split(",") + p[2].split(",")) if x}
    return edges


def stems(lib_obj):
    """Object file stems of a library_object cell ("kamui2:/kamui2/km2api_.elf|...")."""
    return {pathlib.PurePath(x.split(":", 1)[-1]).stem for x in lib_obj.split("|") if x}


def families(lib_obj):
    return {re.sub(r"_d$", "", x.split(":", 1)[0]) for x in lib_obj.split("|") if x}


def classify(lib, fid, names, sdk_reg, edges, game, game_end):
    """{addr: (class, rule, reason)} for the library functions `lib` (sorted addresses)."""
    out = {}
    for a in lib:
        r = fid.get(a, {})
        if r.get("status") == "match":
            kind = {"yes": "unique", "best": "best of several"}.get(r.get("unique"), "tied")
            kind += f" ({r.get('candidates')} candidates)"
            out[a] = ("sdk", "fid", f"FID {kind}: {r['fid_names'].split('|')[0]} from {r['library_object']}")
    named = [a for a in lib if a in out and fid[a].get("unique") in ("yes", "best")]
    for a in lib:
        if a in out:
            continue
        before = [b for b in named if b < a]
        after = [b for b in named if b > a]
        if before and after:
            p, n = fid[before[-1]]["library_object"], fid[after[0]]["library_object"]
            common = stems(p) & stems(n)
            if common:
                out[a] = ("sdk", "object", f"between {before[-1]} and {after[0]}, both in object {sorted(common)[0]}")
            elif families(p) & families(n):
                fam = sorted(families(p) & families(n))[0]
                out[a] = ("sdk", "library", f"between {before[-1]} ({sorted(stems(p))[0]}) and {after[0]} "
                                            f"({sorted(stems(n))[0]}), both library {fam}")
    for a in lib:
        if a not in out and a in sdk_reg and not names.get(a, "").startswith("Lib_"):
            out[a] = ("sdk", "named", f"SDK registry name {names.get(a)}")
    callers = {}
    for src, dsts in edges.items():
        for d in dsts:
            callers.setdefault(d, set()).add(src)
    changed = True
    while changed:
        changed = False
        for a in lib:
            cs = callers.get(a, set())
            if a not in out and cs and all(out.get(c, ("",))[0] == "sdk" for c in cs):
                out[a] = ("sdk", "callers", f"only reached from SDK functions ({len(cs)}: {', '.join(sorted(cs)[:3])})")
                changed = True
    return out


def reach(seeds, edges, stop, lo):
    """Addresses >= lo reachable from seeds' edges, not entering (but listing) the `stop` set."""
    seen, todo = set(), [d for s in seeds for d in edges.get(s, ())]
    while todo:
        a = todo.pop()
        if a in seen or int(a, 16) < lo:
            continue
        seen.add(a)
        if a not in stop:
            todo.extend(edges.get(a, ()))
    return seen


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--fid", required=True)
    ap.add_argument("--out")
    ap.add_argument("--watch")
    a = ap.parse_args(argv)
    ws, fdir = pathlib.Path(a.project), pathlib.Path(a.fid)
    out = pathlib.Path(a.out or fdir)
    game_end = int(json.loads((ws / "project.json").read_text(encoding="utf-8"))["game_end"], 16)
    cat = {f["addr"]: f for f in json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]}
    names = project_names(ws)
    sdk_reg = set(json.loads((ws / "readable" / "names" / "SDK.json").read_text(encoding="utf-8"))["functions"])
    fid = {r["addr"]: r for r in L.read_tsv(fdir / "tc_fid.tsv")}
    edges = read_graph(fdir / "graph.tsv")
    lib = sorted(x for x in cat if int(x, 16) >= game_end)
    game = [x for x in cat if int(x, 16) < game_end]
    cls = classify(lib, fid, names, sdk_reg, edges, game, game_end)
    sdk = {x for x, c in cls.items() if c[0] == "sdk"}
    reached = reach(game, edges, sdk, game_end)
    named_sdk = {x for x in sdk_reg if not names.get(x, "").startswith("Lib_")}
    libs_ = [x for x in sdk_reg if names.get(x, "").startswith("Lib_")]
    lib_reach = {x for x in reach(libs_, edges, named_sdk, game_end) if x not in sdk_reg and x in cat}
    hw = {x for x in lib if cat[x]["hw_refs"] or cat[x]["sys_ops"]}
    for x in lib:
        if x in cls:
            continue
        if x in reached and x not in hw:
            cls[x] = ("engine", "reach", "reached from the game, no SDK evidence")
        elif x in reached:
            cls[x] = ("unknown", "hardware", "reached from the game, but a hardware function (hw_refs/sys_ops)")
        else:
            cls[x] = ("unknown", "unreached", "not reached from the game's calls, no SDK evidence")
    rows = []
    for x in lib:
        r = fid.get(x, {})
        rows.append({"addr": x, "our_name": names.get(x, ""), "class": cls[x][0], "rule": cls[x][1],
                     "reason": cls[x][2], "fid_names": r.get("fid_names", ""),
                     "library_object": r.get("library_object", ""), "score": r.get("score", ""),
                     "status": r.get("status", "absent"), "reached": "yes" if x in reached else "",
                     "lib_reach": "yes" if x in lib_reach else "", "hw": "yes" if x in hw else "",
                     "bytes": cat[x]["bytes"]})
    L.write_tsv(out / "classify.tsv", HEADER, rows)
    engine = [r for r in rows if r["class"] == "engine"]
    (out / "engine_functions.txt").write_text(
        "# candidate engine functions (tools/sdk_classify.py): not SDK, reached from the game, not hardware\n"
        + "".join(r["addr"] + "\n" for r in engine), encoding="utf-8")
    val = []
    for x in sorted(named_sdk):
        r = fid.get(x, {})
        ours, theirs = names.get(x, ""), r.get("fid_names", "")
        first = theirs.split("|")[0] if theirs else ""
        same = first and re.sub(r"_", "", first).lower() == re.sub(r"_", "", ours).lower()
        other = theirs and any(re.sub(r"_", "", t).lower() == re.sub(r"_", "", ours).lower()
                               for t in theirs.split("|"))
        verdict = "agree" if same else "agree_among" if other else "differ" if theirs else (
            "no_fid_" + r.get("status", "absent"))
        val.append({"addr": x, "our_name": ours, "fid_names": theirs, "verdict": verdict,
                    "library_object": r.get("library_object", ""), "score": r.get("score", "")})
    L.write_tsv(out / "validate.tsv", ["addr", "our_name", "fid_names", "verdict", "library_object", "score"], val)

    def count(it, key):
        c = {}
        for r in it:
            c[r[key]] = c.get(r[key], 0) + 1
        return dict(sorted(c.items()))
    summary = {
        "library_functions": len(rows), "class": count(rows, "class"), "rule": count(rows, "rule"),
        "engine_functions": len(engine), "engine_bytes": sum(r["bytes"] for r in engine),
        "validate": count(val, "verdict"),
        "lib_reach": {"functions": len(lib_reach), "class": count([r for r in rows if r["lib_reach"]], "class"),
                      "rule": count([r for r in rows if r["lib_reach"]], "rule")},
    }
    if a.watch:
        rx = re.compile(a.watch)
        summary["watch"] = [{k: r[k] for k in ("addr", "our_name", "class", "rule", "reason", "fid_names")}
                            for r in rows if rx.search(r["our_name"])]
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "watch"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
