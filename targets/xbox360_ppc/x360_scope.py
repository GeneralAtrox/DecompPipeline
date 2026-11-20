"""Which functions a scoped oracle change reaches (core/oracle_stamp, target.json "stamp_scopes").

A scope names a test on an instruction word, or (FLAG_SCOPES) config flags. A function is in scope when a word of its own body (Ghidra's bodies,
state/function_bodies.txt) passes the test: the oracle mocks callees and never runs them (TcOracle), so their code
does not matter. An unknown scope, or a function with no recorded body, counts as in scope: its stamp follows the
change, which only costs a re-prepare. The scope "exit_state" is no word test: TcExit's seeded runs happen only in
a prepare with target.json "exit_state" on, so with it off no function is in scope.
The scope "gate" is no word test either: the functions in it are those TcGateScan.Seeds, the decision the prepare itself
takes, puts in the GATE class. Ghidra's TcGateScan runs that decision on every function (tools/gate_scan.py) and
writes the list, state/gate_scope.txt, bound to the scripts and the catalog it was made from; without a list, or with
one made from other scripts or another catalog, every function is in scope.
"""
import hashlib
import pathlib
import struct
import sys

from core import paths, strict_judge


def vector_word(w):
    """A VMX / VMX128 instruction (TcBase.vmxOpAt's functions are those with one): opcodes 4, 5 and 6, and the vector
    loads and stores, which sit in opcode 31 and which xenontranslate's decoder knows."""
    if w >> 26 in (4, 5, 6):
        return True
    here = str(pathlib.Path(__file__).resolve().parent / "translator")
    if here not in sys.path:
        sys.path.append(here)
    from xenontranslate.decode import decode, VECTOR_OPS
    return decode(w, 0).op in VECTOR_OPS


SCOPES = {
    # TcVectors' extra tries for a function with vector words (VMX_TRIES)
    "vmx": vector_word,
    # TcFixups "single" (the shared TcSingle): the single-precision arithmetic (primary opcode 59: fadds, fsubs, fmuls,
    # fdivs, fsqrts, the single multiply-adds) and frsp (63, XO 12)
    "single_fp": lambda w: w >> 26 == 59 or (w >> 26 == 63 and (w >> 1) & 0x3FF == 12),
}

# A change that does nothing while these flags are all off (each function's harness byte for byte): with them
# off no function is in scope, with any on every one is. The pre-wave trust items (TcDirs, TcBound, TcStrict).
FLAG_SCOPES = {"prewave_flags": ("branch_coverage", "targeted_vectors", "mock_junk", "mock_writes",
                                  "call_snapshots", "exit_frame")}

_cache = {}


def _bodies():
    path = paths.FUNCTION_BODIES
    key = ("bodies", str(path), path.stat().st_mtime_ns if path.exists() else 0)
    if key not in _cache:
        spans = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                t = line.split()
                if len(t) >= 3 and not t[0].startswith("#"):
                    spans.setdefault(t[0].lower(), []).append((int(t[1], 16), int(t[2], 16)))
        _cache[key] = spans
    return _cache[key]


def _image(cfg):
    key = ("image", str(cfg["binary_path"]))
    if key not in _cache:
        _cache[key] = pathlib.Path(cfg["binary_path"]).read_bytes()
    return _cache[key]


GATE_LIST = "gate_scope.txt"  # under state/: TcGateScan's list (tools/gate_scan.py)


def scripts_id():
    """16 hex digits naming the scripts a prepare runs by content (paths.target_scripts and the project's
    TcInputs.java: what paths.sync_scripts assembles)."""
    from core import oracle_stamp
    want = dict(paths.target_scripts(), **{"TcInputs.java": paths.INPUTS})
    h = hashlib.sha256()
    for n in sorted(want):
        h.update(f"{n} {oracle_stamp.file_hash(want[n])}\n".encode())
    return h.hexdigest()[:16]


def gate_header():
    """The first line of state/gate_scope.txt: the scripts and the catalog the list holds for."""
    from core import oracle_stamp
    return f"# TcGateScan scripts {scripts_id()} catalog {oracle_stamp.file_hash(paths.CATALOG)}"


def gate_list():
    """The entries TcGateScan put in GATE (lower-case hex), or None: no list, or one made from other scripts or
    another catalog."""
    path = paths.STATE / GATE_LIST
    if not path.exists():
        return None
    key = ("gate", str(path), path.stat().st_mtime_ns, gate_header())
    if key not in _cache:
        lines = path.read_text(encoding="utf-8").splitlines()
        ok = len(lines) >= 2 and lines[0] == key[3] and lines[1].startswith("# functions ")
        _cache[key] = {t.strip().lower() for t in lines[2:] if t.strip()} if ok else None
    return _cache[key]


def in_scope(cfg, addr, scope):
    if scope == "unlisted_call_args":  # TcTrack.of: CA lines at calls no callee record lists (core/unchecked_args)
        from core import unchecked_args
        return unchecked_args.hook(cfg, addr, paths.FUNCTION_BODIES, 4, True, paths.ROOT / "catalog_inputs.json")
    if scope in FLAG_SCOPES:
        return any(cfg.get(f) for f in FLAG_SCOPES[scope])
    if scope == "exit_state":
        return strict_judge.on(cfg, "exit_state")
    if scope == "gate":
        listed = gate_list()
        return listed is None or addr.lower() in listed
    test = SCOPES.get(scope)
    spans = _bodies().get(addr.lower())
    if test is None or not spans:
        return True
    image, base = _image(cfg), int(str(cfg["load_address"]), 16)
    for lo, hi in spans:
        for a in range(lo, hi, 4):
            off = a - base
            if 0 <= off <= len(image) - 4 and test(struct.unpack_from(">I", image, off)[0]):
                return True
    return False
