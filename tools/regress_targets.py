"""Which part of the regress gate a change can affect: tools/regress.py --targeted BASE.

Every changed file (BASE's merge base -> the working tree, untracked files too) gets the class of the strongest
gate input it is, by what the gate runs (a static import graph of core/, tools/ and the root scripts, from the gate's
entry points), never by its name alone:
- gate:     the gate's own machinery (tools/regress.py, regress_project.py, this file, bounded.py), config.json,
            tests/patterns.json, or a file of no known class: the full gate, every target;
- prepare:  what makes a harness: a module the oracle path imports (core.ghidra's import closure), a target's Ghidra
            scripts, template, language, target.json or hook modules: every item, every harness prepared afresh;
- judge:    the raw judge (core.check's closure) or a target's raw runtime: every item, on harnesses reused from the
            harness cache when their oracle stamp and prepare code are those of the cached one;
- readable: the readable judge (core.readable_check's and core.readable's closures) or a target's readable runtime:
            only the readable items (fixtures with "runtime": "readable" or "readable-src", parity items), reused;
- none:     nothing the gate runs (docs, unit tests, the port, the UI, a module outside the gate's closure).
A file under targets/T/ affects only projects of target T; anything else affects every target. A project of an
affected target always runs its catalog step (--catalog), so a catalog the change moves changes every stamp of
that project and nothing stale is reused. The prepare code hash (prepare_hash) is part of a cached harness's key:
the oracle stamp hashes the oracle's inputs, not the Python that runs it.

usage: python tools/regress_targets.py [BASE] [--files F ...] [--json]   (prints the plan; BASE default master)
"""
import argparse
import ast
import hashlib
import json
import pathlib
import re
import subprocess
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]

ORDER = ("none", "readable", "judge", "prepare", "gate")
GATE_FILES = {"tools/regress.py", "tools/regress_project.py", "tools/regress_targets.py", "tools/bounded.py",
              "config.json", "tests/patterns.json"}
NONE_PREFIX = ("docs/", "port/", "ui/", "tests/test_", "tests/java/", "tests/lockstep_fixture/")
NONE_NAMES = {"LICENSE", ".gitignore", "README.md"}
# prepare code that is not a module: the slot server's Ghidra script (core/ghidra_serve/TcServe.java runs TcPrepare),
# which the oracle stamp does not hash (it hashes the scripts folder), and the oracle scripts several targets share
# (core/ghidra_shared/: every target, though only those whose target.json "shared_scripts" names the folder use it)
PREPARE_DIRS = ("core/ghidra_serve/", "core/ghidra_shared/")
ENTRY = {"prepare": ["core.ghidra"], "judge": ["core.check"], "readable": ["core.readable_check", "core.readable"]}
# targets/T/<first part>: its class (anything else under targets/T is "gate", unknown)
TARGET_PARTS = {"ghidra_scripts": "prepare", "template": "prepare", "ghidra_lang": "prepare",
                "target.json": "prepare", "runtime": "judge", "runtime_rd": "readable", "README.md": "none",
                "xemu": "none", "tools": "none", "match_include": "judge"}


def modules(root=TOOL):
    """{module: (path, {imported modules})} for core/*.py, tools/*.py and the root scripts."""
    found = {}
    for p in sorted(root.glob("core/*.py")) + sorted(root.glob("tools/*.py")) + sorted(root.glob("*.py")):
        pkg = p.parent.name if p.parent != root else ""
        found[f"{pkg}.{p.stem}" if pkg else p.stem] = p
    tools = {m.split(".", 1)[1] for m in found if m.startswith("tools.")}
    graph = {}
    for name, p in found.items():
        pkg = name.split(".")[0] if "." in name else ""
        deps = set()
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom):
                if n.level == 1 and pkg == "core":
                    deps |= {f"core.{n.module.split('.')[0]}"} if n.module else {f"core.{a.name}" for a in n.names}
                elif n.module == "core":
                    deps |= {f"core.{a.name}" for a in n.names}
                elif n.module and n.module.startswith("core."):
                    deps.add(n.module)
                elif n.module and pkg == "tools" and n.module in tools:  # sys.path holds tools/: from bounded import
                    deps.add(f"tools.{n.module}")
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("core."):
                        deps.add(a.name)
                    elif pkg == "tools" and a.name in tools:
                        deps.add(f"tools.{a.name}")
        graph[name] = (p, deps & set(found))
    return graph


def closure(graph, starts):
    seen, todo = set(), list(starts)
    while todo:
        m = todo.pop()
        if m not in seen and m in graph:
            seen.add(m)
            todo += graph[m][1]
    return seen


def module_classes(root=TOOL):
    """{repo-relative path of a .py module: class} for core/, tools/ and the root scripts."""
    graph = modules(root)
    cls = {}
    for kind in ("readable", "judge", "prepare"):  # later (stronger) classes win
        for m in closure(graph, ENTRY[kind]):
            cls[m] = kind
    gate = closure(graph, ["tools.regress", "tools.regress_project", "tools.regress_targets"])
    for m in gate:
        cls.setdefault(m, "gate")  # in the gate's closure but on no judged path: its machinery
    return {graph[m][0].relative_to(root).as_posix(): cls.get(m, "none") for m in graph}


def changed_keys(old, new):
    """The top-level keys whose values differ between two JSON texts (None: either is not a JSON object)."""
    try:
        a, b = json.loads(old) if old else {}, json.loads(new) if new else {}
    except ValueError:
        return None
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    return sorted(k for k in set(a) | set(b) if a.get(k, KeyError) != b.get(k, KeyError))


