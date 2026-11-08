"""Callee records against the callees' own harnesses. Pure (core.paths only for call_regs' default workspace):
tools/doctor.py uses it.

A harness's meta.json "callees" records each callee called, with the registers the judge compares at each call
("regs") and a signature. TcPrepare takes that signature from Ghidra's database view of the callee, looked through
PASS_DEPTH - 1 levels, and that view can be wrong where the callee's own harness is right: the callee's harness
signature is looked through PASS_DEPTH levels and checked against the oracle when the callee is prepared and judged,
and later corrections (sigdrift, a re-prepare) land there, not in the Ghidra project. Toy Commander's 8c061498 and
8c0613cc take r4 (their harnesses: `void fn_8c061498(u32 param_r4)`), while Ghidra still gives callers
`f32 fn_8c061498(void)`; re-preparing a caller reproduced the stale record every time (batch 18).

So the callee's harness is authoritative: ghidra.prepare replaces each record's signature with the callee's own
harness signature (adopt(); the Ghidra one is kept as "ghidra_signature"), and a record is current while it equals
that signature (drift()). The compared registers stay as the caller's harness measured them. A callee without a
harness keeps Ghidra's signature. When a callee is re-prepared with another signature, its callers' records are
stale: oracle_stamp.current requires them current (regress --reuse and --parity re-prepare such a caller),
core/doctor fails on matched functions with a stale record, refine.preflight blocks their readable runs, and
ghidra.prepare lists the callers left stale (tools/reprepare_stale.py re-prepares them until nothing changes).

The judges also take a callee's harness signature at a call no record lists (call_regs, 2026-11-07; core/unchecked_args
says why): the registers it takes are compared, whether or not the caller set them, once it is proven (matched).
"""
import functools
import json
import pathlib

from . import filelock

TOOL = pathlib.Path(__file__).resolve().parents[1]


