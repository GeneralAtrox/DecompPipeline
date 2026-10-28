"""Per-function mutation check (docs/PROOF_GATES.md): does the harness tell wrong C from the matched C?

After a PASS, small deliberate changes of the matched C (mutants) are judged on the same harness with the same judge
(core/check.run_check: lint, the /W4 /WX build, dev + hidden vectors, byte-exact compare). A mutant that still
PASSes is a survivor: the harness cannot see that change, so it would also pass C that is wrong there. Host only: no
agent, no Ghidra; the harness files are only read.

Mutants are deterministic: every candidate edit of the source is listed (a small C tokenizer, no parser), then a
seeded draw (seed, address, source hash) takes up to max_mutants, round-robin over the operators so each gets its
share:
  relop      < <=, > >=, == != (each way)
  arith      binary + -, += -=
  bitwise    binary & |, &= |=
  shift      << >>, <<= >>=
  const      an integer literal +1 or -1 (0 becomes 1); not in initializer braces or preprocessor lines
  negate     if (c) / while (c) -> (!(c))
  delete     one store or assignment statement (x = ...; *p = ...; a.r[0] = ...; wr32(...);) becomes { }
  swap_args  two adjacent, different arguments of a call; or the values of two adjacent TcArgs register
             assignments (a.r[0] = x; a.r[1] = y;), the raw C's way of passing call arguments
  return0    return e; -> return 0;
A mutant the judge refuses before running (lint or build: e.g. /WX on a variable left unused) is dropped and
counted; one whose run times out or fails a vector is killed; one that PASSes survived. "outcomes" keeps each
generated mutant's, in id order: K killed, S survived, L / B dropped by lint / build, - not tried (time cap); the
mutation audit (tools/mutation_audit.py) compares them mutant by mutant.
Each survivor gets a class (core/mutation_ops.class_of): arg_slot (a TcArgs slot no callee's signature reads: the
judge compares declared arguments only), stack (a store to the guest stack, which the judge never compares) or logic.
The first two are equivalent by the judge's design; only logic survivors flag the harness weak_harness.
score = killed / (killed + logic survivors) * 100; score_raw counts every survivor. Logic survivors can still be
equivalent mutants (<= where the equal case cannot occur, a store the function overwrites later): they are listed
with their diff for a reader to judge.
"""
import difflib
import hashlib
import random
import time

from .mutation_ops import OPERATORS, apply, candidates, tokens  # noqa: F401  (tokens, apply: re-exported)

CLASSES = ("logic", "arg_slot", "stack")  # survivor classes (core/mutation_ops.class_of)


def generate(src, addr, cap=16, seed=1):
    """Up to cap distinct mutants of src: [{"id", "op", "line", "what", "cls", "src"}], deterministic for (src, addr,
    seed). Round-robin over OPERATORS from per-operator seeded shuffles."""
    h = hashlib.sha256(f"{seed}:{addr}:".encode() + src.encode()).hexdigest()
    rng = random.Random(int(h[:16], 16))
    by = {op: [] for op in OPERATORS}
    for c in candidates(src):
        by[c["op"]].append(c)
    for op in OPERATORS:
        rng.shuffle(by[op])
    out, seen = [], {src}
    while len(out) < cap and any(by.values()):
        for op in OPERATORS:
            if len(out) >= cap:
                break
            while by[op]:
                c = by[op].pop()
                m = apply(src, c["edits"])
                if m in seen:
                    continue
                seen.add(m)
                out.append({"id": len(out) + 1, "op": op, "line": c["line"], "what": c["what"], "cls": c["cls"],
                            "src": m})
                break
    return out


def diff(src, mutant, name="matched.c"):
    return "".join(difflib.unified_diff(src.splitlines(True), mutant.splitlines(True), f"a/{name}", f"b/{name}",
                                        n=0)).strip()


def outcome(res):
    """A judge result -> killed / survived / dropped_lint / dropped_build."""
    if res["stage"] == "lint":
        return "dropped_lint"
    if res["stage"] == "build":
        return "dropped_build"
    return "survived" if res["verdict"] == "PASS" else "killed"


