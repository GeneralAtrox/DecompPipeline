"""Judge xenon-translate's C with DecompPipeline's judge, unchanged (the xbox360_ppc target's run_check through
core/check: lint, MSVC build, run every test vector of the prepared harness, compare with the oracle's expected
results).

It stops with a message when the workspace has no harness yet. A harness without expected results (its
preparation is unfinished, or the oracle found no valid input: oracle_summary.json "dev_valid" 0) has nothing to
judge and is reported NO_VECTORS, never PASS or FAIL; so is one whose check compared every vector, none wrong, on
fewer valid vectors than "min_valid_vectors" in a set (x360_check.finish; its exit state, if any, is NO_VECTORS too,
never PASS). One the proof refused (meta.json "unsupported") is REFUSED.
Before judging, the workspace's oracle hints are compared with the game's tracked set (tools/game_hints.py): a
difference prints one WARNING line and the results file's rows are unchanged.
Nothing here starts Ghidra: the harnesses are the ones already prepared in the workspace. The workspace is only read:
its judge inputs (project.json, the binary, image.json, catalog_inputs.json, code_ranges.txt, state/, and each
function's harness/ and packets/) are copied into a mirror workspace under --out, and DP runs there (its builds write
into the mirror, never into the game folder). The MSVC environment cached by tools/compile_all.py is given to the
mirror as state/msvc_env.json.

A harness prepared with target.json "exit_state" (its expected.txt has XS lines) is judged on the exit state too: the
translator's exit-state form (xenontranslate/exit_form.py) of the same function is built and run beside it, and the
row gains "exit": {verdict, failing, classes, first}. An enforced class that differs fails the function's verdict
(x360_check.run_check). c_sha256 is the normal form's.

usage: python tools/dp_judge.py [--ws <workspace>] --out <dir> [--only a,b] [--jobs N] [--baseline] [--all]
                                [--plant FILE] [--plant-exit FILE]
  --baseline: judge the workspace's matched (agent-written) C instead of the translation
  --all: every function with a harness, not only the matched ones
  --plant-exit FILE: a planted wrong answer per function, in its exit-state form only, to show the exit judge fails it
    under the class it belongs to: JSON {addr: [pattern, replacement, class]} (re.subn on the exit-state C, the shape
    of tests/test_x360_judge_exit.py MUTANTS); the row gains "plant": {class, n}; a pattern that matches nothing is
    verdict PLANT_MISSING (never a pass). The normal form is judged unchanged.
  --plant FILE: the same for the normal form (the C the judge runs), to show the judge fails a wrong answer: e.g. one
    wrong switch case above index 0 (tests/test_x360_judge_gaps.py TablePlant, whose rows only fail once the oracle
    keeps those vectors: TcJumpTables); the row gains "plant": {class, n}.
"""
import argparse
import concurrent.futures as cf
import copy
import hashlib
import json
import os
import re
import pathlib
import shutil
import sys
import time
from collections import Counter

DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def _load_game_hints():
    """targets/xbox360_ppc/tools/game_hints.py by its path (that folder is not put on sys.path: its module names
    would shadow others')."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("x360_game_hints", HERE.parent / "tools" / "game_hints.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


game_hints = _load_game_hints()


def mirror(ws, dst, addrs):
    dst.mkdir(parents=True, exist_ok=True)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    shutil.copyfile(ws / "project.json", dst / "project.json")
    b = (dst / proj["binary"]).resolve()
    b.parent.mkdir(parents=True, exist_ok=True)
    if not b.exists():
        shutil.copyfile((ws / proj["binary"]).resolve(), b)
    for f in ("image.json", "catalog_inputs.json", "code_ranges.txt"):
        if (ws / f).exists() and not (dst / f).exists():
            shutil.copyfile(ws / f, dst / f)
    st = dst / "state"
    if (ws / "state").is_dir() and not st.exists():
        shutil.copytree(ws / "state", st)
    st.mkdir(parents=True, exist_ok=True)
    env = HERE / "out" / "msvc_env.json"
    if env.exists() and not (st / "msvc_env.json").exists():
        shutil.copyfile(env, st / "msvc_env.json")
    for a in addrs:  # a harness the proof has re-prepared since the last run is copied again
        for sub in ("harness", "packets"):
            s, d = ws / sub / a, dst / sub / a
            if s.is_dir() and (not d.exists() or listing(s) != listing(d)):
                shutil.rmtree(d, ignore_errors=True)
                shutil.copytree(s, d)


def plant(src, spec):
    """(src with spec [pattern, replacement, class] applied by re.subn, how many times it applied)."""
    return re.subn(spec[0], spec[1], src)


def listing(d):
    return sorted((p.relative_to(d).as_posix(), p.stat().st_size, int(p.stat().st_mtime)) for p in d.rglob("*")
                  if p.is_file())


def exit_row(res):
    """The row's "exit" from a check result with an exit record: FAIL when an enforced class failed a vector or the
    runner failed, NO_VECTORS when the check itself is NO_VECTORS (too few vectors to call it a PASS), else PASS."""
    x = res["exit"]
    v = ("FAIL" if x["failing"] or res.get("run_error") else "NO_VECTORS" if res["verdict"] == "NO_VECTORS"
         else "PASS")
    return {"verdict": v, "failing": x["failing"], "classes": x["classes"], "first": x["first"]}


def has_exit_state(expected):
    """The harness was prepared with target.json "exit_state" (TcOracle wrote XS lines)."""
    with open(expected, "rb") as f:
        for line in f:
            if line.startswith(b"XS "):
                return True
            if line.startswith(b"E"):       # the first vector's lines are enough: the flag is per preparation
                return False
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--comments", action="store_true")
    ap.add_argument("--raw", action="store_true", help="no dataflow passes")
    ap.add_argument("--no-struct", action="store_true", help="goto form (no loops/ifs)")
    ap.add_argument("--tag", default="", help="a name for this run's results file")
    ap.add_argument("--plant-exit", default="", help="JSON {addr: [pattern, replacement, class]}")
    ap.add_argument("--plant", default="", help="JSON {addr: [pattern, replacement, class]}, on the normal form")
    args = ap.parse_args()
    ws, out = pathlib.Path(args.ws), pathlib.Path(args.out).resolve()
    plants = json.loads(pathlib.Path(args.plant_exit).read_text(encoding="utf-8")) if args.plant_exit else {}
    nplants = json.loads(pathlib.Path(args.plant).read_text(encoding="utf-8")) if args.plant else {}
    addrs = sorted(p.stem for p in (ws / "matched").glob("*.c"))
    if args.all:
        addrs = sorted(p.parent.name for p in (ws / "harness").glob("*/meta.json"))
    if args.only:
        addrs = [a for a in addrs if a in set(args.only.split(","))]
    if not addrs:
        sys.exit(f"{ws}: no harnesses to judge yet (the Xbox 360 target's proof makes them; this tool starts no Ghidra)")
    game_hints.warn(ws, "dp_judge")
    mws = out / "mirror" / ws.name
    mirror(ws, mws, addrs)
    os.environ["DECOMP_PROJECT"] = str(mws)
    sys.path.insert(0, str(DP))
    from core import check, paths, target_hooks  # noqa: E402
    from xenontranslate import returns, workspace  # noqa: E402
    from xenontranslate.translate import Signature, TranslateError, translate_function  # noqa: E402
    cfg = paths.config()
    w = workspace.Workspace(mws)
    kinds = None if args.baseline else returns.analyse(w, HERE / "out" / "cache")
    kind = "baseline" if args.baseline else "translated"
    kind += ("_" + args.tag) if args.tag else ""
    wd = out / kind / ws.name
    wd.mkdir(parents=True, exist_ok=True)

    def one(a):
        meta = paths.read_json(paths.HARNESS / a / "meta.json")
        if meta.get("unsupported"):  # the proof refused it: there is no harness to judge
            return {"addr": a, "verdict": "REFUSED", "why": meta["unsupported"]}
        exp = paths.HARNESS / a / "expected.txt"
        if not exp.is_file() or not exp.stat().st_size:
            summ = paths.HARNESS / a / "oracle_summary.json"
            why = paths.read_json(summ).get("invalid", {}) if summ.is_file() else "preparation unfinished"
            return {"addr": a, "verdict": "NO_VECTORS", "why": f"no expected results: {why}"}
        t0 = time.time()
        notes, nplanted = [], 0
        if args.baseline:
            wf = ws / "matched" / f"{a}.c"
        else:
            try:
                ad = int(a, 16)
                src, ft = translate_function(w.dol, ad, Signature.from_meta(meta), w.facts, w.bodies[ad],
                                             comments=args.comments, structured=not args.no_struct,
                                             optimise=not args.raw, returns=kinds)
                notes = ft.notes
            except TranslateError as e:
                return {"addr": a, "verdict": "NOTRANSLATE", "why": str(e)}
            except Exception as e:  # noqa: BLE001  (a translator bug: reported, not fatal to the run)
                import traceback
                return {"addr": a, "verdict": "CRASH", "why": traceback.format_exc()[-600:]}
            if a in nplants:
                src, nplanted = plant(src, nplants[a])
                if not nplanted:
                    return {"addr": a, "verdict": "PLANT_MISSING", "why": f"{nplants[a][0]} is not in the C"}
                notes = [f"planted ({nplants[a][2]}): {nplanted} x {nplants[a][0]} -> {nplants[a][1]}"] + notes
            wf = wd / f"{a}.c"
            wf.write_text(src, encoding="utf-8")
        xf, xwhy, planted = None, None, 0
        if not args.baseline and has_exit_state(exp):
            try:
                facts = copy.copy(w.facts)
                facts.exit_state = True
                xsrc, _ = translate_function(w.dol, ad, Signature.from_meta(meta), facts, w.bodies[ad],
                                             comments=args.comments, structured=not args.no_struct,
                                             optimise=not args.raw, returns=kinds)
                if a in plants:
                    pat, rep, pcls = plants[a]
                    xsrc, planted = plant(xsrc, plants[a])
                    if not planted:
                        return {"addr": a, "verdict": "PLANT_MISSING", "why": f"{pat} is not in the exit-state form"}
                    notes = [f"planted ({pcls}): {planted} x {pat} -> {rep}"] + notes
                xf = wd / f"{a}.exit.c"
                xf.write_text(xsrc, encoding="utf-8")
            except Exception as e:  # noqa: BLE001  (TranslateError, or a translator bug: reported in the row)
                xwhy = str(e) if isinstance(e, TranslateError) else repr(e)[:300]
        tt = time.time() - t0
        bd = out / "build" / kind / ws.name / a
        try:
            if xf:
                res = target_hooks.get("run_check")(cfg, a, wf, build_dir=bd, exit_file=xf)
            else:
                res = check.run_check(cfg, a, wf, build_dir=bd)
        except Exception as e:  # noqa: BLE001
            return {"addr": a, "verdict": "ERROR", "why": repr(e)[:300]}
        finally:
            shutil.rmtree(bd, ignore_errors=True)
        d = res.get("first_diffs") or []
        if res["verdict"] == "NO_VECTORS":  # x360_check.finish: compared, none wrong, too few valid vectors
            notes = [res["why"]] + notes
        row = {"addr": a, "verdict": res["verdict"], "stage": res["stage"], "failing": res["failing"],
               "total": res["total"], "lint": res.get("lint", [])[:3], "build": res.get("build", [])[:4],
               "run_error": res.get("run_error"), "diff": d[0]["diffs"][:4] if d else [], "notes": notes,
               "c_sha256": hashlib.sha256(wf.read_bytes()).hexdigest()[:16],
               "translate_s": round(tt, 3), "judge_s": round(time.time() - t0 - tt, 2)}
        x = res.get("exit")
        if x is not None:
            row["exit"] = exit_row(res)
        if a in plants:
            row["plant"] = {"class": plants[a][2], "n": planted}
        if a in nplants:
            row["plant"] = {"class": nplants[a][2], "n": nplanted}
        if xwhy:      # the exit-state form did not translate: the exit state is not judged (the verdict stands)
            row["exit"] = {"verdict": "NOTRANSLATE", "why": xwhy}
        return row

    t0 = time.time()
    rows = []
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for r in ex.map(one, addrs):
            rows.append(r)
    rows.sort(key=lambda r: r["addr"])
    matched = {p.stem for p in (ws / "matched").glob("*.c")}
    for r in rows:
        r["agent_matched"] = r["addr"] in matched
    res = out / f"results_{kind}_{ws.name}{'_all' if args.all else ''}.json"
    res.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    c = Counter(r["verdict"] for r in rows)
    print(f"{ws.name} {kind}: {len(rows)} functions {dict(c)}  {time.time() - t0:.0f}s  -> {res}")
    ex = [r["exit"] for r in rows if "exit" in r]
    if ex:
        cls = Counter()
        for e in ex:
            cls.update({k: 1 for k in e.get("classes") or {}})
        print(f"  exit state: {len(ex)} judged {dict(Counter(e['verdict'] for e in ex))}; "
              f"functions differing per class {dict(cls)}")
    for r in rows:
        if r["verdict"] != "PASS":
            print(" ", r["addr"], r["verdict"], r.get("stage", ""), r.get("why", ""), r.get("lint") or "",
                  r.get("build") or "", r.get("run_error") or "", r.get("diff") or "", *(r.get("notes") or [])[:1])


if __name__ == "__main__":
    main()
