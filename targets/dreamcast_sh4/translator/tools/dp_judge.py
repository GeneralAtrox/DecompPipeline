"""Judge sh4-translate's C with DecompPipeline's judge, unchanged (core/check.run_check: lint, build, run, compare).

The game workspace is only read: its judge inputs (project.json, the binary, state/code_ranges.txt, the MSVC env
and fsca table caches, and each function's harness/ and packets/) are copied into a mirror workspace under
--out, and DP runs there (its builds and caches write into the mirror, never into the game folder).

usage: python tools/dp_judge.py --ws <workspace> --out <dir> [--baseline] [--only a,b|@FILE] [--jobs N]
           [--harness-from WORK]
--sp-input: the port form (translate_function sp_input): what a port compiles (port/tools/tx_port.py); each row
keeps the C's sha256, so the port takes only the very text judged here.
--memo FILE: PASSes kept across runs (core/verdict_memo.RawPass, its key over every judge input): a function whose
C, harness, packet and judge are as at a kept PASS is not judged again (its row says "memo": true).
--facts FILE: project.json keys (a JSON object) the mirror's project.json takes on top of the workspace's, to judge
with a game fact (say "written_code") before the game's project.json has it.
--harness-from WORK: judge on the harnesses of WORK (a tools/oracle_probe.py work dir, tests/.work/probe-TAG: its
harness/ and packets/), made fresh from today's oracle, instead of the workspace's, for the functions WORK has; the
functions judged are then WORK's (or --only's).
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

DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def mirror(ws, dst, addrs, fresh=None, facts=None):
    dst.mkdir(parents=True, exist_ok=True)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    proj.update(facts or {})
    src = ws / proj["binary"]
    proj["binary"] = "binary/" + src.name       # inside the mirror: two games' relative paths can name one file
    (dst / "project.json").write_text(json.dumps(proj, indent=2), encoding="utf-8")
    b = dst / proj["binary"]
    b.parent.mkdir(parents=True, exist_ok=True)
    if not b.exists() or b.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, b)
    st = dst / "state"
    (st / "fsca").mkdir(parents=True, exist_ok=True)
    for f in ("code_ranges.txt", "msvc_env.json", "msvc_env.key.json", "function_bodies.txt", "fr0_results.txt",
              "fsca_table.bin"):
        if (ws / "state" / f).exists():
            shutil.copyfile(ws / "state" / f, st / f)
    for f in (ws / "state" / "fsca").glob("*.bin"):
        if not (st / "fsca" / f.name).exists():
            shutil.copyfile(f, st / "fsca" / f.name)
    for a in addrs:
        for sub in ("harness", "packets"):
            s, d = ws / sub / a, dst / sub / a
            if fresh is not None and (fresh / "harness" / a / "meta.json").exists():
                s = fresh / sub / a
                shutil.rmtree(d, ignore_errors=True)  # a fresh harness replaces what an earlier run mirrored
            if s.is_dir() and not d.exists():
                shutil.copytree(s, d)


def wsname(ws):
    """A name for the workspace's mirror and results: the game folder's when the workspace is decomp/ or main/."""
    parts = [x for x in ws.resolve().parts if x not in ("decomp", "main")]
    return "".join(ch for ch in parts[-1] if ch.isalnum())


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
    ap.add_argument("--sp-input", action="store_true", help="the port form: the body takes sp as an input")
    ap.add_argument("--memo", default="", help="a JSON-lines file of kept PASSes (core/verdict_memo.RawPass)")
    ap.add_argument("--harness-from", default="", help="an oracle_probe work dir: judge on its fresh harnesses")
    ap.add_argument("--facts", default="", help="a JSON file of project.json keys the mirror takes on top")
    ap.add_argument("--part", default="", help="k/n: only every n-th function from the k-th (k = 0..n-1), so a long "
                    "run goes in bounded pieces; the results file is named after the part")
    args = ap.parse_args()
    ws, out = pathlib.Path(args.ws), pathlib.Path(args.out).resolve()
    addrs = sorted(p.stem for p in (ws / "matched").glob("*.c"))
    if args.all:
        addrs = sorted(p.parent.name for p in (ws / "harness").glob("*/meta.json"))
    fresh = pathlib.Path(args.harness_from).resolve() if args.harness_from else None
    if fresh is not None:
        addrs = sorted(p.parent.name for p in (fresh / "harness").glob("*/meta.json"))
    if args.only:
        only = args.only
        if only.startswith("@"):
            only = ",".join(pathlib.Path(only[1:]).read_text(encoding="utf-8").split())
        addrs = sorted(set(only.split(","))) if fresh is not None else [a for a in addrs if a in set(only.split(","))]
    part = ""
    if args.part:
        k, n = map(int, args.part.split("/"))
        addrs = addrs[k::n]
        part = f"_part{k}of{n}"
    name = wsname(ws)
    mws = out / "mirror" / name
    mirror(ws, mws, addrs, fresh, json.loads(pathlib.Path(args.facts).read_text(encoding="utf-8")) if args.facts else None)
    os.environ["DECOMP_PROJECT"] = str(mws)
    sys.path.insert(0, str(DP))
    from core import check, paths, verdict_memo  # noqa: E402
    memo = verdict_memo.RawPass(pathlib.Path(args.memo).resolve()) if args.memo else None
    from sh4translate.translate import Image, Signature, TargetFacts, TranslateError, compact, translate_function
    cfg = paths.config()
    tj = json.loads((paths.TARGET / "target.json").read_text(encoding="utf-8"))
    facts = TargetFacts.from_target_json(tj)
    img = Image(cfg["binary_path"].read_bytes(), facts.image_base, facts.little)
    from core import ret_reg
    fr0_listed = ret_reg.listed(paths.STATE)
    kind = "baseline" if args.baseline else "discovered" if args.discover else "translated"
    kind += "_sp" if args.sp_input else ""
    kind += "_compact" if args.compact else ""
    kind += "_fresh" if fresh is not None else ""
    entries = set()
    fb = paths.STATE / "function_bodies.txt"
    if fb.exists():
        entries = {int(ln.split()[0], 16) for ln in fb.read_text().splitlines() if ln.strip()}
    ranges = {}
    if fb.exists():
        for ln in fb.read_text().splitlines():
            if len(ln.split()) == 3:
                e, lo, hi = (int(x, 16) for x in ln.split())
                ranges.setdefault(e, []).append((lo, hi))
    from sh4translate.translate import spin_ranges, sz_from_caller, written_code
    spins = spin_ranges(cfg)
    written, sz1 = written_code(cfg), sz_from_caller(cfg)
    wd = out / kind / name
    wd.mkdir(parents=True, exist_ok=True)

    def one(a):
        if not (paths.HARNESS / a / "meta.json").exists():
            return {"addr": a, "verdict": "NOHARNESS"}      # matched, but the workspace has no harness for it
        meta = ret_reg.judged(paths.read_json(paths.HARNESS / a / "meta.json"), fr0_listed, a)  # as the judge reads it
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
                                         entries=entries, comments=not args.nocomments, simplify=passes,
                                         ranges=ranges.get(int(a, 16)), spin=spins.get(int(a, 16)),
                                         sp_input=args.sp_input, written=written,
                                         sz_from_caller=int(a, 16) in sz1)
            except TranslateError as e:
                return {"addr": a, "verdict": "NOTRANSLATE", "why": str(e)}
            if args.compact:
                src = compact(src, helper_min=1)
            wf = wd / f"{a}.c"
            wf.write_text(src, encoding="utf-8")
        tt = time.time() - t0
        sha = hashlib.sha256(wf.read_bytes()).hexdigest()
        key = memo.key_of(a, wf) if memo else None
        total = memo.passed(a, key) if memo else None
        if total is not None:
            return {"addr": a, "verdict": "PASS", "stage": "memo", "failing": 0, "total": total, "memo": True,
                    "sha256": sha, "translate_s": round(tt, 3), "judge_s": 0.0}
        bd = out / "build" / kind / name / a
        try:
            res = check.run_check(cfg, a, wf, build_dir=bd)
        except Exception as e:  # noqa: BLE001
            return {"addr": a, "verdict": "ERROR", "why": repr(e)[:300]}
        finally:
            shutil.rmtree(bd, ignore_errors=True)
        d = res.get("first_diffs") or []
        if memo and res["verdict"] == "PASS":
            memo.record(a, key, res["total"])
        return {"addr": a, "sha256": sha, "verdict": res["verdict"], "stage": res["stage"], "failing": res["failing"],
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
    (out / f"results_{kind}_{name}{'_all' if args.all else ''}{part}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    print(f"{name} {kind}: {len(rows)} functions {dict(c)}  {time.time() - t0:.0f}s")
    for r in rows:
        if r["verdict"] != "PASS":
            print(" ", r["addr"], "agent-matched" if r["agent_matched"] else "agent-unmatched", r["verdict"], r.get("stage", ""), r.get("why", ""), r.get("lint") or "",
                  r.get("build") or "", r.get("run_error") or "", r.get("diff") or "")


if __name__ == "__main__":
    main()