def key_classes(keys, mods, root=TOOL):
    """[(class, target or None)] of a change to these config keys: per gate module or target file that names one of
    them as a string literal (config is read by literal key only: cfg["k"], cfg.get("k")), its class; a target's own
    .py or .java file affects that target only. Empty: nothing the gate runs reads them."""
    texts = [(c, None, (root / f).read_text(encoding="utf-8")) for f, c in mods.items() if c != "none"]
    texts += [("prepare", p.relative_to(root / "targets").parts[0], p.read_text(encoding="utf-8", errors="replace"))
              for p in sorted((root / "targets").rglob("*")) if p.is_file() and p.suffix in (".py", ".java")]
    rx = re.compile(r"""["'](?:""" + "|".join(re.escape(k) for k in keys) + r""")["']""") if keys else None
    return sorted({(c, t) for c, t, text in texts if rx and rx.search(text)}, key=str)


def classify(files, root=TOOL, old_text=None):
    """[(file, class, target or None)] for repo-relative paths (posix). old_text(path): the file's text at the base
    (None: not there), for config.json and target.json, which are classed by the keys that changed."""
    mods, out = module_classes(root), []
    for f in files:
        parts = f.split("/")
        json_cfg = f == "config.json" or (parts[0] == "targets" and parts[-1] == "target.json" and len(parts) == 3)
        keys = None
        if json_cfg and old_text is not None:
            new = (root / f).read_text(encoding="utf-8") if (root / f).exists() else None
            keys = changed_keys(old_text(f), new)
        if keys is not None:
            own = parts[1] if parts[0] == "targets" else None  # a target.json: its own target only
            hits = [(c, own or t) for c, t in key_classes(keys, mods, root) if not own or t in (None, own)]
            out += [(f, c, t) for c, t in hits] or [(f, "none", own)]
        elif f in GATE_FILES:
            out.append((f, "gate", None))
        elif parts[0] == "targets" and len(parts) >= 3:
            t, part = parts[1], parts[2]
            if part.endswith(".py"):
                kind = "prepare"  # hooks.py and the modules it loads: the target's own prepare and judge
            else:
                kind = TARGET_PARTS.get(part, "gate")
            out.append((f, kind, t))
        elif f in mods:
            out.append((f, mods[f], None))
        elif f.startswith(PREPARE_DIRS):
            out.append((f, "prepare", None))
        elif f.startswith(NONE_PREFIX) or f in NONE_NAMES or (len(parts) == 1 and f.endswith(".md")) \
                or (f.endswith(".md") and parts[0] in ("core", "tools")):
            out.append((f, "none", None))
        else:
            out.append((f, "gate", None))  # unknown: the full gate
    return out


def plan(classified, targets):
    """{target: mode} over the given target names: the strongest class of any file that affects it."""
    mode = {t: "none" for t in targets}
    for _, kind, t in classified:
        for tt in ([t] if t else targets):
            if tt in mode and ORDER.index(kind) > ORDER.index(mode[tt]):
                mode[tt] = kind
    return mode


def prepare_hash(target, root=TOOL):
    """sha256 (16 hex) of the code that makes a harness of `target` and the oracle stamp does not hash: the Python
    modules of class prepare (or gate), PREPARE_DIRS and the target's own modules (targets/T/*.py), template and
    language files."""
    mods = module_classes(root)
    files = sorted(f for f, c in mods.items() if c in ("prepare", "gate"))
    files += sorted(p.relative_to(root).as_posix() for d in PREPARE_DIRS for p in (root / d).rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts)
    tdir = root / "targets" / target
    files += sorted(p.relative_to(root).as_posix() for p in [*tdir.glob("*.py"), *tdir.glob("template/*"),
                                                             *tdir.glob("ghidra_lang/*")] if p.is_file())
    h = hashlib.sha256()
    for f in files:
        h.update(f"{f} {hashlib.sha256((root / f).read_bytes().replace(b'\r\n', b'\n')).hexdigest()}\n".encode())
    return h.hexdigest()[:16]


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                          check=check, timeout=120)


def changed_files(base, root=TOOL):
    """Files changed from BASE's merge base with HEAD to the working tree, plus untracked ones (not ignored), and
    old_text(path): a file's text at that merge base (None when it was not there)."""
    mb = git(root, "merge-base", base, "HEAD").stdout.strip()
    files = git(root, "diff", "--name-only", "--no-renames", mb).stdout.splitlines()
    files += git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()

    def old_text(path):
        r = git(root, "show", f"{mb}:{path}", check=False)
        return r.stdout if r.returncode == 0 else None
    return mb, sorted(set(files)), old_text


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", nargs="?", default="master")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.files is None:
        mb, files, old_text = changed_files(a.base)
    else:
        mb, files, old_text = None, a.files, None
    rows = classify(files, old_text=old_text)
    targets = sorted(p.name for p in (TOOL / "targets").iterdir() if (p / "target.json").exists())
    mode = plan(rows, targets)
    if a.json:
        print(json.dumps({"merge_base": mb, "files": rows, "mode": mode}, indent=1))
    else:
        for f, kind, t in rows:
            print(f"{kind:8} {t or '*':14} {f}")
        print("modes:", ", ".join(f"{t} {m}" for t, m in mode.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
