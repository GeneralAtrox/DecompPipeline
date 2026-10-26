"""One project's part of tools/regress.py (run by it in a child process; not meant to be run by hand).

Judges fixtures and sampled matched functions with the CURRENT working-tree checker (core/check.py, the target's
runtime and Ghidra scripts) against harnesses prepared afresh into a private work dir. Nothing of the live project
is written: its Ghidra project is copied (fresh slot1, else the master), state/ files the checker reads are
copied, and every path the pipeline writes to (state logs, slots, packets, harnesses, builds) points into WORK.

usage: regress_project.py --project DIR --work DIR --ghidra-work DIR --plan PLAN.json --out RESULT.json
       [--jobs N] [--reuse] [--catalog] [--cache DIR --prepare-hash H]
  --ghidra-work  where the Ghidra project copies go: Ghidra refuses a project path with a '.'-element (tests/.work)
  PLAN.json   {"fixtures": [{id, pattern, addr, pass, fail[, vectors_dev][, config]}], "sample": N, "seed": S,
               "key": KEY, "config": {key: value} (regress.py --set: over the project's config, for this run only),
               "list": [ADDR, ...] (matched functions to judge instead of the sample), "parity": true (also judge each
               sampled or listed function's raw C with the readable runtime: item rd.ADDR)}
              config (a fixture's): over the project's config for that fixture alone (a staged switch the project has
              not turned on, e.g. fpscr_model); its harness is prepared on its own (WORK/harness/ADDR@ID)
              runtime: "readable" judges the fixture's raw C with the readable runtime instead (readable_check.check_raw,
              the 32-bit build refine.py uses) on the same harness; "readable-src" judges readable C (#include
              "game.h") as refine.py does (readable_check.check_readable) with an empty names registry, so the C uses
              raw names (fn_8c...) and raw prototypes and never depends on the project's readable/names; its
              optional "names" ({"functions": {ADDR: {"proto"}}, "globals": {ADDR: {"decl"}}}, a readable/names
              file) is the only registry it has
              needs_catalog: true for a fixture whose function only the working tree's catalog fixups make (a new
              catalog rule): its project's private copy re-runs the fixups and TcCatalog as with --catalog
              vectors_dev: a dev vector count for that fixture's function, merged into the private copy of
              state/vectors.json (the per-function override ghidra.dev_vectors reads); its items report BADDEV
              when the fresh harness does not hold exactly that many valid dev vectors
  --reuse     keep the harnesses in WORK whose oracle stamp is current (core/oracle_stamp.py), re-prepare the rest
              (quick C-only iteration; not a gate run); a "parity" plan does the same. Counts go to RESULT.json
              "harnesses". An item judged on no vector (0/0) reads ORACLE, not FAIL; a group with ORACLE or an
              out-of-time refusal is prepared again alone after the parallel run, and that result is recorded
  --catalog   also re-run the catalog fixups (TcPools, TcDead, TcRuns, TcOrphans, TcPrune, TcSwitch, TcCont) and
              TcCatalog on the private copy (after changing those scripts: a change of the scripts makes a new copy);
              again for a new WORK whose state has no catalog yet
  --cache     the harness cache (tools/regress.py --targeted): every harness prepared here, with its packet, is kept
              in DIR/GROUP/STAMP-H (H: --prepare-hash, tools/regress_targets.prepare_hash, the hash of the Python
              that makes a harness, which the oracle stamp does not cover), unless its items show oracle trouble.
              With plan "reuse_cache": true, a group whose entry for today's stamp and H exists is judged on a copy
              of it instead of being prepared (callee records checked as with --reuse). Entries are written once
              (a temporary folder renamed into place) and never changed; each group keeps its two newest
  plan "items": "readable" judges only the readable items (fixtures with "runtime": "readable" or "readable-src",
              parity items): a change to the readable judge alone
"""
import argparse
import hashlib
import json
import os
import pathlib
import queue
import random
import re
import shutil
import sys
import threading
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
IGNORE = shutil.ignore_patterns("*.lock", "*.lock~")


def stamp_of(catalog):
    st = catalog.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def copy_tree_if_stale(src, dst, stamp):
    """dst = a copy of the Ghidra project folder src, unless dst already holds a copy made at `stamp`."""
    mark = dst / "copied_from.txt"
    if mark.exists() and mark.read_text(encoding="utf-8") == stamp:
        return False
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=IGNORE)
    mark.write_text(stamp, encoding="utf-8")
    return True


