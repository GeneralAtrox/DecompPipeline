"""List readable functions that never read a parameter whose storage the original reads (core/param_unread.py).

usage: python tools/param_unread.py --project DIR [targets|@FILE] [--json FILE] [--write]

TC 8c0117f4 Camera_UpdateKind10 discarded its 12 camera arguments and the judge passed it (docs/PROOF_GATES.md
section 7). Static and text only: readable/src, readable/names, the harness meta and the packet's asm.txt.
- project.json "param_unread": {"waive": {"ADDR param": "reason"}}: a hit judged by hand (a path the original
  never takes that the check cannot see, or a value with no effect in the port); it is listed as waived, not as a hit.
- --write: state/param_unread/summary.json and requeue.txt (`addr param storage`); it requeues nothing.
Exit 0 with no hits, 1 with hits.
"""
import argparse
import json
import pathlib
import re
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("targets", nargs="*", help="addresses, or @FILE (default: every readable file)")
    ap.add_argument("--json", help="write the hits and counts here")
    ap.add_argument("--write", action="store_true", help="state/param_unread/summary.json and requeue.txt")
    a = ap.parse_args(argv)
    use_project(a.project)
    from core import param_unread as pu, paths, readable, readable_abi, sh4_asm

    targets = []
    for t in a.targets:
        targets += pathlib.Path(t[1:]).read_text(encoding="utf-8").split() if t.startswith("@") else [t]
    files = sorted(p.stem for p in readable.SRC.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem))
    if targets:
        want = {paths.norm_addr(t) for t in targets}
        files = [f for f in files if f in want]
    reg = readable.Registry()
    hits, quiet, notes = [], [], []
    counts = {"files": len(files), "params": 0, "unread": 0, "hits": 0, "waived": 0, "not_followed": 0,
              "not_placed": 0, "no_definition": 0}
    waive = (paths.config().get("param_unread") or {}).get("waive") or {}
    waived = []
    for addr in files:
        meta = paths.read_json(paths.HARNESS / addr / "meta.json")
        name = (reg.functions.get(addr) or {}).get("name") or reg_name(reg, addr)
        d = pu.definition((readable.SRC / f"{addr}.c").read_text(encoding="utf-8"), [n for n in (name, f"fn_{addr}")
                                                                                     if n])
        if d is None or not meta:
            counts["no_definition"] += 1
            continue
        proto, body = d
        try:
            params = readable.parse_proto(proto)[2]
            st = readable_abi.own_storages(reg, addr, proto, meta)
        except readable.RegistryError as e:
            counts["not_placed"] += 1
            notes.append(f"{addr}: not placed: {str(e)[:160]}")
            continue
        unread = [(n, s) for (_, n), s in zip(params, st) if s != readable_abi.NONE and n and pu.unread(body, n)]
        counts["params"] += len(params)
        counts["unread"] += len(unread)
        if not unread:
            continue
        text = sh4_asm.read_packet(paths.PACKETS / addr)
        orig = pu.original_reads(sh4_asm.parse(text) if text else [], meta.get("callees"))
        if orig["status"] != "ok":
            counts["not_followed"] += 1
            notes.append(f"{addr}: asm {orig['status']}; unread {', '.join(n for n, _ in unread)}")
            continue
        for n, s in unread:
            row = {"addr": addr, "fn": proto.split("(")[0].split()[-1].lstrip("*"), "param": n, "storage": s,
                   "where": orig["where"].get(s) or "its address is passed to a call"}
            if not pu.reads_storage(orig, s):
                quiet.append(row)
            elif f"{addr} {n}" in waive:
                waived.append(dict(row, reason=waive[f"{addr} {n}"]))
            else:
                hits.append(row)
    counts["hits"] = len(hits)
    counts["waived"] = len(waived)
    counts["unread_not_read_by_original"] = len(quiet)
    for h in hits:
        print(f"{h['addr']}.c: {h['fn']} never reads `{h['param']}` ({h['storage']}); the original uses it: "
              f"{h['where']}")
    for w in waived:
        print(f"waived: {w['addr']} {w['param']}: {w['reason']}")
    for n in notes:
        print(f"note: {n}")
    print(json.dumps(counts))
    data = {"counts": counts, "hits": hits, "waived": waived, "unread_not_read_by_original": quiet, "notes": notes}
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(data, indent=1), encoding="utf-8")
    if a.write:
        out = paths.STATE / "param_unread"
        out.mkdir(parents=True, exist_ok=True)
        paths.write_json(out / "summary.json", data)
        (out / "requeue.txt").write_text("".join(f"{h['addr']} {h['param']} {h['storage']}\n" for h in hits),
                                         encoding="utf-8")
    return 1 if hits else 0


def reg_name(reg, addr):
    proto = (reg.functions.get(addr) or {}).get("proto")
    m = re.search(r"\b([A-Za-z_]\w*)\s*\(", proto or "")
    return m.group(1) if m else None


if __name__ == "__main__":
    sys.exit(main())
