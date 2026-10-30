"""DP's side of tools/gc_truth.py's scores: a GameCube workspace's catalog bodies, translator verdicts and readable
names; the boundary classes of each truth function against DP's catalog; naming agreement as numbers; and where each
REL module's truth functions sit in DP's linked image (targets/gamecube_ppc/tools/gcrel.py's link.json).

Nearest existing: targets/gamecube_ppc/tools/gcrel.py - it gives one address's identity (where/addr/ids) but does not
match a decomp's REL modules and section names to link.json's; gc_truth.py's scoring moved here unchanged so each file
stays under the 400-line cap.
"""
import bisect
import json
import pathlib
import re
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "targets" / "gamecube_ppc" / "tools"))
import gcrel  # noqa: E402


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---- DP's workspace ----

def dp_bodies(ws):
    """{entry: [(lo, hi)]} from state/function_bodies.txt, else from catalog.json's addr and bytes."""
    out = {}
    fb = ws / "state" / "function_bodies.txt"
    if fb.exists():
        for line in fb.read_text(encoding="utf-8").splitlines():
            p = line.split()
            if len(p) == 3:
                out.setdefault(int(p[0], 16), []).append((int(p[1], 16), int(p[2], 16)))
        return out
    for f in (read_json(ws / "state" / "catalog.json", {}) or {}).get("functions", []):
        a = int(f["addr"], 16)
        out[a] = [(a, a + int(f.get("bytes", 4)))]
    return out


def dp_names(ws):
    """{addr: function name} from the readable names registry (readable/names/*.json prototypes)."""
    out = {}
    d = ws / "readable" / "names"
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        for addr, e in ((read_json(f, {}) or {}).get("functions") or {}).items():
            m = re.search(r"\b([A-Za-z_]\w*)\s*\(", e.get("proto", ""))
            if m:
                out[int(addr, 16)] = m.group(1)
    return out


def dp_verdicts(ws, verdicts_file=None):
    """{addr: "PASS" | "FAIL" | "NO_C"}: the translator's judged result per function."""
    out = {}
    if verdicts_file:
        for line in pathlib.Path(verdicts_file).read_text(encoding="utf-8").splitlines():
            p = line.split("#", 1)[0].split()
            if len(p) >= 2:
                out[int(p[0], 16)] = "PASS" if p[1].upper() == "PASS" else "FAIL"
        return out
    rows = (read_json(ws / "state" / "ledger.json", {}) or {}).get("functions") or {}
    for addr, r in rows.items():
        tx = [a for a in r.get("attempts") or [] if a.get("model") == "translated" and a.get("verdict")]
        if tx:
            out[int(addr, 16)] = "PASS" if any(a["verdict"] == "PASS" for a in tx) else "FAIL"
        elif (r.get("translator") or {}).get("ok") is False:
            out[int(addr, 16)] = "NO_C"
    return out


def dp_link(ws, given=None):
    """The workspace's link record (gcrel link.json): the file given, else link.json beside the workspace's binary
    (gcdisc setup writes linked.dol and link.json into one folder), else None (main.dol only)."""
    if given:
        return read_json(pathlib.Path(given)) or sys.exit(f"gc_truth: {given} is not a link.json")
    p = read_json(ws / "project.json", {}) or {}
    if not p.get("binary"):
        return None
    return read_json((ws / p["binary"]).resolve().parent / "link.json")


# ---- REL modules in DP's linked image ----

def norm(path):
    p = path.replace("\\", "/").lower()
    return p[len("files/"):] if p.startswith("files/") else p


def match_module(obj, record):
    """link.json's module for a decomp module object: the one whose disc path ends the object's path (link.json names
    are files/<disc path>), else the one file name alike when only one is."""
    o = norm(obj)
    hits = [m for m in record["modules"] if o == norm(m["name"]) or o.endswith("/" + norm(m["name"]))]
    if not hits:
        base = o.rsplit("/", 1)[-1]
        hits = [m for m in record["modules"] if norm(m["name"]).rsplit("/", 1)[-1] == base]
    return hits[0] if len(hits) == 1 else None