def fsca_table(live_state, dst):
    """The private state's FSCA table: the current file (core/fsca.file(), as `decomp.py setup` writes it) when the
    live project has one (state/fsca/, or the state/fsca_table.bin of before). The live file may be of a format
    before (the one-table 512 KB file), which every reader refuses: copied, it failed every preparation and judge
    until the live project ran setup again."""
    if (live_state / "fsca").is_dir() or (live_state / "fsca_table.bin").exists():
        from core import fsca
        fsca.ensure(dst)  # a new work dir has none; a kept one is rewritten only when the current file changed


def setup(a):
    """Redirect core.paths into WORK; copy what the checker reads. Returns (paths, ghidra, check, cfg, live)."""
    os.environ["DECOMP_PROJECT"] = str(pathlib.Path(a.project).resolve())
    from core import check, ghidra, paths
    assert pathlib.Path(check.__file__).resolve().is_relative_to(TOOL), check.__file__
    work, gwork = pathlib.Path(a.work).resolve(), pathlib.Path(a.ghidra_work).resolve()
    live = {"root": paths.ROOT, "state": paths.STATE, "ghidra": paths.GHIDRA_PROJECT, "slots": paths.GHIDRA_SLOTS,
            "harness": paths.HARNESS, "matched": paths.MATCHED, "reference": paths.REFERENCE, "hints": paths.HINTS,
            "inputs": paths.INPUTS, "catalog": paths.CATALOG}
    for p in (live["root"], live["ghidra"].parent):
        for w in (work, gwork):
            assert not w.is_relative_to(p), f"work dir {w} lies inside the live project {p}"
    state = work / "state"
    state.mkdir(parents=True, exist_ok=True)
    paths.STATE, paths.HARNESS, paths.PACKETS = state, work / "harness", work / "packets"
    paths.BUILD, paths.SCRIPTS, paths.MARKS = work / "build", work / "scripts", state / "ghidra_marks.tsv"
    paths.GHIDRA_PROJECT, paths.GHIDRA_SLOTS = gwork / "master", gwork / "slots"
    paths.CATALOG, paths.CODE_RANGES = state / "catalog.json", state / "code_ranges.txt"
    paths.FUNCTION_BODIES = state / "function_bodies.txt"  # TcCatalog writes it with the catalog
    paths.FSCA_TABLE, paths.MSVC_ENV = state / "fsca" / paths.FSCA_TABLE.name, state / "msvc_env.json"
    for name in ("msvc_env.json", "msvc_env_x86.json", "code_ranges.txt", "catalog.json", "vectors.json",
                 "fr0_results.txt"):
        if (live["state"] / name).exists() and not (a.catalog and name in ("code_ranges.txt", "catalog.json")):
            shutil.copyfile(live["state"] / name, state / name)
    fsca_table(live["state"], paths.FSCA_TABLE)
    for p in (paths.STATE, paths.HARNESS, paths.PACKETS, paths.BUILD, paths.SCRIPTS, paths.GHIDRA_PROJECT,
              paths.GHIDRA_SLOTS, paths.CODE_RANGES, paths.FUNCTION_BODIES, paths.FSCA_TABLE, paths.MSVC_ENV,
              paths.MARKS):
        assert any(pathlib.Path(p).resolve().is_relative_to(w) for w in (work, gwork)), p
    paths.sync_scripts()  # the working tree's target scripts + this project's TcInputs.java, into WORK/scripts
    cfg = paths.config()
    check.msvc_env(cfg)  # once, before the worker threads: concurrent first writes of msvc_env.json collide
    return paths, ghidra, check, cfg, live


CATALOG_OUT = ("CATALOG", "CODE_RANGES", "FUNCTION_BODIES")  # what ghidra.catalog writes into the private state


