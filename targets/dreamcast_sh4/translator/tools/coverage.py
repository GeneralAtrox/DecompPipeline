"""How much of each function DP's existing test vectors exercise, and whether the judge notices small changes.

For every function with a harness in one workspace (mirrored as tools/dp_judge.py does; the game folder is only read):
  1. the translator's C is judged by DP's judge, unchanged (core/check.run_check);
  2. a separate coverage build of the same function (FunctionTranslator.instrument: a counter on every block and on
     both directions of every conditional branch, written to a file when the runner exits) runs the same vectors
     with DP's own build and runner; the judged C is never touched;
  3. with --mutants N, N small changes to one instruction's C each (a constant +1, an operator swapped, a statement
     dropped) are judged again: a mutant the judge fails is killed. Each mutant is marked covered or not by whether
     the vectors ran the block it is in.

usage: python tools/coverage.py --ws <workspace> --out <dir> [--mutants 5] [--jobs 6]
Writes <out>/coverage_<ws>.json (per function: counters, hit/missed, mutants) and <out>/uncovered_<ws>.json (every
block and branch direction no vector reaches: what new vectors should target).
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import random
import re
import shutil
import struct
import sys
import time

DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
from dp_judge import mirror, wsname  # noqa: E402

HEADER = """#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include "tc_rt.h"
static unsigned cv_n[{n}];
static int cv_reg;
static void cv_dump(void)
{{
    FILE *f = fopen("{path}", "wb");
    if (f) {{ fwrite(cv_n, sizeof cv_n, 1, f); fclose(f); }}
}}
static void CV(unsigned k)
{{
    if (!cv_reg) {{ cv_reg = 1; atexit(cv_dump); }}
    cv_n[k]++;
}}
"""

SWAPS = [("+=", "-="), ("-=", "+="), (" & ", " | "), (" | ", " & "), (" ^ ", " & "), ("==", "!="), ("!=", "=="),
         ("<<", ">>"), (">>", "<<"), (" + ", " - "), (" - ", " + "), ("&=", "|="), ("|=", "&="), ("^=", "&=")]
LIT = re.compile(r"\b(0x[0-9a-f]+|\d+)u\b")


def mut_const(lines):
    for k, x in enumerate(lines):
        m = LIT.search(x)
        if m:
            v = int(m.group(1), 0) + 1
            return lines[:k] + [x[:m.start()] + f"0x{v & 0xFFFFFFFF:x}u" + x[m.end():]] + lines[k + 1:]
    return None


def mut_op(lines):
    for k, x in enumerate(lines):
        for a, b in SWAPS:
            if a in x:
                return lines[:k] + [x.replace(a, b, 1)] + lines[k + 1:]
    return None


def mut_drop(lines):
    if not lines or any(c in x for x in lines for c in "{}") or any(x.strip().startswith("@") for x in lines):
        return None
    return []


MUTATORS = {"const": mut_const, "op": mut_op, "drop": mut_drop}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mutants", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--simplify", default="all", help="simplification passes: all, none, or a comma list")
    ap.add_argument("--only", default="", help="a comma list of functions (default: every one with a harness)")
    args = ap.parse_args()
    from sh4translate.simplify import PASSES
    passes = set(PASSES if args.simplify == "all" else () if args.simplify == "none" else args.simplify.split(","))
    ws, out = pathlib.Path(args.ws), pathlib.Path(args.out).resolve()
    addrs = sorted(p.parent.name for p in (ws / "harness").glob("*/meta.json"))
    if args.only:
        addrs = [a for a in addrs if a in set(args.only.split(","))]
    name = wsname(ws)
    mws = out / "mirror" / name
    mirror(ws, mws, addrs)
    os.environ["DECOMP_PROJECT"] = str(mws)
    sys.path.insert(0, str(DP))
    from core import check, paths  # noqa: E402
    from core.compare import compare_vector, parse_results, parse_vectors  # noqa: E402
    from core import readable_stack  # noqa: E402
    from sh4translate.translate import FunctionTranslator, Image, Signature, TargetFacts, spin_ranges, translate_function
    cfg = paths.config()
    tj = json.loads((paths.TARGET / "target.json").read_text(encoding="utf-8"))
    facts = TargetFacts.from_target_json(tj)
    img = Image(cfg["binary_path"].read_bytes(), facts.image_base, facts.little)
    fb = paths.STATE / "function_bodies.txt"
    entries = {int(ln.split()[0], 16) for ln in fb.read_text().splitlines() if ln.strip()} if fb.exists() else set()
    ranges = {}
    for ln in fb.read_text().splitlines() if fb.exists() else ():
        if len(ln.split()) == 3:
            e, lo, hi = (int(x, 16) for x in ln.split())
            ranges.setdefault(e, []).append((lo, hi))
    spins = spin_ranges(cfg)
    from core import ret_reg  # noqa: E402
    fr0_listed = ret_reg.listed(paths.STATE)
    wd = out / "work" / name
    wd.mkdir(parents=True, exist_ok=True)

    def make(a, meta, **kw):
        body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks", [])]
        ft = FunctionTranslator(img, int(a, 16), Signature.from_meta(meta), facts, body or None, entries, False,
                                ranges.get(int(a, 16)), spins.get(int(a, 16)))
        ft.simplify = passes    # the judged C's passes (the counters and mutants go in before them)
        for k, v in kw.items():
            setattr(ft, k, v)
        return ft, ft.translate()

    def judge(a, src, tag):
        wf = wd / f"{a}_{tag}.c"
        wf.write_text(src, encoding="utf-8")
        bd = out / "build" / name / f"{a}_{tag}"
        try:
            return check.run_check(cfg, a, wf, build_dir=bd)
        finally:
            shutil.rmtree(bd, ignore_errors=True)
            wf.unlink(missing_ok=True)

    def covered_run(a, meta):
        """Counter values of the coverage build over every vector of the function (and how many vectors still
        compare equal there: the coverage build must behave as the judged one)."""
        ft, src = make(a, meta, instrument=True)
        bd = out / "build" / name / f"{a}_cov"
        shutil.rmtree(bd, ignore_errors=True)
        bd.mkdir(parents=True)
        cov_file = bd / "cov.bin"
        n = max(1, len(ft.cov))
        src = src.replace('#include "tc_rt.h"\n', HEADER.format(n=n, path=cov_file.as_posix()), 1)
        wf = bd / "cov_src.c"
        wf.write_text(src, encoding="utf-8")
        try:
            harness = paths.HARNESS / a
            from core import ret_reg
            jm = ret_reg.judged(meta, ret_reg.listed(paths.STATE), a)
            ok, msgs = check.build(cfg, jm, wf, bd / "b")
            if not ok:
                return ft, None, "coverage build failed: " + " | ".join(msgs[:3]), 0
            table = paths.fsca_table() if cfg.get("fsca_table") else paths.FSCA_TABLE
            res = bd / "actual.txt"
            cmd = [str(bd / "b" / "runner.exe"), str(cfg["binary_path"]), str(paths.CODE_RANGES), str(table),
                   str(harness / "vectors.txt"), "all", str(res)]
            rc, log, reason = check.run_runner(cmd, cfg["check_timeout_s"], res, check.output_cap(harness))
            counts = list(struct.unpack(f"<{n}I", cov_file.read_bytes())) if cov_file.exists() else None
            vecs, exp, act = (parse_vectors(harness / "vectors.txt"), parse_results(harness / "expected.txt"),
                              parse_results(res))
            image = cfg["binary_path"].read_bytes()
            same = sum(1 for i in vecs if not compare_vector(jm, image, vecs[i], exp[i], act.get(i),
                                                              readable_stack.frame_lo()))
            return ft, counts, None if counts is not None else f"no counter file (runner {reason or rc})", same
        finally:
            shutil.rmtree(bd, ignore_errors=True)

    def one(a):
        meta = ret_reg.judged(paths.read_json(paths.HARNESS / a / "meta.json"), fr0_listed, a)  # as the judge does
        body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks", [])]
        row = {"addr": a}
        src = translate_function(img, int(a, 16), Signature.from_meta(meta), facts, body or None, entries=entries,
                                 simplify=passes, ranges=ranges.get(int(a, 16)), spin=spins.get(int(a, 16)))
        r = judge(a, src, "judged")
        row["verdict"], row["vectors"] = r["verdict"], r["total"]
        if r["verdict"] != "PASS":
            return row
        ft, counts, err, same = covered_run(a, meta)
        if err:
            row["error"] = err
            return row
        row["coverage_build_vectors_equal"] = same
        pts = [dict(p, hits=c) for p, c in zip(ft.cov, counts)]
        blocks = [p for p in pts if p["kind"] == "block"]
        dirs = [p for p in pts if p["kind"] != "block"]
        row["blocks"] = [len(blocks), sum(1 for p in blocks if p["hits"])]
        row["branch_dirs"] = [len(dirs), sum(1 for p in dirs if p["hits"])]
        row["missed"] = [p for p in pts if not p["hits"]]
        if args.mutants:
            hit_blocks = {int(p["addr"], 16) for p in blocks if p["hits"]}
            cands = []
            for x in sorted(ft.emitted):
                for name, fn in MUTATORS.items():
                    if fn(ft.emitted[x]) is not None:
                        cands.append((x, name))
            rnd = random.Random(args.seed * 1000003 + int(a, 16))
            rnd.shuffle(cands)
            muts = []
            for x, name in cands:
                if len(muts) >= args.mutants:
                    break
                ftm, msrc = make(a, meta, mutation=(x, MUTATORS[name]))
                if msrc == make(a, meta)[1]:
                    continue
                mr = judge(a, msrc, f"m{len(muts)}")
                muts.append({"at": f"{x:08x}", "op": name, "covered": ft.block_start(x) in hit_blocks,
                             "verdict": mr["verdict"], "stage": mr["stage"]})
            row["mutants"] = muts
        return row

    t0 = time.time()
    from dp_judge import warm_runtime
    warm_runtime(check, cfg)
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(one, addrs))
    shutil.rmtree(out / "build" / name, ignore_errors=True)
    shutil.rmtree(wd, ignore_errors=True)
    (out / f"coverage_{name}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    unc = [{"func": r["addr"], "addr": p["addr"], "kind": p["kind"]} for r in rows for p in r.get("missed", [])]
    (out / f"uncovered_{name}.json").write_text(json.dumps(unc, indent=1), encoding="utf-8")
    judged = [r for r in rows if "blocks" in r]
    print(f"{name}: {len(rows)} harnessed, {len(judged)} PASS and measured, "
          f"{sum(1 for r in rows if r.get('error'))} coverage errors  {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
