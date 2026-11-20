"""The reach of the Xbox 360 oracle's op-aware extra vectors (facts "seed_extra", TcSeedx), from a prepared workspace.

Each harness's oracle_summary.json "seedx" (TcVectors.seedxJson) holds what the extra vectors reached that none of the
usual vectors of the same run did: basic blocks ("new_blocks") and vector-op cases ("new_cases": "pc:sat",
"pc:den_in", "pc:den_out"; TcVmx.event). The usual vectors are drawn exactly as before the extras existed, so the run
is its own baseline. The headline is the number of functions with at least one new block or case.

--old <harness dir of a workspace prepared without seed_extra> also checks that each function's usual vectors are
unchanged: the old vectors.txt must be the new one's first vectors, byte for byte (V..E blocks), and the old
expected.txt the new one's first results. A function failing that is listed as "changed".

    python seedx_reach.py --harness <ws>/harness [--only addrs.txt] [--old <old ws>/harness] [--json out.json]

Nearest existing: oracle_summary.json "blocks_unhit" (TcPrepare) - it lists the blocks no vector reached in one run,
with no split between usual and extra vectors and no vector-op cases; and TcSwitchHits, which counts switch targets
only. Nothing compared two vector sets' reach.
"""
import argparse
import json
import pathlib
import re
import sys

BLOCK = re.compile(r"^V \d+ \w+\n.*?^E\n", re.S | re.M)


def blocks(text):
    """The V..E blocks of a vectors.txt or expected.txt, in order."""
    return BLOCK.findall(text.replace("\r\n", "\n"))


def prefix_same(old, new):
    """The old file's blocks are the new file's first blocks."""
    a, b = blocks(old), blocks(new)
    return len(b) >= len(a) and b[:len(a)] == a


def reach(harness, only=None, old=None):
    harness = pathlib.Path(harness)
    out = {"functions": 0, "with_seedx": 0, "with_classes": 0, "gained": [], "new_blocks": 0, "new_cases": 0,
           "cases_by_kind": {}, "classes": {}, "stopped": [], "short": [], "changed": [], "missing": []}
    addrs = sorted(only) if only is not None else sorted(p.name for p in harness.iterdir() if p.is_dir())
    for a in addrs:
        summ = harness / a / "oracle_summary.json"
        if not summ.is_file():
            out["missing"].append(a)
            continue
        out["functions"] += 1
        s = json.loads(summ.read_text(encoding="utf-8")).get("seedx")
        if old is not None:
            for name in ("vectors.txt", "expected.txt"):
                o, n = pathlib.Path(old) / a / name, harness / a / name
                if o.is_file() and n.is_file() and not prefix_same(o.read_text(encoding="utf-8"),
                                                                   n.read_text(encoding="utf-8")):
                    out["changed"].append(a)
                    break
        if s is None:
            continue
        out["with_seedx"] += 1
        if s.get("classes"):
            out["with_classes"] += 1
        for c in s.get("classes") or []:
            out["classes"][c] = out["classes"].get(c, 0) + 1
        if s.get("stopped"):
            out["stopped"].append(a)
        if s.get("classes") and (s.get("dev_valid", 0) < s.get("extra", 0) or s.get("hidden_valid", 0) < s.get("extra", 0)):
            out["short"].append(a)
        nb, nc = s.get("new_blocks") or [], s.get("new_cases") or []
        out["new_blocks"] += len(nb)
        out["new_cases"] += len(nc)
        for c in nc:
            for k in c.split(":", 1)[1].split("+"):
                out["cases_by_kind"][k] = out["cases_by_kind"].get(k, 0) + 1
        if nb or nc:
            out["gained"].append({"addr": a, "new_blocks": nb, "new_cases": nc})
    return out


def report(r):
    lines = [f"functions {r['functions']}, with seedx {r['with_seedx']}, with classes {r['with_classes']} "
             f"{json.dumps(r['classes'], sort_keys=True)}",
             f"GAINED {len(r['gained'])} functions reach a block or case no usual vector reached "
             f"({r['new_blocks']} blocks, {r['new_cases']} cases {json.dumps(r['cases_by_kind'], sort_keys=True)})",
             f"extras stopped by the deadline {len(r['stopped'])}, short of seed_extra {len(r['short'])}, "
             f"missing summaries {len(r['missing'])}"]
    if r["changed"]:
        lines.append(f"CHANGED usual vectors in {len(r['changed'])}: {' '.join(r['changed'][:20])}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--harness", required=True)
    ap.add_argument("--only", help="file of addresses (one per line, hex) to count; default every harness")
    ap.add_argument("--old", help="harness dir prepared without seed_extra: check the usual vectors are unchanged")
    ap.add_argument("--json", help="write the full result here")
    a = ap.parse_args(argv)
    only = None
    if a.only:
        only = {t.lower() for t in re.findall(r"\b[0-9a-fA-F]{8}\b", pathlib.Path(a.only).read_text(encoding="utf-8"))}
    r = reach(a.harness, only, a.old)
    print(report(r))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(r, indent=1), encoding="utf-8")
    return 1 if r["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
