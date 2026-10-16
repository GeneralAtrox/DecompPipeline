"""Signature drift: matched functions whose stored harness no longer agrees with the current checker's prototypes.

A harness is frozen when the function is prepared. A later checker deploy that changes prototype inference, or a
callee re-prepared with a new prototype (Toy Commander's SHC helpers 8c067c2c, 8c067d18 and 8c067dc0 gained r0..r3
inputs after their callers matched), leaves matched C judged on an older signature: the next re-judge fails it with
"R3: signature must be exactly" or "call #N: argument r0 (args.rlo[0])". This tool finds them without re-preparing
anything: TcSigs.java computes each function's signature and compared callee registers as TcPrepare would now, on a
private read-only copy of the project's Ghidra project, and compares them with harness/ADDR/meta.json.

usage: python tools/sigdrift.py --project DIR [--status matched] [--list FILE] [--all] [--json FILE]
                                [--work DIR] [--ghidra-work DIR] [--chunk 500] [--timeout 1800]
  signature  the signature, result register or support changed: the matched C fails lint R3 (requeue it)
  compared   a callee is now compared on more registers, or more runtime state is compared: re-judge it; or a
             call records an older signature than the callee's own harness (re-prepared since): re-prepare it
             before its readable C is written or verified (game.h declares raw-named callees from the record)
  --list     write the signature tier (with --all, both tiers) one address per line, for tools/requeue.py @FILE
Nothing of the live project is written: work files go to --work and --ghidra-work (defaults under tests/).
Every Ghidra run is bounded by --timeout seconds (the whole process tree is killed after it).
"""
import argparse
import json
import pathlib
import re
import sys
import types

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
import regress_project as rp  # noqa: E402
from core import callee_drift, sig_override  # noqa: E402


def fresh_records(paths, ghidra, cfg, addrs, chunk, timeout, log):
    slot = paths.GHIDRA_SLOTS / "slot1"
    out = {}
    cfg = dict(cfg, ghidra_timeout_s=timeout)
    for i in range(0, len(addrs), chunk):
        part = addrs[i:i + chunk]
        lst, res = paths.STATE / f"sigdrift_{i}.txt", paths.STATE / f"sigdrift_{i}.jsonl"
        lst.write_text("\n".join(part) + "\n", encoding="utf-8")
        res.unlink(missing_ok=True)
        ghidra._headless(cfg, ["-process", cfg["binary_path"].name, "-noanalysis", "-readOnly",
                               "-scriptPath", str(paths.sync_scripts()), "-postScript", "TcSigs.java", str(res),
                               ghidra.write_facts(cfg), str(lst)], "ghidra_sigdrift.log", project=slot)
        for line in res.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            out[r["addr"]] = r
        log(f"  {min(i + chunk, len(addrs))}/{len(addrs)} functions")
    return out


def callee_sig_drift(meta, own_sig):
    """Calls whose recorded callee signature is older than the callee's own harness (it was re-prepared after this
    function): the readable stage declares and places a raw-named callee from the record, so readable C is built
    against the old signature (8c0610f6: `f32 fn_8c061498(void)`, which now takes r4). Re-prepare to refresh."""
    out = []
    for c in meta["callees"]:
        now = own_sig(c["addr"])
        if now and now.replace(f"fn_{c['addr']}", "") != c["signature"].replace(f"fn_{c['addr']}", ""):
            out.append(f"call to {c['addr']} records `{c['signature']}`, its harness now `{now}`")
    return out


def drift(meta, r, own_sig=lambda a: None):
    """(tier, reasons): tier 'signature', 'compared' or None."""
    if "error" in r:
        return "compared", [f"no fresh record: {r['error']}"]
    sig = []
    over = meta.get("ret_override")
    if over and r.get("signature", "").startswith(over["from"] + " "):  # a names entry's raw_ret (batch 27)
        r = dict(r, signature=sig_override.with_ret(r["signature"], over["to"]), ret_reg=meta.get("ret_reg"))
    if r["signature"] != meta["signature"]:
        sig.append(f"signature {meta['signature']} -> {r['signature']}")
    if r["ret_reg"] != meta.get("ret_reg"):
        sig.append(f"result register {meta.get('ret_reg')} -> {r['ret_reg']}")
    if bool(r["unsupported"]) != bool(meta.get("unsupported")):
        sig.append(f"unsupported {meta.get('unsupported')} -> {r['unsupported']}")
    if sig:
        return "signature", sig
    more = []
    old = {c["addr"]: set(c["regs"]) for c in meta["callees"]}
    for c, regs in r["callees"].items():
        gained = set(regs) - old.get(c, set())
        if gained:
            more.append(f"call to {c} now compares {','.join(sorted(gained))}")
    for key in ("r_state", "fr_state"):
        gained = set(r[key]) - set(meta.get(key) or [])
        if gained:
            more.append(f"{key} gains {','.join(sorted(gained))}")
    more += callee_sig_drift(meta, own_sig)
    return ("compared", more) if more else (None, [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--status", default="matched")
    ap.add_argument("--work")
    ap.add_argument("--ghidra-work")
    ap.add_argument("--chunk", type=int, default=500)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--list")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    proj = pathlib.Path(a.project).resolve()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{proj.parent.name}_{proj.name}").strip("_")
    ns = types.SimpleNamespace(project=str(proj), work=a.work or str(TOOL / "tests" / ".work" / f"sigdrift_{slug}"),
                               ghidra_work=a.ghidra_work or str(TOOL / "tests" / "ghidra_work" / f"sigdrift_{slug}"),
                               catalog=False)
    paths, ghidra, _check, cfg, live = rp.setup(ns)
    rp.ghidra_copies(paths, ghidra, cfg, live, 1, False, print)
    ledger = json.loads((live["state"] / "ledger.json").read_text(encoding="utf-8"))["functions"]
    statuses = set(a.status.split(","))
    addrs = sorted(x for x, r in ledger.items()
                   if r.get("status") in statuses and (live["harness"] / x / "meta.json").exists())
    print(f"{len(addrs)} functions with status {a.status}", flush=True)
    recs = fresh_records(paths, ghidra, cfg, addrs, a.chunk, a.timeout, lambda s: print(s, flush=True))
    rows, sigs = [], {}

    def own_sig(c):
        if c not in sigs:
            p = live["harness"] / c / "meta.json"
            sigs[c] = callee_drift.own_signature(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None
        return sigs[c]

    for x in addrs:
        meta = json.loads((live["harness"] / x / "meta.json").read_text(encoding="utf-8"))
        tier, why = drift(meta, recs.get(x, {"error": "missing"}), own_sig)
        if tier:
            rows.append({"addr": x, "tier": tier, "why": why})
    for tier in ("signature", "compared"):
        sel = [r for r in rows if r["tier"] == tier]
        print(f"\n{tier}: {len(sel)}")
        for r in sel:
            print(f"  {r['addr']}  " + "; ".join(r["why"]))
    if a.list:
        pick = [r["addr"] for r in rows if a.all or r["tier"] == "signature"]
        pathlib.Path(a.list).write_text("".join(f"{x}\n" for x in pick), encoding="utf-8")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