def _read(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def own_signature(meta):
    """The signature a current caller records for this function: its own harness signature."""
    return (meta or {}).get("signature")


class Signatures:
    """Each function's own harness signature under a harness folder, read once each (None: no harness)."""

    def __init__(self, harness_dir):
        self.root, self.cache = pathlib.Path(harness_dir), {}

    def __call__(self, addr):
        if addr not in self.cache:
            self.cache[addr] = own_signature(_read(self.root / addr / "meta.json"))
        return self.cache[addr]


def drift(meta, sig_of):
    """[(callee, recorded signature, the callee's own harness signature)] of meta's callee records that differ from
    the callee's own harness. sig_of: addr -> signature or None (Signatures); a callee without a harness has nothing
    to differ from."""
    out = []
    for c in (meta or {}).get("callees") or []:
        if c["addr"] == (meta or {}).get("addr"):
            continue  # a recursive call: the function's own record and signature come from one prepare
        own = sig_of(c["addr"])
        if own and own != c.get("signature"):
            out.append((c["addr"], c.get("signature"), own))
    return out


def adopt(meta, sig_of):
    """Set each callee record's signature to the callee's own harness signature (the Ghidra one stays in
    "ghidra_signature"). Changes meta in place; returns [(callee, Ghidra's signature, the harness's)] changed."""
    changed = []
    for c in meta.get("callees") or []:
        own = sig_of(c["addr"]) if c["addr"] != meta.get("addr") else None
        if own and own != c.get("signature"):
            c.setdefault("ghidra_signature", c.get("signature"))
            changed.append((c["addr"], c.get("signature"), own))
            c["signature"] = own
    return changed


def scan(harness_dir, addrs=None):
    """{caller: drift list} over the harnesses under harness_dir (only the callers in addrs, when given)."""
    root = pathlib.Path(harness_dir)
    sig_of, out = Signatures(root), {}
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if addrs is not None and d.name not in addrs:
            continue
        meta = _read(d / "meta.json")
        if meta:
            sig_of.cache.setdefault(d.name, own_signature(meta))
            bad = drift(dict(meta, addr=meta.get("addr", d.name)), sig_of)
            if bad:
                out[d.name] = bad
    return out


def callers_left_stale(harness_dir, callee, new_sig):
    """Callers whose record of callee is not new_sig (its signature after a prepare that changed it)."""
    out = []
    root = pathlib.Path(harness_dir)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.name == callee:
            continue
        meta = _read(d / "meta.json") or {}
        if any(c["addr"] == callee and c.get("signature") != new_sig for c in meta.get("callees") or []):
            out.append(d.name)
    return out


def describe(items, limit=12):
    """'caller: callee `old` now `new`; ...' for a {caller: drift} dict."""
    return "; ".join(f"{a}: " + ", ".join(f"{c} `{o}` now `{n}`" for c, o, n in bad)
                     for a, bad in sorted(items.items())[:limit])


# --- A callee's own signature at a call no record lists (core/compare, x360_compare, readable_check) ---
_led, _sig = {}, {}


def _matched(root):
    """The matched addrs of workspace root's ledger, re-read when the file changes."""
    p = pathlib.Path(root) / "state" / "ledger.json"
    try:
        st = p.stat()
    except OSError:
        return set()
    k = (st.st_mtime_ns, st.st_size)
    if _led.get(p, (None,))[0] != k:
        rows = json.loads(filelock.read_text(p)).get("functions") or {}
        _led[p] = (k, {a for a, r in rows.items() if r.get("status") == "matched"})
    return _led[p][1]


def signature_regs(t, index, root=None):
    """The registers callee t's own harness signature takes (meta.json params: argument registers, and sN for a stack
    word, r1+0x50+8N on the Xbox 360, @(4N,r15) on SH), when it is proven (t's ledger row "matched"); else None (no
    harness, not matched, or a parameter index cannot place). t: int or hex; index: the judge's REG_INDEX; root: the
    workspace (default core.paths.ROOT; without one, None)."""
    if root is None:
        try:
            from . import paths
            root = paths.ROOT
        except Exception:  # noqa: BLE001  (no workspace: a unit test of the comparison)
            return None
    t = t if isinstance(t, str) else f"{t:08x}"
    if t not in _matched(root):
        return None
    ppc = "f1" in index
    k = (str(root), t, ppc)
    if k not in _sig:
        try:
            meta = json.loads(filelock.read_text(pathlib.Path(root) / "harness" / t / "meta.json"))
        except (OSError, ValueError):
            meta = None
        regs = [] if meta is not None else None
        for q in (meta or {}).get("params") or []:
            st, off = q.get("storage"), q.get("stack_offset", -1)
            if st == "stack" and isinstance(off, int):
                n = ((off - 0x50) // 8 if off >= 0x50 and off % 8 == 0 else None) if ppc else \
                    (off // 4 if 0 <= off < 32 and off % 4 == 0 else None)
                st = None if n is None else f"s{n}"
            if st not in index:
                regs = None
                break
            regs.append(st)
        _sig[k] = regs
    return _sig[k]


def call_regs(callee_regs, t, ca, index, root=None, caller=None):
    """The registers a judge compares at a call of t: a listed callee's record's; else the CA line's (ca) and, when
    caller's body calls, branches or runs on to t directly (unchecked_args.direct; a call through a pointer keeps
    its CA line alone, as it always has), those t's proven signature takes, whether or not the caller set them."""
    if t in callee_regs:
        return callee_regs[t]
    if caller is not None and not _direct(root, caller, t):
        return list(ca)
    return list(ca) + [r for r in signature_regs(t, index, root) or [] if r not in ca]


_dir = {}


def _direct(root, caller, t):
    """Whether caller's body reaches t directly; True when that cannot be read (no body, image or workspace)."""
    try:
        from . import unchecked_args
        if root is None:
            from . import paths
            root = paths.ROOT
        ws = pathlib.Path(root)
        k = (str(ws), caller)
        if k not in _dir:
            pj, cfg = unchecked_args._cfg(ws)
            w, big = unchecked_args.WORDS[pj["target"]]
            _dir[k] = unchecked_args.direct(cfg, caller, ws / "state" / "function_bodies.txt", w, big,
                                            ws / "catalog_inputs.json")
    except Exception:  # noqa: BLE001
        return True
    return _dir[k] is None or (t if isinstance(t, str) else f"{t:08x}") in _dir[k]


@functools.lru_cache(maxsize=None)
def index_of(target):
    """The judge's REG_INDEX of a target: x360_compare's on the Xbox 360, core/compare's on SH."""
    if target == "xbox360_ppc":
        import importlib.util
        spec = importlib.util.spec_from_file_location("ua_x360_compare", TOOL / "targets" / target / "x360_compare.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.REG_INDEX
    from . import compare
    return compare.REG_INDEX


def unlisted(h):
    """{target hex: [CA regs, or None without a CA line, per call]} of harness folder h's calls no record lists; None
    without meta.json or expected.txt."""
    try:
        meta = json.loads(filelock.read_text(h / "meta.json"))
        lines = filelock.read_text(h / "expected.txt").splitlines()
    except (OSError, ValueError):
        return None
    listed = {(c["addr"] if isinstance(c, dict) else c).lower() for c in meta.get("callees") or []}
    out = {}
    for i, x in enumerate(lines):
        t = f"{int(x.split()[1], 16):08x}" if x.startswith("CALL ") else None
        if t and t not in listed:
            nx = lines[i + 1] if i + 1 < len(lines) else ""
            out.setdefault(t, []).append(nx.split()[1:] if nx.rstrip() == "CA" or nx.startswith("CA ") else None)
    return out


def sig_key(root, h, target):
    """The proven signatures of the unlisted targets harness folder h calls: the verdict memo's input (a caller's PASS
    from before its callee was proven compared fewer registers)."""
    idx = index_of(target)
    return ";".join(f"{t}={signature_regs(t, idx, root)}" for t in sorted(unlisted(h) or {}))
