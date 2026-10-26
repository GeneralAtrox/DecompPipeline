"""Check the CFG structurer on every function of a workspace without the judge: each function is translated with
SH4_CFG_CHECK set, so cfgstruct runs the structured tree against the goto graph it came from (sh4translate/cfgcheck)
on many branch decisions and stops at the first statement where they part.

The workspace is only read. A function the translator refuses is skipped (the judge reports those). Each meta.json
is read as the judge and the translator stage read it (core/ret_reg.judged: fr0_results.txt).

usage: python tools/struct_check.py --ws <workspace> [--all] [--only a,b|@FILE] [--jobs N] [--out results.json]
           [--emit DIR]
--emit DIR: write each function's C to DIR/ADDR.c (the translator is deterministic: two translators' C over one
workspace, diffed, name the functions a change can affect, so only those need the judge).
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import sys
import time

DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

_w = {}


def setup(ws):
    os.environ["SH4_CFG_CHECK"] = "1"
    from sh4translate.translate import Image, TargetFacts
    ws = pathlib.Path(ws)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    tj = json.loads((DP / "targets" / "dreamcast_sh4" / "target.json").read_text(encoding="utf-8"))
    facts = TargetFacts.from_target_json(tj)
    fb = ws / "state" / "function_bodies.txt"
    sys.path.insert(0, str(DP))
    from core import ret_reg
    _w["fr0"] = ret_reg.listed(ws / "state")
    _w.update(ws=ws, facts=facts, img=Image((ws / proj["binary"]).read_bytes(), facts.image_base, facts.little),
              entries={int(ln.split()[0], 16) for ln in fb.read_text().splitlines() if ln.strip()} if fb.exists()
              else set())
    from sh4translate.translate import spin_ranges
    _w["spin"] = spin_ranges(proj)
    _w["ranges"] = {}
    for ln in fb.read_text().splitlines() if fb.exists() else ():
        if len(ln.split()) == 3:
            e, lo, hi = (int(x, 16) for x in ln.split())
            _w["ranges"].setdefault(e, []).append((lo, hi))


def one(a, emit=""):
    from core import ret_reg
    from sh4translate.cfgcheck import Mismatch
    from sh4translate.translate import Signature, TranslateError, translate_function
    meta = json.loads((_w["ws"] / "harness" / a / "meta.json").read_text(encoding="utf-8"))
    meta = ret_reg.judged(meta, _w["fr0"], a)
    body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks", [])]
    t0 = time.time()
    try:
        src = translate_function(_w["img"], int(a, 16), Signature.from_meta(meta), _w["facts"], body or None,
                                 entries=_w["entries"], ranges=_w["ranges"].get(int(a, 16)),
                                 spin=_w["spin"].get(int(a, 16)))
    except Mismatch as e:
        return {"addr": a, "verdict": "MISMATCH", "why": str(e)[:600], "s": round(time.time() - t0, 2)}
    except TranslateError:
        return {"addr": a, "verdict": "NOTRANSLATE", "s": round(time.time() - t0, 2)}
    except Exception as e:  # noqa: BLE001
        return {"addr": a, "verdict": "ERROR", "why": repr(e)[:300], "s": round(time.time() - t0, 2)}
    if emit:
        (pathlib.Path(emit) / f"{a}.c").write_text(src, encoding="utf-8")
    return {"addr": a, "verdict": "OK", "s": round(time.time() - t0, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--all", action="store_true", help="every function with a harness, not only the matched ones")
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default="")
    ap.add_argument("--emit", default="")
    args = ap.parse_args()
    ws = pathlib.Path(args.ws)
    addrs = sorted(p.stem for p in (ws / "matched").glob("*.c"))
    if args.all:
        addrs = sorted(p.parent.name for p in (ws / "harness").glob("*/meta.json"))
    addrs = [a for a in addrs if (ws / "harness" / a / "meta.json").exists()]
    if args.only:
        only = args.only
        if only.startswith("@"):
            only = ",".join(pathlib.Path(only[1:]).read_text(encoding="utf-8").split())
        addrs = [a for a in addrs if a in set(only.split(","))]
    if args.emit:
        pathlib.Path(args.emit).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with cf.ProcessPoolExecutor(args.jobs, initializer=setup, initargs=(str(ws),)) as ex:
        rows = list(ex.map(one, addrs, [args.emit] * len(addrs), chunksize=8))
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"{len(rows)} functions {dict(Counter(r['verdict'] for r in rows))}  {time.time() - t0:.0f}s")
    for r in rows:
        if r["verdict"] in ("MISMATCH", "ERROR"):
            print(" ", r["addr"], r["verdict"], r["why"])


if __name__ == "__main__":
    main()
