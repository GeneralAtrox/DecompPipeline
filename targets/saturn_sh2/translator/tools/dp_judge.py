"""Judge sh2-translate's C with DecompPipeline's judge, unchanged (core/check.run_check: lint, build, run, compare).

The game workspace is only read: its judge inputs (project.json, the binary, state/code_ranges.txt, the MSVC env
and fsca table caches, and each function's harness/ and packets/) are copied into a mirror workspace under
--out, and DP runs there (its builds and caches write into the mirror, never into the game folder).

usage: python tools/dp_judge.py --ws <workspace> --out <dir> [--baseline] [--only a,b] [--jobs N]
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import shutil
import sys
import time

DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def mirror(ws, dst, addrs):
    dst.mkdir(parents=True, exist_ok=True)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    shutil.copyfile(ws / "project.json", dst / "project.json")
    b = dst / proj["binary"]
    b.parent.mkdir(parents=True, exist_ok=True)
    if not b.exists():
        shutil.copyfile(ws / proj["binary"], b)
    st = dst / "state"
    (st / "fsca").mkdir(parents=True, exist_ok=True)
    for f in ("code_ranges.txt", "msvc_env.json", "msvc_env.key.json", "function_bodies.txt"):
        if (ws / "state" / f).exists():
            shutil.copyfile(ws / "state" / f, st / f)
    for f in (ws / "state" / "fsca").glob("*.bin"):
        if not (st / "fsca" / f.name).exists():
            shutil.copyfile(f, st / "fsca" / f.name)
    for a in addrs:
        for sub in ("harness", "packets"):
            s, d = ws / sub / a, dst / sub / a
            if s.is_dir() and not d.exists():
                shutil.copytree(s, d)


def warm_runtime(check, cfg):
    """Compile DP's shared runtime objects (build/runtime_obj) once, before any parallel build: two builds that
    both find them missing race there (one replaces the folder while the other links from it: LNK1181)."""
    env = check.msvc_env(cfg)
    cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
    if cl:
        check.runtime_objs(cl, env)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", action="store_true", help="judge the workspace's matched (agent) C instead")
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--nocomments", action="store_true")
    ap.add_argument("--discover", action="store_true",
                    help="no Ghidra body: find the extent from the entry (other functions' entries end it)")
    ap.add_argument("--simplify", default="all",
                    help="simplification passes: all, none, or a comma list of shift,const,copy,dce,inline")
    ap.add_argument("--compact", action="store_true",
                    help="judge the compact form (what functions near DP's 60 KB cap get) of every function")
    ap.add_argument("--all", action="store_true", help="every function with a harness, not only the matched ones")
    args = ap.parse_args()
    ws, out = pathlib.Path(args.ws), pathlib.Path(args.out).resolve()
    addrs = sorted(p.stem for p in (ws / "matched").glob("*.c"))
    if args.all:
        addrs = sorted(p.parent.name for p in (ws / "harness").glob("*/meta.json"))
    if args.only:
        addrs = [a for a in addrs if a in set(args.only.split(","))]
    mws = out / "mirror" / ws.name
    mirror(ws, mws, addrs)
    os.environ["DECOMP_PROJECT"] = str(mws)
    sys.path.insert(0, str(DP))
    from core import check, paths  # noqa: E402
    from sh2translate.translate import Image, Signature, TargetFacts, TranslateError, compact, translate_function
    cfg = paths.config()
    tj = json.loads((paths.TARGET / "target.json").read_text(encoding="utf-8"))
    facts = TargetFacts.from_target_json(tj)
    img = Image(cfg["binary_path"].read_bytes(), facts.image_base)
    kind = "baseline" if args.baseline else "discovered" if args.discover else "translated"
    kind += "_compact" if args.compact else ""
    entries = set()
    fb = paths.STATE / "function_bodies.txt"
    if fb.exists():
        entries = {int(ln.split()[0], 16) for ln in fb.read_text().splitlines() if ln.strip()}
    wd = out / kind / ws.name
    wd.mkdir(parents=True, exist_ok=True)

    def one(a):
        meta = paths.read_json(paths.HARNESS / a / "meta.json")
        t0 = time.time()
        if args.baseline:
            wf = ws / "matched" / f"{a}.c"
        else:
            try:
                body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks", [])]
                if args.discover:
                    body = None
                passes = (None if args.simplify == "all" else () if args.simplify == "none"
                          else tuple(args.simplify.split(",")))
                src = translate_function(img, int(a, 16), Signature.from_meta(meta), facts, body or None,
                                         entries=entries, comments=not args.nocomments, simplify=passes)
            except TranslateError as e:
                return {"addr": a, "verdict": "NOTRANSLATE", "why": str(e)}
            if args.compact:
                src = compact(src, helper_min=1)
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
        return {"addr": a, "verdict": res["verdict"], "stage": res["stage"], "failing": res["failing"],
                "total": res["total"], "lint": res.get("lint", [])[:3], "build": res.get("build", [])[:4],
                "run_error": res.get("run_error"), "diff": d[0]["diffs"][:4] if d else [],
                "translate_s": round(tt, 3), "judge_s": round(time.time() - t0 - tt, 2)}

    t0 = time.time()
    rows = []
    warm_runtime(check, cfg)
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for r in ex.map(one, addrs):
            rows.append(r)
    rows.sort(key=lambda r: r["addr"])
    matched = {p.stem for p in (ws / "matched").glob("*.c")}
    for r in rows:
        r["agent_matched"] = r["addr"] in matched
    (out / f"results_{kind}_{ws.name}{'_all' if args.all else ''}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    print(f"{ws.name} {kind}: {len(rows)} functions {dict(c)}  {time.time() - t0:.0f}s")
    for r in rows:
        if r["verdict"] != "PASS":
            print(" ", r["addr"], "agent-matched" if r["agent_matched"] else "agent-unmatched", r["verdict"], r.get("stage", ""), r.get("why", ""), r.get("lint") or "",
                  r.get("build") or "", r.get("run_error") or "", r.get("diff") or "")


if __name__ == "__main__":
    main()
