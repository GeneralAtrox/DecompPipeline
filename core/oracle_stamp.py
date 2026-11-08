"""The oracle-input stamp of a harness: a hash of everything the Ghidra oracle reads to make one function's harness.

ghidra.prepare writes it into the harness's meta.json ("oracle_stamp"). Two harnesses with the same stamp were made
from the same inputs, so a run that only needs a current harness (tools/regress.py --parity, --reuse) reuses one
whose stamp matches and re-prepares the rest. The inputs:
- the oracle scripts (every *.java in the scripts folder TcPrepare runs from: the target's scripts, the harness
  generator TcPrepare/TcVectors/TcOracle, and the project's TcInputs.java);
- the facts file (calling convention, memory map, oracle switches and deadlines from config), with the code ranges
  file it names hashed by content (its path differs between a project and a private copy);
- the function, its dev and hidden vector counts (the seed is the address), its hints file, and whether
  project.json "spin_waits" lists it, and the words its evidence names (TcSpin; an unlisted function's stamp
  has no such line), and the hardware ops and register values project.json "hw_ops" lists for it (TcHw; likewise),
  and the run flag project.json "run_flags" lists for it (TcCalls; likewise), its "oracle_limits" (likewise) and its
  float callees (TcFret; likewise);
- the fsca table (its content, core/fsca.FILE_SHA256: every prepare makes the table it reads that one), the
  catalog (the Ghidra project's function bodies), the binary's sha256 and the Ghidra install: what decides its
  output (ghidra_identity), not the folder it is in, so the same install on another machine, or moved, gives the
  same stamp.
A wall-clock deadline is an input too (the facts file holds it), but how far a loaded machine gets before it is not:
the stamp says which inputs made a harness, not that a run under load would make the same one.
A scoped oracle change (config "stamp_scopes", from target.json) leaves the stamps of the functions it cannot reach
as they were: each scope lists script files with their hashes before and after the change, and a function the
target's stamp_scope hook (core/target_hooks) puts outside the scope is stamped with the "before" hashes (a file the
change added has none, null: such a function's stamp has no line for it). A scope with "flags" (config switches)
needs no hook: a change that makes no harness byte while its switches are off reaches every function while one is
on and none while all are off. Only while every file's hash is still its
"after" hash: any later edit of one stamps every function anew.
The callee records are an input too: each records the callee's signature the harness was built with, and a harness
is current only while they equal the callees' own harness signatures (current(); core/callee_drift.py).
"""
import hashlib
import pathlib
import re
import threading

from . import callee_drift, compare, fsca, hw_ops, oracle_limits, paths, ret_reg, sz_entry, target_hooks

_FILE_CACHE = {}
_LOCK = threading.Lock()
_LANG = {}  # (the install's Ghidra folder, language id): the .ldefs file that defines it (ghidra_identity)
_GHIDRA = {}  # (folder, language, the stats of application.properties and that .ldefs): ghidra_identity()


def file_hash(path):
    """sha256 of a file's bytes ('-' when it does not exist), cached while its mtime and size stay the same."""
    p = pathlib.Path(path)
    try:
        st = p.stat()
    except OSError:
        return "-"
    key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    with _LOCK:
        if key in _FILE_CACHE:
            return _FILE_CACHE[key]
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    with _LOCK:
        _FILE_CACHE[key] = h
    return h


def _stat(p):
    try:
        st = pathlib.Path(p).stat()
    except (OSError, TypeError):
        return None
    return st.st_mtime_ns, st.st_size


def _language_file(root, lang):
    """The .ldefs file under the install's Ghidra folder root that defines language id lang, a processor's or an
    installed extension's (GameCube: the GameCubeLoader extension's Gekko_Broadway); None when none does."""
    for ldefs in sorted(root.glob("Processors/*/data/languages/*.ldefs")) + sorted(
            root.glob("Extensions/*/data/languages/*.ldefs")):
        try:
            if f'id="{lang}"' in ldefs.read_text(encoding="utf-8", errors="replace"):
                return ldefs
        except OSError:
            pass
    return None


