"""Verdict parity of the readable runtime on a workspace's existing harnesses: each matched function's raw C judged by
the raw judge (core/check.run_check: the target's runtime) and by the readable runtime (core/readable_check.check_raw:
the 32-bit build refine.py uses, guest RAM at its own address). The two verdicts must agree; any disagreement is a
runtime defect. Unlike tools/regress.py --parity it prepares nothing (no Ghidra): it judges the harnesses as they are,
so run it on a private copy of the workspace (builds go to its build/parity/, removed per function).

usage: python tools/readable_parity.py --project DIR [--list FILE | ADDR ...] [--json OUT] [--keep]
Exit 0 when every judged function's two verdicts agree.
"""
import argparse
import json
import pathlib
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import expand_addrs, use_project  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--list")
    ap.add_argument("addrs", nargs="*")
    ap.add_argument("--json")
    ap.add_argument("--keep", action="store_true", help="keep each function's two build folders")
    a = ap.parse_args()
    use_project(a.project)
    from core import check, paths, readable_check
    cfg = paths.config()
    if a.list:
        addrs = [w for line in pathlib.Path(a.list).read_text(encoding="utf-8").splitlines()
                 for w in line.split("#", 1)[0].split()]
    else:
        addrs = expand_addrs(a.addrs) if a.addrs else sorted(p.stem for p in paths.MATCHED.glob("*.c"))
    rows, t0 = [], time.monotonic()
    for addr in addrs:
        work = paths.MATCHED / f"{addr}.c"
        base = paths.BUILD / "parity" / addr
        row = {"addr": addr}
        try:
            raw = check.run_check(cfg, addr, work, build_dir=base / "raw")
            rd = readable_check.check_raw(cfg, addr, work, base / "rd")
            row |= {"raw": raw["verdict"], "readable": rd["verdict"],
                    "raw_failing": raw.get("failing"), "readable_failing": rd.get("failing"),
                    "total": raw.get("total"), "readable_total": rd.get("total")}
            if raw["verdict"] != rd["verdict"] or raw.get("failing") != rd.get("failing"):
                row["raw_detail"] = (raw.get("lint") or raw.get("build") or raw.get("first_diffs"))
                row["readable_detail"] = (rd.get("build") if rd.get("stage") == "build" else rd.get("first_diffs"))
                row["readable_error"] = rd.get("run_error")
        except Exception as e:  # noqa: BLE001  (one function's failure must not stop the run: reported)
            row |= {"raw": row.get("raw", "ERROR"), "readable": "ERROR", "error": f"{type(e).__name__}: {e}"}
        row["agree"] = ("error" not in row and row.get("raw") == row.get("readable")  # an error is no agreement
                        and row.get("raw_failing") == row.get("readable_failing"))
        rows.append(row)
        print(f"{addr}  raw {row.get('raw'):5}  readable {row.get('readable'):5}  "
              f"{'agree' if row['agree'] else 'DISAGREE'}", flush=True)
        if not a.keep:
            shutil.rmtree(base, ignore_errors=True)
    agree = sum(r["agree"] for r in rows)
    summary = {"project": str(paths.ROOT), "target": paths.PROJECT["target"], "functions": len(rows), "agree": agree,
               "raw_pass": sum(r.get("raw") == "PASS" for r in rows),
               "readable_pass": sum(r.get("readable") == "PASS" for r in rows),
               "seconds": round(time.monotonic() - t0, 1), "rows": rows}
    print(f"parity: {agree}/{len(rows)} agree (raw PASS {summary['raw_pass']}, readable PASS "
          f"{summary['readable_pass']}) in {summary['seconds']} s")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return 0 if agree == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
