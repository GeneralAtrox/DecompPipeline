"""Measure DP's GameCube target against a finished public decompilation (test truth only).

A matched decomp (pikmin, melee, ac-decomp, pikmin2, tp) is a measuring stick: its decomp-toolkit config gives
every function's address and size, so DP's catalog, translator and readable step can be scored against a known
right answer. Clean room: this tool only reads the decomp's config/ (config.yml, symbols.txt, splits.txt,
build.sha1) and writes reports with addresses and numbers. It never copies the decomp's names, types or C into DP,
never prints a name, and refuses to write a report inside the DP repository or the game workspace, so no agent
can read one as a hint (CLEANROOM.md beside each clone).

usage:
  python tools/gc_truth.py summary --decomp DIR [--version V] [--out FILE.json]
      Each version: the DOL's SHA-1, its REL modules, functions in main.dol and in RELs, and the share of
      functions in C units, C++ units and unsplit code (main.dol and RELs apart).
  python tools/gc_truth.py check-disc --decomp DIR FILE
      FILE is a disc image (.iso/.gcm, uncompressed) or a main.dol: its DOL's SHA-1 against every version's hash.
  python tools/gc_truth.py score --decomp DIR --version V --project WS --out DIR [--verdicts FILE] [--link FILE]
      Scores the workspace against the decomp's functions, main.dol's and (with a link record) the REL modules':
      - catalog: each truth function is exact (same entry, same end), split (2+ DP entries inside it), merged
        (one DP body covers its entry and another truth entry), boundary_differs (one DP function overlaps it,
        with another start or end) or missed; DP functions starting outside every truth function are extra;
      - translator: of the functions the judge saw a translated attempt for (ledger attempts with model
        "translated", or FILE: "ADDR PASS|FAIL" per line from a standalone run), how many passed;
      - naming: readable/names/*.json prototypes against the decomp's names, as token agreement scores only.
      REL modules: a workspace set up on gcdisc's linked.dol (targets/gamecube_ppc/tools/gcrel.py) has link.json
      beside its binary (or give --link): each REL truth function, (module, section, offset), is placed at its
      linked address and scored with main.dol's, per module apart. A module that is not linked, or whose code
      sections do not line up, is counted, not scored, with the reason.
      Writes OUT/score.json and OUT/score.md. Exit 0.

Code: the decomp's config in tools/gc_truth_decomp.py, DP's side and the scores in tools/gc_truth_dp.py.

Nearest existing: tools/regress_project.py and targets/gamecube_ppc/translator/tools/regress.py - they compare DP
against its own earlier results; nothing in DP scored it against an outside, known-right answer.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
from gc_truth_decomp import (Version, dol_bytes, read_config, read_splits, read_symbols,  # noqa: E402,F401
                             versions)
from gc_truth_dp import (classify, dp_bodies, dp_link, dp_names, dp_verdicts, name_scores, rel_truth,  # noqa: E402,F401
                         truth_tokens, words)

KINDS = ("exact", "boundary_differs", "split", "merged", "missed")


def inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def tally(kinds, addrs):
    out = {k: 0 for k in KINDS}
    for a in addrs:
        out[kinds[a]] += 1
    out["exact_rate"] = round(out["exact"] / len(addrs), 4) if addrs else None
    return out


def translator(verd, kinds):
    tx = {"judged": 0, "pass": 0, "fail": 0, "no_c": 0, "outside_truth": 0, "by_boundary": {}}
    for a, r in verd.items():
        if a not in kinds:
            tx["outside_truth"] += 1
            continue
        key = {"PASS": "pass", "FAIL": "fail", "NO_C": "no_c"}[r]
        tx[key] += 1
        tx["judged"] += r != "NO_C"
        b = tx["by_boundary"].setdefault(kinds[a], {"pass": 0, "fail": 0, "no_c": 0})
        b[key] += 1
    tx["pass_rate"] = round(tx["pass"] / tx["judged"], 4) if tx["judged"] else None
    return tx


def score(args):
    decomp, ws, out = pathlib.Path(args.decomp), pathlib.Path(args.project), pathlib.Path(args.out)
    for root, what in ((TOOL, "the DP repository"), (ws, "the game workspace")):
        if inside(out, root):
            sys.exit(f"refused: --out is inside {what}; reports stay outside, where no agent reads them")
    v = Version(decomp, args.version)
    modules = v.rel_modules()
    rel, skipped = rel_truth(modules, dp_link(ws, args.link))
    main = {a for a, s, _, _ in v.functions if s > 0}
    truth = [(a, s) for a, s, _, _ in v.functions] + [(a, s) for a, s, _, _ in rel]
    names = {a: n for a, _, _, n in v.functions}
    names.update({a: n for a, _, _, n in rel})
    bodies = dp_bodies(ws)
    kinds, extra = classify(truth, bodies)
    main_cat = tally(kinds, [a for a in kinds if a in main])
    per_mod = {}
    for a, _, obj, _ in rel:
        if a in kinds and a not in main:
            per_mod.setdefault(obj, set()).add(a)
    rel_all = sorted(set().union(*per_mod.values())) if per_mod else []
    counted = {m["object"]: len(m["functions"]) for m in modules}
    report = {
        "decomp": decomp.name, "decomp_commit": git_head(decomp), "version": args.version,
        "dol_sha1": v.dol_sha1(), "workspace": str(ws),
        "truth_functions_main_dol": sum(main_cat[k] for k in KINDS), "dp_functions": len(bodies),
        "catalog": dict(main_cat, extra_dp=extra),
        "rel": {"modules": len(modules), "scored_modules": len(per_mod), "truth_functions": len(rel_all),
                "catalog": tally(kinds, rel_all),
                "by_module": {o: tally(kinds, sorted(s)) for o, s in sorted(per_mod.items())},
                "not_scored": {o: {"functions": counted[o], "why": w} for o, w in sorted(skipped.items())}},
        "translator": translator(dp_verdicts(ws, args.verdicts), kinds),
        "naming": name_scores(names, dp_names(ws)),
        "rel_modules": len(modules),
        "rel_functions_not_scored": sum(counted[o] for o in skipped),
    }
    # addresses only, never names: the lists DP's owners need to look into
    lists = {k: sorted(f"{a:08x}" for a, kk in kinds.items() if kk == k) for k in KINDS if k != "exact"}
    out.mkdir(parents=True, exist_ok=True)
    (out / "score.json").write_text(json.dumps(dict(report, addresses=lists), indent=1), encoding="utf-8")
    (out / "score.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    return 0


def kind_rows(c, tot):
    pct = (lambda a, b: f"{100 * a / b:.1f}%" if b else "-")
    return ["| Kind | Functions | Share |", "|---|---|---|"] + [f"| {k} | {c[k]} | {pct(c[k], tot)} |" for k in KINDS]


def markdown(r):
    c, t, n, rel = r["catalog"], r["translator"], r["naming"], r["rel"]
    pct = (lambda a, b: f"{100 * a / b:.1f}%" if b else "-")
    tot = r["truth_functions_main_dol"]
    lines = [f"# {r['decomp']} {r['version']} against DP", "",
             f"Decomp commit {r['decomp_commit']}; DOL SHA-1 {r['dol_sha1']}; workspace {r['workspace']}.", "",
             "## Catalog (function boundaries, main.dol)", ""] + kind_rows(c, tot)
    lines += [f"| total truth functions | {tot} | |", f"| DP functions starting outside every truth function | "
              f"{c['extra_dp']} | |", ""]
    if rel["truth_functions"]:
        lines += [f"## Catalog (REL modules: {rel['scored_modules']} of {rel['modules']} scored)", ""]
        lines += kind_rows(rel["catalog"], rel["truth_functions"]) + [
            f"| total truth functions | {rel['truth_functions']} | |", ""]
        lines += ["| Module | Functions | Exact |", "|---|---|---|"] + [
            f"| {o} | {sum(m[k] for k in KINDS)} | {pct(m['exact'], sum(m[k] for k in KINDS))} |"
            for o, m in rel["by_module"].items()] + [""]
    lines += ["## Translator (judged per function)", "",
              f"{t['pass']} of {t['judged']} passed ({pct(t['pass'], t['judged'])}); {t['fail']} failed; "
              f"{t['no_c']} gave no C; {t['outside_truth']} judged functions are not truth functions.", "",
              "## Readable naming agreement (score only)", ""]
    if n["named"]:
        lines.append(f"{n['named']} named functions: {n['exact']} exact ({pct(n['exact'], n['named'])}), "
                     f"{n['any_word']} share a word, mean word overlap {n['mean_overlap']}.")
    else:
        lines.append("No readable names yet.")
    lines += [""]
    for o, s in rel["not_scored"].items():
        lines.append(f"REL module {o}: {s['functions']} functions not scored ({s['why']}).")
    lines += [f"REL modules: {r['rel_modules']}; {r['rel_functions_not_scored']} REL functions not scored.", ""]
    return "\n".join(lines)


def git_head(repo):
    head = repo / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            p = repo / ".git" / ref[5:]
            if p.exists():
                return p.read_text().strip()[:12]
            packed = (repo / ".git" / "packed-refs").read_text()
            m = re.search(r"^([0-9a-f]{40}) " + re.escape(ref[5:]) + "$", packed, re.M)
            return m.group(1)[:12] if m else "?"
        return ref[:12]
    except OSError:
        return "?"


def summary(args):
    decomp = pathlib.Path(args.decomp)
    rows = []
    for ver in [args.version] if args.version else versions(decomp):
        v = Version(decomp, ver)
        mods = v.module_functions()
        rel_fn = sum(n for _, n in mods)
        rows.append({"version": ver, "dol_object": v.cfg.get("object"), "dol_sha1": v.dol_sha1(),
                     "main_functions": len(v.functions), "rel_modules": len(mods), "rel_functions": rel_fn,
                     "rel_share": round(rel_fn / (rel_fn + len(v.functions)), 4) if rel_fn else 0.0,
                     "module_hashes": [{"object": m.get("object"), "sha1": (m.get("hash") or "").lower()}
                                       for m in v.modules],
                     "languages": v.languages(), "rel_languages": v.languages(modules=True)})
    rep = {"decomp": decomp.name, "decomp_commit": git_head(decomp), "versions": rows}
    if args.out:
        out = pathlib.Path(args.out)
        if inside(out, TOOL):
            sys.exit("refused: --out is inside the DP repository")
        out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    for r in rows:
        print(f"{decomp.name} {r['version']}: main.dol {r['main_functions']} functions (SHA-1 {r['dol_sha1']}), "
              f"{r['rel_modules']} RELs with {r['rel_functions']} functions ({100 * r['rel_share']:.1f}%), "
              f"units {r['languages']}, REL units {r['rel_languages']}")
    return 0


def check_disc(args):
    decomp = pathlib.Path(args.decomp)
    sha = hashlib.sha1(dol_bytes(args.file)).hexdigest()
    hits = [ver for ver in versions(decomp) if Version(decomp, ver).dol_sha1() == sha]
    print(f"main.dol SHA-1 {sha}: " + (f"matches {', '.join(hits)}" if hits else "matches no version of this decomp"))
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("summary")
    p.add_argument("--decomp", required=True)
    p.add_argument("--version")
    p.add_argument("--out")
    p = sub.add_parser("check-disc")
    p.add_argument("--decomp", required=True)
    p.add_argument("file")
    p = sub.add_parser("score")
    p.add_argument("--decomp", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--verdicts")
    p.add_argument("--link", help="a gcrel link.json (default: link.json beside the workspace's binary)")
    a = ap.parse_args(argv)
    return {"summary": summary, "check-disc": check_disc, "score": score}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
