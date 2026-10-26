"""Fresh oracle: every matched harness made by today's oracle, and the matched raw C passing all of them.

tools/fresh_oracle.py and tools/final_audit.py use it. Read-only on the project:
- Stamper computes the stamp ghidra.prepare would write today (core/oracle_stamp) from the same inputs: the target's
  scripts plus the project's TcInputs.java (what paths.sync_scripts assembles into state/ghidra_scripts), the facts
  text (ghidra.facts_body), hints, vector counts, fsca table, catalog, binary and Ghidra install. It neither
  assembles the scripts folder nor writes the facts file.
- scan() lists each matched function whose harness is not current, by kind (KINDS).
- recount() re-judges every matched function's raw C (matched/ADDR.c) on its own harness with the raw judge
  (check.run_check: lint, /W4 /WX build, 96 vectors), in parallel, building in a temporary folder removed after each.
Re-preparing the harnesses scan() lists: tools/reprepare_stale.py --stamps (servers stopped; bounded per run by
--max, resumable: a re-prepared harness carries today's stamp, so the next run skips it).
"""
import concurrent.futures
import pathlib
import shutil
import tempfile

from . import callee_drift, check, ghidra, oracle_stamp, paths

KINDS = {
    "no harness": "no harness/ADDR/meta.json",
    "no stamp": "the harness carries no oracle stamp (prepared before stamps existed)",
    "old stamp": "the harness was made from other oracle inputs (scripts, TcInputs, facts, hints, vector counts, "
                 "catalog, fsca table, binary or Ghidra changed since)",
    "callee records": "a callee record differs from the callee's own harness signature",
}


def matched_functions():
    """Catalog functions whose main-ledger status is matched (the catalog's matched count), in address order."""
    cat = paths.read_json(paths.CATALOG) or {}
    funcs = {e["addr"] for e in (cat.get("functions", []) if isinstance(cat, dict) else cat)}
    led = (paths.read_json(paths.LEDGER) or {}).get("functions") or {}
    return sorted((a for a, r in led.items() if r.get("status") == "matched" and a in funcs), key=lambda a: int(a, 16))


class Stamper:
    """today(addr): the oracle stamp a prepare of addr would record now (the scripts, facts and catalog as they were
    when this Stamper was made: one per scan)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.scripts = paths.target_scripts()
        self.scripts["TcInputs.java"] = paths.INPUTS
        self.facts = oracle_stamp.facts_hashed(ghidra.facts_body(cfg))
        self.shared = oracle_stamp.common(self.scripts, self.facts)  # the scripts and catalog as of this Stamper

    def today(self, addr):
        hints = paths.HINTS / f"{addr}.txt"
        return oracle_stamp.stamp_of(self.cfg, addr, self.scripts, self.facts, hints if hints.exists() else None,
                                     ghidra.dev_vectors(self.cfg, addr), self.cfg["vectors_hidden"], self.shared)


def scan(cfg, addrs):
    """{addr: (kind, detail)} of the harnesses of addrs that are not current (KINDS); current ones are left out."""
    st, sig_of, out = Stamper(cfg), callee_drift.Signatures(paths.HARNESS), {}
    for a in addrs:
        meta = paths.read_json(paths.HARNESS / a / "meta.json")
        if not meta:
            out[a] = ("no harness", "")
        elif not meta.get("oracle_stamp"):
            out[a] = ("no stamp", "")
        elif meta["oracle_stamp"] != (now := st.today(a)):
            out[a] = ("old stamp", f"{meta['oracle_stamp']} (today {now})")
        else:
            bad = callee_drift.drift(dict(meta, addr=meta.get("addr", a)), sig_of)
            if bad:
                out[a] = ("callee records", ", ".join(c for c, _, _ in bad))
    return out


def judge_one(cfg, addr, root, memo=None):
    """The raw judge's verdict on matched/ADDR.c with its own harness: (verdict, detail). memo (core/verdict_memo,
    the wave's step 5 only): a PASS recorded earlier in the same wave on exactly these inputs is taken as it is."""
    src = paths.MATCHED / f"{addr}.c"
    if not src.exists():
        return "MISSING", "no matched/ADDR.c"
    if not (paths.HARNESS / addr / "meta.json").exists():
        return "MISSING", "no harness"
    if memo is not None and (total := memo.passed(addr, src)) is not None:
        return "PASS", f"{total} vectors"
    bd = pathlib.Path(tempfile.mkdtemp(prefix=f"{addr}_", dir=root))
    try:
        res = check.run_check(cfg, addr, str(src), build_dir=bd)
    except Exception as e:  # noqa: BLE001  (one broken function must not stop the recount)
        return "ERROR", f"{type(e).__name__}: {e}"[:200]
    finally:
        shutil.rmtree(bd, ignore_errors=True)
    if res["verdict"] == "PASS":
        return "PASS", f"{res.get('total')} vectors"
    why = "; ".join((res.get("lint") or []) + (res.get("build") or []))[:160] or res.get("run_error") or \
        f"{res.get('failing')}/{res.get('total')} vectors differ"
    return "FAIL", f"{res.get('stage')}: {why}"


def recount(cfg, addrs, workers=4, log=print, memo=None):
    """{addr: (verdict, detail)} for every addr, judged in parallel (workers threads, each a bounded build and run).
    memo: see judge_one (only tools/wave.py's step 5 passes one)."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="fresh_oracle_"))
    out, done = {}, 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            kw = {"memo": memo} if memo is not None else {}
            futs = {ex.submit(judge_one, cfg, a, root, **kw): a for a in addrs}
            for f in concurrent.futures.as_completed(futs):
                out[futs[f]] = f.result()
                done += 1
                if done % 200 == 0:
                    log(f"  recount: {done}/{len(addrs)} judged, "
                        f"{sum(v[0] == 'PASS' for v in out.values())} pass so far")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return out


def report(cfg, recount_too=False, workers=4, log=print):
    """Print the fresh-oracle check; returns (ok, facts). ok: every matched harness current and, with recount_too, the
    raw recount passes every matched function (passed == the catalog's matched count)."""
    matched = matched_functions()
    stale = scan(cfg, matched)
    by_kind = {}
    for a, (k, _) in stale.items():
        by_kind.setdefault(k, []).append(a)
    log(f"oracle stamps: {len(matched) - len(stale)}/{len(matched)} matched harnesses current; not current: "
        f"{len(stale)}" + "".join(f"; {k} {len(v)}" for k, v in sorted(by_kind.items())))
    for k, v in sorted(by_kind.items()):
        log(f"  {k} ({KINDS[k]}): {len(v)}: " + " ".join(v[:40]) + (" ..." if len(v) > 40 else ""))
    if stale:
        log("  re-prepare them (servers stopped, bounded, resumable): python tools/bounded.py 7200 python "
            "tools/reprepare_stale.py --project DIR --stamps --max 100   (repeat until it reports nothing left)")
    facts = {"matched": len(matched), "stale": len(stale), "stale_by_kind": {k: len(v) for k, v in by_kind.items()}}
    ok = not stale
    if recount_too:
        res = recount(cfg, matched, workers=workers, log=log)
        passed = sum(v[0] == "PASS" for v in res.values())
        bad = {a: v for a, v in res.items() if v[0] != "PASS"}
        log(f"raw recount: {passed}/{len(matched)} matched raw C pass their own harness (catalog matched count "
            f"{len(matched)})")
        for a, (v, d) in sorted(bad.items())[:60]:
            log(f"  {v} {a}: {d}")
        facts |= {"recount_passed": passed, "recount_failed": sorted(bad)}
        ok = ok and passed == len(matched)
    return ok, facts
