"""Proof gates over matched functions (docs/PROOF_GATES.md): the coverage gate and the mutation check's record.

A PASS proves the C equals the original on the vectors the oracle ran. Two things can leave that proof weak:
- the vectors never run parts of the original (blocks no valid vector reaches): the C there is unjudged;
- the harness is too blunt to tell wrong C from right (core/mutation.py: small deliberate changes still PASS).

Coverage gate (config "coverage_gate", overridable per project in project.json, key by key):
  min_block_pct           a matched function passes when the valid vectors (dev + hidden) reach at least this share
                          of the original's basic blocks (oracle_summary.json blocks_hit_all / blocks_total) ...
  allow_uncovered_blocks  ... or leave at most this many blocks unreached (small functions, where one block is more
                          than 100 - min_block_pct percent) ...
  min_blocks              ... the allowance applies only from this many blocks up (below it, every block is needed)
  enforce                 true: the final audit's "proof" check FAILs while a function is below the gate; false:
                          report only (the check is INFO). Either way a function below it is matched_low_coverage,
                          not verified, and in the backlog (tools/proof_backlog.py). Default true (a new game
                          enforces from the start); an existing game sets false in its project.json until its
                          backlog is clear (proof_backlog.py status, then enforce)
  waive                   {addr: reason}: blocks no input can reach, judged by hand (e.g. a fault path); the
                          function counts as verified with its reason shown
A matched function below the gate is in the state "matched_low_coverage": its C is not a FAIL (it matches every
vector), it is not skipped, and it is not counted as verified. It is work for the reach pass, the watcher's hints or
more vectors (tools/coverage_gate.py --list FILE).

Mutation check (config "mutation_check"): results live in state/mutation.json (tools/mutation_check.py, or the
worker after a PASS with on_pass). mode "off" | "report" (survivors only flag the harness "weak_harness") |
"enforce" (a function whose score is below min_score is not verified either).

The ledger keeps status "matched" for all of these (the matched C exists and passes; the readable stage, the
doctor and retire rely on that); the proof state is derived from the harness summary, the gate settings and
state/mutation.json, and the worker records it in the row's "proof" at a match.
"""
import collections
import json
import math
import pathlib

from . import filelock, toolcfg

TOOL = pathlib.Path(__file__).resolve().parents[1]

COVERAGE_DEFAULTS = {"min_block_pct": 90, "allow_uncovered_blocks": 1, "min_blocks": 3, "enforce": True,
                     "waive": {}}
MUTATION_DEFAULTS = {"mode": "report", "min_score": 80, "max_mutants": 16, "time_cap_s": 240,
                     "mutant_timeout_s": 10, "seed": 1, "on_pass": False}

VERIFIED = "verified"
LOW = "matched_low_coverage"
WEAK = "weak_harness"  # a flag in report mode; a state (not verified) in enforce mode
STATES = (VERIFIED, LOW, WEAK)
MUTATION_FILE = "mutation.json"  # in the workspace's state/


def _settings(key, defaults, cfg=None):
    """defaults <- the tool's config.json (+ config.local.json) <- cfg[key] (paths.config(): project.json over the
    tool's config, so a project's partial dict keeps the other keys from config.json)."""
    out = dict(defaults)
    out.update(toolcfg.load(TOOL).get(key) or {})
    out.update((cfg or {}).get(key) or {})
    return out


def coverage_settings(cfg=None):
    s = _settings("coverage_gate", COVERAGE_DEFAULTS, cfg)
    s["waive"] = dict(s.get("waive") or {})
    return s


def mutation_settings(cfg=None):
    s = _settings("mutation_check", MUTATION_DEFAULTS, cfg)
    if s["mode"] not in ("off", "report", "enforce"):
        raise ValueError(f"mutation_check.mode must be off, report or enforce, not {s['mode']!r}")
    return s


def allowed_uncovered(total, gate):
    """How many of `total` blocks may stay unreached under the gate."""
    by_pct = math.floor(total * (100 - gate["min_block_pct"]) / 100 + 1e-9)
    small = gate["allow_uncovered_blocks"] if total >= gate["min_blocks"] else 0
    return max(by_pct, small, 0)


def unhit_blocks(summary):
    """The start addresses of the blocks no valid vector reached (oracle_summary.json "blocks_unhit", TcPrepare;
    omitted when every block is reached), or None when unknown: a summary from before the field, or a refused
    harness."""
    u = (summary or {}).get("blocks_unhit")
    if isinstance(u, list):
        return [str(x) for x in u]
    if summary and summary.get("blocks_total") is not None and summary.get("blocks_hit_all") == summary["blocks_total"]:
        return []
    return None


