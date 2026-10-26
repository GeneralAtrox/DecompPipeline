"""Prepare a list of functions in Ghidra ahead of the workers, several Ghidra slots at once, and print each one's
oracle figures. With config "harness_reuse" on, a worker then reuses the harness without opening Ghidra
(core/job.reusable_harness), so a large worker pool never waits on Ghidra slots. Functions whose harness is already current are left alone.

usage: python tools/prepare_list.py --project DIR ADDR|@FILE... [--slots N] [--serve] [--out FILE.tsv] [--dry]
       python tools/prepare_list.py --project DIR --harness-stale [ADDR|@FILE...] [--slots N] [--serve] [--out FILE.tsv]
           [--dry]
  @FILE      one or more addresses per line ('#' comments); keep long hex lists off the command line
  --harness-stale  also every function the readable ledger marks harness_stale (refine / verify: its matched raw C
             fails its harness, so the harness needs a prepare): the list an operator copied out by hand
  --slots N  Ghidra slots for this run instead of config "ghidra_parallel" (slot 0 is the master project, the rest
             are read-only copies, made on first use)
  --serve    prepare on read-only slot copies 1..N (N = --slots or "ghidra_parallel"), each through its long-lived
             Ghidra (core/ghidra_server, config "ghidra_serve" enabled for this run): one JVM start and one queue turn
             per copy instead of per function, the same harness bytes (reprepare_stale --serve's rule). The master's
             queued marks wait for its next prepare
  --out      a TSV of the results: addr, state (current / prepared / error), signature, unsupported, dev_valid,
             hidden_valid, blocks_hit_all, blocks_total, block_pct, detail
  --dry      list which functions would be prepared, prepare nothing
Servers stopped: it refuses while the project's server answers (prepare uses the master Ghidra project and writes
harness/). Resumable: a prepared harness is current, so a run cut short (tools/bounded.py) leaves only the rest.
Exit 0 when every function has a current harness, 1 otherwise.
"""
import argparse
import concurrent.futures
import pathlib
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import expand_addrs, server_state, use_project  # noqa: E402

COLUMNS = ("addr", "state", "signature", "unsupported", "dev_valid", "hidden_valid", "blocks_hit_all", "blocks_total",
           "block_pct", "detail")


def row(addr, state, meta=None, summary=None, detail=""):
    s = summary or {}
    total, hit = s.get("blocks_total"), s.get("blocks_hit_all")
    pct = round(100.0 * hit / total, 1) if total else ""
    return {"addr": addr, "state": state, "signature": (meta or {}).get("signature", ""),
            "unsupported": (meta or {}).get("unsupported") or "", "dev_valid": s.get("dev_valid", ""),
            "hidden_valid": s.get("hidden_valid", ""), "blocks_hit_all": "" if hit is None else hit,
            "blocks_total": "" if total is None else total, "block_pct": pct, "detail": detail}


def run(cfg, addrs, prepare, reusable, workers, log=print):
    """Prepare every addr without a current harness, workers at a time. prepare(addr) -> (meta, summary) takes a
    Ghidra slot itself; reusable(addr) -> (meta, summary) or None. Returns the result rows in addrs' order."""
    rows, todo = {}, []
    for a in addrs:
        got = reusable(a)
        if got:
            rows[a] = row(a, "current", *got)
        else:
            todo.append(a)
    log(f"{len(addrs)} function(s): {len(addrs) - len(todo)} current, {len(todo)} to prepare ({workers} slot(s))")

    def one(a):
        try:
            meta, summary = prepare(a)
        except Exception as e:  # noqa: BLE001  (one bad prepare must not stop the others)
            return row(a, "error", detail=f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}")
        return row(a, "prepared", meta, summary)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for n, rw in enumerate(ex.map(one, todo), 1):
            rows[rw["addr"]] = rw
            log(f"  [{n}/{len(todo)}] {rw['addr']}: {rw['state']}"
                + (f" blocks {rw['blocks_hit_all']}/{rw['blocks_total']} ({rw['block_pct']}%)" if rw["blocks_total"] != "" else "")
                + (f" unsupported: {rw['unsupported']}" if rw["unsupported"] else "")
                + (f" {rw['detail']}" if rw["detail"] else ""))
    return [rows[a] for a in addrs]


def write_tsv(path, rows):
    clean = lambda v: str(v).replace("\t", " ").replace("\n", " ")  # noqa: E731
    lines = ["\t".join(COLUMNS)] + ["\t".join(clean(r[c]) for c in COLUMNS) for r in rows]
    pathlib.Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def harness_stale():
    """The functions the readable ledger (core/refine) marks harness_stale, in address order."""
    from core import refine
    return sorted(a for a, r in refine.load_ledger().items() if r.get("status") == "harness_stale")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("addrs", nargs="*")
    ap.add_argument("--harness-stale", action="store_true")
    ap.add_argument("--slots", type=int)
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    from core import oplog  # the manual operator steps record (docs/DEFINITION_OF_DONE.md gate 2)
    if not a.dry:
        oplog.begin("prepare_list.py", [oplog.workspace(a.project)])
    ws = use_project(a.project)
    addrs = expand_addrs(a.addrs)
    if a.harness_stale:
        addrs = list(dict.fromkeys(addrs + harness_stale()))
    if not addrs:
        ap.error("no addresses (ADDR, @FILE or --harness-stale)")
    if not a.dry and server_state(ws, timeout=3) is not None:
        sys.exit("the project's server is running: stop it first (prepare needs the master Ghidra project)")
    from core import ghidra, job, paths
    cfg = paths.config()
    if a.slots:
        cfg["ghidra_parallel"] = a.slots  # this run only
    workers = max(1, int(cfg.get("ghidra_parallel", 1)))
    if a.serve:  # this run only: slots 1..N served; slot 0, the master, unused
        cfg["ghidra_parallel"] = workers + 1
        cfg["ghidra_serve"] = {**(cfg.get("ghidra_serve") or {}), "enabled": True}
    known = {f["addr"] for f in (paths.read_json(paths.CATALOG) or {}).get("functions", [])}
    unknown = [x for x in addrs if x not in known]
    if unknown:
        sys.exit(f"{len(unknown)} address(es) are not catalog functions: {' '.join(unknown[:10])}")

    def prepare(addr):
        with ghidra.slot(cfg, first=1 if a.serve else 0) as i:
            return ghidra.prepare(cfg, addr, slot_index=i)

    def reusable(addr):
        return job.reusable_harness(cfg, addr)

    if a.dry:
        todo = [x for x in addrs if not reusable(x)]
        print(f"{len(addrs)} function(s): {len(addrs) - len(todo)} current, {len(todo)} to prepare [dry]")
        return 0
    rows = run(cfg, addrs, prepare, reusable, workers, log=lambda m: print(m, flush=True))
    if a.out:
        write_tsv(a.out, rows)
    bad = [r for r in rows if r["state"] == "error"]
    print(f"{sum(r['state'] == 'prepared' for r in rows)} prepared, {sum(r['state'] == 'current' for r in rows)} "
          f"already current, {len(bad)} error(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    from core import oplog
    sys.exit(oplog.main(main))
