"""refine.py lookalike: find, map, propose, apply, judge and adopt look-alikes (core/lookalike), batch 23.

  refine.py --project TARGET lookalike --from SOURCE [--apply] [--judge | --adopt] [--near N]

- always: state/lookalike.json, the report and the proposal: names entries for the target ({"functions",
  "globals"}, each with "lookalike": "PROJECT:ADDR"), the source header items they and the target's own entries
  need (`header`), what is kept (the target already names that address), conflicts and unmapped names;
- --apply: the proposal into the target: readable/names/lookalike_KEY.json (merged with an earlier one; an address
  or name the target already has is never written) and readable/include/sys/lookalike_KEY.h, included from types.h;
- --judge: every identical function's rewritten source file judged by the target's own judge (readable_check, every
  lint) against the target's headers plus the proposal, in a scratch header snapshot: nothing in readable/ or the
  ledger is written. The measurement of a dry run;
- --adopt: the same against the target's live headers (after --apply); a strict PASS is written to
  readable/src/ADDR.c and recorded readable with `source: lookalike PROJECT:ADDR` and an attempt {"kind":
  "lookalike"} (no agent). A function that already has a readable file is left alone.
The rewrite gives each mapped name the target's name for that address (the same name once the proposal is applied),
else the target's raw name; a file with an unmapped name is not judged.
"""
import json
import os
import re
import shutil

from . import filelock, fn_claim, global_extents, header_snapshot, lookalike, paths, readable, readable_headers
from . import readable_check as rcheck
from . import refine, retire, xmatch
from .lint import norm_sig

TRY_SOURCES = 3  # identical sources tried per target function (copies of one stub in the source project)


def key_of(src_ws):
    name = json.loads((src_ws / "project.json").read_text(encoding="utf-8")).get("name") or src_ws.name
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "source"


def source_extents(cfg, src, build_dir):
    """{global addr: sizeof} of the source registry's globals, against the source's own headers."""
    globs = sorted((a, e["decl"]) for a, e in src.reg.globals.items())
    try:
        got, _ = global_extents.sizes(cfg, globs, build_dir, (src.ws / "readable" / "include", rcheck.RUNTIME_RD,
                                                              paths.RUNTIME))
        return got
    except RuntimeError:
        return {}


def plan(cfg, src_ws, near_max, log):
    """The report: identical and near functions, each identical function's mapped candidates, the proposal."""
    src_img, tgt_img = xmatch.Image(src_ws), xmatch.Image(paths.ROOT)
    same, near = lookalike.find(src_img, tgt_img, lookalike.readable_sources(src_ws),
                                lookalike.matched_targets(paths.ROOT), near_max, retire.mirrors(paths.ROOT))
    src = lookalike.Source(src_ws)
    src.extents = source_extents(cfg, src, paths.BUILD / f"rd_lookalike_ext_{os.getpid()}")
    tgt_reg = readable.Registry()
    cands = {}
    for t, hits in sorted(same.items()):
        rows = []
        for h in hits:
            text = src.text(h["from"])
            names = lookalike.map_names(src, h["from"], t, h["pairs"], text)
            rows.append({"from": h["from"], "names": names,
                         "unmapped": sorted(n for n, m in names.items() if not m["tgt"])})
        rows.sort(key=lambda r: (bool(r["unmapped"]), r["from"]))  # fully mapped sources first
        cands[t] = rows
    project = key_of(src_ws)
    best = {t: rows[0]["names"] for t, rows in cands.items() if not rows[0]["unmapped"]}
    prop = lookalike.propose(src, tgt_reg, best, project, retire.mirrors(paths.ROOT))
    for t, rows in cands.items():  # a self name from one of several identical sources is a guess: say so
        e = prop["functions"].get(t)
        if e and len(rows) > 1:
            e["ambiguous"] = f"{len(rows)} identical source functions"
    log(f"lookalike: {len(same)} identical, {len(near)} near-identical (not used); "
        f"{len(best)} fully mapped; proposal {len(prop['functions'])} function(s), {len(prop['globals'])} global(s), "
        f"{len(prop['kept'])} kept (already named), {len(prop['conflicts'])} conflict(s)")
    return {"from": str(src_ws), "key": project, "identical": {t: [r["from"] for r in rows] for t, rows in cands.items()},
            "near": near, "candidates": cands, "proposal": prop, "src": src, "tgt_reg": tgt_reg}


