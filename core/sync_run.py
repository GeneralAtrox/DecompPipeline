"""refine.py sync-names: keep readable files in step with the names registry, without an agent.

For each readable file using a raw name the registry has named since (core/raw_names), or an old name a designer
renamed (the entry's "was": ["g_oldName"]; renamed in code and comments, never as a member), or whose own definition no
longer matches its registry prototype (a designer's prototype change), core/sync_names rewrites it, and the
readable judge (readable_check.check_readable, against the command's header snapshot) decides:
1. "clean": every raw name, no casts added (a `(u32)(uintptr_t)` in front of a now-typed pointer argument dropped);
2. "cast": every raw name, each changed argument or result cast to its new type;
3. "required": only the names the judge refuses in raw form (the file's own definition, a callee whose raw signature
   no longer maps onto its prototype, a function used as data), cast; the others stay raw (they still compile).
The first form that PASSes replaces readable/src/ADDR.c and is recorded in the readable ledger as an attempt
{"kind": "sync", "verdict": "PASS", "renames": [...], no tokens}; the row becomes readable (verified, headers).
When none passes, the file and its row stay as they are (`sync_refused` notes why) and verify judges the old file.
Parameters are paired by storage (readable_abi); a local holding a converted old parameter takes or becomes the new
one, and a local named like a new parameter is renamed NAME_local. A row whose function is running (an agent redo in
flight) is skipped, and so is one another process holds (batch 21: a file needing a sync is built and written under
the function's claim, core/fn_claim). Idempotent: a file without such raw names or prototype changes is not touched.
"""
import concurrent.futures
import os

from . import filelock, fn_claim, header_snapshot, paths, raw_names, readable, readable_abi, refine
from . import readable_check as rcheck
from .sync_names import Sync, SyncRefused, own_header

TIERS = (("clean", "all"), ("cast", "all"), ("cast", "required"))


def required(reg, addr, meta, named):
    """The raw names in named the judge refuses as they are (RR4, R3, undeclared): every one but the callees whose
    raw signature still fits their prototype (readable_abi.raw_alias_ok), and named globals."""
    harness = paths.HARNESS / addr
    ok = {f"fn_{c['addr']}" for c in meta["callees"] if c["addr"] != addr
          and readable_abi.raw_alias_ok(reg, c, rcheck.callee_storage(harness, c))}
    return {r: n for r, n in named.items() if r not in ok and not raw_names.RAW_GLOBAL.fullmatch(r)}


def plan(reg, addr, text, meta, named, mode, which):
    """(new text, Sync) for one tier, or raises SyncRefused."""
    names = named if which == "all" else required(reg, addr, meta, named)
    s = Sync(reg, addr, meta, lambda c: rcheck.callee_storage(paths.HARNESS / addr, c), names, mode)
    return s.run(text), s


def needs_sync(reg, fns, globs, addr, text):
    """({raw: name} of the named raw identifiers in text, whether its own header differs from the registry's)."""
    named, _ = raw_names.find(text, fns, globs, was_names(reg))
    meta = paths.read_json(paths.HARNESS / addr / "meta.json") if addr in reg.functions else None
    try:
        header = bool(meta) and own_header(reg, addr, meta, raw_names.mask(text)[0]) is not None
    except (SyncRefused, readable.RegistryError):
        header = False
    return named, header


def sync_one(cfg, reg, fns, globs, addr, dry=False):
    """Sync one file. Returns {"addr", "outcome": unchanged|skipped|synced|refused, ...}."""
    src = readable.SRC / f"{addr}.c"
    text = src.read_text(encoding="utf-8", errors="replace")
    named, header = needs_sync(reg, fns, globs, addr, text)
    if not named and not header:
        return {"addr": addr, "outcome": "unchanged"}
    try:  # it builds and may rewrite the file: under the function's claim (core/fn_claim); busy: skipped
        with fn_claim.claim(addr, "sync-names", fn_claim.wait_s(cfg)):
            return apply(cfg, reg, addr, text if src.read_text(encoding="utf-8", errors="replace") == text
                         else None, named, dry)
    except fn_claim.Busy as e:
        return {"addr": addr, "outcome": "skipped", "why": str(e)}


def apply(cfg, reg, addr, text, named, dry):
    """sync_one's tiers on the file's text (None: the file changed since it was read: skipped)."""
    if text is None:
        return {"addr": addr, "outcome": "skipped", "why": "the file changed while sync-names read it"}
    row = refine.load_ledger().get(addr, {})
    if row.get("status") == "running":
        return {"addr": addr, "outcome": "skipped", "why": "an agent redo is running"}
    missing = refine.raw_missing(addr)
    if missing:
        return {"addr": addr, "outcome": "skipped", "why": missing}
    meta = paths.read_json(paths.HARNESS / addr / "meta.json")
    tried, seen = [], set()
    for mode, which in TIERS:
        try:
            new, s = plan(reg, addr, text, meta, named, mode, which)
        except SyncRefused as e:
            tried.append(f"{mode}/{which}: {e}")
            continue
        if new in seen or new == text:
            continue
        seen.add(new)
        work = paths.BUILD / addr / "rd_sync.c"
        work.parent.mkdir(parents=True, exist_ok=True)
        work.write_text(new, encoding="utf-8")
        res = rcheck.check_readable(cfg, addr, str(work), paths.BUILD / addr / "rd_sync", reg=reg)
        if res["verdict"] == "PASS":
            left = sorted(set(named) - {r for r, what in s.done if what != "comment"})
            out = {"addr": addr, "outcome": "synced", "tier": f"{mode}/{which}", "renames": s.summary(),
                   "shims": s.shims, "raw_left": left, "result": res}
            if not dry:
                record(addr, new, out, res)
            return out
        why = "; ".join((res.get("lint") or []) + (res.get("build") or []))[:240] or \
            f"{res.get('failing')}/{res.get('total')} vectors differ"
        tried.append(f"{mode}/{which}: {res['stage']}: {why}")
    out = {"addr": addr, "outcome": "refused", "why": " | ".join(tried)[:600], "names": named}
    if not dry:
        refine.update(addr, sync_refused={"at": refine.now(), "why": out["why"], "names": sorted(named)})
    return out


