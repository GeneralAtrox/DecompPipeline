"""Callee records against the callees' own harnesses. Pure (no core.paths): tools/doctor.py uses it.

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
"""
import json
import pathlib


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
