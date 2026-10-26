"""Readable C without an agent (core/rtx): translate each matched function, lift the raw C to readable C, and judge
it with the readable judge and the gates, unchanged (core/readable_check, draft_run.readable_gaps, core/draft_gate).

The game workspace is only read. Its judge inputs are copied into a mirror workspace under --out, and the judge
runs there (its builds write into the mirror). Results: OUT/results/<game>.json, one row per function:
{"addr", "stage": "lift" | "judge" | "gaps" | "gate" | "pass", "why", "lines"}; the machine files are in
OUT/readable/<game>/ADDR.c.

usage: python tools/readable_tx.py --ws <workspace> --out <dir> [--only a,b] [--limit N] [--jobs N] [--nojudge]
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import pathlib
import shutil
import sys
import time

DP = pathlib.Path(__file__).resolve().parents[1]

STATE_FILES = ("code_ranges.txt", "msvc_env.json", "msvc_env_x86.json", "function_bodies.txt", "fr0_results.txt",
               "fsca_table.bin", "catalog.json")


def mirror(ws, dst, addrs):
    """The judge's inputs for addrs: project.json and the binary, small state files, every function's meta.json
    (callers read their callees'), the judged functions' harness, matched/ and the registry (readable/include,
    readable/names)."""
    dst.mkdir(parents=True, exist_ok=True)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    src = ws / proj["binary"]
    proj["binary"] = "binary/" + src.name
    (dst / "project.json").write_text(json.dumps(proj, indent=2), encoding="utf-8")
    b = dst / proj["binary"]
    b.parent.mkdir(parents=True, exist_ok=True)
    if not b.exists() or b.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, b)
    st = dst / "state"
    (st / "fsca").mkdir(parents=True, exist_ok=True)
    for f in STATE_FILES:
        if (ws / "state" / f).exists():
            shutil.copyfile(ws / "state" / f, st / f)
    for f in (ws / "state" / "fsca").glob("*.bin"):
        if not (st / "fsca" / f.name).exists():
            shutil.copyfile(f, st / "fsca" / f.name)
    for m in (ws / "harness").glob("*/meta.json"):
        d = dst / "harness" / m.parent.name
        if not (d / "meta.json").exists():
            d.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(m, d / "meta.json")
    for a in addrs:
        s, d = ws / "harness" / a, dst / "harness" / a
        for f in s.iterdir() if s.is_dir() else ():
            if f.is_file() and not (d / f.name).exists():
                shutil.copyfile(f, d / f.name)
    for sub in ("matched", "readable/include", "readable/names"):
        if (dst / sub).exists():
            shutil.rmtree(dst / sub)
        if (ws / sub).exists():
            shutil.copytree(ws / sub, dst / sub)


def wsname(ws):
    parts = [x for x in ws.resolve().parts if x not in ("decomp", "main")]
    return "".join(ch for ch in parts[-1] if ch.isalnum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--nojudge", action="store_true", help="lift only (no build, no vectors)")
    ap.add_argument("--tx", default="", help="a folder of the translator's raw C (ADDR.c) to lift instead of "
                    "translating here")
    args = ap.parse_args()
    ws, out = pathlib.Path(args.ws), pathlib.Path(args.out).resolve()
    addrs = sorted(p.stem for p in (ws / "matched").glob("*.c") if (ws / "harness" / p.stem / "meta.json").exists())
    if args.only:
        addrs = [a for a in addrs if a in set(args.only.split(","))]
    if args.limit:
        addrs = addrs[:args.limit]
    name = wsname(ws)
    mws = out / "mirror" / name
    mirror(ws, mws, [] if args.nojudge else addrs)
    rows = []
    t0 = time.time()
    with cf.ProcessPoolExecutor(max(1, args.jobs), initializer=_init, initargs=(str(mws), vars(args), str(out), name)) as ex:
        for k, row in enumerate(ex.map(_one, addrs, chunksize=8)):
            rows.append(row)
            if (k + 1) % 200 == 0:
                print(f"{k + 1}/{len(addrs)} {dict(collections.Counter(r['stage'] for r in rows))}", flush=True)
    res = out / "results"
    res.mkdir(parents=True, exist_ok=True)
    tag = "_lift" if args.nojudge else ""
    (res / f"{name}{tag}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(name, len(rows), dict(collections.Counter(r["stage"] for r in rows)), f"{time.time() - t0:.0f} s")


CTX = {}


def _init(mws, args, out, name):
    """A worker: the mirror as the project, the registry and the translator, once."""
    os.environ["DECOMP_PROJECT"] = mws
    sys.path.insert(0, str(DP))
    from core import draft_gate, paths, readable
    from core.rtx import source
    cfg = paths.config()
    spec = source.spec()
    if spec is None:
        raise SystemExit(f"no readable pass for target {paths.PROJECT['target']} (targets/<target>/rtx.py)")
    args = argparse.Namespace(**args)
    from core.rtx import usage
    usage.SRC = pathlib.Path(args.ws) / "readable" / "src"  # the agents' files name constants (the mirror has none)
    CTX.update(cfg=cfg, spec=spec, reg=readable.Registry(), args=args, out=pathlib.Path(out), name=name,
               max_lines=cfg.get("readable_draft_max_lines", draft_gate.MAX_LINES),
               raw_of=translator(args, cfg, paths), wd=pathlib.Path(out) / "readable" / name)
    CTX["wd"].mkdir(parents=True, exist_ok=True)


def _one(a):
    from core import draft_gate, draft_run, paths, readable
    from core import readable_check as rcheck
    from core.rtx import job as rjob
    from core.rtx import lift
    c = CTX
    t0 = time.time()
    row = {"addr": a}
    meta = paths.read_json(paths.HARNESS / a / "meta.json")
    try:
        raw = c["raw_of"](a, meta)
        job = rjob.make(c["cfg"], c["reg"], a, meta, raw, c["spec"])
        text = lift.lift(raw, job)
    except (lift.Unsupported, readable.RegistryError) as e:
        row.update(stage="lift", why=str(e)[:300])
        return row
    except Exception as e:  # noqa: BLE001  (a lift bug: counted, never adopted)
        row.update(stage="lift", why=f"error: {type(e).__name__}: {e}"[:300])
        return row
    row["lift_s"] = round(time.time() - t0, 3)
    f = c["wd"] / f"{a}.c"
    f.write_text(text, encoding="utf-8")
    row["lines"] = draft_gate.lines(text)
    gaps = draft_run.readable_gaps(text)
    gate = draft_gate.reasons(text, max_lines=10 ** 9)  # the literal check alone; the length is reported apart
    row["gaps"], row["gate"] = gaps, gate
    if c["args"].nojudge:
        row["stage"] = "lifted"
        return row
    try:
        res = rcheck.check_readable(c["cfg"], a, str(f), c["out"] / "build" / c["name"] / a, reg=c["reg"])
    except Exception as e:  # noqa: BLE001
        row.update(stage="judge", why=f"error: {type(e).__name__}: {e}"[:300])
        return row
    if res["verdict"] != "PASS" or res.get("stage") != "compare" or res.get("run_error"):
        row.update(stage="judge", why=draft_run.why(res, [])[:300])
    elif gaps:
        row.update(stage="gaps", why=", ".join(gaps)[:300])
    elif gate:
        row.update(stage="gate", why=", ".join(gate)[:300])
    else:
        row["stage"] = "pass"
    row["s"] = round(time.time() - t0, 2)
    return row


def translator(args, cfg, paths):
    """raw_of(addr, meta): the translator's raw C of a function, without comments."""
    if args.tx:
        from core.draft import strip_comments
        d = pathlib.Path(args.tx)
        return lambda a, meta: strip_comments((d / f"{a}.c").read_text(encoding="utf-8"))
    tdir = paths.TARGET / "translator"
    sys.path.insert(0, str(tdir))
    from core import ret_reg
    from sh4translate.translate import Image, Signature, TargetFacts, spin_ranges, translate_function
    facts = TargetFacts.from_target_json(json.loads((paths.TARGET / "target.json").read_text(encoding="utf-8")))
    img = Image(cfg["binary_path"].read_bytes(), facts.image_base, facts.little)
    entries, ranges = set(), {}
    if paths.FUNCTION_BODIES.exists():
        for ln in paths.FUNCTION_BODIES.read_text().splitlines():
            if len(ln.split()) == 3:
                e, lo, hi = (int(x, 16) for x in ln.split())
                entries.add(e)
                ranges.setdefault(e, []).append((lo, hi))
    spins, listed = spin_ranges(cfg), ret_reg.listed(paths.STATE)

    def raw_of(a, meta):
        meta = ret_reg.judged(meta, listed, a)
        body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks", [])]
        return translate_function(img, int(a, 16), Signature.from_meta(meta), facts, body or None, entries=entries,
                                  comments=False, ranges=ranges.get(int(a, 16)), spin=spins.get(int(a, 16)))
    return raw_of


if __name__ == "__main__":
    main()
