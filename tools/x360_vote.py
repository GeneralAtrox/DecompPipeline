"""The Xbox 360 instruction vote: a second, independent check of the PowerPC translator. Per instruction test case of
a corpus in the Xenia PPC testing format (default C:\\Projects\\XeniaRedux\\src\\xenia\\cpu\\ppc\\testing, BSD; read
from that path, never copied here), three answers:
  A  our translator's C (targets/xbox360_ppc/tools/vote_leg_a.py; runs on any machine with cl),
  B  Ghidra's p-code emulation of the same words (vote_leg_b.py; build box A: needs ghidra_home and a JDK),
  C  the corpus's expected values (their origin is not proven hardware truth: a disagreement is a lead, not a verdict).

usage: py -3.12 tools/x360_vote.py encode-check [--corpus DIR]
       py -3.12 tools/x360_vote.py leg-a --work DIR [--corpus DIR] [--out A.json] [--summary A.txt] [--files GLOB]
       py -3.12 tools/x360_vote.py leg-b {prepare,run,collect,all} --work DIR [--corpus DIR] [--files GLOB] [--out B.json]
       py -3.12 tools/x360_vote.py settle A.json [--out SETTLE.json]
       py -3.12 tools/x360_vote.py vote A.json B.json [--out VOTE.json]

Every case is accounted for: count in = agree + differ + nan_payload + crash + unsupported (with its reason).
Vote classes per case (vote()): all_agree; A_suspect = A != (B = C) (our translator suspect); shared_or_corpus =
(A = B) != C (the corpus is wrong, or A and B share a bug); B_suspect = B != (A = C) (Ghidra suspect); three_way;
incomplete (a leg has no answer).

Nearest existing: targets/xbox360_ppc/x360_check.py (judges a function on Ghidra's vectors; no outside expected values)
and tools/decode_check.py (decoder against a disassembler, no execution); neither runs the corpus.
"""
import argparse
import collections
import fnmatch
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
VT = ROOT / "targets" / "xbox360_ppc" / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VT))

import vote_corpus as VC  # noqa: E402
import vote_encode as E  # noqa: E402
import vote_encode_check as K  # noqa: E402
import vote_leg_a as A  # noqa: E402
import vote_settle as S  # noqa: E402

CLASSES = ("all_agree", "A_suspect", "shared_or_corpus", "B_suspect", "three_way", "incomplete")


def case_id(c):
    return f"{c['file']}:{c['label']}"


def kind(words):
    """The instruction kind of a case: the mnemonics other than blr/li/lis/ori/sldi set-up, in order."""
    ops = []
    for w in words or []:
        o = K.D.decode(w).op
        if o not in ("bclr",) and o not in ops:
            ops.append(o)
    return " ".join(ops) or "?"


def run_leg_a(corpus, work, cfg, files=None):
    """(result dict, summary lines). The result: {"leg": "A", "corpus", "cases": {id: {...}}, "per_file": {...}}."""
    cases = VC.read_corpus(corpus)
    if files:
        cases = [c for c in cases if fnmatch.fnmatch(c["file"], files)]
    plan, groups = A.prepare(cases)
    srcs, fn_of, refused = [], {}, {}
    for g, words in enumerate(groups):
        src, why = A.translate_group(len(srcs), list(words))
        if src is None:
            refused[g] = why
        else:
            fn_of[g] = len(srcs)
            srcs.append(src)
    plan = [(fn_of.get(fn), w, why or (refused.get(fn) if fn is not None else None)) for fn, w, why in plan]
    work.mkdir(parents=True, exist_ok=True)
    ok, msg = A.build(work, srcs, cfg)
    if not ok:
        raise RuntimeError(msg)
    got = A.run(work, cases, plan)
    names = [A.named(s) for s in srcs]
    out = make_result("A", corpus, cases, plan, got, lambda fn: names[fn])
    out.update(build=msg, functions=len(srcs))
    return out, summary(out)