def unhit_text(blocks, n=6):
    """'8c010020, 8c010044 and 3 more' (the first n)."""
    more = len(blocks) - n
    return ", ".join(blocks[:n]) + (f" and {more} more" if more > 0 else "")


def coverage_verdict(summary, gate, addr=None):
    """The gate on one harness summary: {"ok": True/False/None, "blocks_total", "blocks_hit", "uncovered", "pct",
    "allowed", "unhit" (when known), "waived"}. ok None: no summary or no block data (nothing to judge)."""
    if not summary or summary.get("blocks_total") is None or summary.get("blocks_hit_all") is None:
        return {"ok": None, "why": "no oracle summary with block counts"}
    total, hit = int(summary["blocks_total"]), int(summary["blocks_hit_all"])
    unc = max(total - hit, 0)
    allowed = allowed_uncovered(total, gate)
    v = {"ok": unc <= allowed, "blocks_total": total, "blocks_hit": hit, "uncovered": unc,
         "pct": round(100.0 * hit / total, 1) if total else 100.0, "allowed": allowed}
    if unc and unhit_blocks(summary):
        v["unhit"] = unhit_blocks(summary)
    g = grade(summary)
    if g:  # report only (TcDirs): the grade and the branch directions taken
        v.update(grade=g["grade"], branch_dirs=g["branch_dirs"], branch_pct=g["branch_pct"])
        if summary.get("branch_dirs_unhit"):
            v["dirs_unhit"] = [str(x) for x in summary["branch_dirs_unhit"][:20]]
    why = gate["waive"].get(addr) if addr else None
    if not v["ok"] and why:
        v.update(ok=True, waived=why)
    return v


FULL, TESTED = "fully proven", "proven where tested"


def grade(summary):
    """The coverage grade of a harness summary with branch directions (TcDirs, target "branch_coverage"): "fully
    proven" when the valid vectors reach every block and take every branch direction, else "proven where tested";
    {"grade", "blocks": [hit, total], "branch_dirs": [hit, total], "branch_pct"}. None without direction data (the
    flag off, a harness from before it, or a refused one). Report only: no gate reads it."""
    s = summary or {}
    if s.get("branch_dirs_total") is None or s.get("blocks_total") is None or s.get("blocks_hit_all") is None:
        return None
    bt, bh = int(s["blocks_total"]), int(s["blocks_hit_all"])
    dt, dh = int(s["branch_dirs_total"]), int(s.get("branch_dirs_hit_all") or 0)
    return {"grade": FULL if bh >= bt and dh >= dt else TESTED, "blocks": [bh, bt], "branch_dirs": [dh, dt],
            "branch_pct": round(100.0 * dh / dt, 1) if dt else 100.0}


def mutation_flag(row, mset):
    """'weak' when the mutation record has logic survivors (the harness is flagged weak_harness; arg_slot and stack
    survivors are equivalent by the judge's design), 'ok', or None (no record, the mode is off, or nothing could be
    judged)."""
    if not row or mset["mode"] == "off" or row.get("tried", 0) == 0 or row.get("error"):
        return None
    logic = (row.get("survived_by_class") or {}).get("logic", row.get("survived", 0))  # classes: core/mutation
    return "weak" if logic else "ok"


def state(cov, mut_row, mset, gate=None):
    """The proof state of a matched function: matched_low_coverage, weak_harness or verified. Below the coverage gate
    is matched_low_coverage whether or not the gate enforces (enforce only decides the final audit's FAIL); a weak
    harness counts against verified only in mutation mode enforce with a score below min_score."""
    if cov.get("ok") is False:
        return LOW
    if mset["mode"] == "enforce" and mut_row and mut_row.get("tried") and mut_row.get("score") is not None \
            and mut_row["score"] < mset["min_score"]:
        return WEAK
    return VERIFIED


def read_summary(harness_dir, addr):
    p = pathlib.Path(harness_dir) / addr / "oracle_summary.json"
    try:
        return json.loads(filelock.read_text(p)) if p.exists() else None
    except ValueError:
        return None


def read_mutation(ws):
    p = pathlib.Path(ws) / "state" / MUTATION_FILE
    try:
        return (json.loads(filelock.read_text(p)) if p.exists() else {}).get("functions") or {}
    except ValueError:
        return {}


def project_cfg(ws):
    """The settings a workspace sees without core.paths: the tool's config with its project.json over it."""
    pj = json.loads((pathlib.Path(ws) / "project.json").read_text(encoding="utf-8"))
    return {"coverage_gate": pj.get("coverage_gate"), "mutation_check": pj.get("mutation_check")}


def matched(ws):
    led = json.loads(filelock.read_text(pathlib.Path(ws) / "state" / "ledger.json")) if (
        pathlib.Path(ws) / "state" / "ledger.json").exists() else {}
    return sorted(a for a, r in (led.get("functions") or {}).items() if r.get("status") == "matched")


