"""The Xbox 360 kit's feature list, generated from its source: every kernel (xboxkrnl) and XAM import ordinal the kit
answers, read from runtime/*.c and *.h each time (tools/AutoOnboard/onboard_gaps.py calls features() on the checkout it runs from),
so it cannot go stale. `--out FILE` publishes the same JSON.

How it reads the kit: runtime/xb_imports.c's xb_import_call names the functions an import goes to, by library
(`KRNL(imp) && f(` for the kernel, `!KRNL(imp) && f(` for XAM, and the last `KRNL(imp) ? f : g`); a function whose
`default:` hands the ordinal on (`return g(ord`) passes its library to g. Every `case N:` at the first switch's own
level in those functions is an ordinal the kit answers; one whose body only returns no_effect() is listed apart
("answered 0, no effect": the kit logs it once and does nothing). Names come from data/export_names.json.

    python kit_imports.py [--out FILE]

Nearest existing: port/tools/survey.py - lists the imports a game's code reaches, not the ones the kit answers;
port/tools/export_names.py - names every ordinal of the two libraries, implemented or not. port/tools/kit_features.py
(the Dreamcast's) reads SDK function definitions, a different shape of kit.
"""
import argparse
import json
import pathlib
import re

KIT = pathlib.Path(__file__).resolve().parents[1]
LIBS = {"krnl": "xboxkrnl.exe", "xam": "xam.xex"}
FUNC = re.compile(r"^(?:static\s+)?int\s+(\w+)\s*\([^)]*\)\s*\{", re.M)
CASE = re.compile(r"^([ \t]*)((?:case[ \t]+\d+[ \t]*:[ \t]*)+)(.*)$", re.M)


def bodies(kit):
    """{function: body text} of every top-level int function in runtime/*.c and *.h."""
    out = {}
    for f in sorted((pathlib.Path(kit) / "runtime").glob("*.[ch]")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in FUNC.finditer(text):
            end = re.search(r"^\}", text[m.end():], re.M)
            out[m.group(1)] = text[m.end():m.end() + (end.start() if end else len(text))]
    return out


def routes(funcs):
    """{function: library} from xb_import_call and the default: hand-ons."""
    top = funcs.get("xb_import_call", "")
    lib = {f: "krnl" for f in re.findall(r"(?<!!)KRNL\(imp\)\s*&&\s*(\w+)\(", top)}
    lib.update({f: "xam" for f in re.findall(r"!KRNL\(imp\)\s*&&\s*(\w+)\(", top)})
    m = re.search(r"KRNL\(imp\)\s*\?\s*(\w+)\(.*?:\s*(\w+)\(", top)
    if m:
        lib.update({m.group(1): "krnl", m.group(2): "xam"})
    todo = list(lib)
    while todo:
        f = todo.pop()
        for g in re.findall(r"default:\s*return\s+(\w+)\(\s*ord", funcs.get(f, "")):
            if g not in lib:
                lib[g] = lib[f]
                todo.append(g)
    return lib


def cases(body):
    """{ordinal: True when its case only answers no_effect} at the first switch's own level."""
    found = list(CASE.finditer(body))
    if not found:
        return {}
    level = min(len(m.group(1)) for m in found)
    tops = [m for m in found if len(m.group(1)) == level]
    out = {}
    for i, m in enumerate(tops):
        nxt = tops[i + 1].start() if i + 1 < len(tops) else len(body)
        rest = body[m.end():nxt]
        noeff = bool(re.match(r"\s*(/\*.*?\*/\s*)*return\s+no_effect\(", m.group(3) + rest, re.S))
        out.update({int(o): noeff for o in re.findall(r"\d+", m.group(2))})
    return out


def features(kit=KIT):
    """{"console", "source", "libraries": {library: {ordinal: {"name", "no_effect", "in"}}}, "functions": {...}}."""
    kit = pathlib.Path(kit)
    funcs = bodies(kit)
    lib = routes(funcs)
    try:
        names = json.loads((kit / "data" / "export_names.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        names = {}
    out = {LIBS[k]: {} for k in LIBS}
    for f, k in sorted(lib.items()):
        for o, noeff in cases(funcs.get(f, "")).items():
            row = out[LIBS[k]].setdefault(str(o), {"name": (names.get(LIBS[k].split(".")[0], {}).get(str(o)) or
                                                            [None])[0], "no_effect": noeff, "in": f})
            row["no_effect"] = row["no_effect"] and noeff
    return {"console": "xbox360", "source": "targets/xbox360_ppc/port/runtime (xb_import_call and what it calls)",
            "libraries": out, "functions": lib}


def lib_key(name):
    """A catalog import's library name (xboxkrnl.exe, xam.xex, ...) as features()' key, or None for another one."""
    n = str(name).lower()
    return LIBS["krnl"] if "krnl" in n else LIBS["xam"] if n.startswith("xam") else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    a = ap.parse_args()
    f = features()
    text = json.dumps(f, indent=1)
    if a.out:
        pathlib.Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(", ".join(f"{k}: {len(v)} ordinals ({sum(r['no_effect'] for r in v.values())} no effect)"
                    for k, v in f["libraries"].items()) + f"; from {len(f['functions'])} functions")


if __name__ == "__main__":
    main()