def record(addr, new, out, res):
    """Write the synced file and its ledger record (one attempt-like entry, kind "sync")."""
    src = readable.SRC / f"{addr}.c"
    tmp = src.with_suffix(f".c.sync{os.getpid()}")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, src)
    cur = header_snapshot.active_hash()

    def merge(led):
        row = led.setdefault(addr, {})
        atts = row.setdefault("attempts", [])
        atts.append({"n": len(atts) + 1, "kind": "sync", "at": refine.now(), "verdict": "PASS", "stage": res["stage"],
                     "failing": res.get("failing"), "total": res.get("total"), "tier": out["tier"],
                     "renames": out["renames"], "shims": out["shims"], "raw_left": out["raw_left"], "headers": cur})
        row.update(status="readable", verified=refine.now(), headers=cur)
        for k in ("stale_headers", "sync_refused"):
            row.pop(k, None)
        return row

    with refine._LOCK:
        filelock.update_json(refine.LEDGER, merge, {})


def registry_names(reg):
    """({addr: name} of named functions, {addr: name} of named globals) of a registry."""
    fns = {a: readable.parse_proto(e["proto"])[1] for a, e in reg.functions.items()}
    globs = {a: readable.global_name(e["decl"]) for a, e in reg.globals.items()}
    return ({a: n for a, n in fns.items() if n != f"fn_{a}"},
            {a: n for a, n in globs.items() if n.lower() not in (f"g_{a}", f"dat_{a}")})


def was_names(reg):
    """{old name: current name} of a registry: the designer renames its entries record ("was": ["g_oldName"]), cached on
    the registry object."""
    if getattr(reg, "_was", None) is None:
        entries = [(e, readable.parse_proto(e["proto"])[1]) for e in reg.functions.values()]
        entries += [(e, readable.global_name(e["decl"])) for e in reg.globals.values()]
        reg._was = raw_names.was_map(entries, {n for _, n in entries})
    return reg._was


def raw_report(reg, files):
    """({addr: {raw: name}} of files still using raw names the registry has named, (occurrences, distinct functions,
    files) of raw names of functions without a name), against reg."""
    fns, globs = registry_names(reg)
    bad, occ, distinct, nfiles = {}, 0, set(), 0
    for addr in files:
        p = readable.SRC / f"{addr}.c"
        if not p.exists():
            continue
        named, unnamed = raw_names.find(p.read_text(encoding="utf-8", errors="replace"), fns, globs,
                                        was_names(reg))
        if named:
            bad[addr] = named
        occ, nfiles = occ + sum(unnamed.values()), nfiles + bool(unnamed)
        distinct |= set(unnamed)
    return bad, (occ, len(distinct), nfiles)


def sync(cfg, files, workers=4, dry=False, reg=None, log=print):
    """Sync files (addresses). Returns {addr: outcome dict} of the files that had raw names of named functions."""
    reg = reg or readable.Registry()
    fns, globs = registry_names(reg)
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for res in ex.map(lambda a: _safe(cfg, reg, fns, globs, a, dry), files):
            if res["outcome"] != "unchanged":
                out[res["addr"]] = res
    tag = "SYNC (dry)" if dry else "SYNC"
    for addr, r in sorted(out.items()):
        if r["outcome"] == "synced":
            ren = ", ".join(f"{x['from']}->{x['to']} {x['what']}" + (f" x{x['n']}" if x["n"] > 1 else "")
                            for x in r["renames"])
            log(f"{tag} {addr}: {r['tier']}: {ren}" + (f"; shims: {'; '.join(r['shims'])}" if r["shims"] else "")
                + (f"; still raw: {' '.join(r['raw_left'])}" if r["raw_left"] else ""))
        else:
            log(f"{tag} {r['outcome'].upper()} {addr}: {r.get('why')}")
    n = {k: sum(r["outcome"] == k for r in out.values()) for k in ("synced", "refused", "skipped")}
    log(f"{tag}: {len(files)} file(s), {len(out)} needing a sync: {n['synced']} synced"
        f"{' (dry: nothing written)' if dry else ''}, {n['refused']} refused (file kept), {n['skipped']} skipped")
    return out


def _safe(cfg, reg, fns, globs, addr, dry):
    try:
        return sync_one(cfg, reg, fns, globs, addr, dry)
    except (SyncRefused, readable.RegistryError, OSError, ValueError, KeyError) as e:
        return {"addr": addr, "outcome": "refused", "why": f"{type(e).__name__}: {e}"[:300], "names": {}}
