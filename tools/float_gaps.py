"""List the matched and readable functions the two float gaps let through (docs/PROOF_GATES.md, float gaps).

usage: python tools/float_gaps.py --project DIR [--write] [--json FILE]

Static and host-only (packets, harness meta.json, matched/ and readable/src/ C, the binary); no Ghidra, no runner.
For every function with a matched C file:
- fr0_result: the original returns in fr0 (core/ret_reg.py) but the signature returns void, u32 or s32, and its
  callers agree (core/ret_use.fr0_confirmed; without that, fr0_unconfirmed in summary.json only). The judge now
  compares fr0 for these, and the signature needs a raw_ret f32 override (tools/sig_override.py --candidates lists
  them as fr0_exit).
- narrowed: a real reader uses a result register the signature does not name (core/ret_use.narrowed), on r0 or fr0,
  for any function with a listing. The judge compares it once listed, and the signature needs a raw_ret override.
- dropped: a readable caller calls a function as a bare statement where the original reads its result (PS2 8c0b2db4
  calls a void fn_8c0b1de8, then `chance = 0.0f;`). A listed callee's mock now draws floats (TcFret), so a reached call
  fails; unreached: no vector reaches the call, and only this row reports it.
- literal: an f32 constant the original reads from its pool (core/float_lits.py) that the C writes in no form;
  readable_lost when the matched C writes it and the readable C does not (TC 8c035a0c), matched when the matched C
  lacks it too (TC 8c023148: guessed 0.0f/1.0f). Suspects to review: a computed constant is a false one.
- f32_callee: the function reads fr0 after a call to a callee returning f32 (core/ret_use.reads_after). Its vectors
  change when it is re-prepared with TcFret (TC 8c035a0c, 8c07ea08, 8c0805a0); re-judge it after that re-prepare.
--write puts the lists in the workspace's state/float_gaps/: requeue.txt (fr0_result and literal: `addr reason`),
rejudge.txt (f32_callee) and summary.json, and the fr0_result and narrowed addresses in state/fr0_results.txt, the list
the judge reads (core/ret_reg.listed), and state/fret_callers.txt, the float mocks the oracle draws for the callers that
read those results (TcFret; those callers need a re-prepare, tools/selective_restamp.py --change fret). It requeues nothing: each game's session runs its own list.
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


def f32_callee_reads(rp, a, meta):
    """[callee] of the f32 callees whose result a reads (or may read) after the call."""
    from core import ret_use, sh4_asm
    f32s = {c["addr"] for c in meta.get("callees") or [] if (c.get("signature") or "").startswith("f32 ")}
    ins = rp.insns(a) if f32s else None
    if not ins:
        return []
    regs = {c["addr"]: set(c.get("regs") or ()) for c in meta.get("callees") or []}
    flow, own, out = sh4_asm.Flow(ins), ret_use.result_reg(meta), []
    for x in ins:
        t = x.call_target()
        if t in f32s and x.mn in sh4_asm.CALLS and t not in out:
            if ret_use.reads_after(ins, flow, x.idx, "fr0", lambda c: regs.get(c, set()), own) is not False:
                out.append(t)
    return out


DEFINITION = re.compile(r"^(?!static\b)(?!typedef\b)(?!extern\b)[A-Za-z_][\w \*]*?[ \*](\w+)\s*\([^;{)]*\)\s*\{", re.M)


def readable_name(text):
    """The public function a readable file defines (its last non-static definition), or None."""
    names = DEFINITION.findall(text)
    return names[-1] if names else None


def bare_calls(text, name):
    """How many statements of a C text call name and keep nothing of its result."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.S)
    return len(re.findall(r"(?:\A|[;{}])\s*(?:\(void\)\s*)?" + re.escape(name) + r"\s*\(", text))


def reaches(harness, callee):
    """Does a vector of this harness reach a call to callee? None without a harness."""
    p = harness / "expected.txt"
    if not p.exists():
        return None
    with p.open(encoding="utf-8", errors="replace") as f:
        return any(line.startswith("CALL " + callee) for line in f)


def dropped(rd, narrowed_and_read):
    """[{addr, callee, regs, bare, sites, unreached}]: readers whose readable C discards more calls of a callee than the
    original has calls that it does not read. narrowed_and_read: {callee: {caller: (reading sites, sites)}}."""
    def text(a):
        p = rd / "src" / f"{a}.c"
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    out = []
    for f, row in sorted(narrowed_and_read.items()):
        own = text(f)
        name = (readable_name(own) if own else None) or f"fn_{f}"
        for g, (reading, sites) in sorted(row["readers"].items()):
            caller = text(g)
            n = bare_calls(caller, name) if caller else 0
            if n > sites - reading:
                out.append({"addr": g, "callee": f, "name": name, "regs": row["regs"], "bare": n, "sites": sites,
                            "reading": reading, "unreached": reaches(rd.parent / "harness" / g, f) is False})
    return out