def ghidra_copies(paths, ghidra, cfg, live, jobs, rebuild_catalog, log):
    """Private master + `jobs` read-only slot copies, refreshed when the live catalog changes.
    Each mode has its own copies (gwork/master, slots; with the catalog step: master_catalog, slots_catalog): the
    catalog step changes its master, so one set of copies made a gate run after a --targeted run (or the other way
    round) copy the whole project again (~80 s a project). The catalog step's files (catalog, code ranges, function
    bodies) are kept with its master (catalog_out/, marked with the master's copied_from.txt) and put back into the
    work state on a reused master: the work state is shared by both modes, and a plain run copies the live catalog
    over it."""
    if rebuild_catalog:
        paths.GHIDRA_PROJECT = paths.GHIDRA_PROJECT.with_name("master_catalog")
        paths.GHIDRA_SLOTS = paths.GHIDRA_SLOTS.with_name("slots_catalog")
    stamp = stamp_of(live["catalog"])
    slot1 = live["slots"] / "slot1"
    fresh = (slot1 / "copied_from.txt").exists() and (slot1 / "copied_from.txt").read_text(encoding="utf-8") == stamp
    src = slot1 if fresh and (slot1 / (paths.GHIDRA_NAME + ".gpr")).exists() else live["ghidra"]
    t = time.time()
    # A rebuilt catalog is only as fresh as the scripts that made it: changed scripts make a new copy and catalog.
    files = sorted(paths.sync_scripts().glob("*.java"))
    scripts = hashlib.sha256(b"".join(q.read_bytes() for q in files)).hexdigest()[:12]
    copied = copy_tree_if_stale(src, paths.GHIDRA_PROJECT, stamp + (f":catalog:{scripts}" if rebuild_catalog else ""))
    if copied:
        log(f"copied Ghidra project from {src} ({time.time() - t:.0f} s)")
    mstamp = (paths.GHIDRA_PROJECT / "copied_from.txt").read_text(encoding="utf-8")
    if rebuild_catalog:
        out = paths.GHIDRA_PROJECT.with_name("catalog_out")
        if not copied and catalog_kept(paths, out, mstamp):
            log("catalog of the private copy: kept from its catalog step (catalog_out)")
        else:  # a new copy, or no kept catalog: the fixups and TcCatalog run on the private master
            t = time.time()
            ghidra.catalog(cfg)  # on the private master only (paths are redirected)
            log(f"catalog fixups (TcPools ... TcCont) and TcCatalog on the private copy ({time.time() - t:.0f} s)")
            keep_catalog(paths, out, mstamp)
    for i in range(1, jobs + 1):
        copy_tree_if_stale(paths.GHIDRA_PROJECT, paths.GHIDRA_SLOTS / f"slot{i}", mstamp)


def keep_catalog(paths, out, mstamp):
    """Keep the catalog step's files with the master they were made on (mstamp: its copied_from.txt)."""
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for name in CATALOG_OUT:
        shutil.copyfile(getattr(paths, name), out / getattr(paths, name).name)
    (out / "made_on.txt").write_text(mstamp, encoding="utf-8")  # last: a cut-short copy is never taken


def catalog_kept(paths, out, mstamp):
    """Put the kept catalog step's files back into the work state when they were made on this very master copy
    (unchanged since: the copy is made again when its stamp changes). False: none kept, so the step runs."""
    mark = out / "made_on.txt"
    if not mark.exists() or mark.read_text(encoding="utf-8") != mstamp:
        return False
    if not all((out / getattr(paths, name).name).exists() for name in CATALOG_OUT):
        return False
    for name in CATALOG_OUT:
        shutil.copyfile(out / getattr(paths, name).name, getattr(paths, name))
    return True


def plan_items(plan, live):
    """[(item id, kind, addr, C file, expected verdict, pattern, group)]: fixtures (pass and fail), then the sample.
    group: the harness the item is judged on, ADDR, or ADDR@ID for a fixture with its own config.
    A fixture with "refused" (a regex) has no C: the preparation must refuse the function with a matching reason."""
    items = []
    for f in plan["fixtures"]:
        group = f"{f['addr']}@{f['id']}" if f.get("config") else f["addr"]
        for side, want in (("pass", "PASS"), ("fail", "FAIL")):
            if f.get(side):
                items.append((f"{f['id']}.{side}", "fixture", f["addr"], TOOL / f[side], want, f["pattern"], group))
        if f.get("refused"):
            items.append((f"{f['id']}.refused", "fixture", f["addr"], None, "REFUSED", f["pattern"], group))
    if plan.get("sample") or plan.get("list"):
        ledger = json.loads((live["state"] / "ledger.json").read_text(encoding="utf-8"))["functions"]
        taken = {f["addr"] for f in plan["fixtures"]}
        matched = sorted(x for x, r in ledger.items()
                         if r.get("status") == "matched" and x not in taken and (live["matched"] / f"{x}.c").exists())
        if plan.get("list"):
            missing = sorted(set(plan["list"]) - set(matched) - taken)
            if missing:
                sys.exit(f"--list: not matched (or a fixture's function): {' '.join(missing[:20])}")
            chosen = sorted(set(plan["list"]) - taken)
        else:
            rng = random.Random(f"{plan.get('seed', 1)}:{plan.get('key', '')}")
            chosen = sorted(rng.sample(matched, min(plan["sample"], len(matched))))
        for x in chosen:
            items.append((f"sample.{x}", "sample", x, live["matched"] / f"{x}.c", "PASS", "matched", x))
            if plan.get("parity"):  # the same raw C under the readable runtime must agree (PASS)
                items.append((f"rd.{x}", "parity", x, live["matched"] / f"{x}.c", "PASS", "parity", x))
    return items


