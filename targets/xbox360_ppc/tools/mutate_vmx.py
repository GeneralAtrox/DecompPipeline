"""Mutation check of the Xbox 360 vector helpers (targets/xbox360_ppc/translator/xenontranslate/vmx.py): mutate one
operator or constant of one helper at a time in a private copy of the tree, re-translate and re-judge the functions
that use that helper, and count the mutants the judge kills.

usage: python mutate_vmx.py plan TREE KEEP_DIR OUT_JSON [--per-helper N] [--seed S]
       python mutate_vmx.py run  TREE WORKSPACE KEEP_DIR PLAN_JSON RESULT_JSONL [--first I] [--count N] [--jobs J] [--funcs F]
Nearest existing: tools/translate_backlog.py - it judges C that is already chosen; this chooses the faults to inject.
"""
import glob
import json
import os
import random
import re
import subprocess
import sys

VMX = os.path.join("targets", "xbox360_ppc", "translator", "xenontranslate", "vmx.py")
CALLS = re.compile(r"^(lanes_\w+|words|fcmp|merge|pack|unpack|sat)\(")
OPS = [  # (regex of the site, replacement or None for "number + 1"); one site per mutant
    (r"(?<![+\w])\+(?![+=])", "-"), (r"(?<![-\w>])-(?![-=>\d])", "+"),
    (r"<<", ">>"), (r">>", "<<"),
    (r" < ", " <= "), (r" > ", " >= "), (r"<=", "<"), (r">=", ">"),
    (r"==", "!="), (r"!=", "=="),
    (r"(?<![&\w])&(?![&=])", "|"), (r"(?<![|\w])\|(?![|=])", "&"), (r"\^", "|"),
    (r"&&", "||"), (r"\|\|", "&&"),
    (r"0x80000000u", "0x40000000u"), (r"0x7fffffffu", "0x3fffffffu"), (r"0x00400000u", "0x00200000u"),
    (r"0x7fc00000u", "0x7f800000u"), (r"0xffffffffu", "0x7fffffffu"), (r"0x7f800000u", "0x7f000000u"),
    (r"\.w\[k\]", ".w[3 - k]"), (r"\.w\[\d\]", None),
    (r"(?<![\w.])[1-9]\d?(?![\w.])", None),
]


def regions(text):
    """(start, end, helper) of each line of C text (or generating call) in vmx.py."""
    out, in_c, name, pos = [], False, None, 0
    for line in text.splitlines(keepends=True):
        s, e = pos, pos + len(line)
        pos = e
        m = re.match(r'helper\("(\w+)"', line)
        if m and not in_c:
            name = m.group(1)
        if CALLS.match(line):
            q = re.search(r'"(xv_\w+)"', line)
            if q:
                out.append((s, e, q.group(1)))
        elif (in_c or '"""' in line) and name:
            out.append((s, e, name))
        if line.count('"""') % 2:
            in_c = not in_c
    return out


def sites(text):
    res = []
    for s, e, name in regions(text):
        line = text[s:e]
        if line.lstrip().startswith(("#", "//", "helper(")) or re.match(r"\s*static ", line):
            continue
        for pat, rep in OPS:
            for m in re.finditer(pat, line):
                g = m.group(0)
                if rep is None:
                    if g.startswith(".w["):
                        r = ".w[" + str((int(g[3]) + 1) % 4) + "]"
                    else:
                        r = str(int(g) + 1)
                else:
                    r = rep
                if g == r:
                    continue
                res.append({"helper": name, "at": s + m.start(), "end": s + m.end(), "from": g, "to": r,
                            "line": line.strip()[:100]})
    return res


def uses(keep):
    use = {}
    for p in glob.glob(os.path.join(keep, "build", "*", "official", "work.c")):
        addr = p.split(os.sep)[-3]
        t = open(p, errors="replace").read()
        for m in set(re.findall(r"static [\w ]+? (xv_\w+)\(", t)):
            use.setdefault(m, []).append((len(t), addr))
    for k in use:
        use[k].sort()
    return use


def plan(tree, keep, out, per, seed):
    text = open(os.path.join(tree, VMX), encoding="utf-8").read()
    use = uses(keep)
    by = {}
    for st in sites(text):
        if st["helper"] in use:
            by.setdefault(st["helper"], []).append(st)
    rnd = random.Random(seed)
    muts = []
    for h in sorted(by):
        c = by[h]
        rnd.shuffle(c)
        for st in c[:per]:
            st["funcs"] = [a for _, a in use[h]]
            muts.append(st)
    for i, m in enumerate(muts):
        m["id"] = i
    json.dump(muts, open(out, "w"), indent=1)
    print(f"{len(by)} used helpers with sites; {len(muts)} mutants planned ({per} per helper at most)")


def run(tree, ws, keep, planf, resf, first, count, jobs, nf):
    muts = json.load(open(planf))[first:first + count]
    vm = os.path.join(tree, VMX)
    orig = open(vm, encoding="utf-8").read()
    done = set()
    if os.path.exists(resf):
        done = {json.loads(l)["id"] for l in open(resf) if l.strip()}
    sizes = {p.split(os.sep)[-3]: os.path.getsize(p)
             for p in glob.glob(os.path.join(keep, "build", "*", "official", "work.c"))}
    try:
        for m in muts:
            if m["id"] in done:
                continue
            funcs = sorted(m["funcs"], key=lambda a: sizes.get(a, 0))[:nf]
            open(vm, "w", encoding="utf-8").write(orig[:m["at"]] + m["to"] + orig[m["end"]:])
            lst = os.path.join(tree, "mut_list.txt")
            open(lst, "w").write("\n".join(funcs) + "\n")
            out = os.path.join(tree, "mut_out.json")
            if os.path.exists(out):
                os.remove(out)
            try:
                subprocess.run([sys.executable, "tools/translate_backlog.py", "--project", ws, "--only", "@" + lst,
                                "--jobs", str(jobs), "--json", out], cwd=tree, timeout=900,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                pass
            rows = json.load(open(out))["rows"] if os.path.exists(out) else []
            ver = {r["addr"]: r["verdict"] for r in rows}
            killers = [a for a, v in ver.items() if v != "PASS"]
            rec = {"id": m["id"], "helper": m["helper"], "from": m["from"], "to": m["to"], "line": m["line"],
                   "funcs": funcs, "verdicts": ver, "killed": bool(killers), "killers": killers}
            with open(resf, "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(m["id"], m["helper"], repr(m["from"]), "->", repr(m["to"]),
                  "KILLED" if killers else ("SURVIVED" if ver else "NO-RESULT"), flush=True)
    finally:
        open(vm, "w", encoding="utf-8").write(orig)


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = {k: v for k, v in zip(a, a[1:]) if k.startswith("--")}
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--"))]
    if pos[0] == "plan":
        plan(pos[1], pos[2], pos[3], int(opt.get("--per-helper", 6)), int(opt.get("--seed", 1)))
    else:
        run(pos[1], pos[2], pos[3], pos[4], pos[5], int(opt.get("--first", 0)), int(opt.get("--count", 10 ** 6)),
            int(opt.get("--jobs", 6)), int(opt.get("--funcs", 3)))
