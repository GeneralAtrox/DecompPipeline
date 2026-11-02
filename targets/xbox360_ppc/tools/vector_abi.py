"""Vector ABI exposure: which VMX functions pass vector values across their boundary, which the judge cannot check.

The oracle and the C runner give a function no vector arguments and compare no vector results, so a function whose
behaviour depends on a vector register it was handed (an argument), hands one to a callee, or returns one is judged on
scalar effects only: a PASS says less than it seems to. This finds them statically (per-register forward dataflow over
the function's blocks, jump tables resolved), assuming the Xbox 360 vector ABI as inferred from the code: arguments
v1..v13 (v2..v13 documented), the result v1 or v2, v0-v13 and v32-v63 volatile across calls.

A function is "exposed" when any of: A) it reads a vector register that still holds its entry value (not just spills a
nonvolatile one to the frame); B) at a call or tail call a locally defined argument register reaches a callee that
reads it (callee-aware, to a fixpoint over the catalog's vector functions); C) a result register is defined and never
read before a return; D) it reads v0-v13 after a call without redefining it.

usage: python vector_abi.py WORKSPACE OUT_DIR   writes OUT_DIR/vabi.json {addr: [A|B|C|D, ...]}, table.tsv, rows.json
       python vector_abi.py WORKSPACE --show ADDR ...   annotated listing of one function
vabi.json reasons: A B C D as above, E = a vector estimate op (vrefp, vrsqrtefp, vexptefp, vlogefp: hardware-specific
values the oracle cannot give). Applied to a catalog by catalog_refuse.py --vabi OUT_DIR/vabi.json.
Nearest existing: ppcscan.py - it counts instruction classes per function; this needs a dataflow over the blocks.
"""
from vabi_flow import *  # noqa: F401,F403  (the state, then the analysis: this file is the report and the entry point)


def show(a):
    fn = analyze(build(a))
    starts = {s: b for b, s in enumerate(fn.bstart)}
    evk = collections.defaultdict(list)
    for x in fn.ev:
        evk[x[1]].append(x)
    print("function %08x  insns=%d  blocks=%d  bctr=%d (resolved %d)" % (a, fn.n, len(fn.blocks), fn.nbctr, fn.nbctr_res))
    for k, i in enumerate(fn.ins):
        if k in starts:
            b = starts[k]
            print("  ; --- block %d -> succ %s%s" % (b, fn.blocks[b][3], "" if fn.reach[b] else "  (UNREACHABLE)"))
        ex = ""
        for x in evk.get(k, []):
            if x[0] == "A": ex += "   <<A: reads entry-live %s%s>>" % (regs_str(x[2]), " (spill-store)" if x[3] else "")
            elif x[0] == "D": ex += "   <<D: reads post-call %s>>" % regs_str(x[2])
            elif x[0] == "Dhi": ex += "   <<Dhi: %s>>" % regs_str(x[2])
            elif x[0] == "call": ex += "   <<CALL tgt=%s argsDefinedLocally=%s>>" % ("%08x" % x[2] if x[2] else "indirect", regs_str(x[3]))
            elif x[0] == "tail": ex += "   <<TAIL tgt=%s argsDefinedLocally=%s v1v2local=%d>>" % ("%08x" % x[2] if x[2] else "indirect", regs_str(x[3]), x[5])
            elif x[0] == "ret": ex += "   <<RET(%s) v1v2local=%d>>" % (x[2], x[3])
        print("  %08x  %s%s" % (i.addr, disasm(i), ex))
    print("  C_strong (v2 def that reaches a blr and is never read/passed):", fn.Cstrong)
    print("  A args=%s vol=%s nv=%s spill=%s | B mask=%s | Cb=%s Ct=%s | D=%s Dhi=%s" % (
        regs_str(fn.Aargs), regs_str(fn.Avol), regs_str(fn.Anv), regs_str(fn.Aspill), regs_str(fn.Bmask), fn.Cb, fn.Ct,
        regs_str(fn.D), regs_str(fn.Dhi)))