def same_as_live(live, harness, addr):
    """Whether the fresh harness is byte-identical to the live one (information: the oracle changed or not)."""
    def meta(d):  # the stamp is not compared: a harness made before stamps existed has none
        m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        m.pop("oracle_stamp", None)
        return m
    try:
        return meta(live["harness"] / addr) == meta(harness) and all(
            (live["harness"] / addr / n).read_bytes() == (harness / n).read_bytes() for n in ("vectors.txt", "expected.txt"))
    except (OSError, ValueError):
        return None


def oracle_trouble(r):
    """A result the oracle's wall-clock deadline may have made: 0/0 (ORACLE), or refused for running out of time."""
    return r.get("got") == "ORACLE" or "did not finish in time" in (r.get("unsupported") or "") \
        or "did not finish in time" in (r.get("detail") or "")


def oracle_note(res, meta_path):
    """The ORACLE detail of a result judged on no vector at all (0/0: an oracle problem, not a verdict), else None."""
    if res.get("verdict") == "PASS" or res.get("stage") != "compare" or res.get("total"):
        return None
    try:
        why = json.loads(pathlib.Path(meta_path).read_text(encoding="utf-8")).get("unsupported")
    except (OSError, ValueError):
        why = None
    return f"oracle problem, not a verdict: {why or 'no valid vectors'}"[:200]


def cache_get(cache, group, key, harness, packet):
    """Copy the cache entry GROUP/KEY into harness and packet; False when there is none (or it went meanwhile)."""
    src = cache / group / key
    try:
        if not (src / "harness" / "meta.json").exists():
            return False
        for d, sub in ((harness, "harness"), (packet, "packet")):
            shutil.rmtree(d, ignore_errors=True)
            if (src / sub).exists():
                shutil.copytree(src / sub, d)
        return True
    except OSError:
        return False


