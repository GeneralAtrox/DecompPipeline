"""Which volatile registers a call leaves alone, for the port kit. The compiler's whole-program mode knows what each callee writes and uses that: 8216bff8 sets r4 to a
global's address, calls 8216bdf8 and then stores through r4, which the ABI says is gone. So a call clears only what the callee really can write.

The answer comes from each function's own code: the volatile registers it writes outside its entry block, joined with those of every function it calls, until nothing
changes. Anything not known to be harmless counts as writing them all: a pointer call, an import, a function the translator refuses or cannot find. Library functions the
kit replaces are read the same way, since their callers were compiled against the machine code: the host function touches none of the caller's registers.

Nearest existing: returns.py - works out each callee's result register; this works out the registers it leaves alone.
"""
import json

from .expr import Const
from .ir import CallS, Switch
from .translate import CALL_CLOBBERS, Clobber, FunctionTranslator, Signature, TranslateError

VOLATILE = frozenset(CALL_CLOBBERS) | {"r3", "f1"}


def own_writes(blocks):
    """(the volatile registers this function's own code writes, the addresses it calls; None stands for a pointer call)"""
    own, calls = set(), set()
    for b in blocks[1:]:
        for s in list(b.stmts) + (list(b.term.default) if isinstance(b.term, Switch) else []):
            if isinstance(s, CallS):
                calls.add(s.target.v & 0xFFFFFFFF if isinstance(s.target, Const) else None)
            elif not isinstance(s, Clobber):
                own |= s.writes() & VOLATILE
    return own, calls


def code_writes(ws, resolve):
    """{address: (own writes, calls)} for every catalog function and every direct call target resolve finds a body for."""
    out, work, seen = {}, sorted(ws.bodies), set(ws.bodies)
    while work:
        ad = work.pop()
        body = ws.bodies.get(ad) or resolve(ad)
        if body is None:
            continue
        try:
            blocks = FunctionTranslator(ws.dol, ad, Signature.generic_for(ad), ws.facts, body).build()
        except (TranslateError, Exception):  # noqa: BLE001  (untranslatable: it may write anything)
            continue
        out[ad] = own_writes(blocks)
        for c in out[ad][1] - seen - {None}:
            seen.add(c)
            work.append(c)
    return out


def solve(code):
    """{address: the volatile registers a call to it leaves alone}, for those that leave any."""
    written = {ad: set(own) for ad, (own, _) in code.items()}
    changed = True
    while changed:
        changed = False
        for ad, (_, calls) in code.items():
            w = written[ad]
            if len(w) == len(VOLATILE):
                continue
            n = len(w)
            for c in calls:
                w |= written.get(c, VOLATILE)
            changed |= len(w) != n
    return {ad: VOLATILE - w for ad, w in written.items() if len(w) != len(VOLATILE)}


def analyse(ws, cache_dir, resolve):
    """The answer of solve, kept under cache_dir by the same key as the return kinds' summaries."""
    from .returns import _write, summaries_key
    path = cache_dir / f"keeps_{summaries_key(ws, True)}.json"
    if path.exists():
        return {int(a, 16): set(r) for a, r in json.loads(path.read_text(encoding="utf-8")).items()}
    out = solve(code_writes(ws, resolve))
    _write(path, {f"{a:08x}": sorted(r) for a, r in out.items()})
    return out