def main():
    t0 = time.time()
    if "--diag" in sys.argv:
        h = collections.Counter(); hd = collections.Counter(); fa = collections.defaultdict(set)
        for a, f in FUNCS.items():
            if not (f["counts"].get("vmx") and f["kind"] == "function"): continue
            fn = analyze(build(a))
            for x in fn.ev:
                if x[0] == "A":
                    op = fn.ins[x[1]].op
                    for r in bits_list(x[2]):
                        cls = "args" if 2 <= r < 14 else "v0/1" if r < 2 else "v32-63" if 32 <= r < 64 else "nv"
                        h[(cls, op if not x[3] else "ST:" + op)] += 1; fa[(cls, op if not x[3] else "ST:" + op)].add(a)
                if x[0] == "D":
                    for r in bits_list(x[2]): hd[(r, fn.ins[x[1]].op)] += 1; fa[("D", r)].add(a)
        for k, v in h.most_common(40): print("A", k, v, "funcs", len(fa[k]))
        for k, v in hd.most_common(40): print("D", k, v)
        return
    if "--show" in sys.argv:
        for a in sys.argv[sys.argv.index("--show") + 1:]:
            show(int(a, 16)); print()
        return
    vec = sorted(a for a, f in FUNCS.items() if f["counts"].get("vmx"))
    print("functions with vmx count>0:", len(vec))
    OTHER = ("fp_mode", "estimate", "atomic", "cache", "sys")
    res = {}
    for a in vec:
        f = FUNCS[a]
        if f["kind"] != "function":
            continue
        fn = analyze(build(a))
        fn.vest = any(i.op in VEST for i in fn.ins)
        fn.illegal = sum(1 for i in fn.ins if i.op == "illegal")
        fn.other = [c for c in OTHER if f["counts"].get(c)]
        fn.refuse = f["refuse"]
        res[a] = fn
    print("analysed", len(res), "in %.1fs" % (time.time() - t0))
    trig = collections.Counter()
    for a, fn in res.items():
        if fn.other:
            for i in fn.ins:
                for x in ppcscan.classify(i.word):
                    if x in OTHER: trig[(x, i.op)] += 1
    # interprocedural: which arg regs a callee consumes (explicit reads + forwarding), fixpoint
    use = {a: fn.Aargs1 for a, fn in res.items()}

    def callee_use(t):
        if t is None: return ARGS                   # indirect: unknown
        if t in use: return use[t]
        if t in THUNKS: return 0
        if t in FUNCS: return 0                     # not a vector function: reads no vector registers (pass-through ignored)
        return ARGS                                 # outside the catalog: unknown
    changed = True
    while changed:
        changed = False
        for a, fn in res.items():
            u = fn.Aargs1
            for x in fn.calls: u |= x[4] & callee_use(x[2])
            for x in fn.tails: u |= x[4] & callee_use(x[2])
            if u != use[a]: use[a] = u; changed = True
    for a, fn in res.items():
        b2 = 0
        for x in fn.calls: b2 |= x[3] & callee_use(x[2])
        for x in fn.tails: b2 |= x[3] & callee_use(x[2])
        fn.B2mask1 = b2
        fn.B2mask = b2 & SPEC
    rows = []
    for a, fn in sorted(res.items()):
        Aspec = bool(fn.Aargs | fn.Avol | fn.Anv | fn.Aspill)
        A2 = bool(fn.Aargs | fn.Avol | fn.Anv)
        B = bool(fn.Bmask); C = fn.Cb or fn.Ct; D = bool(fn.D)
        notes = []
        if fn.other: notes.append("other-refusal:" + "+".join(fn.other))
        if fn.vest: notes.append("vector-estimate-op")
        if fn.illegal: notes.append("illegal-words=%d" % fn.illegal)
        if fn.nbctr: notes.append("bctr=%d(table-resolved %d,indirect-tail %d,unresolved-switch %d)" % (fn.nbctr, fn.nbctr_res, fn.nbctr_tail, fn.nbctr - fn.nbctr_res - fn.nbctr_tail))
        if fn.unreach_insns: notes.append("unreachable-insns=%d" % fn.unreach_insns)
        if fn.falls_off: notes.append("falls-off-end")
        if fn.Aargs: notes.append("Aargs=" + regs_str(fn.Aargs))
        if fn.Avol: notes.append("Avol=" + regs_str(fn.Avol))
        if fn.Anv: notes.append("Anv=" + regs_str(fn.Anv))
        if fn.Aspill: notes.append("Aspill=" + regs_str(fn.Aspill))
        if fn.Bmask: notes.append("Bregs=" + regs_str(fn.Bmask))
        if fn.D: notes.append("Dregs=" + regs_str(fn.D))
        if fn.Dhi: notes.append("Dhi=" + regs_str(fn.Dhi))
        pop = "other_refusal" if fn.other else ("vestimate" if fn.vest else "judge")
        rows.append(dict(addr=a, n=fn.n, A=int(Aspec), B=int(B), C=int(C), D=int(D), bctr=fn.nbctr, notes=";".join(notes),
                         pop=pop, A2=int(A2), Aargs=int(bool(fn.Aargs)), B2=int(bool(fn.B2mask)), Cb=int(fn.Cb), Ct=int(fn.Ct),
                         D2=int(bool(fn.D & 4)), Dhi=int(bool(fn.Dhi)), ncalls=len(fn.calls), ntails=len(fn.tails),
                         bres=fn.nbctr_res, btail=fn.nbctr_tail, unreach=fn.unreach_insns, illegal=fn.illegal, other=fn.other, vest=fn.vest,
                         Aspill=int(bool(fn.Aspill)), Cstrong=fn.Cstrong, Cstrong1=fn.Cstrong1, Cunread=fn.Cunread, Cunread1=fn.Cunread1, RP1=fn.RP1, RP2=fn.RP2,
                         Cb1=fn.Cb1, Ct1=fn.Ct1, B1=int(bool(fn.Bmask1)), B21=int(bool(fn.B2mask1)), Av1=int(bool(fn.Avol & 0) or any(
                             x[0] == "A" and x[2] & 2 for x in fn.ev))))
    exposed = {}
    for r in rows:
        why = [k for k, on in (("A", r["A2"] or r["Av1"]), ("B", r["B21"]), ("C", r["Cunread"] or r["Cunread1"]), ("D", r["D"])) if on]
        if r["pop"] == "vestimate":
            exposed["%08x" % r["addr"]] = ["E"]
        elif why and r["pop"] == "judge":
            exposed["%08x" % r["addr"]] = why
    os.makedirs(OUT, exist_ok=True)
    json.dump(exposed, open(os.path.join(OUT, "vabi.json"), "w"), sort_keys=True)
    print("exposed:", sum(1 for v in exposed.values() if v != ["E"]), "of", sum(1 for r in rows if r["pop"] == "judge"),
          "judgeable vector functions;", sum(1 for v in exposed.values() if v == ["E"]), "use a vector estimate op")
    cols = ["addr", "ninstr", "A", "B", "C", "D", "bctr", "notes", "pop", "A_noSpill", "A_args", "B_refined", "C_blr", "C_tail", "C_strong", "D_v2", "D_v32_63", "ncalls", "ntail", "A_reads_v1", "B_incl_v1", "B_refined_incl_v1", "C_v1", "C_strong_v1", "C_unread_v2", "C_unread_v1", "RetCalleeDefined_v1", "RetCalleeDefined_v2"]
    with open(OUT + r"\table.tsv", "w") as fo:
        fo.write("\t".join(cols) + "\n")
        for r in rows:
            fo.write("\t".join(str(x) for x in ("%08x" % r["addr"], r["n"], r["A"], r["B"], r["C"], r["D"], r["bctr"], r["notes"], r["pop"],
                                                r["A2"], r["Aargs"], r["B2"], r["Cb"], r["Ct"], int(r["Cstrong"]), r["D2"], r["Dhi"], r["ncalls"], r["ntails"], r["Av1"], r["B1"], r["B21"], int(r["Cb1"] or r["Ct1"]), int(r["Cstrong1"]), int(r["Cunread"]), int(r["Cunread1"]), int(r["RP1"]), int(r["RP2"]))) + "\n")
    json.dump(rows, open(OUT + r"\rows.json", "w"))
    json.dump({"%s|%s" % k: v for k, v in trig.items()}, open(OUT + r"\trigger_ops.json", "w"))
    print("done %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
