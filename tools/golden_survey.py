"""Every golden and expected file, and where it came from (AGENTS.md "Test rules"): the files of DP, of port folders and of
decomp workspaces, each against its provenance record.

Nearest existing: tools/dashboard.py - it counts a workspace's verdicts; this is about what those checks compare with.

    py -3.12 tools/golden_survey.py [--port DIR].. [--ws DIR].. [--json FILE]
    py -3.12 tools/golden_survey.py --label-native --root DIR [--ledger FILE] --why TEXT KEY..
    py -3.12 tools/golden_survey.py --label SOURCE --evidence FILE --root DIR [--ledger FILE] [--why TEXT] KEY

DP's own records are in tests/goldens_provenance.json, read by tests/test_test_rules.py: a new golden there fails until labelled.
A workspace's harness/*/expected.txt comes from the oracle, which runs the original machine code; its record is the meta's
oracle_stamp, only counted here. A port folder's reference recordings (build/reference/*/manifest.json) are emulator captures.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import provenance  # noqa: E402

DP_LEDGER = ROOT / "tests" / "goldens_provenance.json"
# tracked files that hold what a check expects
GOLDEN = re.compile(r"golden|baseline|(^|/)regress/[^/]*\.json$|translator/results/[^/]*\.json$", re.I)
NOT_GOLDEN = re.compile(r"^docs/|\.(py|md|c|h|cpp|java)$")
INLINE = re.compile(r"^([A-Z][A-Z0-9_]*GOLDEN[A-Z0-9_]*) = ", re.M)  # a golden written in a test


def dp_files(root=ROOT):
    names = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, timeout=60).stdout.splitlines()
    out = [n for n in names if GOLDEN.search(n) and not NOT_GOLDEN.search(n) and pathlib.PurePath(n).name != provenance.FILE
           and n != "tests/goldens_provenance.json"]
    for n in names:
        if re.match(r"tests/.*\.py$", n) and (root / n).is_file():
            out += [f"{n}#{m}" for m in INLINE.findall((root / n).read_text(encoding="utf-8", errors="replace"))]
    return sorted(out)


def survey_ledger(ledger, keys):
    rows = []
    for k in keys:
        r = ledger.rows.get(k) or {}
        rows.append({"file": k, "source": r.get("source"), "problem": ledger.problem(k), "evidence": r.get("evidence")})
    return rows


def port_rows(port):
    port = pathlib.Path(port)
    rows = []
    for sub, pat in (("goldens", "*/*.png"), ("baseline", "*.txt")):
        d = port / "tests" / sub
        rows += [dict(r, folder=str(d)) for r in survey_ledger(provenance.Ledger(d), sorted(
            p.relative_to(d).as_posix() for p in d.glob(pat)))]
    return rows


def ws_counts(ws):
    n = stamped = 0
    for h in (pathlib.Path(ws) / "harness").iterdir():
        if (h / "expected.txt").is_file():
            n += 1
            m = h / "meta.json"
            stamped += m.is_file() and "oracle_stamp" in json.loads(m.read_text(encoding="utf-8"))
    return {"expected": n, "oracle_stamped": stamped}


def tally(rows):
    out = {"files": len(rows), "original": 0, "native": 0, "no_record": 0, "bad": 0}
    for r in rows:
        k = ("no_record" if r["source"] is None else "bad") if r["problem"] else (
            "native" if r["source"] == provenance.NATIVE else "original")
        out[k] += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", action="append", default=[])
    ap.add_argument("--ws", action="append", default=[])
    ap.add_argument("--json")
    ap.add_argument("--label-native", action="store_true")
    ap.add_argument("--label", choices=provenance.ORIGINAL)
    ap.add_argument("--evidence")
    ap.add_argument("--ledger")
    ap.add_argument("--root")
    ap.add_argument("--why", default="")
    ap.add_argument("keys", nargs="*")
    a = ap.parse_args(argv)
    if a.label_native or a.label:
        led = provenance.Ledger(a.root, a.ledger)
        try:
            for k in a.keys:
                if a.label_native:
                    led.native(k, a.why)
                else:
                    led.original(k, a.label, a.evidence, a.why)
        except provenance.Refused as e:
            print(f"refused: {e}")
            return 1
        led.save()
        print(f"labelled {len(a.keys)} in {led.file}")
        return 0
    rep = {"dp": survey_ledger(provenance.Ledger(ROOT, DP_LEDGER), dp_files()), "ports": {}, "workspaces": {}}
    print("DP", tally(rep["dp"]))
    for p in a.port:
        rep["ports"][p] = port_rows(p)
        refs = len(list(pathlib.Path(p).glob("build/reference/*/manifest.json")))
        print(p, tally(rep["ports"][p]), f"emulator reference recordings {refs}")
    for w in a.ws:
        rep["workspaces"][w] = ws_counts(w)
        print(w, rep["workspaces"][w])
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