def make_result(leg, corpus, cases, plan, got, names):
    """A leg's result: {"leg", "corpus", "cases": {id: record}, "per_file"}. got: {case index: parsed out.txt (a "U"
    reason makes the case unsupported)}; names(fn): the registers that function's code names."""
    res, per_file = {}, collections.defaultdict(collections.Counter)
    for i, (c, (fn, words, why)) in enumerate(zip(cases, plan)):
        r = {"words": [f"{w:08x}" for w in words or []], "skip": c["skip"]}
        for flag in ("bracket_float", "bracket_mem"):
            if c.get(flag):
                r[flag] = True
        g = got.get(i)
        if why is None and g is not None and g.get("unsupported"):
            why = g["unsupported"]
        if why is not None:
            r.update(status="unsupported", reason=why)
        elif g is None or g.get("crash"):
            r.update(status="crash", reason=f"exception {g['crash']}" if g else "no output for the case")
        else:
            mine = A.ours(c, g, names(fn))
            st, diffs = A.compare(c, mine, [K.D.decode(w).op for w in words], g["q"])
            r["status"] = st
            if st != "agree":
                r["outs"] = {k: A.fmt(v) for k, v in mine.items()}
                r["diffs"] = [[k, A.fmt(e), A.fmt(a)] for k, e, a in diffs]
                r["inputs"] = {k: A.fmt(v) for k, v in c["reg_in"].items()}
            if A.mem_case(c):
                r["rebased"] = True
        res[case_id(c)] = r
        per_file[c["file"]][r["status"]] += 1
    return {"leg": leg, "corpus": str(corpus), "cases": res, "per_file": {f: dict(n) for f, n in sorted(per_file.items())}}


def summary(out, limit=40):
    cases = out["cases"]
    tot = collections.Counter(r["status"] for r in cases.values())
    reasons = collections.Counter(reason_key(r["reason"]) for r in cases.values() if r["status"] == "unsupported")
    lines = [f"leg {out['leg']}: {len(cases)} cases in {len(out['per_file'])} files ({out.get('build', '')}): "
             + ", ".join(f"{k} {tot[k]}" for k in ("agree", "differ", "nan_payload", "crash", "unsupported"))]
    assert sum(tot.values()) == len(cases)
    lines.append("unsupported by reason: " + "; ".join(f"{n} {k}" for k, n in reasons.most_common(8)))
    bad = {f: n for f, n in out["per_file"].items() if set(n) - {"agree"}}
    lines.append(f"files all agree: {len(out['per_file']) - len(bad)}; others (file: counts):")
    row = ""
    for f, n in bad.items():
        item = f"{f.replace('instr_', '').replace('.s', '')} " + "/".join(f"{k[:2]}{v}" for k, v in sorted(n.items()))
        if len(row) + len(item) > 116:
            lines.append("  " + row)
            row = ""
        row += item + "; "
    if row:
        lines.append("  " + row)
    groups = collections.defaultdict(list)
    for cid, r in cases.items():
        if r["status"] in ("differ", "crash", "nan_payload"):
            groups[(r["status"], kind([int(w, 16) for w in r["words"]]))].append((cid, r))
    lines.append(f"DIFFER/CRASH/NAN by instruction kind ({len(groups)} kinds):")
    for (st, k), rs in sorted(groups.items(), key=lambda x: -len(x[1])):
        cid, r = rs[0]
        d = r.get("diffs", [["", "", r.get("reason")]])[0]
        lines.append(f"  {st} {k} x{len(rs)}: e.g. {cid} {' '.join(r['words'])} in {short(r.get('inputs'))} "
                     f"{d[0]} corpus {short(d[1])} ours {short(d[2])}")
    if len(lines) > limit:
        lines = lines[:limit - 1] + [f"  ... {len(lines) - limit + 1} more lines: see the JSON"]
    return lines


def short(v):
    s = json.dumps(v, separators=(",", ":")) if not isinstance(v, str) else v
    return s if len(s) <= 90 else s[:87] + "..."