def scan(ws):
    from core import asm_pool, float_lits, paths, ret_reg, ret_use
    cfg = paths.config()
    image, base = cfg["binary_path"].read_bytes(), int(cfg["load_address"], 16)
    ranges = asm_pool.code_ranges(paths.CODE_RANGES)
    rp = ret_use.Project(ws)
    rd = ws / "readable"
    defs = float_lits.defines(p.read_text(encoding="utf-8", errors="replace")
                              for p in sorted(rd.glob("include/**/*.h")) + sorted(rd.glob("src/*.h")))
    narrowed = rp.narrowed()
    rows = {"fr0_result": [], "fr0_unconfirmed": [], "literal": [], "f32_callee": [],
            "dropped": dropped(rd, narrowed),
            "narrowed": [{"addr": a, "regs": n["regs"], "signature": rp.meta[a].get("signature"),
                          "evidence": n["evidence"], "readers": sorted(n["readers"]),
                          "readable": (rd / "src" / f"{a}.c").exists()} for a, n in sorted(narrowed.items())]}
    for a, meta in sorted(rp.meta.items()):
        matched = paths.MATCHED / f"{a}.c"
        if not matched.exists():
            continue
        readable = ws / "readable" / "src" / f"{a}.c"
        ins = rp.insns(a)
        if not ins:
            continue
        if meta.get("ret") in ("void", "u32", "s32") and not meta.get("ret_reg") and ret_reg.fr0_result(ins):
            ev = rp.evidence(a)
            rows["fr0_result" if ret_use.fr0_confirmed(ev) else "fr0_unconfirmed"].append(
                {"addr": a, "signature": meta.get("signature"), "evidence": ev, "readable": readable.exists()})
        fpu = set()
        consts = float_lits.pool_floats(ins, image, base, ranges, fpu)
        if consts:
            m_text = matched.read_text(encoding="utf-8", errors="replace")
            m_miss, m_lits = float_lits.missing(consts, m_text), float_lits.literals(m_text)
            r_miss = (float_lits.missing(consts, readable.read_text(encoding="utf-8", errors="replace"), defs)
                      if readable.exists() else {})
            lost = {k: v for k, v in r_miss.items()  # matched has it: by any form for an fmov.s word, else as a float
                    if k not in m_miss and (k in fpu or float_lits.as_float(v, m_lits))}
            if m_miss or lost:
                rows["literal"].append({"addr": a, "case": "readable_lost" if lost else "matched",
                                        "missing": {f"{k:08x}": float_lits.describe(v)
                                                    for k, v in sorted((lost or m_miss).items())},
                                        "readable": readable.exists()})
        callees = f32_callee_reads(rp, a, meta)
        if callees:
            rows["f32_callee"].append({"addr": a, "callees": callees, "readable": readable.exists()})
    return rows


def ret_reg_list():
    from core import ret_reg
    return ret_reg.LIST


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    ws = use_project(a.project)
    rows = scan(ws)
    req = {}
    for r in rows["fr0_result"]:
        req.setdefault(r["addr"], []).append("fr0_result: returns in fr0, signature " + (r["signature"] or "?"))
    for r in rows["narrowed"]:
        req.setdefault(r["addr"], []).append(f"narrowed: callers read {'+'.join(r['regs'])}, signature "
                                             + (r["signature"] or "?"))
    for r in rows["dropped"]:
        req.setdefault(r["addr"], []).append(f"dropped: discards {r['name']}'s {'+'.join(r['regs'])}"
                                             + (", and no vector reaches the call" if r["unreached"] else ""))
    for r in rows["literal"]:
        req.setdefault(r["addr"], []).append(f"literal ({r['case']}): " + ", ".join(
            f"{k} = {v}" for k, v in r["missing"].items()))
    summary = {"project": str(ws), "requeue": len(req), "rejudge": len(rows["f32_callee"]),
               **{k: len(v) for k, v in rows.items()},
               "literal_readable_lost": sum(r["case"] == "readable_lost" for r in rows["literal"])}
    print(json.dumps(summary))
    for addr, why in sorted(req.items()):
        print(f"  {addr}  " + "; ".join(why))
    if a.write:
        out = ws / "state" / "float_gaps"
        out.mkdir(parents=True, exist_ok=True)
        (out / "requeue.txt").write_text("".join(f"{addr} {'; '.join(why)}\n" for addr, why in sorted(req.items())),
                                         encoding="utf-8")
        regs = {r["addr"]: {"fr0"} for r in rows["fr0_result"]}
        for r in rows["narrowed"]:
            regs.setdefault(r["addr"], set()).update(r["regs"])
        (ws / "state" / ret_reg_list()).write_text(
            "# result registers beyond the signature: the judge compares them (tools/float_gaps.py); no register: fr0\n"
            + "".join(f"{a} {' '.join(sorted(rs))}\n" for a, rs in sorted(regs.items())), encoding="utf-8")
        from core import ret_reg, ret_use
        (ws / "state" / ret_reg.FRET).write_text(
            "".join(f"{g} {' '.join(cs)}\n" for g, cs in sorted(ret_use.Project(ws).fret_callers(ws / "state").items())),
            encoding="utf-8")
        (out / "rejudge.txt").write_text("".join(f"{r['addr']} f32 callee {', '.join(r['callees'])}\n"
                                                 for r in rows["f32_callee"]), encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
        print(f"wrote {out}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
