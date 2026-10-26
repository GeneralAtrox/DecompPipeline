"""Oracle probe: prepare any functions (skipped, failed or matched) with the CURRENT working-tree oracle on a private
copy of one project, and report how many valid vectors each gets; optionally judge a C file on the fresh harness.

usage: python tools/bounded.py 3600 python tools/oracle_probe.py --project DIR [--tag T] [--jobs N] [--catalog]
           [--json OUT] [--judge-matched] [--c ADDR=FILE ...] ADDR... | @FILE
  --tag            names the private work dirs: tests/.work/probe-T and tests/ghidra_work/probe_T (default: the
                   project folder's name); reused while the live catalog is unchanged, like tools/regress.py
  --judge-matched  also judge the live matched/ADDR.c on the fresh harness (a matched function: does it still pass?)
  --c ADDR=FILE    judge FILE as ADDR's raw C on the fresh harness (e.g. a failed function's best attempt)
  --catalog        re-run the catalog fixups and TcCatalog on the private copy first (after changing those scripts)
Nothing of the live project is written (tools/regress_project.setup redirects every path the pipeline writes into the
work dir). Per function: dev and hidden valid/tried, the top invalid reasons, the refusal (meta "unsupported"), the
preparation time and, with a C file, the verdict. Exit 0 (a report tool).
Used for the r15 oracle tooling-gap round: one probe before a change and one after, on the same list.
"""
import argparse
import json
import pathlib
import queue
import sys
import threading
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tools"))
import regress_project as rp  # noqa: E402


def addresses(args):
    out = []
    for a in args:
        out += pathlib.Path(a[1:]).read_text(encoding="utf-8").split() if a.startswith("@") else [a]
    return [x.lower() for x in dict.fromkeys(out)]


def summary_line(r):
    inv = ", ".join(f"{k} x{v}" for k, v in sorted((r.get("invalid") or {}).items(), key=lambda kv: -kv[1])[:3])
    verdict = f" C {r['verdict']} {r.get('failing', '')}" if r.get("verdict") else ""
    why = f" REFUSED {r['unsupported'][:90]}" if r.get("unsupported") else ""
    return (f"{r['addr']} dev {r.get('dev', '?')} hidden {r.get('hidden', '?')} {r.get('secs', 0):5.0f}s{verdict}{why}"
            + (f" | {inv}" if inv and not r.get("ok") else "") + (f" ERROR {r['error']}" if r.get("error") else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--tag")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--judge-matched", action="store_true")
    ap.add_argument("--c", action="append", default=[], metavar="ADDR=FILE")
    ap.add_argument("addrs", nargs="+")
    a = ap.parse_args()
    tag = a.tag or pathlib.Path(a.project).resolve().name
    a.work = str(TOOL / "tests" / ".work" / f"probe-{tag}")
    a.ghidra_work = str(TOOL / "tests" / "ghidra_work" / f"probe_{tag}")
    t0 = time.time()
    lock = threading.Lock()

    def log(msg):
        with lock:
            print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    paths, ghidra, check, cfg, live = rp.setup(a)
    work = pathlib.Path(a.work).resolve()
    todo = addresses(a.addrs)
    cfiles = {k.lower(): pathlib.Path(v) for k, v in (c.split("=", 1) for c in a.c)}
    if a.judge_matched:
        for x in todo:
            if x not in cfiles and (live["matched"] / f"{x}.c").exists():
                cfiles[x] = live["matched"] / f"{x}.c"
    jobs = max(1, min(a.jobs, len(todo)))
    rp.ghidra_copies(paths, ghidra, cfg, live, jobs, a.catalog, log)
    q = queue.Queue()
    for x in todo:
        q.put(x)
    results = []

    def one(addr, slot):
        harness, t = work / "harness" / addr, time.time()
        r = {"addr": addr}
        try:
            staging = {"scripts": paths.sync_scripts(), "packet": work / "packets" / addr, "harness": harness,
                       "hints": live["hints"] / f"{addr}.txt", "callees": live["harness"]}
            meta, s = ghidra.prepare(cfg, addr, slot_index=slot, staging=staging)
            s = s or {}
            r.update(dev=f"{s.get('dev_valid')}/{s.get('dev_tried')}", hidden=f"{s.get('hidden_valid')}/{s.get('hidden_tried')}",
                     dev_valid=s.get("dev_valid") or 0, hidden_valid=s.get("hidden_valid") or 0,
                     blocks=f"{s.get('blocks_hit_all')}/{s.get('blocks_total')}", invalid=s.get("invalid") or {},
                     examples=(s.get("examples") or [])[:3], unsupported=meta.get("unsupported"))
            r["ok"] = not r["unsupported"] and r["dev_valid"] >= 16 and r["hidden_valid"] >= 16
        except Exception as e:  # noqa: BLE001
            r["error"] = str(e).splitlines()[0][:200]
        r["secs"] = round(time.time() - t, 1)
        if addr in cfiles and not r.get("error") and not r.get("unsupported"):
            try:
                res = check.run_check(cfg, addr, str(cfiles[addr]), build_dir=work / "build" / addr, harness_dir=harness)
                diffs = (res.get("first_diffs") or [{}])[0].get("diffs") or res.get("lint") or res.get("build") or []
                r.update(verdict=res["verdict"], stage=res["stage"], failing=f"{res.get('failing')}/{res.get('total')}",
                         detail=(res.get("run_error") or (diffs[0] if diffs else ""))[:200])
            except Exception as e:  # noqa: BLE001
                r.update(verdict="ERROR", detail=str(e).splitlines()[0][:200])
        log(summary_line(r))
        return r

    def worker(slot):
        while True:
            try:
                x = q.get_nowait()
            except queue.Empty:
                return
            r = one(x, slot)
            with lock:
                results.append(r)

    ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(1, jobs + 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    results.sort(key=lambda r: todo.index(r["addr"]))
    ok = sum(1 for r in results if r.get("ok"))
    passed = sum(1 for r in results if r.get("verdict") == "PASS")
    log(f"done: {len(results)} prepared, {ok} with 16+ valid dev and hidden vectors and no refusal"
        + (f", {passed}/{len(cfiles)} C PASS" if cfiles else "") + f"; {sum(r['secs'] for r in results):.0f} s preparing")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"project": str(live["root"]), "secs": round(time.time() - t0, 1),
                                                    "results": results}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
