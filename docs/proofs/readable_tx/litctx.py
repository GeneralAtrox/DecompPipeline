"""Where the gate's flagged literals sit in machine files: usage: litctx.py MERGED.json NAME OUT_DIR..."""
import collections, json, pathlib, re, sys
rows = json.load(open(sys.argv[1])); name = sys.argv[2]; dirs = [pathlib.Path(d) for d in sys.argv[3:]]
def text(a):
    for d in dirs:
        p = d / "readable" / name / f"{a}.c"
        if p.exists(): return p.read_text()
ctx = collections.Counter(); files_only = collections.Counter(); n = 0
for r in rows:
    if r["stage"] != "gate": continue
    t = text(r["addr"]); n += 1
    body = t[t.find("{", t.find(")")):]
    kinds = set()
    for g in r["gate"]:
        lit = g.split(" is ")[0]
        for m in re.finditer(r"(?<![\w.])" + re.escape(lit) + r"(?![\w.])", body):
            before = body[max(0, m.start() - 40):m.start()].rstrip(); after = body[m.end():m.end() + 12].lstrip()
            if re.search(r"(<<|>>)\s*$", before) or after.startswith(("<<", ">>")): k = "shift"
            elif re.search(r"\[\s*$", before): k = "index"
            elif re.search(r"[+\-]\s*$", before) or re.search(r"(\+=|-=)\s*$", before) or after[:1] in "+-" and not after.startswith(("++","--")): k = "add/offset"
            elif re.search(r"\*\s*$", before) or after.startswith("*"): k = "multiply"
            elif re.search(r"(&|\||\^)\s*$", before) or after[:1] in "&|^": k = "bit op"
            elif re.search(r"(->|\.)\w+(\[[^\]]*\])?\s*(==|!=|=|<|>|<=|>=)\s*(\([^()]*\)\s*)*$", before): k = "member compare/set"
            elif re.search(r"(==|!=|<=|>=|<|>)\s*$", before) or re.match(r"(==|!=|<=|>=|<|>)", after): k = "compare (local)"
            elif re.search(r"(\(|,)\s*(\([^()]*\)\s*)*$", before) and re.search(r"\w\s*\(", before): k = "call argument"
            elif re.search(r"return\s*(\([^()]*\)\s*)*$", before): k = "return"
            elif re.search(r"=\s*(\([^()]*\)\s*)*$", before): k = "local set"
            else: k = "other"
            ctx[k] += 1; kinds.add(k)
    files_only[tuple(sorted(kinds))] += 1
print(n, "gate files"); print(ctx.most_common())
for k, c in files_only.most_common(8): print(c, k)
ARITH = {"add/offset", "shift", "bit op", "index", "multiply"}
print("arithmetic only:", sum(c for k, c in files_only.items() if set(k) <= ARITH),
      "| + local compares/sets:", sum(c for k, c in files_only.items() if set(k) <= ARITH | {"compare (local)", "local set"}))
