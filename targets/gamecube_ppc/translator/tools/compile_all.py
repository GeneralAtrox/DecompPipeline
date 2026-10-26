"""Translate every function of the executable (state/function_bodies.txt) and compile each with MSVC exactly as
DecompPipeline's GameCube build does (/O1 /fp:strict /W4 /WX /TC against the target's tc_rt.h), after DP's lint.
No judge: this shows how much of the game the translator turns into C that DP accepts to build, and why the rest
does not, grouped by cause.

Bounded throughout: translation runs in worker processes that each stop after --worker-timeout seconds (a function
still running then is recorded as a timeout and the next worker resumes after it); each compiler batch has a
timeout too.

usage: python tools/compile_all.py --ws <workspace> [--out out/all] [--workers 4] [--batch 48] [--limit N]
       python tools/compile_all.py --worker <json args>   (internal)
"""
import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parents[1]
DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
sys.path.insert(0, str(HERE))


# ------------------------------------------------------------------------------------------------ worker side
def worker(spec):
    sys.path.insert(0, str(DP))
    from core.lint import lint  # noqa: E402
    from ppctranslate import returns, workspace  # noqa: E402
    from ppctranslate.translate import TranslateError, translate_function  # noqa: E402
    ws = workspace.Workspace(spec["ws"])
    kinds = returns.analyse(ws, spec["cache"])
    cdir = pathlib.Path(spec["cdir"])
    with open(spec["log"], "a", encoding="utf-8") as log:
        for a in spec["addrs"]:
            ad = int(a, 16)
            log.write(json.dumps({"start": a}) + "\n")
            log.flush()
            t0 = time.time()
            row = {"addr": a}
            try:
                sig = ws.signature(ad)
                src, ft = translate_function(ws.dol, ad, sig, ws.facts, ws.bodies[ad], returns=kinds)
                errs = lint(src, {"name": sig.name, "signature": sig.text})
                (cdir / f"{a}.c").write_text(src, encoding="utf-8")
                row.update(status="lint" if errs else "ok", lint=errs[:3], bytes=len(src), notes=ft.notes[:5],
                           generic=sig.generic, insns=sum((hi - lo) // 4 for lo, hi in ws.bodies[ad]))
            except TranslateError as e:
                row.update(status="untranslated", why=str(e))
            except Exception as e:  # noqa: BLE001
                row.update(status="crash", why=f"{type(e).__name__}: {e}"[:300])
            row["s"] = round(time.time() - t0, 3)
            log.write(json.dumps(row) + "\n")
            log.flush()


# ------------------------------------------------------------------------------------------------ parent side
def read_log(p):
    rows, started = {}, []
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            if "start" in r:
                started.append(r["start"])
            else:
                rows[r["addr"]] = r
    return rows, started


def translate_all(ws, addrs, out, workers, wtimeout):
    cdir = out / "c"
    cdir.mkdir(parents=True, exist_ok=True)
    parts = [addrs[k::workers] for k in range(workers)]
    results = {}
    lock = threading.Lock()

    def run(k, todo):
        log = out / f"translate_{k}.jsonl"
        if log.exists():
            log.unlink()
        while todo:
            spec = {"ws": str(ws), "cdir": str(cdir), "log": str(log), "addrs": todo, "cache": str(out / "cache")}
            try:
                subprocess.run([sys.executable, __file__, "--worker", json.dumps(spec)], timeout=wtimeout,
                               capture_output=True, text=True)
            except subprocess.TimeoutExpired:
                pass
            rows, started = read_log(log)
            done = set(rows)
            if started and started[-1] not in done:
                rows[started[-1]] = {"addr": started[-1], "status": "timeout"}     # (only after a stop)
                done.add(started[-1])
                log.write_text("".join(json.dumps(r) + "\n" for r in rows.values()), encoding="utf-8")
            with lock:
                results.update(rows)
            todo = [a for a in todo if a not in done]
    ts = [threading.Thread(target=run, args=(k, p)) for k, p in enumerate(parts)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return results


DIAG = re.compile(r"^(.*?\\|)?(?P<file>[0-9a-f]{8})\.c\((?P<line>\d+)\)\s*: (?P<kind>error|warning) (?P<code>C\d+): (?P<msg>.*)$")


def compile_all(ws, ok, out, batch, btimeout):
    cfg_env = json.loads((ws / "state" / "msvc_env.json").read_text(encoding="utf-8"))
    env = dict(cfg_env)
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = None
    for d in path.split(";"):
        if d and (pathlib.Path(d) / "cl.exe").exists():
            cl = str(pathlib.Path(d) / "cl.exe")
            break
    if not cl:
        sys.exit("cl.exe not found in the workspace's MSVC environment")
    runtime = DP / "targets" / "gamecube_ppc" / "runtime"
    odir = out / "obj"
    odir.mkdir(parents=True, exist_ok=True)
    diags = collections.defaultdict(list)
    built = set()
    for k in range(0, len(ok), batch):
        group = ok[k:k + batch]
        cmd = [cl, "/nologo", "/c", "/O1", "/fp:strict", "/W4", "/WX", "/TC", "/MP4", f"/I{runtime}", f"/Fo{odir}\\"]
        cmd += [str(out / "c" / f"{a}.c") for a in group]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=btimeout, env=env, cwd=out)
            text = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            for a in group:
                diags[a].append(("timeout", "the compiler batch timed out"))
            continue
        for ln in text.splitlines():
            m = DIAG.match(ln.strip())
            if m and m.group("code") != "C2220":
                diags[m.group("file")].append((m.group("code"), m.group("msg")[:160]))
        for a in group:
            o = odir / f"{a}.obj"
            if o.exists():
                built.add(a)
                o.unlink()
        print(f"  compiled {min(k + batch, len(ok))}/{len(ok)}: {len(built)} built so far", flush=True)
    return built, diags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker")
    ap.add_argument("--ws")
    ap.add_argument("--out", default=str(HERE / "out" / "all"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker-timeout", type=int, default=900)
    ap.add_argument("--batch", type=int, default=48)
    ap.add_argument("--batch-timeout", type=int, default=600)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-translate", action="store_true", help="compile the C already in --out")
    args = ap.parse_args()
    if args.worker:
        worker(json.loads(args.worker))
        return
    ws, out = pathlib.Path(args.ws).resolve(), pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    from ppctranslate import returns, workspace
    w = workspace.Workspace(ws)
    returns.analyse(w, out / "cache", log=print)       # once, before the workers read it
    addrs = [f"{a:08x}" for a in sorted(w.bodies)]
    if args.limit:
        addrs = addrs[:args.limit]
    t0 = time.time()
    if args.skip_translate:
        rows = {}
        for p in out.glob("translate_*.jsonl"):
            rows.update(read_log(p)[0])
    else:
        rows = translate_all(ws, addrs, out, args.workers, args.worker_timeout)
    t1 = time.time()
    print(f"translated {len(rows)} functions in {t1 - t0:.0f}s", flush=True)
    ok = sorted(a for a, r in rows.items() if r["status"] == "ok")
    built, diags = compile_all(ws, ok, out, args.batch, args.batch_timeout)
    t2 = time.time()
    for a in ok:
        r = rows[a]
        if a in built and not diags.get(a):
            r["status"] = "built"
        else:
            r["status"] = "compile"
            r["diags"] = diags.get(a, [("?", "no object and no message")])[:4]
    # ---- summary, grouped by cause
    st = collections.Counter(r["status"] for r in rows.values())
    insn = collections.Counter()
    for r in rows.values():
        insn[r["status"]] += r.get("insns") or sum((hi - lo) // 4 for lo, hi in w.bodies[int(r["addr"], 16)])
    causes = collections.defaultdict(list)
    for r in rows.values():
        if r["status"] == "untranslated":
            key = "untranslated: " + re.sub(r" at [0-9a-f]{8}.*$| [0-9a-f]{8}\b.*$", "", r["why"])
        elif r["status"] == "crash":
            key = "translator crash: " + r["why"].split(":")[0]
        elif r["status"] == "timeout":
            key = "translation timed out"
        elif r["status"] == "lint":
            key = "lint: " + re.sub(r"'[^']*'", "'…'", r["lint"][0])[:90]
        elif r["status"] == "compile":
            c, m = r["diags"][0]
            key = f"compile {c}: " + re.sub(r"'[^']*'", "'…'", m)[:90]
        else:
            continue
        causes[key].append(r["addr"])
    summary = {"functions": len(rows), "status": dict(st), "instructions": dict(insn),
               "seconds": {"translate": round(t1 - t0), "compile": round(t2 - t1)},
               "causes": {k: {"count": len(v), "examples": v[:8]} for k, v in
                          sorted(causes.items(), key=lambda kv: -len(kv[1]))}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "functions.json").write_text(json.dumps(sorted(rows.values(), key=lambda r: r["addr"]), indent=0),
                                        encoding="utf-8")
    tot = sum(insn.values())
    print(f"{len(rows)} functions: {dict(st)}")
    print(f"instructions: built {insn['built']}/{tot} ({100 * insn['built'] / max(tot, 1):.1f}%)")
    for k, v in summary["causes"].items():
        print(f"  {v['count']:5}  {k}   e.g. {', '.join(v['examples'][:4])}")


if __name__ == "__main__":
    main()
