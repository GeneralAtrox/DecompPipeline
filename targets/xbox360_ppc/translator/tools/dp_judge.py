"""Judge xenon-translate's C with DecompPipeline's judge, unchanged (the xbox360_ppc target's run_check through
core/check: lint, MSVC build, run every test vector of the prepared harness, compare with the oracle's expected
results).

It stops with a message when the workspace has no harness yet. A harness without expected results (its
preparation is unfinished, or the oracle found no valid input: oracle_summary.json "dev_valid" 0) has nothing to
judge and is reported NO_VECTORS, never PASS or FAIL; one the proof refused (meta.json "unsupported") is REFUSED.
Nothing here starts Ghidra: the harnesses are the ones already prepared in the workspace. The workspace is only read:
its judge inputs (project.json, the binary, image.json, catalog_inputs.json, code_ranges.txt, state/, and each
function's harness/ and packets/) are copied into a mirror workspace under --out, and DP runs there (its builds write
into the mirror, never into the game folder). The MSVC environment cached by tools/compile_all.py is given to the
mirror as state/msvc_env.json.

usage: python tools/dp_judge.py [--ws <workspace>] --out <dir> [--only a,b] [--jobs N] [--baseline] [--all]
  --baseline: judge the workspace's matched (agent-written) C instead of the translation
  --all: every function with a harness, not only the matched ones
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import shutil
import sys
import time
from collections import Counter

DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


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


def listing(d):
    return sorted((p.relative_to(d).as_posix(), p.stat().st_size, int(p.stat().st_mtime)) for p in d.rglob("*")
                  if p.is_file())


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
    args = ap.parse_args()
    ws, out = pathlib.Path(args.ws), pathlib.Path(args.out).resolve()
    addrs = sorted(p.stem for p in (ws / "matched").glob("*.c"))
    if args.all:
        addrs = sorted(p.parent.name for p in (ws / "harness").glob("*/meta.json"))
    if args.only:
        addrs = [a for a in addrs if a in set(args.only.split(","))]
    if not addrs:
        sys.exit(f"{ws}: no harnesses to judge yet (the Xbox 360 target's proof makes them; this tool starts no Ghidra)")
    mws = out / "mirror" / ws.name
    mirror(ws, mws, addrs)
    os.environ["DECOMP_PROJECT"] = str(mws)
    sys.path.insert(0, str(DP))
    from core import check, paths  # noqa: E402
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
        notes = []
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
            wf = wd / f"{a}.c"
            wf.write_text(src, encoding="utf-8")
        tt = time.time() - t0
        bd = out / "build" / kind / ws.name / a
        try:
            res = check.run_check(cfg, a, wf, build_dir=bd)
        except Exception as e:  # noqa: BLE001
            return {"addr": a, "verdict": "ERROR", "why": repr(e)[:300]}
        finally:
            shutil.rmtree(bd, ignore_errors=True)
        d = res.get("first_diffs") or []
        v = res.get("vectors") or {}
        if res["verdict"] == "FAIL" and res["stage"] == "compare" and not res["failing"] and "run_error" not in res:
            notes = ["too few valid vectors, none wrong: " + ", ".join(f"{k} {v[k]['total']}" for k in v)
                     + f" (the judge needs {cfg['min_valid_vectors']} in each)"] + notes
        return {"addr": a, "verdict": res["verdict"], "stage": res["stage"], "failing": res["failing"],
                "total": res["total"], "lint": res.get("lint", [])[:3], "build": res.get("build", [])[:4],
                "run_error": res.get("run_error"), "diff": d[0]["diffs"][:4] if d else [], "notes": notes,
                "c_sha256": hashlib.sha256(wf.read_bytes()).hexdigest()[:16],
                "translate_s": round(tt, 3), "judge_s": round(time.time() - t0 - tt, 2)}

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
    for r in rows:
        if r["verdict"] != "PASS":
            print(" ", r["addr"], r["verdict"], r.get("stage", ""), r.get("why", ""), r.get("lint") or "",
                  r.get("build") or "", r.get("run_error") or "", r.get("diff") or "", *(r.get("notes") or [])[:1])


if __name__ == "__main__":
    main()