def cache_put(cache, group, key, harness, packet, keep=2):
    """Keep harness and packet as the cache entry GROUP/KEY (written once: a temporary folder renamed into place);
    the group keeps its `keep` newest entries."""
    gdir = cache / group
    dst = gdir / key
    if dst.exists():
        return
    tmp = gdir / f".tmp-{key}-{os.getpid()}-{threading.get_ident()}"
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(harness, tmp / "harness")
        if packet.exists():
            shutil.copytree(packet, tmp / "packet")
        os.replace(tmp, dst)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return
    old = sorted((d for d in gdir.iterdir() if d.is_dir() and not d.name.startswith(".tmp-")),
                 key=lambda d: d.stat().st_mtime, reverse=True)[keep:]
    for d in old:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    for opt in ("--project", "--work", "--ghidra-work", "--plan", "--out"):
        ap.add_argument(opt, required=True)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--cache")
    ap.add_argument("--prepare-hash")
    a = ap.parse_args()
    assert bool(a.cache) == bool(a.prepare_hash), "--cache needs --prepare-hash"
    t0 = time.time()
    lock = threading.Lock()

    def log(msg):
        with lock:
            print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    plan = json.loads(pathlib.Path(a.plan).read_text(encoding="utf-8"))
    if not a.catalog and any(f.get("needs_catalog") for f in plan["fixtures"]):
        a.catalog = True  # its function exists only after the working tree's catalog fixups
        log("a fixture needs the working tree's catalog: the private copy re-runs the catalog fixups")
    paths, ghidra, check, cfg, live = setup(a)
    from core import oracle_stamp
    work = pathlib.Path(a.work).resolve()
    cfg.update(plan.get("config") or {})
    over = {f["addr"]: int(f["vectors_dev"]) for f in plan["fixtures"] if f.get("vectors_dev")}
    if over:  # fixture dev-count overrides, into the private copy only
        vj = paths.STATE / "vectors.json"
        assert vj.resolve().is_relative_to(work), vj
        vj.write_text(json.dumps((paths.read_json(vj) or {}) | over, indent=1), encoding="utf-8")
    items = plan_items(plan, live)
    readable_ids = {f"{f['id']}.{side}" for f in plan["fixtures"] if f.get("runtime") == "readable"
                    for side in ("pass", "fail")} | {it[0] for it in items if it[1] == "parity"}
    source_ids = {f"{f['id']}.{side}": f.get("names") for f in plan["fixtures"] if f.get("runtime") == "readable-src"
                  for side in ("pass", "fail")}
    if plan.get("items") == "readable":  # a change to the readable judge alone: only what it judges
        items = [it for it in items if it[0] in readable_ids or it[0] in source_ids]
        log(f"readable items only: {len(items)}")
    groups = list(dict.fromkeys(i[6] for i in items))
    jobs = max(1, min(a.jobs, len(groups)))
    ghidra_copies(paths, ghidra, cfg, live, jobs, a.catalog, log)
    by_group, group_cfg = {}, {}
    for it in items:
        by_group.setdefault(it[6], []).append(it)
    for f in plan["fixtures"]:
        if f.get("config"):
            group_cfg[f"{f['addr']}@{f['id']}"] = dict(cfg, **f["config"])  # its own facts file: content-named
    if readable_ids or source_ids:  # the 32-bit environment, once, into the private state (like check.msvc_env)
        from core import readable_check as rcheck
        assert rcheck.MSVC_ENV_X86.resolve().is_relative_to(work), rcheck.MSVC_ENV_X86
        rcheck.msvc_env_x86(cfg)
    q = queue.Queue()
    for g in groups:
        q.put(g)
    results = []

    def judge(it, harness, cfg):
        iid, kind, addr, cfile, want, pattern, _ = it
        t = time.time()
        r = {"id": iid, "kind": kind, "addr": addr, "pattern": pattern, "expect": want, "file": str(cfile)}
        try:
            if iid in readable_ids:
                res = rcheck.check_raw(cfg, addr, str(cfile), work / "build" / iid, harness_dir=harness)
            elif iid in source_ids:
                from core import readable
                res = rcheck.check_readable(cfg, addr, str(cfile), work / "build" / iid,
                                            reg=readable.Registry.empty(source_ids[iid]), harness_dir=harness,
                                            callee_harness=(live["harness"],))
            else:
                res = check.run_check(cfg, addr, str(cfile), build_dir=work / "build" / iid, harness_dir=harness)
            diffs = (res.get("first_diffs") or [{}])[0].get("diffs") or res.get("lint") or res.get("build") or []
            got = res["verdict"]
            if want == "FAIL" and got == "FAIL" and res["stage"] not in ("run", "compare"):
                got = f"FAIL@{res['stage']}"  # a fail fixture must fail on behaviour, not on a rule or the build
            detail = (res.get("run_error") or (diffs[0] if diffs else ""))[:200]
            note = oracle_note(res, harness / "meta.json")
            if note:
                got, detail = "ORACLE", note
            if addr in over:  # the fixture's dev-count override must have reached the harness
                dev = (paths.read_json(harness / "oracle_summary.json") or {}).get("dev_valid")
                if dev != over[addr]:
                    got, detail = "BADDEV", f"dev vectors {dev}, override {over[addr]}"
            r.update(got=got, stage=res["stage"], failing=f"{res.get('failing')}/{res.get('total')}", detail=detail)
        except Exception as e:  # noqa: BLE001
            r.update(got="ERROR", detail=f"check: {str(e).splitlines()[0][:200]}")
        r["secs"] = round(time.time() - t, 1)
        return r

    refuse_rx = {f"{f['id']}.refused": f["refused"] for f in plan["fixtures"] if f.get("refused")}

    def refused(it, harness):
        why = (paths.read_json(harness / "meta.json") or {}).get("unsupported") or ""
        ok = bool(why) and re.search(refuse_rx[it[0]], why) is not None
        return {"id": it[0], "kind": it[1], "addr": it[2], "pattern": it[5], "expect": it[4], "file": "",
                "got": "REFUSED" if ok else "PREPARED", "detail": (why or "not refused")[:200]}

    reuse = a.reuse or bool(plan.get("parity"))  # keep a harness whose oracle stamp is current
    cache = pathlib.Path(a.cache).resolve() if a.cache else None
    from_cache = bool(cache and plan.get("reuse_cache"))
    counts = {"reused": 0, "prepared": 0, "retried_alone": 0, "cached": 0}

    scripts = paths.sync_scripts()  # this checkout's scripts, in their own content-named folder

    def one_group(group, slot, fresh=False):
        """Prepare (or reuse) one group's harness and judge its items."""
        addr, gcfg = group.split("@")[0], group_cfg.get(group, cfg)
        harness = work / "harness" / group
        t = time.time()
        prep = {}
        try:
            hints = live["hints"] / f"{addr}.txt"
            now = oracle_stamp.stamp(gcfg, addr, scripts, ghidra.write_facts(gcfg), hints,
                                     ghidra.dev_vectors(gcfg, addr), gcfg["vectors_hidden"])
            old = paths.read_json(harness / "meta.json") if reuse and not fresh else None
            key = f"{now}-{a.prepare_hash}" if cache else None
            if from_cache and not fresh and cache_get(cache, group, key, harness, work / "packets" / group):
                old = paths.read_json(harness / "meta.json")
                prep["cached"] = True
            if old and not oracle_stamp.current(old, now, live["harness"]):  # stamp and callee records current
                prep["reused"] = True
            else:
                prep.pop("cached", None)
                staging = {"scripts": scripts, "packet": work / "packets" / group, "harness": harness,
                           "hints": hints, "callees": live["harness"]}  # callee records from the live harnesses
                meta, summary = ghidra.prepare(gcfg, addr, slot_index=slot, staging=staging)
                prep = {"unsupported": meta.get("unsupported"), "reused": False,
                        "valid": f"{(summary or {}).get('dev_valid')}+{(summary or {}).get('hidden_valid')}"}
            with lock:
                counts["reused" if prep["reused"] else "prepared"] += 1
                counts["cached"] += bool(prep.get("cached"))
            prep["prep_secs"] = round(time.time() - t, 1)
            prep["same_as_live"] = same_as_live(live, harness, addr)
            out = [dict(refused(it, harness) if it[4] == "REFUSED" else judge(it, harness, gcfg), **prep)
                   for it in by_group[group]]
            if cache and not prep["reused"] and not any(oracle_trouble(r) for r in out):
                if (paths.read_json(harness / "meta.json") or {}).get("oracle_stamp") == now:
                    cache_put(cache, group, key, harness, work / "packets" / group)
        except Exception as e:  # noqa: BLE001
            err = f"prepare: {str(e).splitlines()[0][:200]}"
            out = [{"id": it[0], "kind": it[1], "addr": addr, "pattern": it[5], "expect": it[4], "got": "ERROR",
                    "detail": err, "file": str(it[3])} for it in by_group[group]]
        for r in out:
            log(f"{r['id']:34} expect {r['expect']:4} got {r['got']:5} {r.get('failing', '')} {r['detail'][:90]}")
        return out

    def worker(slot):
        while True:
            try:
                group = q.get_nowait()
            except queue.Empty:
                return
            out = one_group(group, slot)
            with lock:
                results.extend(out)

    ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(1, jobs + 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # Load-dependent oracle outcomes (a wall-clock deadline hit while the other slots ran: refused "did not finish in
    # time", or no valid vector at all) are prepared once more, alone, and the alone result is the one recorded.
    group_of = {it[0]: g for g, its in by_group.items() for it in its}
    again = sorted({group_of[r["id"]] for r in results if r.get("got") != r.get("expect") and oracle_trouble(r)})
    for group in again:
        first = {r["id"]: r for r in results if group_of.get(r["id"]) == group}
        log(f"{group}: oracle trouble under load ({next(iter(first.values()))['got']}); preparing it again alone")
        out = one_group(group, 1, fresh=True)
        counts["retried_alone"] += 1
        for r in out:
            r["first_run"] = f"{first[r['id']].get('got')}: {first[r['id']].get('detail', '')}"[:200]
        results[:] = [r for r in results if r["id"] not in first] + out
    log(f"harnesses: {counts['reused']} reused (oracle stamp current; {counts['cached']} from the cache), "
        f"{counts['prepared']} prepared"
        + (f", {counts['retried_alone']} prepared again alone" if again else ""))
    order = {it[0]: n for n, it in enumerate(items)}
    results.sort(key=lambda r: order.get(r["id"], 1 << 30))
    pathlib.Path(a.out).write_text(json.dumps({"project": str(live["root"]), "secs": round(time.time() - t0, 1),
                                               "harnesses": counts, "results": results}, indent=1), encoding="utf-8")
    log(f"done: {len(results)} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
