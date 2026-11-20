"""The two passes that made Too Human's 8237bd08 (22,655 instructions) take over 5 minutes to translate, now close to
linear, with the same answers:

- structure.candidates asked, for every goto, whether the span to its label is closed by walking the span
  (structure.region), so a function was quadratic per round and cubic over the rounds. spans.closed_at answers from
  one index per list and round.
- structure() ran every round over the whole function, and a function needed as many rounds as its deepest part
  (8237bd08: about 1000 over 63,000 statements). rounds.segments cuts it where the parts settle alone.
- Each round made every statement's summary anew (8237bd08: 50 million). structure.summary keeps them on the units.
- Slices copied per statement (us[k + 1:] per label, us[g + 1:p] per goto): quadratic in a long list.
- passes.State.kill scanned every known value for the killed name. pstate.State keeps a reverse index.

    python -m unittest tests.test_structure_speed
"""
import pathlib
import random
import sys
import unittest
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from xenontranslate import structure  # noqa: E402
from xenontranslate.structure import U  # noqa: E402


def nested_ifs(n):
    """`if (cK) goto L_K; xK` for K = 0..n-1, then the labels in reverse: n ifs, each inside the one before."""
    lines = []
    for k in range(n):
        lines += [f"    if (c{k}) goto L_{k};", f"    x{k} = 1;"]
    for k in reversed(range(n)):
        lines += [f"L_{k}:;", f"    y{k} = 1;"]
    return lines


class Speed(unittest.TestCase):
    def test_structure_work_is_quadratic_not_cubic(self):
        """Fails before the change: the span walks cost about n^3 / 3 summaries (2.7 million at n = 200)."""
        n = 200
        real, calls = structure.summary, [0]

        def counted(u, memo):
            calls[0] += 1
            return real(u, memo)
        structure.summary = counted
        try:
            out = structure.structure(nested_ifs(n))
        finally:
            structure.summary = real
        self.assertFalse(any("goto" in x for x in out))
        self.assertEqual(sum(x.strip().startswith("if (!c") for x in out), n)
        self.assertLess(calls[0], 10 * n * n, f"{calls[0]} summaries for {n} nested ifs")


def reference_closed(us, s, e, refs, memo, skip=None):
    """The walk spans.closed_at replaced (structure.closed before): every label in us[s:e] but us[skip]'s has all its
    gotos inside."""
    labels, gotos, brk = structure.region(us[s:e], memo)
    sk = {us[skip].name} if skip is not None else set()
    return all(gotos[x] == refs[x] for x in labels if x not in sk), brk


def random_list(rng, names, free, depth=0):
    """free: the label names not placed yet (a label is placed once, as in the translator's output)."""
    us = []
    for _ in range(rng.randint(3, 14)):
        r = rng.random()
        if r < 0.3 and free:
            us.append(U("label", free.pop(rng.randrange(len(free)))))
        elif r < 0.6:
            us.append(U("goto", rng.choice(names), rng.choice([None, "c"])))
        elif r < 0.7:
            us.append(U("op", lines=["@BREAK"], cond="c"))
        elif r < 0.8 and depth < 2:
            us.append(U(rng.choice(["loop", "if", "do"]), cond="c", a=random_list(rng, names, free, depth + 1)))
        elif r < 0.9:
            us.append(U("op", lines=["{", f"    if (c) goto {rng.choice(names)};", "}"]))
        else:
            us.append(U("op", lines=["x = 1;"]))
    return us


