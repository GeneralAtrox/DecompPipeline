"""Translate every function of a code range (a whole overlay) and check the C builds clean with DP's flags.

Entries come from a function list (DP's state/function_bodies.txt: "entry lo hi") or, with --discover, from the
program's entry point by following constant call targets. Each function's extent is found from its entry (other
entries end it), so no Ghidra body is needed. Signatures: DP's harness meta.json where one exists, else a generic
`u32 fn_X(u32 a0..a3)` (r4..r7 in, r0 out).

usage: python tools/overlay.py --image IMG --load ADDR --range LO HI --out DIR
          (--bodies function_bodies.txt [--pointers] | --discover ENTRY [--pointers]) [--harness DIR] [--compile]
--bodies --pointers: the listed functions plus the code they reach that the list lacks (call targets and code
addresses held as data, as --discover finds them), outside every listed body.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from sh2translate.translate import (FunctionTranslator, Image, Signature, TargetFacts, compact, fit,  # noqa: E402
                                    TranslateError)

DP = pathlib.Path(__file__).resolve().parents[4]  # the DecompPipeline checkout this translator is part of
RUNTIME = DP / "targets" / "saturn_sh2" / "runtime"
sys.path.insert(1, str(DP))
from core.lint import lint  # noqa: E402  (pure text rules; imports nothing of the workspace)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--load", required=True, help="load address of the image file")
    ap.add_argument("--range", nargs=2, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bodies")
    ap.add_argument("--discover")
    ap.add_argument("--pointers", action="store_true",
                    help="with --discover or --bodies, also follow code addresses held as data (pool constants, their tables)")
    ap.add_argument("--harness")
    ap.add_argument("--compile", action="store_true")
    ap.add_argument("--msvc-env", default=None)
    ap.add_argument("--simplify", default="all", help="simplification passes: all, none, or a comma list")
    ap.add_argument("--call-sites", action="store_true",
                    help="each call's return address as a /*@ret ADDR*/ mark for the port (gen_saturn.py); not for judged C")
    args = ap.parse_args()
    from sh2translate.simplify import PASSES
    passes = set(PASSES if args.simplify == "all" else () if args.simplify == "none" else args.simplify.split(","))
    facts = TargetFacts.from_target_json(json.loads((DP / "targets/saturn_sh2/target.json").read_text()))
    raw = pathlib.Path(args.image).read_bytes()
    load = int(args.load, 16)
    if load >= facts.image_base:
        img = Image(bytes(load - facts.image_base) + raw, facts.image_base)
    else:   # a file loaded below the image (work RAM low, 0x002xxxxx): an image of its own
        img = Image(raw, load)
    lo, hi = int(args.range[0], 16), int(args.range[1], 16)
    out = pathlib.Path(args.out)
    (out / "c").mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    def translate(e):
        """(row, translator) for the function at e, with the current entry set."""
        meta = None
        if args.harness and (pathlib.Path(args.harness) / f"{e:08x}" / "meta.json").exists():
            meta = json.loads((pathlib.Path(args.harness) / f"{e:08x}" / "meta.json").read_text())
        sig = Signature.from_meta(meta) if meta else Signature.generic(e)
        made = []

        def make(cm, helper=False):
            ft = FunctionTranslator(img, e, sig, facts, None, entries, comments=cm)
            ft.simplify = passes    # the same C translate_function gives
            ft.call_helper = helper
            ft.call_sites = args.call_sites
            made.append(ft)
            return ft.translate()
        try:
            src = fit(make, False)    # as translate_function: under DP's 60 KB lint cap
            ft = made[-1]
            errs = lint(src, {"name": sig.name, "signature": sig.text})
            if errs:
                return {"ok": False, "why": "lint: " + "; ".join(errs)}, ft, None
            return {"ok": True, "bytes": len(src), "insns": len(ft.primary) + len(ft.slots),
                    "switches": len(ft.switches)}, ft, src
        except TranslateError as x:
            return {"ok": False, "why": str(x)}, made[-1] if made else None, None
        except Exception as x:  # noqa: BLE001
            return {"ok": False, "why": "crash: " + repr(x)}, made[-1] if made else None, None

    def inrange(v):
        return lo <= v < hi and v % 2 == 0

    import bisect
    listed_lo, listed_hi = [], []   # the --bodies list's body ranges: an address inside one is that function's own code

    def listed(a):
        k = bisect.bisect_right(listed_lo, a) - 1
        return k >= 0 and a < listed_hi[k]

    def thunk(ft, v):
        """True when the code at v runs straight from v to a `bra` (and its slot) into code already known, without
        touching known code before it: a thunk into another function's shared tail. Dragon Force's CD driver holds
        002208fc, `bra 00220798; mov #1,r4`, in a table, and 00220798 (`rts; mov #1,r0`) is a tail the function before
        0022079c shares; dropped for landing in known code, it was a STUB returning 0 and the driver's sector transfer
        never ran. Data before a function, decoded, runs into it rather than branching there (fall_out)."""
        a = v
        while a in ft.insns and a not in code:
            i = ft.insns[a]
            if i.op in ("bra", "braf", "jmp", "rts", "rte"):
                return i.op == "bra" and a + 2 not in code and i.ea in code
            a += 2
        return False

    found_by = {}
    if args.bodies:
        entries, ranges = set(), []
        for ln in pathlib.Path(args.bodies).read_text().splitlines():
            p = ln.split()
            if not p or p[0].startswith("#"):
                continue
            entries.add(int(p[0], 16))
            if len(p) == 3:
                ranges.append((int(p[1], 16), int(p[2], 16)))
        for a, b in sorted(ranges):
            if listed_hi and a < listed_hi[-1]:     # fragments that touch or overlap: one range
                listed_hi[-1] = max(listed_hi[-1], b)
            else:
                listed_lo.append(a)
                listed_hi.append(b)
        # with --pointers, the listed functions are the start of the discovery below: a function list made by analysis
        # (Ghidra's census) lacks code reached only through a table of code addresses (Saturn Bomberman's KRNL calls
        # 060342f8 through its handler table at 060b5364: a STUB at run time). Only addresses outside every listed body
        # are added, so no listed function is cut short.
        todo = sorted(entries) if args.pointers else []
    else:
        # discovery: from the entry point, follow constant call targets (jsr/bsr, constant jmp, tail calls); with
        # --pointers, also code addresses the program holds as data (see below), until nothing new turns up
        entries = {int(args.discover, 16)}
        todo = [int(args.discover, 16)]
    if todo:
        seen, code, pool = set(), set(), set()
        while True:
            while todo:
                e = todo.pop(0)
                if e in seen:
                    continue
                seen.add(e)
                row, ft, _ = translate(e)
                if row["ok"] or row["why"].startswith("lint"):
                    code.update(ft.primary, ft.slots)
                    for x in list(ft.primary) + list(ft.slots):
                        i = ft.insns[x]
                        if i.op == "mov.l@pc":
                            pool.update(range(i.ea, i.ea + 4))
                        elif i.op == "mov.w@pc":
                            pool.update(range(i.ea, i.ea + 2))
                for c in sorted(getattr(ft, "callees", ())):
                    if inrange(c) and c not in entries and not listed(c):
                        entries.add(c)
                        found_by[c] = "call"
                        todo.append(c)
            if not args.pointers:
                break
            # Code addresses held as data: a literal-pool word of a translated function that points to code no
            # function covers (a callback or task handler passed along), and the words of a table such a constant
            # points to (handler tables walked while each word is a code address). A candidate is kept only when it
            # translates on its own, without running into code or pool words already known or on into another
            # function (data that precedes a function decodes as instructions that fall into it).
            cands = []
            for x in sorted(pool):
                if x % 4 or not img.has(x, 4):
                    continue
                v = img.u32(x)
                if not (lo <= v < hi) or v in code or v in pool:
                    continue
                if v % 2 == 0 and v not in entries:
                    cands.append((v, "pointer"))
                w = v & ~3
                for k in range(256):     # a table of code addresses at v
                    y = w + 4 * k
                    if not img.has(y, 4) or y in code:
                        break
                    t = img.u32(y)
                    if not inrange(t):
                        break
                    if t not in entries and t not in code:
                        cands.append((t, "table"))
            # and tables no constant names (reached through a struct or computed base): 3+ code addresses in a row at
            # one stride, outside known code and pool words. A plain table has them at stride 4; a table of records
            # has one in each record (Saturn Bomberman's 060b957c: 16-byte records with the handler at +12, which
            # 0606ae6c calls through `mov.l @(12,r2),r3; jsr @r3`; the census lacked 0606e89e, 0606ea8c, 06074604)
            held = {}
            for y in range(lo & ~3, hi, 4):
                t = img.u32(y) if img.has(y, 4) and y not in code and y not in pool else None
                if t is not None and inrange(t) and t not in pool:
                    held[y] = t
            for y in sorted(held):
                for stride in range(4, 68, 4):
                    if y - stride in held:
                        continue    # not where the run at this stride starts
                    run = [y]
                    while run[-1] + stride in held:
                        run.append(run[-1] + stride)
                    if len(run) >= 3:
                        cands += [(held[z], "table") for z in run if held[z] not in entries and held[z] not in code]
            cands = list(dict.fromkeys(cands))
            new = 0
            for v, how in cands:
                if v in entries or v in code or v in pool or listed(v):
                    continue
                row, ft, _ = translate(v)
                cov = set(ft.primary) | set(ft.slots)
                if not row["ok"] and not row["why"].startswith("lint"):
                    continue
                if any(y in pool for y in cov) or (cov & code and not thunk(ft, v)):
                    continue
                if getattr(ft, "fall_out", None):
                    continue    # runs on into another function: data just before a function, not an entry
                if any(ft.insns[y].op == "nop" and y not in ft.slots for y in sorted(cov)[:1]):
                    continue    # starting on a nop: alignment padding before a function
                entries.add(v)
                found_by[v] = how
                todo.append(v)
                new += 1
                code.update(cov)    # a later candidate of this round must not overlap this one either
            if not new:
                break
        start = f"listed {len(entries) - len(found_by)}" if args.bodies else "entry 1"
        print("discovered " + ", ".join(f"{k} {n}" for k, n in sorted(
            __import__("collections").Counter(found_by.values()).items())) + f", {start}  in {time.time() - t0:.2f}s")
    # translate every entry against the final entry set (a later entry can end an earlier function's body)
    rows, covered = {}, set()
    for e in sorted(x for x in entries if lo <= x < hi):
        row, ft, src = translate(e)
        rows[e] = row
        if not args.bodies or e in found_by:
            row["found"] = found_by.get(e, "entry")
        if src is not None:
            (out / "c" / f"{e:08x}.c").write_text(src, encoding="utf-8")
        if row["ok"] or row["why"].startswith("lint"):
            covered.update(ft.primary, ft.slots)
    print(f"code covered: {2 * len(covered)} bytes of {hi - lo} ({200 * len(covered) / (hi - lo):.1f}%)")
    t_tr = time.time() - t0
    ok = [e for e, r in rows.items() if r["ok"]]
    print(f"functions {len(rows)}  translated {len(ok)}  in {t_tr:.2f}s  "
          f"({sum(rows[e]['insns'] for e in ok)} instructions)")
    from collections import Counter
    why = Counter(r["why"].split(" at ")[0].split(":")[0] for r in rows.values() if not r["ok"])
    for k, v in why.most_common(12):
        print(f"  {v:5d}  {k}")
    if args.compile and ok:
        env = json.loads(pathlib.Path(args.msvc_env).read_text())
        files = [str((out / "c" / f"{e:08x}.c").resolve()) for e in sorted(ok)]
        objdir = out / "obj"
        objdir.mkdir(exist_ok=True)
        t1 = time.time()
        bad = []
        for k in range(0, len(files), 100):
            import shutil
            cl = shutil.which("cl.exe", path=next(v for k, v in env.items() if k.upper() == "PATH"))
            cmd = [cl, "/nologo", "/c", "/O1", "/fp:strict", "/W4", "/WX", "/TC", f"/I{RUNTIME}",
                   f"/Fo{objdir.resolve()}\\", *files[k:k + 100]]
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600, cwd=objdir)
            for ln in p.stdout.splitlines():
                if ("error" in ln or "warning" in ln) and ".c(" in ln:
                    bad.append(ln.strip())
        objs = {p.stem for p in objdir.glob("*.obj")}
        failed = [f"{e:08x}" for e in ok if f"{e:08x}" not in objs]
        print(f"compiled {len(ok) - len(failed)}/{len(ok)} clean at /W4 /WX in {time.time() - t1:.1f}s")
        for ln in bad[:15]:
            print("  ", ln[-160:])
        for e in failed:
            rows[int(e, 16)]["compiled"] = False
    (out / "overlay.json").write_text(json.dumps({f"{e:08x}": r for e, r in sorted(rows.items())}, indent=1))


if __name__ == "__main__":
    main()
