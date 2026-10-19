"""Raw return-type overrides (batch 27): list the candidates, check the names entries' "raw_ret" keys, apply them.

usage: python tools/sig_override.py --project DIR [--candidates] [--apply] [--json FILE]
  (no option)   check every "raw_ret" in readable/names (core/sig_override.validate) and say which harnesses
                differ from it; writes nothing. Exit 1 when one is refused.
  --candidates  also list core/ret_use's candidates (a void function returning a tail callee's value; an f32 no
                call site reads), with their evidence. Candidates are never applied: a designer adds "raw_ret".
  --apply       for each valid override whose harness differs: rewrite harness/ADDR/meta.json, rewrite the matched
                C (core/sig_rewrite) and keep it only when check.py passes it (else it is listed to be matched again:
                tools/requeue.py), re-record the callers' callee records (core/callee_drift.adopt: no vectors depend on
                a callee's return type) and judge their matched C. Old files go to state/sig_override_backup/. An
                override removed from the names entry is reverted the same way.
Readable prototypes that cannot carry the new kind are reported (the designer edits the names entry), and the
readable files of the function and its callers are listed for `refine.py verify`. Servers stopped.
"""
import argparse
import json
import pathlib
import shutil
import sys
import tempfile

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project  # noqa: E402


def backup(paths, src, name):
    d = paths.STATE / "sig_override_backup"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, d / name)


def judge(paths, check, cfg, addr, text):
    """check.py's verdict for raw C text on the function's current harness."""
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / f"{addr}.c"
        f.write_text(text, encoding="utf-8")
        return check.run_check(cfg, addr, f, build_dir=pathlib.Path(td) / "build")["verdict"]


def apply_one(paths, check, cfg, addr, kind, report):
    from core import callee_drift, sig_override, sig_rewrite
    mp = paths.HARNESS / addr / "meta.json"
    meta = paths.read_json(mp)
    old = meta["ret"]
    if not sig_override.apply_meta(meta, kind):
        return
    backup(paths, mp, f"{addr}.meta.json")
    paths.write_json(mp, meta)
    report["applied"].append(f"{addr}: {old} -> {kind}")
    mc = paths.MATCHED / f"{addr}.c"
    if mc.exists():
        new = sig_rewrite.rewrite(mc.read_text(encoding="utf-8"), addr, old, kind)
        verdict = judge(paths, check, cfg, addr, new) if new is not None else "NOT_REWRITTEN"
        if verdict == "PASS":
            backup(paths, mc, f"{addr}.c")
            mc.write_text(new, encoding="utf-8", newline="\n")
            report["matched_pass"].append(addr)
        else:
            report["requeue"].append(f"{addr} ({verdict})")
    sig = callee_drift.Signatures(paths.HARNESS)
    for d in sorted(paths.HARNESS.iterdir()):
        cm = paths.read_json(d / "meta.json") if d.name != addr else None
        if not cm or not any(c["addr"] == addr for c in cm.get("callees") or []):
            continue
        if callee_drift.adopt(cm, sig):
            backup(paths, d / "meta.json", f"{d.name}.meta.json")
            paths.write_json(d / "meta.json", cm)
        cc = paths.MATCHED / f"{d.name}.c"
        if cc.exists():
            v = judge(paths, check, cfg, d.name, cc.read_text(encoding="utf-8"))
            report["callers"].append(f"{d.name} {v}")
            if v != "PASS":
                report["requeue"].append(f"{d.name} (caller, {v})")
        if (paths.ROOT / "readable" / "src" / f"{d.name}.c").exists():
            report["verify"].append(d.name)
    if (paths.ROOT / "readable" / "src" / f"{addr}.c").exists():
        report["verify"].append(addr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--candidates", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    ws = use_project(a.project)
    from core import paths, readable, ret_use, sig_override
    from core import check
    cfg = paths.config()
    names = ws / "readable" / "names"
    over = sig_override.overrides(names)
    catalog = {f["addr"] for f in paths.read_json(paths.CATALOG)["functions"]}
    rp = ret_use.Project(ws) if (a.candidates or any(k == "void" for k, _ in over.values())) else None
    report = {"refused": [], "pending": [], "applied": [], "matched_pass": [], "requeue": [], "callers": [],
              "verify": [], "readable_proto": [], "reverted": [], "candidates": []}
    entries = {}
    for f in sorted(names.glob("*.json")):
        entries.update((paths.read_json(f) or {}).get("functions") or {})
    for addr, (kind, fname) in sorted(over.items()):
        meta = paths.read_json(paths.HARNESS / addr / "meta.json")
        why = sig_override.validate(addr, kind, catalog, meta, lambda: rp.evidence(addr))
        if why:
            report["refused"].append(f"{why} ({fname})")
            continue
        proto = (entries.get(addr) or {}).get("proto")
        bad = proto and sig_override.readable_mismatch(readable.parse_proto(proto)[0], kind)
        if bad:
            report["readable_proto"].append(f"{addr}: {bad} ({fname})")
        if meta and meta["ret"] != kind:
            report["pending"].append(f"{addr}: harness ret {meta['ret']}, raw_ret {kind}")
            if a.apply:
                apply_one(paths, check, cfg, addr, kind, report)
    for d in sorted(paths.HARNESS.iterdir()) if paths.HARNESS.is_dir() else []:
        m = paths.read_json(d / "meta.json") if d.name not in over else None
        if m and m.get("ret_override"):
            report["pending"].append(f"{d.name}: raw_ret removed, harness keeps {m['ret']}")
            if a.apply:
                apply_one(paths, check, cfg, d.name, m["ret_override"]["from"], report)
                report["reverted"].append(d.name)
    if a.candidates:
        report["candidates"] = rp.candidates()
    for k, v in report.items():
        if k == "candidates":
            by = {c: sum(x["case"] == c for x in v) for c in ("tail", "tail_unknown", "f32_unread", "fr0_exit")}
            print(f"candidates: {len(v)} (tail: {by['tail']}, tail_unknown: {by['tail_unknown']}, "
                  f"f32_unread: {by['f32_unread']}, fr0_exit: {by['fr0_exit']})")
            for x in v:
                ev = x["evidence"]
                print(f"  {x['addr']} {x['case']}: {x['ret']} -> {x['suggest']} ({x['why']}; sites {ev['sites']}, "
                      f"r0 {ev['r0']}, fr0 {ev['fr0']}, unknown {ev['unknown']}, address taken {ev['address_taken']})")
        elif v:
            print(f"{k}: {len(v)}")
            for line in v:
                print(f"  {line}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 1 if report["refused"] or report["requeue"] else 0


if __name__ == "__main__":
    sys.exit(main())
