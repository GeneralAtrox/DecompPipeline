"""vectors.txt line letters (Xbox 360): no two readers or writers claim the same letter.

x360-land found master's stricter judge (TcStrict, tc_strict.h) and the exit state (TcExit, runner.c) both using "N";
runner.c's first branch won and the exit seeds were never read. The exit seeds are now "Q". This test takes the letters
from the code itself, not from a list:
  writers: what TcPrepare writes to vectors.txt, followed through TcStrict.header, TcVectors' vout appends, TcStrict.vec
           (its "vec" builder) and TcOracle.Result.exitIn (TcExit's vectorLines): each letter has one owning class;
  readers: runner.c's main loop (its if / else-if chain on line[0], where a branch that calls tcrt_strict_line hands the
           line to tc_strict.h), tc_strict.h's tcrt_strict_line and x360_compare.parse_vectors: a letter is tested in
           one branch of a chain only, and one owner (runner.c or tc_strict.h) reads it.
The Plant case shows the check fails on the merge's first text, where TcExit and runner.c both used "N".
usage: python tests/test_x360_vector_letters.py
"""
import pathlib
import re
import sys
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360  # noqa: E402

SCRIPTS = X360 / "ghidra_scripts"
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
LINE_START = re.compile(r'(?:^|\\n)([A-Z]{1,2})(?= |\\n|$)')


def sources():
    out = {p.name: p.read_text(encoding="utf-8") for p in SCRIPTS.glob("*.java")}
    for n in ("runner.c", "tc_strict.h"):
        out[n] = (X360 / "runtime" / n).read_text(encoding="utf-8")
    out["x360_compare.py"] = (X360 / "x360_compare.py").read_text(encoding="utf-8")
    return out


def letters(code):
    """The line letters the string literals of a piece of Java start: "V ", "\\nR", "E\\n"."""
    out = set()
    for lit in LITERAL.findall(code):
        out.update(LINE_START.findall(lit))
    return out


def body(src, name):
    """The text of method `name` (its first definition) in a Java source, braces matched."""
    m = re.search(r"\b(?:static\s+)?String\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", src)
    if not m:
        raise AssertionError(f"no method {name}")
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def statements(src, builder):
    """The statements that append to `builder` (a bare name, or after a dot)."""
    return [s for s in src.split(";") if re.search(r"(?:^|[\s.(])" + re.escape(builder) + r"\.append\(", s.strip())]


def writer_claims(src):
    """{letter: {owning class}} of what TcPrepare writes to vectors.txt."""
    claims = {}

    def add(owner, ls):
        for c in ls:
            claims.setdefault(c, set()).add(owner)

    def method_letters(cls, meth):
        code = body(src[cls + ".java"], meth)
        ret = re.search(r"return\b[^;]*?\.(\w+)\.toString\(\)", code)
        if ret:   # a method that returns a builder field: what the class appends to that field
            return letters(";".join(statements(src[cls + ".java"], ret.group(1))))
        return letters(code)

    expr = re.search(r'resolve\("vectors\.txt"\),\s*\((.*?)\)\.getBytes', src["TcPrepare.java"], re.S).group(1)
    for term in (t.strip() for t in expr.split("+")):
        call = re.fullmatch(r"(Tc\w+)\.(\w+)\(.*\)", term)
        if call:
            add(call.group(1), method_letters(call.group(1), call.group(2)))
        elif re.fullmatch(r"\w+", term):   # a builder (vout): every script's appends to it
            for name, text in src.items():
                if not name.endswith(".java"):
                    continue
                for st in statements(text, term):
                    add(name[:-5], letters(st))
                    for c, m in re.findall(r"\b(Tc\w+)\.(\w+)\(", st):
                        if c + ".java" in src and re.search(r"\bString\s+" + m + r"\s*\(", src[c + ".java"]):
                            add(c, method_letters(c, m))
                    for fld in re.findall(r"\.append\(\w+\.(\w+)\)", st):   # a String field another class fills
                        for owner, text2 in src.items():
                            for meth in re.findall(r"\." + fld + r"\s*=\s*\w+\.(\w+)\(", text2) if owner.endswith(".java") else ():
                                for c2, t3 in src.items():
                                    if c2.endswith(".java") and re.search(r"\bString\s+" + meth + r"\s*\(", t3):
                                        add(c2[:-5], letters(body(t3, meth)))
        else:
            raise AssertionError(f"vectors.txt term not followed: {term}")
    return claims


