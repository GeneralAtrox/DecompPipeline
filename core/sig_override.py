"""Raw return-type overrides (batch 27): a designer corrects a raw function's return type with the key "raw_ret"
in its readable/names entry: `"8c07b738": {"proto": "s32 Lib_WindowTrackMouse(LibWindow *window)", "raw_ret": "s32"}`.

The oracle records both result registers of every vector (expected.txt `RET r0 fr0`), so the raw ret only decides
which one the judge compares (core/compare.compare_return): r0 (u32, s32), fr0 (f32) or none (void). An override
therefore needs no new vectors. It is applied to harness/ADDR/meta.json ("ret", "signature", and "ret_override":
{"from", "to"}) by ghidra.prepare after every TcPrepare, and by tools/sig_override.py --apply to existing harnesses,
which also rewrites the matched C (core/sig_rewrite.py), re-records the callers' callee records and re-judges both.

An override is refused (validate) when:
- the address is not a function of the catalog;
- the kind is not void, u32, s32 or f32;
- it is void while the raw ret is not, unless core/ret_use shows that no call site reads the result (void drops the
  return comparison: it must never loosen the judge).
"""
import json
import pathlib
import re

KINDS = ("void", "u32", "s32", "f32")
KEY = "raw_ret"


def overrides(names_dir):
    """{addr: (kind, names file name)} from every readable/names/*.json functions entry with "raw_ret"."""
    out = {}
    d = pathlib.Path(names_dir)
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for addr, e in (data.get("functions") or {}).items():
            if isinstance(e, dict) and KEY in e:
                out[addr.lower().removeprefix("0x").rjust(8, "0")] = (e[KEY], f.name)
    return out


def with_ret(sig, kind):
    """A raw signature text with its return type replaced: `f32 fn_8c07b738(s32 param_3)` -> `s32 fn_...`."""
    m = re.match(r"^\s*(.*?)\s*\b(fn_[0-9a-f]{8}\s*\()", sig)
    return f"{kind} {sig[m.start(2):]}" if m else sig


def apply_meta(meta, kind):
    """Set meta's raw ret to kind (in place). Returns True when meta changed. The first raw ret is kept in
    ret_override["from"], so a second override or a re-apply is idempotent."""
    if kind not in KINDS:
        raise ValueError(f"raw_ret {kind!r}: not one of {', '.join(KINDS)}")
    orig = (meta.get("ret_override") or {}).get("from", meta.get("ret"))
    if meta.get("ret") == kind and (meta.get("ret_override") or orig == kind):
        return False
    if orig == kind:  # the override was removed back to the raw one
        meta.pop("ret_override", None)
    else:
        meta["ret_override"] = {"from": orig, "to": kind}
        meta.pop("ret_reg", None)  # the override is the ABI register of its kind
    meta["ret"] = kind
    meta["signature"] = with_ret(meta["signature"], kind)
    return True


def apply_from_names(meta, addr, names_dir):
    """ghidra.prepare's hook: apply the names entry's raw_ret (if any) to a freshly prepared meta. A kind that is not
    valid raises (the prepare fails naming it) instead of silently judging with the wrong register."""
    o = overrides(names_dir).get(addr)
    return apply_meta(meta, o[0]) if o else False


def validate(addr, kind, catalog, meta, evidence):
    """Why the override cannot be applied, or None. catalog: the set of function addresses; meta: the function's
    harness meta (None: none yet); evidence: () -> core/ret_use evidence dict (only called for void)."""
    if addr not in catalog:
        return f"{addr}: no function at this address in the catalog"
    if kind not in KINDS:
        return f"{addr}: raw_ret {kind!r} is not one of {', '.join(KINDS)}"
    raw = ((meta or {}).get("ret_override") or {}).get("from", (meta or {}).get("ret"))
    if kind == "void" and raw not in (None, "void"):
        ev = evidence()
        if not ev["sites"] or ev["unknown"] or ev["r0"] or ev["fr0"] or ev["address_taken"]:
            return (f"{addr}: raw_ret void would drop the {raw} return comparison, but the call sites do not show the "
                    f"result unused ({ev['sites']} site(s), {ev['unknown']} unknown, {ev['r0']} read r0, {ev['fr0']} "
                    f"read fr0, address taken: {ev['address_taken']})")
    return None


def readable_mismatch(proto_ret, kind):
    """A readable prototype's return type that cannot carry the raw kind, or None (pointers and integer types are
    r0, float is fr0, void none)."""
    t = re.sub(r"\b(const|volatile)\b", "", proto_ret).strip()
    cls = "void" if t == "void" else "f32" if t in ("f32", "float") else "int"
    want = "void" if kind == "void" else "f32" if kind == "f32" else "int"
    return None if cls == want else f"the readable prototype returns `{proto_ret}`, the raw ret is now {kind}"