def score(killed, survived):
    return round(100.0 * killed / (killed + survived), 1) if killed + survived else None


def check_function(addr, src, judge, mset, log=None, clock=time.monotonic):
    """The mutation check of one function. judge(src_text, tag) -> a core/check result dict for that C on the
    function's harness (dev + hidden). The original goes first: a baseline that does not PASS stops the check
    (error). Returns the record for state/mutation.json."""
    t0 = clock()
    rec = {"addr": addr, "src_sha256": hashlib.sha256(src.encode()).hexdigest(), "seed": mset["seed"],
           "tried": 0, "killed": 0, "survived": 0, "dropped": 0, "dropped_lint": 0, "dropped_build": 0,
           "by_op": {}, "survivors": [], "survived_by_class": {c: 0 for c in CLASSES}, "time_capped": False,
           "candidates": 0, "outcomes": ""}
    base = judge(src, "base")
    if base["verdict"] != "PASS":
        rec["error"] = (f"baseline {base['verdict']} at {base['stage']}: the matched C does not pass its harness "
                        f"here ({base.get('failing')}/{base.get('total')} failing)")
        rec["score"] = None
        rec["seconds"] = round(clock() - t0, 1)
        return rec
    muts = generate(src, addr, mset["max_mutants"], mset["seed"])
    rec["candidates"] = len(candidates(src))
    for m in muts:
        if clock() - t0 > mset["time_cap_s"]:
            rec["time_capped"] = True
            rec["outcomes"] += "-" * (len(muts) - len(rec["outcomes"]))
            break
        res = judge(m["src"], f"m{m['id']:02d}")
        o = outcome(res)
        rec["outcomes"] += {"killed": "K", "survived": "S", "dropped_lint": "L", "dropped_build": "B"}[o]
        per = rec["by_op"].setdefault(m["op"], {"killed": 0, "survived": 0, "dropped": 0})
        if o.startswith("dropped"):
            rec["dropped"] += 1
            rec[o] += 1
            per["dropped"] += 1
        else:
            rec["tried"] += 1
            rec[o] += 1
            per[o] += 1
            if o == "survived":
                rec["survived_by_class"][m["cls"]] += 1
                rec["survivors"].append({"id": m["id"], "op": m["op"], "cls": m["cls"], "line": m["line"],
                                         "what": m["what"], "diff": diff(src, m["src"], f"{addr}.c")})
        if log:
            log(f"  {addr} m{m['id']:02d} {m['op']:9s} {o:13s} line {m['line']}: {m['what']}")
    rec["generated"] = len(muts)
    rec["score_raw"] = score(rec["killed"], rec["survived"])
    rec["score"] = score(rec["killed"], rec["survived_by_class"]["logic"])  # equivalent-by-design ones left out
    rec["seconds"] = round(clock() - t0, 1)
    return rec


def judge_with(cfg, addr, build_root, timeout_s, harness_dir=None):
    """judge(src_text, tag) for check_function: the official check (core/check.run_check, dev + hidden) of the text
    on the function's harness, built under build_root/tag (removed afterwards). The runner gets timeout_s (a mutant
    that loops is killed by its timeout, as the judge would kill it); run_check caps its output (a mutant writing
    across the guest RAM fails there: check.run_runner)."""
    import pathlib
    import shutil
    from . import check
    cfg_m = dict(cfg, check_timeout_s=timeout_s)
    root = pathlib.Path(build_root)

    def judge(text, tag):
        d = root / tag
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        (d / "work.c").write_text(text, encoding="utf-8")
        try:
            return check.run_check(cfg_m, addr, str(d / "work.c"), sets=("dev", "hidden"), build_dir=d / "b",
                                   harness_dir=harness_dir, fused=False)  # kills by the comparison alone
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return judge


def save(path, recs, mset):
    """Merge records into state/mutation.json ({"settings", "functions": {addr: rec}}) under its file lock."""
    from . import filelock

    def upd(data):
        data["settings"] = {k: mset[k] for k in ("mode", "min_score", "max_mutants", "time_cap_s", "seed")}
        data.setdefault("functions", {}).update(recs)
    filelock.update_json(path, upd, default={})