def reason_key(r):
    r = r or ""
    for k in ("translator: unsupported instruction", "translator:", "encoder:", "round trip:", "trap:",
              "corpus value", "no blr"):
        if r.startswith(k):
            return r[:60] if k == "translator:" else k.rstrip(":")
    return r[:60]


def same(a, b):
    """Two legs' answers for one case are the same: both agree with C, or their outs are equal."""
    return (a.get("status") == "agree") == (b.get("status") == "agree") and (
        a.get("status") == "agree" or a.get("outs") == b.get("outs"))


def vote_one(a, b):
    """The class of one case from leg A's and leg B's records (C is the corpus, the reference both were held to)."""
    ok = ("agree", "differ", "nan_payload")
    if a is None or b is None or a.get("status") not in ok or b.get("status") not in ok:
        return "incomplete"
    ac, bc = a["status"] == "agree", b["status"] == "agree"
    if ac and bc:
        return "all_agree"
    if bc:
        return "A_suspect"
    if ac:
        return "B_suspect"
    return "shared_or_corpus" if a.get("outs") == b.get("outs") else "three_way"


def vote(a_res, b_res):
    out, n = {}, collections.Counter()
    for cid in sorted(set(a_res["cases"]) | set(b_res["cases"])):
        k = vote_one(a_res["cases"].get(cid), b_res["cases"].get(cid))
        out[cid] = k
        n[k] += 1
    return {"classes": dict(n), "cases": out}


def settle_all(corpus, res, limit=25):
    """Settle every differ / nan_payload case of a leg A result by vote_settle's rules: (per-case {id: [kind, verdict,
    note]}, summary lines grouped by kind, at most limit kinds)."""
    by_id = {case_id(c): c for c in VC.read_corpus(corpus)}
    out, groups = {}, collections.defaultdict(collections.Counter)
    first = {}
    mc = collections.Counter()
    for cid, r in res["cases"].items():
        if r["status"] == "agree" and r["words"]:
            mc[S.model_check(by_id[cid], [int(w, 16) for w in r["words"]], K.D.decode)] += 1
        if r["status"] not in ("differ", "nan_payload"):
            continue
        k, v, note = S.settle(by_id[cid], r, K.D.decode)
        out[cid] = [k, v, note]
        op = k.split()[0].rstrip(".")
        k = ("fp arith (" + ("single" if op.endswith("s") else "double") + ")" + k[len(op):]) if op in S.FPA else k
        if k.startswith("fp arith"):
            k = k.replace("(single).", "(single)").replace("(double).", "(double)")
        groups[k][v] += 1
        first.setdefault((k, v), (cid, note))
    lines = [f"model check on cases where A and the corpus agree (scalar arithmetic): {mc[True]} match the rule, "
             f"{mc[False]} do not", f"settled {len(out)} cases in {len(groups)} kinds; verdicts: "
             + ", ".join(f"{v} {n}" for v, n in sum(groups.values(), collections.Counter()).most_common())]
    for k, n in sorted(groups.items(), key=lambda x: -sum(x[1].values()))[:limit]:
        v = n.most_common(1)[0][0]
        cid, note = first[(k, v)]
        lines.append(f"  {k}: " + " ".join(f"{a} {b}" for a, b in n.most_common()) + f" (e.g. {cid}) {note}")
    if len(groups) > limit:
        lines.append(f"  ... {len(groups) - limit} more kinds in the JSON")
    return out, lines