class SameAnswers(unittest.TestCase):
    def test_closed_at_matches_the_walk(self):
        from xenontranslate.spans import closed_at
        rng = random.Random(7)
        checked = 0
        for _ in range(300):
            names = [f"L_{k}" for k in range(rng.randint(2, 8))]
            free = list(names)
            pre = random_list(rng, names, free)
            us = random_list(rng, names, free)
            root = pre + [U("loop", a=us)]         # gotos outside us too
            memo = {}
            refs = structure.region(root, memo)[1]
            self.assertEqual(structure.all_refs(root, {}), refs)
            labs = [k for k, u in enumerate(us) if u.kind == "label"]
            for s in range(len(us) + 1):
                for e in range(s, len(us) + 1):
                    for skip in [None] + [k for k in labs if s <= k < e]:
                        self.assertEqual(closed_at(structure.summary, us, s, e, refs, memo, skip),
                                         reference_closed(us, s, e, refs, memo, skip), (s, e, skip))
                        checked += 1
        self.assertGreater(checked, 10000)

    def test_planted_wrong_index_is_caught(self):
        """A closed_at that forgets gotos from outside the list (the 'bad' prefix) must differ from the walk."""
        from xenontranslate import spans
        us = [U("label", "L_1"), U("op", lines=["x = 1;"])]
        root = [U("goto", "L_1"), U("loop", a=us)]
        memo = {}
        refs = structure.region(root, memo)[1]
        self.assertEqual(spans.closed_at(structure.summary, us, 0, 2, refs, memo), (False, False))
        pos, lo, hi, bad, brk = spans.span_index(structure.summary, us, refs, memo)
        memo[("span", id(us))] = (pos, lo, hi, [0] * len(bad), brk)
        self.assertNotEqual(spans.closed_at(structure.summary, us, 0, 2, refs, memo),
                            reference_closed(us, 0, 2, refs, memo))


def blocks(rng, n):
    """A function made of n small parts, each with labels of its own (a goto to another part's label now and then)."""
    lines, every = [], []
    for b in range(n):
        own = [f"L_{b}_{k}" for k in range(rng.randint(1, 4))]
        every += own
        free = list(own)
        for _ in range(rng.randint(2, 9)):
            r = rng.random()
            to = rng.choice(every) if rng.random() < 0.04 else rng.choice(own)
            if r < 0.25 and free:
                lines.append(f"{free.pop()}:;")
            elif r < 0.45:
                lines.append(f"    if (c{rng.randint(0, 3)}) goto {to};")
            elif r < 0.55:
                lines.append(f"    goto {to};")
            elif r < 0.65:
                lines.append(f"    {{ if (d) goto {to}; }}")
            elif r < 0.72:
                lines.append("    return 0;")
            elif r < 0.78:
                lines.append("    /* note */")
            else:
                lines.append(f"    x{rng.randint(0, 9)} = 1;")
        lines += [f"{x}:;" for x in free]
        if rng.random() < 0.7:
            lines.append(f"    if (e{b}) goto {rng.choice(own)};")
    return lines


def whole(lines):
    """structure() with the function settled whole, as before rounds.segments (the answer key)."""
    from xenontranslate import rounds
    real = rounds.segments
    rounds.segments = lambda us: [us]
    try:
        return structure.structure(lines)
    finally:
        rounds.segments = real


class Pieces(unittest.TestCase):
    def test_pieces_give_the_whole_answer(self):
        from xenontranslate import rounds
        rng = random.Random(5)
        cut = 0
        for _ in range(600):
            lines = blocks(rng, rng.randint(1, 12))
            cut += len(rounds.segments(structure.parse(lines))) > 1
            self.assertEqual(structure.structure(lines), whole(lines), "\n".join(lines))
        self.assertGreater(cut, 300)

    def test_deep_part_takes_its_rounds_alone(self):
        """Fails before the change: every round ran over all 400 small parts, for the 60 rounds the deep part needs."""
        n, parts = 60, 400
        lines = nested_ifs(n)
        for b in range(parts):
            lines += [f"L_b{b}:;", f"    x{b} = 1;", f"    if (e{b}) goto L_b{b};"]
        real, calls = structure.summary, [0]

        def counted(u, memo):
            calls[0] += 1
            return real(u, memo)
        structure.summary = counted
        try:
            out = structure.structure(lines)
        finally:
            structure.summary = real
        self.assertEqual(sum(x.strip().startswith("do {") for x in out), parts)
        self.assertLess(calls[0], 20 * n * n, f"{calls[0]} summaries")


def summary_anew(u, memo):
    """structure.summary before it kept its answers on the units: every one made anew each round (the answer key)."""
    k = id(u)
    if k in memo:
        return memo[k]
    if u.kind == "label":
        r = ({u.name}, Counter(), False)
    elif u.kind == "goto":
        r = (set(), Counter([u.name]), False)
    elif u.kind == "op":
        text = "\n".join(u.lines)
        r = (set(structure.LABEL_ANY.findall(text)), Counter(structure.GOTO_ANY.findall(text)), u.lines == ["@BREAK"])
    else:
        labels, gotos, brk = set(), Counter(), False
        for v in (u.a or []) + (u.b or []):
            ls, gs, b = summary_anew(v, memo)
            labels |= ls
            gotos += gs
            brk = brk or b
        r = (labels, gotos, brk and u.kind not in ("do", "loop", "while"))
    memo[k] = r
    return r