def rel_truth(modules, record):
    """Each REL module's truth functions at their linked addresses: ([(addr, size, module object, name)], {object: why
    not scored}). A decomp code section maps to the module's executable sections in order (the splits.txt
    "Sections:" order; a module without splits and one executable section maps ".text" to it)."""
    out, skipped = [], {}
    for m in modules:
        if not m["functions"]:
            continue
        lm = match_module(m["object"], record) if record else None
        if lm is None:
            skipped[m["object"]] = "not linked" if record else "no link.json"
            continue
        exe = sorted(s["index"] for s in lm["sections"] if s["exec"])
        names = m["code_sections"] or ([".text"] if len(exe) == 1 else [])
        if len(names) != len(exe):
            skipped[m["object"]] = (f"{len(names)} code sections in the decomp, {len(exe)} in the module" if names
                                    else f"no splits.txt section list, {len(exe)} code sections in the module")
            continue
        sec = dict(zip(names, exe))
        for off, size, s, name in m["functions"]:
            if s in sec and size > 0:
                try:
                    out.append((gcrel.address(record, lm["module_id"], sec[s], off), size, m["object"], name))
                except gcrel.LinkError:
                    skipped[m["object"]] = f"a function outside section {s}"
    return out, skipped


# ---- scoring ----

def classify(truth, bodies):
    """{truth entry: kind} (exact / boundary_differs / split / merged / missed) and the extra DP entries."""
    truth = sorted({(a, s) for a, s in truth if s > 0})
    t_starts = [a for a, _ in truth]
    entries = sorted(bodies)
    covers = []  # (lo, hi, dp entry) of every DP body range
    for e, rs in bodies.items():
        covers += [(lo, hi, e) for lo, hi in rs]
    covers.sort()
    # which truth entries each DP function's body covers
    dp_cover = {}
    for lo, hi, e in covers:
        i = bisect.bisect_left(t_starts, lo)
        while i < len(t_starts) and t_starts[i] < hi:
            dp_cover.setdefault(e, set()).add(t_starts[i])
            i += 1
    covered_by = {}
    for e, ts in dp_cover.items():
        for t in ts:
            covered_by.setdefault(t, set()).add(e)
    kinds = {}
    for a, s in truth:
        end = a + s
        i, j = bisect.bisect_left(entries, a), bisect.bisect_left(entries, end)
        inside = entries[i:j]
        if a in bodies:
            dp_end = max(hi for _, hi in bodies[a])
            if len(dp_cover.get(a, ())) > 1:
                kinds[a] = "merged"
            elif len(inside) > 1:
                kinds[a] = "split"
            else:
                kinds[a] = "exact" if dp_end == end else "boundary_differs"
        elif covered_by.get(a):
            kinds[a] = "merged"
        elif len(inside) > 1:
            kinds[a] = "split"
        else:
            kinds[a] = "boundary_differs" if inside else "missed"
    extra = 0
    for e in entries:
        i = bisect.bisect_right(t_starts, e) - 1
        if i < 0 or not (truth[i][0] <= e < truth[i][0] + truth[i][1]):
            extra += 1
    return kinds, extra


_DEMANGLE = re.compile(r"^(__\w+?|\w+?)__(?=[0-9QFC])")


def truth_tokens(name):
    """Words of a decomp name: CodeWarrior C++ "base__Q24Game4PikiFv" gives the innermost class and the base."""
    base, cls = name, ""
    m = _DEMANGLE.match(name)
    if m:
        base, rest = m.group(1), name[m.end():]
        parts = []
        q = re.match(r"^Q(\d)", rest)
        rest = rest[2:] if q else rest
        for _ in range(int(q.group(1)) if q else 1):
            n = re.match(r"^(\d+)", rest)
            if not n:
                break
            k = int(n.group(1))
            parts.append(rest[len(n.group(1)):len(n.group(1)) + k])
            rest = rest[len(n.group(1)) + k:]
        cls = parts[-1] if parts else ""
        base = {"__ct": "ctor", "__dt": "dtor"}.get(base, base)
    return words(cls) + words(base)


def words(s):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w]


def name_scores(truth_names, dp):
    """Agreement of DP's readable names with the decomp's, as numbers only: exact (same words in the same order),
    mean token overlap (Jaccard), and how many share at least one word."""
    pairs = [(truth_tokens(truth_names[a]), words(n)) for a, n in dp.items() if a in truth_names]
    if not pairs:
        return {"named": 0, "exact": 0, "any_word": 0, "mean_overlap": None}
    exact = sum(1 for t, d in pairs if t and t == d)
    overlap = [len(set(t) & set(d)) / len(set(t) | set(d)) if set(t) | set(d) else 0.0 for t, d in pairs]
    return {"named": len(pairs), "exact": exact, "any_word": sum(1 for o in overlap if o > 0),
            "mean_overlap": round(sum(overlap) / len(overlap), 4)}
