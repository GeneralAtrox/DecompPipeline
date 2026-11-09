"""What passes.py may not move or merge: reads of the runtime's last-call record, and hardware-register loads.
Kept out of passes.py, which is over the 400-line cap (tools/sizes.py)."""
from . import expr as X
from . import passes as P
from .expr import Call


def reads_last(e):
    """e reads the runtime's record of the last call (tc_lastr4, tc_lastf, tc_lastr): a value the next call replaces."""
    if isinstance(e, Call) and e.fn.startswith("tc_last"):
        return True
    return any(reads_last(c) for c in P.children(e))


def has_call(e):
    if isinstance(e, Call) and not e.load and e.fn.startswith("tc_"):
        return True
    return any(has_call(c) for c in P.children(e))


def hw_loads(e):
    """e's loads from a constant hardware-register address (0xc8000000 and up: not RAM): volatile reads."""
    return [c for c in P.loads_in(e) if c.args and (P.const_addr(c.args[0]) or 0) >= 0xC8000000]


def in_arm(e, name):
    """name occurs in a ?: arm of e (evaluated only on one path)."""
    if isinstance(e, X.Cond) and (P.occurrences(e.a, name) or P.occurrences(e.b, name)):
        return True
    return any(in_arm(c, name) for c in P.children(e))