class KeptSummaries(unittest.TestCase):
    def test_kept_summaries_give_the_same_answer(self):
        rng = random.Random(9)
        cases = [nested_ifs(40)] + [blocks(rng, rng.randint(1, 12)) for _ in range(600)]
        real = structure.summary
        try:
            for lines in cases:
                structure.summary = real
                kept = structure.structure(lines)
                structure.summary = summary_anew
                self.assertEqual(kept, structure.structure(lines), "\n".join(lines))
        finally:
            structure.summary = real

    def test_op_text_scanned_once(self):
        """Fails before the change: each of the ~100 rounds scanned every statement's text again (about 20,000 scans)."""
        n = 100
        real, calls = structure.LABEL_ANY, [0]

        class Counted:
            def findall(self, text):
                calls[0] += 1
                return real.findall(text)

            def search(self, text):
                return real.search(text)
        structure.LABEL_ANY = Counted()
        try:
            out = structure.structure(nested_ifs(n))
        finally:
            structure.LABEL_ANY = real
        self.assertEqual(sum(x.strip().startswith("if (!c") for x in out), n)
        self.assertLessEqual(calls[0], 2 * n, f"{calls[0]} scans of {2 * n} statements")

    def test_planted_stale_summary_is_caught(self):
        """A kept summary that ignores a goto's new name (thread retargets gotos) must change some answer."""
        rng = random.Random(9)
        cases = [blocks(rng, rng.randint(1, 12)) for _ in range(600)]
        real = structure.summary

        def stale(u, memo):
            if u.kind == "goto" and u.sm is not None:
                memo[id(u)] = u.sm
                return u.sm
            return real(u, memo)
        differ = 0
        try:
            for lines in cases:
                structure.summary = stale
                a = structure.structure(lines)
                structure.summary = summary_anew
                differ += a != structure.structure(lines)
        finally:
            structure.summary = real
        self.assertGreater(differ, 0)


class OldState:
    """passes.State before the reverse index: the answer key."""

    def __init__(self):
        self.v, self.m = {}, {}

    def kill(self, name):
        self.v.pop(name, None)
        for k in [k for k, x in self.v.items() if name in x.vars()]:
            del self.v[k]
        for k in [k for k, (_, x) in self.m.items() if name in x.vars()]:
            del self.m[k]


class StateIndex(unittest.TestCase):
    def test_same_values_in_the_same_order(self):
        from xenontranslate.expr import Cast, Const, Var
        from xenontranslate.pstate import State
        rng = random.Random(11)
        names = [f"r{k}" for k in range(12)]

        def val():
            r = rng.random()
            return (Const(rng.randint(0, 9), "u64") if r < 0.3 else Var(rng.choice(names), "u64") if r < 0.7
                    else Cast("u32", Var(rng.choice(names), "u64")))
        for _ in range(200):
            new, old = State(), OldState()
            for _ in range(200):
                r = rng.random()
                if r < 0.4:
                    k, x = rng.choice(names), val()
                    new.kill(k); old.kill(k)  # noqa: E702
                    if k not in x.vars():
                        new.set_v(k, x); old.v[k] = x  # noqa: E702
                elif r < 0.6:
                    a, x = rng.randrange(0, 64, 4), ("wr32", val())
                    if a in new.m and rng.random() < 0.5:
                        new.del_m(a); del old.m[a]  # noqa: E702
                    else:
                        new.set_m(a, x); old.m[a] = x  # noqa: E702
                elif r < 0.62:
                    new.clear_m(); old.m.clear()  # noqa: E702
                elif r < 0.7:
                    o = new.copy()
                    o.kill(rng.choice(names))
                    new = new.meet(o)
                    old.v, old.m = dict(new.v), dict(new.m)
                else:
                    k = rng.choice(names)
                    new.kill(k); old.kill(k)  # noqa: E702
                self.assertEqual(list(new.v.items()), list(old.v.items()))
                self.assertEqual(list(new.m.items()), list(old.m.items()))
                rv = Counter((n, k) for k, x in new.v.items() for n in x.vars())
                self.assertEqual(rv, Counter((n, k) for n, ks in new.rv.items() for k in ks))


if __name__ == "__main__":
    unittest.main()
