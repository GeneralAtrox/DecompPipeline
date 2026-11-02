"""Which result a mocked call gives (choose_calls) and the other one, r3 or f1, after it (other_result), out of passes.py for the 400-line cap."""
from .passes import Assign, Call, CallS, Const, live_out, liveness


def other_result(fn):
    """The mocked callee sets both r3 and f1 (tc_rt.h): the call's kind names the one the C receives, and the other
    one, when it is read after the call (a later pointer call passes it, the function returns it), comes from
    tc_lastf() / tc_lastr(). Before this, the other register kept its value from before the call."""
    live_in = liveness(fn)
    for b in fn.blocks:
        live = live_out(fn, live_in, b)
        out = []
        for s in reversed(b.stmts):
            if isinstance(s, CallS) and s.kind in ("r", "f") and not s.keep_other:
                name, fn_, ty = ("f1", "tc_lastf", "f64") if s.kind == "r" else ("r3", "tc_lastr", "u64")
                if name in live:
                    out.append(Assign(name, Call(fn_, [], ty, load=True)))
                    live.discard(name)
            out.append(s)
            live -= s.writes()
            live |= s.reads()
        out.reverse()
        b.stmts = out


def choose_calls(fn, notes, returns=None, port=False):
    """tc_call (r3) or tc_callf (f1) for each call: the callee's known result register (DP's meta, or the
    executable-wide returns.analyse), else whichever is read after it, r3 when both are. port: a callee whose own code
    writes neither (void) leaves r3 and f1 as they were, as on the machine (kind 'v')."""
    live_in = liveness(fn)
    for b in fn.blocks:
        live = live_out(fn, live_in, b)
        for s in reversed(b.stmts):
            if isinstance(s, CallS) and (s.kind is None or port):
                r, f = "r3" in live, "f1" in live
                known = (returns or {}).get(s.target.v & 0xFFFFFFFF) if isinstance(s.target, Const) else None
                if known in ("r", "f"):
                    s.kind = known
                    s.note = None if (r if known == "r" else f) else "dead"
                    live -= s.writes()
                    live |= s.reads()
                    continue
                if port and known == "v":
                    s.kind = "v"
                    live |= s.reads()
                    continue
                if r and f:
                    notes.append(f"call at {s.addr:08x}: both r3 and f1 are read after it; only r3 is the result")
                s.kind = "f" if f and not r else "r"
                s.note = "dead" if not (r or f) else None
            live = (live - s.writes()) | s.reads()
    other_result(fn)