def chain(code, test):
    """[(letters, branch text)] of an if / else-if chain on a line's first character. test: the regex of one test
    with the letter as group 1."""
    out = []
    conds = list(re.finditer(r"(?:\bif|\belif)\s*\(?((?:[^{:\n]|\n(?!\s*(?:}|elif|else)))*?)\)?\s*[{:]\s*(?:/\*|#|\n)", code))
    for i, m in enumerate(conds):
        ls = re.findall(test, m.group(1))
        if ls:
            end = conds[i + 1].start() if i + 1 < len(conds) else len(code)
            out.append((ls, code[m.end():end]))
    return out


def reader_claims(src):
    """({letter: {owner}}, [(chain name, letter)] tested in two branches of one chain)."""
    claims, dups = {}, []
    loop = src["runner.c"]
    loop = loop[loop.index("while (fgets(line"):]
    strict = body_c(src["tc_strict.h"], "tcrt_strict_line")
    py = src["x360_compare.py"]
    py = py[py.index("def parse_vectors"):py.index("\ndef ", py.index("def parse_vectors") + 1)]
    for name, code, test in (("runner.c", loop, r"line\[0\] == '([A-Z])'"),
                             ("tc_strict.h", strict, r"line\[0\] == '([A-Z])'"),
                             ("x360_compare.parse_vectors", py, r't\[0\] == "([A-Z]{1,2})"')):
        seen = set()
        for ls, branch in chain(code, test):
            owner = "tc_strict.h" if name == "runner.c" and "tcrt_strict_line(" in branch else name
            owner = "runner.c" if name == "x360_compare.parse_vectors" else owner   # the Python mirror of runner.c
            for c in ls:
                if c in seen:
                    dups.append((name, c))
                seen.add(c)
                claims.setdefault(c, set()).add(owner)
    return claims, dups


def body_c(src, name):
    m = re.search(r"\b" + name + r"\s*\([^)]*\)\s*\{", src)
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def collisions(src):
    """The problems found, as text; empty when every letter has one meaning."""
    w = writer_claims(src)
    r, dups = reader_claims(src)
    out = [f"{name}: {c} is tested in two branches" for name, c in dups]
    out += [f"writers: {c} is written by {sorted(o)}" for c, o in sorted(w.items()) if len(o) > 1]
    out += [f"readers: {c} is read by {sorted(o)}" for c, o in sorted(r.items()) if len(o) > 1]
    # one meaning end to end: TcStrict's letters are tc_strict.h's, everything else runner.c's
    side = {c: {"tc_strict.h" if x == "TcStrict" else "runner.c" for x in o} for c, o in w.items()}
    out += [f"{c}: written for {sorted(side[c])}, read by {sorted(r.get(c, {'nobody'}))}"
            for c in sorted(side) if side[c] != r.get(c, set())]
    return out


def plant_both_n(src):
    """The merge's first text: TcExit's exit seeds and runner.c's exit branch on "N", as the stricter judge's key."""
    src = dict(src)
    src["TcExit.java"] = src["TcExit.java"].replace('new StringBuilder("Q")', 'new StringBuilder("N")', 1)
    src["runner.c"] = src["runner.c"].replace("line[0] == 'Q'", "line[0] == 'N'", 1)
    assert src["TcExit.java"] != sources()["TcExit.java"] and src["runner.c"] != sources()["runner.c"]
    return src


class Letters(unittest.TestCase):
    def test_the_letters_are_found(self):
        src = sources()
        w = writer_claims(src)
        r, _ = reader_claims(src)
        # the derivation reaches each source: TcVectors' own lines, TcStrict's header and per-vector lines, TcExit's
        self.assertTrue({"V", "R", "E", "M"} <= {c for c, o in w.items() if "TcVectors" in o}, w)
        self.assertEqual({c for c, o in w.items() if "TcStrict" in o}, {"J", "U", "N", "W"})
        self.assertEqual({c for c, o in w.items() if "TcExit" in o}, {"Q", "G", "K"})
        self.assertEqual({c for c, o in r.items() if "tc_strict.h" in o}, {"J", "U", "N", "W"})
        self.assertTrue({"V", "R", "F", "C", "D", "M", "Q", "G", "K", "A", "E"} <= set(r))

    def test_no_collision(self):
        self.assertEqual(collisions(sources()), [])


class Plant(unittest.TestCase):
    def test_both_n_fails(self):
        got = collisions(plant_both_n(sources()))
        self.assertIn("writers: N is written by ['TcExit', 'TcStrict']", got)
        self.assertIn("runner.c: N is tested in two branches", got)


if __name__ == "__main__":
    unittest.main()
