"""The oracle-input stamp of a harness: a hash of everything the Ghidra oracle reads to make one function's harness.

ghidra.prepare writes it into the harness's meta.json ("oracle_stamp"). Two harnesses with the same stamp were made
from the same inputs, so a run that only needs a current harness (tools/regress.py --parity, --reuse) reuses one
whose stamp matches and re-prepares the rest. The inputs:
- the oracle scripts (every *.java in the scripts folder TcPrepare runs from: the target's scripts, the harness
  generator TcPrepare/TcVectors/TcOracle, and the project's TcInputs.java);
- the facts file (calling convention, memory map, oracle switches and deadlines from config), with the code ranges
  file it names hashed by content (its path differs between a project and a private copy);
- the function, its dev and hidden vector counts (the seed is the address), its hints file;
- the fsca table, the catalog (the Ghidra project's function bodies), the binary's sha256 and the Ghidra install.
A wall-clock deadline is an input too (the facts file holds it), but how far a loaded machine gets before it is not:
the stamp says which inputs made a harness, not that a run under load would make the same one.
The callee records are an input too: each records the callee's signature the harness was built with, and a harness
is current only while they equal the callees' own harness signatures (current(); core/callee_drift.py).
"""
import hashlib
import pathlib
import re
import threading

from . import callee_drift, paths

_FILE_CACHE = {}
_LOCK = threading.Lock()


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


def facts_text(facts_path):
    """The facts file's text with the code ranges path replaced by the hash of the file it names."""
    if not facts_path or facts_path == "-":
        return "-"
    return facts_hashed(pathlib.Path(facts_path).read_text(encoding="utf-8"))


def stamp(cfg, addr, scripts_dir, facts_path, hints, ndev, nhidden):
    """The stamp (16 hex digits) of the harness ghidra.prepare makes from these inputs."""
    scripts = {p.name: p for p in pathlib.Path(scripts_dir).glob("*.java")}
    return stamp_of(cfg, addr, scripts, facts_text(facts_path), hints, ndev, nhidden)


def facts_hashed(body):
    """facts_text of a facts file whose text is body (None: no facts file), without the file."""
    if body is None:
        return "-"
    return re.sub(r"^code_ranges (.*)$", lambda m: "code_ranges " + file_hash(m.group(1).strip()), body, flags=re.M)


def stamp_of(cfg, addr, scripts, facts, hints, ndev, nhidden):
    """stamp() from its inputs: scripts {file name: path} (the *.java of the scripts folder), facts (facts_text)."""
    h = hashlib.sha256()
    for name in sorted(scripts):
        h.update(f"script {name} {file_hash(scripts[name])}\n".encode())
    h.update(f"facts {facts}\n".encode())
    h.update(f"function {addr} dev {int(ndev)} hidden {int(nhidden)}\n".encode())
    h.update(f"hints {file_hash(hints) if hints else '-'}\n".encode())
    h.update(f"fsca {file_hash(paths.FSCA_TABLE)} catalog {file_hash(paths.CATALOG)}\n".encode())
    h.update(f"binary {cfg.get('binary_sha256')} ghidra {cfg.get('ghidra_home')}\n".encode())
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