def header_text(rep, include_dir, own):
    """The source header items the proposal and the target's own entries need, minus what the target's headers
    (other than our own lookalike header `own`) define."""
    hs_src = readable_headers.load(rep["src"].ws / "readable" / "include")
    hs_tgt = readable_headers.Headers(include_dir)
    defined = {n for it in hs_tgt.items if it.header != own for n in it.names}
    reg, prop = rep["tgt_reg"], rep["proposal"]
    texts = [rep["src"].text(rows[0]["from"]) for rows in rep["candidates"].values() if not rows[0]["unmapped"]]
    texts += [e["proto"] for e in list(reg.functions.values()) + list(prop["functions"].values())]
    texts += [e["decl"] for e in list(reg.globals.values()) + list(prop["globals"].values())]
    items, skipped = lookalike.header_items(hs_src, texts, defined)
    body = "\n\n".join(it.text for it in items)
    guard = f"LOOKALIKE_{rep['key'].upper()}_H"
    return (f"/* Proposed by refine.py lookalike from {rep['key']}: the types and constants the look-alike names need. "
            f"Review, then keep or move. */\n#ifndef {guard}\n#define {guard}\n\n{body}\n\n#endif\n"), skipped


def write_proposal(rep, names_dir, include_dir):
    """Write the proposal into a target header tree (the live one: --apply; a scratch snapshot: --judge)."""
    key = rep["key"]
    own = f"sys/lookalike_{key}.h"
    nf = names_dir / f"lookalike_{key}.json"
    old = json.loads(nf.read_text(encoding="utf-8")) if nf.exists() else {"functions": {}, "globals": {}}
    for kind in ("functions", "globals"):
        old.setdefault(kind, {}).update(rep["proposal"][kind])
    names_dir.mkdir(parents=True, exist_ok=True)
    filelock.write_atomic(nf, json.dumps(old, indent=1) + "\n")
    text, skipped = header_text(rep, include_dir, own)
    (include_dir / "sys").mkdir(parents=True, exist_ok=True)
    filelock.write_atomic(include_dir / own, text)
    types = include_dir / "types.h"
    line = f'#include "{own}"'
    if not types.exists():
        filelock.write_atomic(types, f'#ifndef TYPES_H\n#define TYPES_H\n\n#include "rd_rt.h"\n\n{line}\n\n#endif\n')
    elif line not in types.read_text(encoding="utf-8"):
        t = types.read_text(encoding="utf-8")
        k = t.rfind("#endif")
        filelock.write_atomic(types, (t[:k] + line + "\n\n" + t[k:]) if k >= 0 else t + "\n" + line + "\n")
    return nf, include_dir / own, skipped


def rewritten(rep, t, row, reg):
    """The source file rewritten for target function t (names as the target registry reg has them), or None."""
    if row["unmapped"]:
        return None
    mirror = retire.mirrors(paths.ROOT)
    ren = {n: lookalike.target_name(reg, m, mirror) for n, m in row["names"].items()}
    text = lookalike.rewrite(rep["src"].text(row["from"]), ren)
    src_e = rep["src"].reg.functions.get(row["from"])
    src_proto = lookalike.rewrite(src_e["proto"], ren) if src_e else None
    tgt_proto = reg.proto(t, paths.read_json(paths.HARNESS / t / "meta.json"))
    if src_proto and norm_sig(src_proto) == norm_sig(tgt_proto):
        return text
    return lookalike.retarget(text, tgt_proto)  # the target names it otherwise: its own prototype