def ghidra_identity(cfg):
    """What decides the Ghidra install's output, wherever its folder is (16 hex digits; '-' without an install): the
    version and build lines of its application.properties, its decompiler program, and every file of the folder
    whose .ldefs defines cfg "ghidra_language" (the .ldefs, .sla, .slaspec, .sinc, .pspec, .cspec: the SH-4 tcfix
    language that targets/dreamcast_sh4/ghidra_lang installs is one; its .sla names its sources, not their path),
    with an extension's jars when an extension defines it.
    Taken once per process while application.properties and that .ldefs keep their size and mtime."""
    home = cfg.get("ghidra_home")
    root = pathlib.Path(home) / "Ghidra" if home else None
    props = root / "application.properties" if root else None
    if props is None or not props.is_file():
        return "-"
    lang = str(cfg.get("ghidra_language") or "")
    with _LOCK:
        ldefs = _LANG.get((str(root), lang), False)
    if ldefs is False:
        ldefs = _language_file(root, lang)
        with _LOCK:
            _LANG[(str(root), lang)] = ldefs
    key = (str(root), lang, _stat(props), _stat(ldefs))
    with _LOCK:
        if key in _GHIDRA:
            return _GHIDRA[key]
    h = hashlib.sha256()
    for line in sorted(props.read_text(encoding="utf-8", errors="replace").splitlines()):
        if line.strip() and not line.startswith("#"):  # the first line is a comment: the time the file was written
            h.update(f"property {line.strip()}\n".encode())
    for exe in sorted(root.glob("Features/Decompiler/os/*/decompile*")):
        h.update(f"decompiler {exe.parent.name}/{exe.name} {file_hash(exe)}\n".encode())
    if ldefs:
        for f in sorted(ldefs.parent.iterdir()):
            if f.is_file():
                h.update(f"language {f.name} {file_hash(f)}\n".encode())
        ext = ldefs.parents[2]
        if ext.parent.name == "Extensions":  # an extension's language: its loader code decides the import too
            for f in sorted([*ext.glob("lib/*.jar"), *ext.glob("extension.properties")]):
                h.update(f"extension {f.name} {file_hash(f)}\n".encode())
    else:
        h.update(f"no language {lang}\n".encode())
    out = h.hexdigest()[:16]
    with _LOCK:
        _GHIDRA[key] = out
    return out


def facts_text(facts_path):
    """The facts file's text with the code ranges path replaced by the hash of the file it names."""
    if not facts_path or facts_path == "-":
        return "-"
    return facts_hashed(pathlib.Path(facts_path).read_text(encoding="utf-8"))


def stamp(cfg, addr, scripts_dir, facts_path, hints, ndev, nhidden):
    """The stamp (16 hex digits) of the harness ghidra.prepare makes from these inputs."""
    scripts = {p.name: p for p in pathlib.Path(scripts_dir).glob("*.java")}
    return stamp_of(cfg, addr, scripts, facts_text(facts_path), hints, ndev, nhidden)


SCOPED_FACTS = re.compile(r"^tcout_tail_exports .*\n?", re.M)  # facts lines a finder scope stamps per function


def facts_hashed(body):
    """facts_text of a facts file whose text is body (None: no facts file), without the file. A SCOPED_FACTS line is
    left out: the flag's scope (a "finder" scope) moves the stamps it can change, and only those."""
    if body is None:
        return "-"
    body = SCOPED_FACTS.sub("", body)
    return re.sub(r"^code_ranges (.*)$", lambda m: "code_ranges " + file_hash(m.group(1).strip()), body, flags=re.M)


def _regout_tail(cfg, addr):
    """In the tcout_tail_exports scope: a tail jump lands on addr's entry (tail_scope, cached per
    workspace)."""
    root = str(paths.ROOT)
    if root not in _FINDER_CACHE:
        _FINDER_CACHE[root] = tail_scope(paths.ROOT)
    return addr.lower() in _FINDER_CACHE[root]


def tail_scope(ws):
    """The addresses (8 hex digits) whose TcOut exports target.json "tcout_tail_exports" can change (TcOut.readAfter):
    a tail jump (bra, braf, jmp; core/regout's decoder and sites) lands on the function's entry and the function
    leaves some r4..r7 changed. A superset: an unresolved tail jump counts for every callee its meta.json lists."""
    from . import regout
    c, out = regout.Code(*regout.load(ws)), set()
    for f in c.bodies:
        for _, t, tail in c.sites(f):
            if tail:
                out.update(g for g in ([t] if t is not None else c.callees.get(f, []))
                           if g in c.bodies and ~c.kept(g) & regout.R4_7)
    return {f"{x:08x}" for x in out}


FINDERS = {"regout_tail": _regout_tail}  # a "flags" scope's "finder": with its flags on, the functions it can change
_FINDER_CACHE = {}


