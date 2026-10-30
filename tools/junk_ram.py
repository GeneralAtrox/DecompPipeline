"""Report-only junk-RAM check (docs/proposals/learnings_dc_decomp_kickoff.md idea 2): run a function's C on each of
its vectors twice, with the guest RAM nobody set left zero (the judge's way) and filled with a per-address junk
pattern, and list the functions whose results differ between the two runs.

C that reads the wrong place in unset global or heap RAM can read 0 exactly where the original read 0, and pass. A
C-against-C difference is a lead, not a verdict: the games read a lot of unset RAM themselves (the junk round: 55%
of correct functions differ, all false alarms). --original adds the judge's own junk pass (core/junk_ram, config
"junk_ram"): the C's junk run against the original's, run by xemu on the same junk. Builds go to a temporary folder;
the junk image, xemu's runner and the junk oracle are kept under the workspace's build/junk_ram/ (the judge's
cache); no verdict, record or stamp moves.

Nearest existing: core/junk_ram (the judge pass, which this reports from: the junk image, the junk oracle, the
verdicts), core/check.run_check (the judge, which runs the C on junk only with the flag on and only for a check
that passed). This tool adds the C-against-C comparison and runs any C (--src) without a verdict.

usage: python tools/bounded.py 3600 python tools/junk_ram.py --project DIR [ADDR... | @FILE] [--src ADDR=FILE ...]
           [--original] [--seed S] [--workers N] [--json OUT] [-v]
  ADDR/@FILE   the functions (default: every matched one with a harness)
  --src        judge this C file for ADDR instead of its matched C (a mutant, a draft)
  --original   also the judge's junk pass (core/junk_ram): on the vectors where xemu's zero run agrees with
               expected.txt and the original's junk run does not fault, the C's junk run against the original's (the
               "junk verdict"); the vectors left out are counted
  --seed       the junk pattern's seed (default 1, the judge's; --original takes 1 only)
Prints per function: vectors, how many differ between the two runs, the first differences, and how many vectors of
the zero run differ from expected.txt (the judge's own comparison: 0 for C that passes). Exit 0 (report tool).
Dreamcast (dreamcast_sh4) only.
"""
import argparse
import concurrent.futures
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import expand_addrs, use_project  # noqa: E402



def junk_image(image, seed=1):
    """core/junk_ram.junk_image (the judge's junk pattern; one copy)."""
    from core import junk_ram
    return junk_ram.junk_image(image, seed)


def differences(meta, image, vecs, zero, junk, expected, frame_lo, strict):
    """Per vector: the judge's differences between the zero run and the junk run (the zero run in place of the
    oracle's expected), and whether the zero run itself differs from expected.txt. Vectors the zero run did not
    finish are left out (they fail the judge anyway). Returns ({vid: [diff...]}, [vid zero-run fails])."""
    from core.compare import compare_vector
    out, zfail = {}, []
    for vid, vec in sorted(vecs.items()):
        z = zero.get(vid)
        if z is None or not z.get("done"):
            zfail.append(vid)
            continue
        if vid in expected and compare_vector(meta, image, vec, expected[vid], z, frame_lo, strict, []):
            zfail.append(vid)
        d = compare_vector(meta, image, vec, z, junk.get(vid), frame_lo, strict, [])
        if d:
            out[vid] = d
    return out, zfail