def judge_all(cfg, rep, adopt, log):
    """Judge every identical function's candidates; adopt (live headers) or only report (scratch snapshot)."""
    reg = readable.Registry()
    out = {}
    for t, rows in sorted(rep["candidates"].items()):
        if adopt and (readable.SRC / f"{t}.c").exists():
            out[t] = {"outcome": "kept", "why": "already has a readable file"}
            continue
        tried = []
        for row in rows[:TRY_SOURCES]:
            text = rewritten(rep, t, row, reg)
            if text is None:
                tried.append(f"{row['from']}: " + (f"unmapped {' '.join(row['unmapped'])}" if row["unmapped"] else
                                                   "the target's prototype has another parameter count"))
                continue
            work = paths.BUILD / t / "rd_lookalike.c"
            work.parent.mkdir(parents=True, exist_ok=True)
            work.write_text(text, encoding="utf-8")
            try:
                with fn_claim.claim(t, "lookalike", fn_claim.wait_s(cfg)):
                    res = rcheck.check_readable(cfg, t, str(work), paths.BUILD / t / "rd_lookalike", reg=reg)
                    if res["verdict"] == "PASS" and adopt:
                        record(t, work, row["from"], rep["key"], res)
            except fn_claim.Busy as e:
                tried.append(f"{row['from']}: busy ({e})")
                break
            if res["verdict"] == "PASS":
                out[t] = {"outcome": "adopted" if adopt else "pass", "from": row["from"]}
                break
            why = "; ".join((res.get("lint") or []) + (res.get("build") or [])[:2])[:240] or \
                f"{res.get('failing')}/{res.get('total')} vectors differ: " + \
                "; ".join(res.get("first_diffs", [{}])[0].get("diffs", [])[:2])
            tried.append(f"{row['from']}: {res['stage']}: {why}")
        out.setdefault(t, {"outcome": "fail", "why": tried})
        log(f"{t}: {out[t]['outcome']} {out[t].get('from') or ' | '.join(out[t].get('why') or [])}"[:400])
    return out


def record(t, work, s_addr, key, res):
    src = readable.SRC / f"{t}.c"
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work, src)
    cur = header_snapshot.active_hash()

    def merge(led):
        row = led.setdefault(t, {})
        atts = row.setdefault("attempts", [])
        atts.append({"n": len(atts) + 1, "kind": "lookalike", "at": refine.now(), "from": f"{key}:{s_addr}",
                     "verdict": "PASS", "stage": res["stage"], "failing": res.get("failing"), "total": res.get("total"),
                     "headers": cur})
        row.update(status="readable", verified=refine.now(), finished=refine.now(), headers=cur,
                   source=f"lookalike {key}:{s_addr}")
        for k in ("stale_headers", "note", "redo_failed", "redo_of"):
            row.pop(k, None)
        return row

    with refine._LOCK:
        filelock.update_json(refine.LEDGER, merge, {}, dump=refine.dump)


def scratch_snapshot(cfg, rep):
    """A header snapshot of the target's live headers plus the proposal (--judge): (snapshot, problems)."""
    d = header_snapshot._new_dir("run")
    header_snapshot._copy(*header_snapshot.live_dirs(), d)
    write_proposal(rep, d / "names", d / "include")
    problems = header_snapshot.validate(cfg, d)
    return header_snapshot.Snapshot(d, "lookalike proposal"), problems


def run(cfg, src_ws, apply=False, judge=False, adopt=False, near_max=lookalike.NEAR_MAX, log=print):
    rep = plan(cfg, src_ws, near_max, log)
    header = header_text(rep, readable.INCLUDE, f"sys/lookalike_{rep['key']}.h")[0]
    result = {"identical": rep["identical"], "near": rep["near"], "proposal": rep["proposal"],
              "unmapped": {t: rows[0]["unmapped"] for t, rows in rep["candidates"].items() if rows[0]["unmapped"]}}
    if apply:
        nf, hf, skipped = write_proposal(rep, readable.NAMES, readable.INCLUDE)
        log(f"applied: {paths.show(nf)}, {paths.show(hf)} (names the target headers define already: {len(skipped)})")
    snap = None
    if judge or adopt:
        try:
            if adopt:
                snap = header_snapshot.acquire(cfg, log=log, wait_s=0, fallback=False)
            else:
                snap, problems = scratch_snapshot(cfg, rep)
                if problems:
                    raise header_snapshot.HeaderError("; ".join(problems[:4]))
        except header_snapshot.HeaderError as e:
            if snap:
                snap.release()
            log(f"HEADERS: the target's headers{'' if adopt else ' plus the proposal'} do not validate: {e}")
            result["headers_error"] = str(e)[:2000]
            snap = None
        if snap:
            header_snapshot.use(snap)
            try:
                result["judged"] = judge_all(cfg, rep, adopt, log)
            finally:
                snap.release()
    paths.write_json(paths.STATE / "lookalike.json", dict(result, header=header, key=rep["key"], source=rep["from"]))
    return result