def encode_check(corpus):
    cases = VC.read_corpus(corpus)
    ops, failed, words = collections.Counter(), collections.Counter(), 0
    for c in cases:
        w, why = K.assemble(c)
        if w is None:
            failed[why[:80]] += 1
            continue
        words += len(w)
        for x in w:
            ops[K.D.decode(x).op] += 1
    mn = {t.split()[0] for c in cases for k, t in c["lines"] if k == "insn"}
    tab = K.table_check()
    gold = K.golden_anchor(set(ops))
    lit = K.literal_anchor()
    lines = [f"cases {len(cases)}; assembled {len(cases) - sum(failed.values())}; words round-tripped {words} "
             f"(decode.py op + fields equal, and its text re-encodes to the same word); {len(mn)} mnemonics as written, "
             f"{len(ops)} decoder ops",
             "not assembled: " + "; ".join(f"{n} {k}" for k, n in failed.most_common()),
             f"own opcode tables vs decode.py: {sum(len(t) for t in (E.DFORM, E.DS, E.XO31, E.X31, E.X19, E.A59, E.A63, E.X63, E.VA, E.VC, E.VX))} "
             f"entries, differing or absent there: {tab}",
             f"golden_words.json anchor: {sum(gold.values())}/{len(gold)} corpus ops re-encode to the game's words; "
             f"failed: {[k for k, v in gold.items() if not v]}; corpus ops without a golden word: "
             f"{sorted(set(ops) - set(gold))}",
             f"test-literal anchor: {sum(1 for v in lit.values() if v[0])} mnemonics matched "
             f"({sum(v[0] for v in lit.values())} words), mismatched: {[n for v in lit.values() for n in v[2]]}"]
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode-check")
    e.add_argument("--corpus", default=str(VC.DEFAULT))
    a = sub.add_parser("leg-a")
    a.add_argument("--corpus", default=str(VC.DEFAULT))
    a.add_argument("--work", required=True)
    a.add_argument("--out")
    a.add_argument("--summary")
    a.add_argument("--files")
    b = sub.add_parser("leg-b")
    b.add_argument("step", choices=("prepare", "run", "collect", "all"))
    b.add_argument("--corpus", default=str(VC.DEFAULT))
    b.add_argument("--work", required=True)
    b.add_argument("--files")
    b.add_argument("--out")
    b.add_argument("--summary")
    s = sub.add_parser("settle")
    s.add_argument("a")
    s.add_argument("--corpus", default=str(VC.DEFAULT))
    s.add_argument("--out")
    v = sub.add_parser("vote")
    v.add_argument("a")
    v.add_argument("b")
    v.add_argument("--out")
    args = ap.parse_args(argv)
    if args.cmd == "encode-check":
        print("\n".join(encode_check(args.corpus)))
    elif args.cmd == "leg-a":
        from core import toolcfg
        res, lines = run_leg_a(pathlib.Path(args.corpus), pathlib.Path(args.work), toolcfg.load(ROOT), args.files)
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")
        text = "\n".join(lines)
        if args.summary:
            pathlib.Path(args.summary).write_text(text + "\n", encoding="utf-8")
        print(text)
    elif args.cmd == "leg-b":
        import vote_leg_b as B
        from core import toolcfg
        work = pathlib.Path(args.work).resolve()
        cases = VC.read_corpus(args.corpus)
        if args.files:
            cases = [c for c in cases if fnmatch.fnmatch(c["file"], args.files)]
        plan = B.prepare(work, cases)
        if args.step in ("run", "all"):
            ok, msg = B.run(work, toolcfg.load(ROOT))
            print(msg)
            if not ok:
                return 1
        if args.step in ("collect", "all"):
            res = make_result("B", args.corpus, cases, plan, B.collect(work), B.all_named)
            if args.out:
                pathlib.Path(args.out).write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")
            text = "\n".join(summary(res))
            if args.summary:
                pathlib.Path(args.summary).write_text(text + "\n", encoding="utf-8")
            print(text)
    elif args.cmd == "settle":
        out, lines = settle_all(args.corpus, json.loads(pathlib.Path(args.a).read_text(encoding="utf-8")))
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(out, indent=0), encoding="utf-8")
        print("\n".join(lines))
    elif args.cmd == "vote":
        ra = json.loads(pathlib.Path(args.a).read_text(encoding="utf-8"))
        rb = json.loads(pathlib.Path(args.b).read_text(encoding="utf-8"))
        res = vote(ra, rb)
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(res, indent=0), encoding="utf-8")
        print(json.dumps(res["classes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
