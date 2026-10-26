"""Which register each function of the executable returns its result in: r3 (an integer or pointer), f1 (a float),
or neither. DP's meta gives this for the functions it has harnesses for; for the rest a call site cannot tell from
its own code alone, since the generic form passes every argument register and so both r3 and f1 look read after
nearly every call.

The answer comes from each function's own body. Forward over its blocks, it tracks which of r3 / f1 its own code
wrote last: an instruction writing r3 makes it 'r', one writing f1 makes it 'f'. A call leaves only the callee's
result meaningful (the ABI's volatile registers are garbage after it), so it sets the state to the callee's own
answer ('r', 'f', or nothing for a void or unknown callee). The states that reach the returns decide:
  only 'r' (or nothing)  -> 'r'          only 'f' (or nothing)  -> 'f'
  nothing at all         -> 'v'          both 'r' and 'f'       -> '?' (the caller keeps its own rule)
Callees' answers feed their callers, so the whole executable is iterated until nothing changes. A value the caller
left untouched in r3 (an identity function) counts as nothing: the analysis never guesses a result that the
function's own code did not produce.
"""
import hashlib
import json
import os
import pathlib

from .ir import CallS, Ret, Switch
from .translate import Clobber, FunctionTranslator, Signature, TranslateError

ROUNDS = 12


def summarise(blocks):
    """Per block: the events in order, ('w', 'r'|'f') for an own write or ('c', target address or None) for a call;
    then the successor labels and whether the block returns."""
    out = {}
    for b in blocks:
        ev = []
        sts = list(b.stmts) + (list(b.term.default) if isinstance(b.term, Switch) else [])
        for s in sts:
            if isinstance(s, CallS):
                t = s.target
                ev.append(("c", t.v & 0xFFFFFFFF if hasattr(t, "v") else None))
            elif not isinstance(s, Clobber):
                w = s.writes()
                if "r3" in w:
                    ev.append(("w", "r"))
                if "f1" in w:
                    ev.append(("w", "f"))
            if isinstance(s, Ret):
                ev.append(("ret", None))
        succ = list(b.term.succs())
        if isinstance(b.term, Ret):
            ev.append(("ret", None))
        out[b.label] = (ev, succ)
    return out


def answer(summary, entry, kinds):
    """The function's answer given its callees' current answers."""
    state = {entry: {None}}
    work = [entry]
    rets = set()
    while work:
        lab = work.pop()
        ev, succ = summary[lab]
        cur = set(state[lab])
        for k, x in ev:
            if k == "w":
                cur = {x}
            elif k == "c":
                c = kinds.get(x)
                cur = {c if c in ("r", "f") else None}
            else:
                rets |= cur
        for s in succ:
            if s not in summary:
                continue
            old = state.get(s, set())
            if not cur <= old:
                state[s] = old | cur
                work.append(s)
    rets.discard(None)
    if not rets:
        return "v"
    return rets.pop() if len(rets) == 1 else "?"


KEEP = 4  # cache files of each kind kept per cache folder (other checkouts' translator code may share the folder)
_CODE = []


def code_hash():
    """This translator's own code (every ppctranslate/*.py): a cache made by other code is never read."""
    if not _CODE:
        h = hashlib.sha256()
        for p in sorted(pathlib.Path(__file__).resolve().parent.glob("*.py")):
            h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
        _CODE.append(h.hexdigest()[:16])
    return _CODE[0]


def summaries_key(ws):
    """The executable, the function bodies, the target facts and the translator's code."""
    return hashlib.sha256(pathlib.Path(ws.binary).read_bytes() + repr(ws.facts).encode() +
                          json.dumps(sorted(ws.bodies.items())).encode() + code_hash().encode()).hexdigest()[:16]


def summaries_path(ws, cache_dir):
    return pathlib.Path(cache_dir) / f"summaries_{summaries_key(ws)}.json"


def _write(path, data):
    """Whole or absent (workers may write it at once); older files of its kind beyond KEEP go."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
    kind = path.name.split("_", 1)[0]
    old = sorted(path.parent.glob(f"{kind}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP:]
    for p in old:
        p.unlink(missing_ok=True)


def code_summaries(ws, cache_dir=None):
    """{address: (entry label, summary)} of every function's own code, each built in the generic form (no meta):
    the slow part of analyse. Cached under cache_dir by the executable, the function bodies, the target facts and
    the translator's code, so it is built once per workspace (a new harness's meta changes only analyse's rounds)."""
    cache = summaries_path(ws, cache_dir) if cache_dir else None
    if cache and cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))  # (events as [kind, value] lists: answer reads either)
        return {int(a, 16): (e, sm) for a, (e, sm) in sorted(rows.items())}
    out = {}
    for ad, body in sorted(ws.bodies.items()):
        try:
            ft = FunctionTranslator(ws.dol, ad, Signature.generic_for(ad), ws.facts, body)
            blocks = ft.build()
        except (TranslateError, Exception):  # noqa: BLE001  (untranslatable: no answer)
            continue
        out[ad] = (blocks[0].label, summarise(blocks))
    if cache:
        _write(cache, {f"{a:08x}": v for a, v in out.items()})
    return out


def _fixed(ws):
    """DP's meta answers: {address: kind} of every function whose harness signature is not generic."""
    out = {}
    for ad in sorted(ws.bodies):
        sig = ws.signature(ad)
        if not sig.generic:
            out[ad] = "v" if sig.ret == "void" else "f" if sig.ret in ("f32", "f64") else "r"
    return out


def kinds_path(ws, cache_dir, fixed=None):
    """Where analyse keeps its answer: by the summaries' key and DP's meta answers (every input of the rounds)."""
    fixed = _fixed(ws) if fixed is None else fixed
    f = hashlib.sha256(json.dumps(sorted(fixed.items())).encode()).hexdigest()[:16]
    return pathlib.Path(cache_dir) / f"kinds_{summaries_key(ws)}_{f}.json"


def cached_kinds(ws, cache_dir):
    """analyse's answer when cache_dir holds it for these very inputs, else None (no summaries read)."""
    try:
        rows = json.loads(kinds_path(ws, cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {int(a, 16): k for a, k in rows.items()}


def analyse(ws, cache_dir=None, log=None):
    """{address: 'r' | 'f' | 'v' | '?'} for every function in the workspace; DP's meta answers where it has one.
    Kept under cache_dir (cached_kinds): the rounds over every function ran again for each function translated."""
    fixed = _fixed(ws)
    out = kinds_path(ws, cache_dir, fixed) if cache_dir else None
    if out is not None and out.exists():
        got = cached_kinds(ws, cache_dir)
        if got is not None:
            return got
    summaries, entries = {}, {}
    code = code_summaries(ws, cache_dir)
    for ad in sorted(ws.bodies):
        if ad not in fixed and ad in code:
            entries[ad], summaries[ad] = code[ad]
    kinds = dict(fixed)
    changed = []
    for rnd in range(ROUNDS):
        changed = []
        for ad, sm in summaries.items():
            k = answer(sm, entries[ad], kinds)
            if kinds.get(ad) != k:
                kinds[ad] = k
                changed.append(ad)
        if log:
            log(f"return kinds, round {rnd + 1}: {len(changed)} changed")
        if not changed:
            break
    for ad in changed:                  # still flipping (mutual recursion): no answer
        kinds[ad] = "?"
    if out is not None:
        _write(out, {f"{a:08x}": k for a, k in kinds.items()})
    return kinds


def ret_type(kind):
    return {"r": "u32", "f": "f64", "v": "void"}.get(kind)


__all__ = ["analyse", "ret_type", "Signature"]