def scoped_hashes(cfg, addr, scripts):
    """{file name: hash} of the scripts whose "before" hash stands for this function (config "stamp_scopes"); None
    for a file the change added. A "flags" scope with a "finder" (FINDERS) holds, with its flags on, only for the
    functions the finder names: the rest keep their stamps (a superset finder, as TcOut's tail jumps)."""
    out = {}
    hook = target_hooks.get("stamp_scope")
    for sc in cfg.get("stamp_scopes") or []:
        files, flags = sc.get("scripts") or {}, sc.get("flags")
        if (not hook and flags is None) or any(n not in scripts or file_hash(scripts[n]) != h[1] for n, h in files.items()):
            continue
        on = any(cfg.get(f) for f in flags) if flags is not None else hook(cfg, addr, sc["scope"])
        if on and sc.get("finder"):
            on = FINDERS[sc["finder"]](cfg, addr)
        if not on:
            out.update({n: h[0] for n, h in files.items()})
    return out


def common(scripts, facts, before=None):
    """The inputs every function's stamp shares, hashed once: (sha256 fed the scripts and facts lines, the catalog's
    hash). stamp_of(shared=...) with it gives the same stamp as without, minus the 54 file stats and path resolves
    per function (PS2: 18 ms per function, 2 minutes per scan of its 6,541 matched harnesses). before: scoped_hashes
    of a function a scoped change does not reach (its scripts lines differ, so it cannot share them)."""
    before = before or {}
    h = hashlib.sha256()
    for name in sorted(scripts):
        if name in before and before[name] is None:
            continue  # a file a scoped change added, outside its scope: stamped as before the file was there
        h.update(f"script {name} {before.get(name) or file_hash(scripts[name])}\n".encode())
    h.update(f"facts {facts}\n".encode())
    return h, file_hash(paths.CATALOG)


def stamp_of(cfg, addr, scripts, facts, hints, ndev, nhidden, shared=None):
    """stamp() from its inputs: scripts {file name: path} (the *.java of the scripts folder), facts (facts_text).
    shared: common(scripts, facts) taken earlier (oracle_fresh.Stamper: one per scan), else taken now."""
    before = scoped_hashes(cfg, addr, scripts)
    head, catalog = common(scripts, facts, before) if before or not shared else shared
    h = head.copy()
    h.update(f"function {addr} dev {int(ndev)} hidden {int(nhidden)}\n".encode())
    if compare.spin_wait(cfg, addr):
        h.update(b"spin wait\n")
        if compare.spin_arg(cfg, addr) != "spin":
            h.update(f"spin words {compare.spin_arg(cfg, addr)[5:]}\n".encode())
    if hw_ops.arg(cfg, addr) != "-":
        h.update(f"hw ops {hw_ops.arg(cfg, addr)}\n".encode())
    if compare.run_flag(cfg, addr):
        h.update(f"run flag {compare.run_flag_arg(cfg, addr)}\n".encode())
    if sz_entry.arg(cfg, paths, addr) != "-":
        h.update(f"fpscr sz {sz_entry.arg(cfg, paths, addr)}\n".encode())
    if oracle_limits.arg(cfg, addr) != "-":
        h.update(f"limits {oracle_limits.arg(cfg, addr)}\n".encode())
    if ret_reg.fret_arg(paths.STATE, addr) != "-":
        h.update(f"float callees {ret_reg.fret_arg(paths.STATE, addr)}\n".encode())
    h.update(f"hints {file_hash(hints) if hints else '-'}\n".encode())
    table = fsca.FILE_SHA256 if cfg.get("fsca_table") else "-"  # every prepare makes it the table TcPrepare reads
    h.update(f"fsca {table} catalog {catalog}\n".encode())
    h.update(f"binary {cfg.get('binary_sha256')} ghidra {ghidra_identity(cfg)}\n".encode())
    return h.hexdigest()[:16]


def current(meta, now, harness_dir=None):
    """Why the harness with this meta.json is not current, or None: its stamp is not `now` (the stamp of today's
    inputs), or a callee record differs from the callee's own harness under harness_dir (default the project's)."""
    if not meta or meta.get("oracle_stamp") != now:
        return "oracle inputs changed"
    bad = callee_drift.drift(meta, callee_drift.Signatures(harness_dir or paths.HARNESS))
    if bad:
        return "callee records differ from the callees' harnesses: " + ", ".join(c for c, _, _ in bad)
    return None


def record(meta_path, value):
    """Write the stamp into a harness's meta.json."""
    meta = paths.read_json(meta_path)
    if meta is not None and meta.get("oracle_stamp") != value:
        meta["oracle_stamp"] = value
        paths.write_json(meta_path, meta)
