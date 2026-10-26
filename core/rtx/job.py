"""One function's facts for core/rtx/lift, from the registry and its harness: the prototype and where each of its
parameters lives, the raw signature's parameters, the callees' prototypes, and the draft names (core/draft_run)."""
from __future__ import annotations

import re

from .. import draft_run, readable, readable_abi
from .cast import Cast, Var
from .flow import Unsupported
from .lift import Job


_TYPES = {}


def _types(reg):
    """core/rtx/fields.Types of the registry's headers (one per process)."""
    from .fields import Types
    if "t" not in _TYPES:
        _TYPES["t"] = Types(readable.INCLUDE, reg.kind)
    return _TYPES["t"]


def make(cfg, reg, addr, meta, raw_text, spec):
    proto = reg.proto(addr, meta)
    ret, _, params = readable.parse_proto(proto)
    if readable.is_variadic(proto):
        raise Unsupported("a variadic function")
    try:
        storages = readable_abi.own_storages(reg, addr, proto, meta)
    except readable.RegistryError as e:
        raise Unsupported(f"own parameters: {e}") from e
    raw_params = [(p["name"], p["storage"], p.get("stack_offset", -1)) for p in meta["params"]]
    names = draft_run.names_for(cfg, reg, addr, meta, raw_text)
    job = Job(spec, proto, params, raw_params, meta.get("ret") or "void",
              names, reg.kind, reg.kind(ret))
    job.proto_storages = storages
    job.addr = addr
    job.size = lambda t: 4 if reg.kind(t) in ("i", "f") else 2 if reg.kind(t) in ("h", "H") else 1
    job.proto_ret = ret
    job.used_params = set()
    job.typedefs = set(reg.typedefs) | {"Vec3", "Mat4"}

    def pointer(t, depth=0):
        """Whether t is a pointer type, through typedefs (a function-pointer typedef too)."""
        t = re.sub(r"\b(?:const|volatile)\b", "", t or "").strip()
        if "*" in t:
            return True
        target = reg.typedefs.get(t)
        return target is not None and depth < 8 and pointer(target, depth + 1)
    job.pointer = pointer
    job.callee_types, job.callee_ret, job.callee_params = {}, {}, {}
    for c in meta["callees"]:
        try:
            cp = reg.proto(c["addr"], raw_sig=c["signature"])
        except readable.RegistryError:
            continue
        r, n, ps = readable.parse_proto(cp)
        job.callee_types[c["addr"]] = [t for t, _ in ps]
        job.callee_ret[n] = r
        job.callee_params[n] = [t for t, _ in ps]
    job.types = _types(reg)
    job.etypes, job.families = {}, {}
    job.global_types = {n: t for n, t, dims in names.globals.values() if not dims}
    from .. import draft_gate
    job.named_values = {v: ns for v, ns in draft_gate.named_values().items()}
    job.avoid = set(names.functions.values()) | {n for n, _, _ in names.globals.values()}
    by_storage = {st: (t, n) for st, (t, n) in zip(storages, params)}

    def stack_param(raw):
        off = next(o for r, s, o in raw_params if r == raw)
        hit = by_storage.get(f"stack+{off}")
        if hit is None:
            raise Unsupported(f"stack parameter {raw} (+{off}) has no prototype parameter")
        t, n = hit
        job.used_params.add(n)
        return Var(n) if t == "u32" else Cast("u32", Var(n)) if reg.kind(t) != "f" else Var(n)
    job.stack_param = stack_param
    job.stack_param_kind = lambda raw: reg.kind(by_storage.get(f"stack+{next(o for r, s, o in raw_params if r == raw)}",
                                                             ("u32", ""))[0])
    return job