def scan(ws, addrs=None, cfg=None):
    """Every matched function's proof (or addrs'): {addr: {"state", "coverage", "mutation"}}. Read-only, any
    workspace (no core.paths)."""
    ws = pathlib.Path(ws)
    cfg = cfg if cfg is not None else project_cfg(ws)
    gate, mset = coverage_settings(cfg), mutation_settings(cfg)
    muts = read_mutation(ws)
    out = {}
    for a in (addrs if addrs is not None else matched(ws)):
        cov = coverage_verdict(read_summary(ws / "harness", a), gate, a)
        m = muts.get(a)
        out[a] = {"state": state(cov, m, mset, gate), "coverage": cov, "mutation_flag": mutation_flag(m, mset),
                  "mutation": m}
    return out


def figure(rows):
    """Counts over scan(): states, no coverage data, waived, weak harnesses (flag), mutation records."""
    st = collections.Counter(r["state"] for r in rows.values())
    return {"matched": len(rows), VERIFIED: st[VERIFIED], LOW: st[LOW], WEAK: st[WEAK],
            "no_coverage_data": sum(1 for r in rows.values() if r["coverage"].get("ok") is None),
            "waived": sum(1 for r in rows.values() if r["coverage"].get("waived")),
            "weak_flag": sum(1 for r in rows.values() if r["mutation_flag"] == "weak"),
            "mutation_records": sum(1 for r in rows.values() if r["mutation"]),
            "graded": sum(1 for r in rows.values() if r["coverage"].get("grade")),
            "fully_proven": sum(1 for r in rows.values() if r["coverage"].get("grade") == FULL)}


def summary_line(f, gate=None):
    gate = gate or COVERAGE_DEFAULTS
    rule = (f"gate: >= {gate['min_block_pct']}% of blocks, or at most {gate['allow_uncovered_blocks']} unreached "
            f"from {gate['min_blocks']} blocks up" + ("" if gate.get("enforce", True) else "; report only"))
    return (f"proof: {f[VERIFIED]}/{f['matched']} matched functions verified; {f[LOW]} matched_low_coverage ({rule})"
            + (f"; {f['waived']} waived" if f["waived"] else "")
            + (f"; {f['no_coverage_data']} without block data" if f["no_coverage_data"] else "")
            + (f"; fully proven {f['fully_proven']} of {f['graded']} graded (every branch direction taken)"
               if f.get("graded") else "")
            + f"; mutation records {f['mutation_records']}, weak harnesses {f['weak_flag']}"
            + (f", {f[WEAK]} not verified (enforce)" if f[WEAK] else ""))


def record(cfg, addr, summary, mut_row=None):
    """The ledger row's "proof" at a match (job.finish)."""
    gate, mset = coverage_settings(cfg), mutation_settings(cfg)
    cov = coverage_verdict(summary, gate, addr)
    out = {"state": state(cov, mut_row, mset, gate), "coverage": cov}
    if mut_row:
        out["mutation"] = {k: mut_row.get(k) for k in ("tried", "killed", "survived", "survived_by_class", "dropped",
                                                        "score", "score_raw")}
        out["weak_harness"] = mutation_flag(mut_row, mset) == "weak"
    return out


def distribution(rows):
    """Coverage distribution of scan() rows: percent buckets and the unreached-block histogram."""
    pct = collections.Counter()
    unc = collections.Counter()
    for r in rows.values():
        c = r["coverage"]
        if c.get("ok") is None:
            continue
        p = c["pct"]
        pct["100" if p >= 100 else "90-99" if p >= 90 else "80-89" if p >= 80 else "70-79" if p >= 70
            else "50-69" if p >= 50 else "<50"] += 1
        unc[min(c["uncovered"], 10)] += 1
    order = ["100", "90-99", "80-89", "70-79", "50-69", "<50"]
    return {"pct": {k: pct[k] for k in order}, "uncovered": {("10+" if k == 10 else str(k)): unc[k]
                                                              for k in sorted(unc)}}


def describe(rec):
    """One line for a ledger row's "proof" (job.finish appends it to verdict_detail when not verified)."""
    c = rec.get("coverage") or {}
    parts = []
    if c.get("ok") is False:
        parts.append(f"{c['blocks_hit']}/{c['blocks_total']} blocks reached ({c['pct']}%), {c['uncovered']} unreached, "
                     f"{c['allowed']} allowed" + (f" (unreached blocks at {unhit_text(c['unhit'])})"
                                                  if c.get("unhit") else ""))
    m = rec.get("mutation")
    if m and m.get("score") is not None:
        parts.append(f"mutation score {m['score']} ({m['survived']} of {m['tried']} mutants survived)")
    return "; ".join(parts) or rec.get("state", "")