def check_one(cfg, addr, src_text, root, junk_bin, xexe=None):
    """Build src_text for addr once (the official build), run it on the zero and the junk image, compare; with
    xexe (true), also the judge's junk pass: the C's junk run against the original's (core/junk_ram)."""
    from core import check, paths, readable_stack, ret_reg, strict_judge
    from core.compare import compare_vector, parse_results, parse_vectors
    harness = paths.HARNESS / addr
    meta = ret_reg.judged(paths.read_json(harness / "meta.json"), ret_reg.listed(paths.STATE), addr)
    d = root / addr
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    t0 = time.time()
    try:
        (d / "work.c").write_text(src_text, encoding="utf-8")
        ok, msgs = check.build(cfg, meta, d / "work.c", d / "b")
        if not ok:
            return {"addr": addr, "error": "build: " + " | ".join(msgs[:3])}
        table = paths.fsca_table() if cfg.get("fsca_table") else paths.FSCA_TABLE
        res = {}
        for tag, binary in (("zero", cfg["binary_path"]), ("junk", junk_bin)):
            out = d / f"{tag}.txt"
            cmd = [str(d / "b" / "runner.exe"), str(binary), str(paths.CODE_RANGES), str(table),
                   str(harness / "vectors.txt"), "all", str(out)]
            rc, log, reason = check.run_runner(cmd, cfg["check_timeout_s"], out, check.output_cap(harness))
            res[tag] = parse_results(out)
            if reason or rc:
                res[f"{tag}_run_error"] = reason or f"exit {rc}: {log.strip()[-200:]}"
        image = cfg["binary_path"].read_bytes()
        vecs = parse_vectors(harness / "vectors.txt")
        exp, lo, strict = parse_results(harness / "expected.txt"), readable_stack.frame_lo(), strict_judge.modes(cfg)
        diff, zfail = differences(meta, image, vecs, res["zero"], res["junk"], exp, lo, strict)
        if xexe:  # the judge's own junk pass (core/junk_ram): its cached junk oracle and its verdicts
            from core import junk_ram
            ojunk, unfit = junk_ram.oracle(cfg, addr, harness, meta, image, vecs, exp, lo, strict)
            vdiff, judged, skipped = junk_ram.verdicts(meta, image, vecs, list(vecs), ojunk, res["junk"], unfit, lo,
                                                       strict)
            odiff = {v for v in vecs if v not in unfit and v in exp and compare_vector(
                meta, image, vecs[v], exp[v], ojunk[v], lo, strict, [])}
            orig = {"xemu_disagrees": skipped["xemu"], "original_faults_with_junk": skipped["fault"],
                    "usable": judged, "original_differs": sorted(odiff), "junk_verdict_fails": sorted(vdiff),
                    "junk_verdict_first": [f"V{v}: {x}" for v in sorted(vdiff)[:2] for x in vdiff[v][:3]]}
    finally:
        shutil.rmtree(d, ignore_errors=True)
    rec = {"addr": addr, "vectors": len(vecs), "differ": sorted(diff), "zero_fails": len(zfail),
           "first": [f"V{v}: {x}" for v in sorted(diff)[:2] for x in diff[v][:3]], "seconds": round(time.time() - t0, 1)}
    rec.update({k: v for k, v in res.items() if k.endswith("run_error")})
    if xexe:
        rec["original"] = orig
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("addrs", nargs="*")
    ap.add_argument("--src", action="append", default=[], help="ADDR=FILE")
    ap.add_argument("--original", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--json")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    ws = use_project(a.project)
    from core import paths
    if paths.PROJECT.get("target") != "dreamcast_sh4":
        raise SystemExit(f"{ws}: target {paths.PROJECT.get('target')}, the junk image is Dreamcast's layout only")
    cfg = paths.config()
    srcs = dict(x.split("=", 1) for x in a.src)
    led = (paths.read_json(paths.LEDGER) or {}).get("functions") or {}
    addrs = expand_addrs(a.addrs) if a.addrs else sorted(
        x for x, r in led.items() if r.get("status") == "matched" and (paths.MATCHED / f"{x}.c").exists())
    addrs = [x for x in dict.fromkeys(addrs + list(srcs)) if (paths.HARNESS / x / "expected.txt").exists()]
    root = pathlib.Path(tempfile.mkdtemp(prefix="junk_ram_"))
    if not paths.MSVC_ENV.exists():  # check.msvc_env caches it in state/: keep the workspace untouched
        paths.MSVC_ENV = root / "msvc_env.json"
    junk_bin = root / "junk_image.bin"
    junk_bin.write_bytes(junk_image(cfg["binary_path"].read_bytes(), a.seed))
    xexe = bool(a.original)
    if xexe and a.seed != 1:
        raise SystemExit("--original uses the judge's junk oracle (core/junk_ram), made with seed 1")
    print(f"junk-RAM check of {ws}: {len(addrs)} function(s), seed {a.seed}; report only (no writes)", flush=True)
    recs, lock = {}, threading.Lock()

    def one(addr):
        f = pathlib.Path(srcs[addr]) if addr in srcs else paths.MATCHED / f"{addr}.c"
        try:
            rec = check_one(cfg, addr, f.read_text(encoding="utf-8", errors="replace"), root, junk_bin, xexe)
        except Exception as e:  # noqa: BLE001  (one function's crash is its own error)
            rec = {"addr": addr, "error": f"{type(e).__name__}: {e}"[:300]}
        rec["src"] = str(f)
        with lock:
            recs[addr] = rec
            if rec.get("error"):
                print(f"{addr}: ERROR {rec['error']}", flush=True)
            else:
                o = rec.get("original")
                print(f"{addr}: {len(rec['differ'])}/{rec['vectors']} vectors differ with junk RAM; zero run fails "
                      f"{rec['zero_fails']} vs expected" + (
                          f"; original (xemu, {o['usable']} usable vectors): {len(o['original_differs'])} differ "
                          f"with junk, junk verdict fails {len(o['junk_verdict_fails'])}" if o else "")
                      + f"  {rec['seconds']} s", flush=True)
                if a.verbose:
                    for x in rec["first"] + ((o or {}).get("junk_verdict_first") or []):
                        print(f"    {x}")
    try:
        with concurrent.futures.ThreadPoolExecutor(max(1, min(a.workers, 4))) as ex:
            list(ex.map(one, addrs))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    ok = [r for r in recs.values() if not r.get("error")]
    flagged = sorted(r["addr"] for r in ok if r["differ"])
    print(f"\n== {len(recs)} functions, {len(recs) - len(ok)} errors; differ with junk RAM: {len(flagged)}"
          + (f" ({' '.join(flagged)})" if flagged else ""))
    if xexe:
        jv = sorted(r["addr"] for r in ok if r["original"]["junk_verdict_fails"])
        print(f"   junk verdict (the C against the original, both with junk, on xemu's usable vectors) fails: {len(jv)}"
              + (f" ({' '.join(jv)})" if jv else ""))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"project": str(ws), "seed": a.seed, "functions": recs}, indent=1),
                                        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
