"""The constant propagator's state (passes.Propagator), in its own file (the size cap on passes.py)."""


class State:
    """Var -> Const/Var value; stack slot address -> (store fn, value). Written only through set_v / set_m / del_m /
    clear_m, which keep the reverse index (variable -> the keys of v and of m whose value reads it): kill is then the
    keys that read the name, not a scan of the whole state. That scan made propagation quadratic in a big function's
    live values (Too Human's 8237bd08, 22,655 instructions, took over 5 minutes). Dict order is the same as before,
    so read()'s first overlapping stack slot is too."""
    __slots__ = ("v", "m", "rv", "rm")

    def __init__(self, v=None, m=None):
        self.v, self.m, self.rv, self.rm = {}, {}, {}, {}
        for k, x in (v or {}).items():
            self.set_v(k, x)
        for k, x in (m or {}).items():
            self.set_m(k, x)

    def copy(self):
        s = State.__new__(State)
        s.v, s.m = dict(self.v), dict(self.m)
        s.rv = {n: set(ks) for n, ks in self.rv.items()}
        s.rm = {n: set(ks) for n, ks in self.rm.items()}
        return s

    def meet(self, o):
        v = {k: x for k, x in self.v.items() if k in o.v and o.v[k] == x}
        m = {k: x for k, x in self.m.items() if k in o.m and o.m[k][0] == x[0] and o.m[k][1] == x[1]}
        return State(v, m)

    def __eq__(self, o):
        return self.v == o.v and self.m == o.m

    def set_v(self, k, x):
        if k in self.v:
            self._unindex(self.rv, k, self.v[k])
        self.v[k] = x
        for n in x.vars():
            self.rv.setdefault(n, set()).add(k)

    def set_m(self, k, x):
        if k in self.m:
            self._unindex(self.rm, k, self.m[k][1])
        self.m[k] = x
        for n in x[1].vars():
            self.rm.setdefault(n, set()).add(k)

    def del_m(self, k):
        self._unindex(self.rm, k, self.m.pop(k)[1])

    def clear_m(self):
        self.m.clear()
        self.rm.clear()

    @staticmethod
    def _unindex(r, k, x):
        for n in x.vars():
            ks = r.get(n)
            if ks is not None:
                ks.discard(k)
                if not ks:
                    del r[n]

    def kill(self, name):
        if name in self.v:
            self._unindex(self.rv, name, self.v.pop(name))
        for k in list(self.rv.get(name, ())):
            self._unindex(self.rv, k, self.v.pop(k))
        for k in list(self.rm.get(name, ())):
            self.del_m(k)

