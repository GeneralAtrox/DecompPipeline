"""Scope list (project.json "extra_functions", core/scope.py): the one definition of "in scope", the extras' own
systems (core/sysassign.py), the doctor's checks, the cross-project matcher's scope and the extras' byte lines
(tools/completeness.py, targets/dreamcast_sh4/tools/coverage.py). Pure fixtures in temp folders; no Ghidra.
The process-bound parts (orchestrator, catalog fixup arguments, decomp.py audit, final audit) are in
tests/test_scope_runs.py.

usage: python -W error tests/test_scope.py   (python tests/test_tooling.py runs these too)
"""
import atexit
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="scope_", dir=TOOL / "tests" / ".work")
TMP = pathlib.Path(_TMP.name)
for p in (str(TOOL / "tools"), str(TOOL)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import doctor, scope, sysassign, xmatch  # noqa: E402  (pure: no core.paths)
import completeness  # noqa: E402  (tools/completeness.py)

atexit.register(_TMP.cleanup)

G = [f"8c0100{i:x}0" for i in range(4)]  # game functions (game_end 8c068000)
E1, E2, SDK1, E3, SDK2, E4, E5 = (f"8c0700{x}" for x in ("00", "40", "80", "c0", "e0", "f0", "f8"))
EXTRAS = [E1, E2, E3, E4]


def put(ws, rel, data):
    p = pathlib.Path(ws) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def sys_ws(name, extras=EXTRAS, more=()):
    """Game G0..G3 in two systems; extras E1 E2 (neighbours), E3 (SDK on both sides), E4 (calls E1); SDK between."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix=name, dir=TMP))
    pj = {"target": "dreamcast_sh4", "game_end": "8c068000"}
    if extras is not None:
        pj["extra_functions"] = extras
    put(ws, "project.json", pj)
    put(ws, "state/catalog.json", {"functions": [{"addr": a} for a in G + [E1, E2, SDK1, E3, SDK2, E4] + list(more)]})
    put(ws, "state/systems.json", {"resolution": 2.0, "systems": [
        {"id": "S01", "hint": "", "functions": [G[0], G[1]]}, {"id": "S02", "hint": "", "functions": [G[2], G[3]]}]})
    put(ws, "state/graph.tsv", "\n".join([
        f"{G[1]}\t{E3}\t\t\t",  # a game function calls E3: never pulls E3 into a game system
        f"{G[0]}\t\t\t8c300000\t", f"{E2}\t\t\t8c300000\t",  # a global shared with the game: ignored too
        f"{E4}\t{E1}\t\t\t",  # E4 calls E1: one cluster with E1, E2
    ]) + "\n")
    return ws


class InScope(unittest.TestCase):
    def test_without_the_key_only_the_game_ranges(self):
        cfg = {"game_end": "8c068000"}
        self.assertTrue(scope.in_scope(cfg, "8c010000"))
        self.assertFalse(scope.in_scope(cfg, E1))
        self.assertEqual(scope.Scope(cfg).extras, set())
        over = {"game_ranges": [["8c558000", "8c5f9dd0"], ["8c600000", "8c610000"]]}
        self.assertEqual(scope.game_ranges(over), [(0x8C558000, 0x8C5F9DD0), (0x8C600000, 0x8C610000)])
        self.assertTrue(scope.in_scope(over, "8c600010"))
        self.assertFalse(scope.in_scope(over, "8c5f9dd0"))  # end exclusive
        self.assertTrue(scope.Scope({}).in_ranges("8c999990"))  # neither key: every function (as before)

    def test_list_key(self):
        cfg = {"game_end": "8c068000", "extra_functions": ["8C070000", "0x8c070040", "8c010000", "8c070000"]}
        sc = scope.Scope(cfg)
        self.assertEqual(sc.extras, {E1, E2})
        self.assertEqual(sc.listed_in_ranges, ["8c010000"])  # listed, but a game function: counted as game
        self.assertTrue(E1 in sc and "8c010000" in sc and SDK1 not in sc)
        self.assertTrue(sc.is_extra(E2) and not sc.is_extra("8c010000"))
        self.assertEqual(sc.split([SDK1, E2, "8c010010", E1]), (["8c010010"], [E1, E2]))
        self.assertEqual(scope.listed(cfg), [E1, E2, "8c010000"])

    def test_file_key(self):
        ws = pathlib.Path(tempfile.mkdtemp(prefix="file", dir=TMP))
        put(ws, "state/engine.txt", f"# engine functions\n{E1}\n\n{E3}  # with a comment\n")
        cfg = {"game_end": "8c068000", "extra_functions": "state/engine.txt"}
        self.assertEqual(scope.Scope(cfg, ws).extras, {E1, E3})
        self.assertTrue(scope.in_scope(cfg, E3, ws))
        self.assertIn("file state/engine.txt", scope.describe(cfg))

    def test_bad_values_fail_loudly(self):
        ws = pathlib.Path(tempfile.mkdtemp(prefix="bad", dir=TMP))
        with self.assertRaisesRegex(ValueError, "not a hex address"):
            scope.Scope({"game_end": "8c068000", "extra_functions": ["8c07zz00"]})
        with self.assertRaises(FileNotFoundError):
            scope.Scope({"game_end": "8c068000", "extra_functions": "nope.txt"}, ws)
        with self.assertRaisesRegex(ValueError, "workspace"):
            scope.Scope({"game_end": "8c068000", "extra_functions": "nope.txt"})
        with self.assertRaisesRegex(ValueError, "list of hex addresses or a file"):
            scope.Scope({"game_end": "8c068000", "extra_functions": 5})

    def test_workspace_sets(self):
        ws = sys_ws("sets")
        self.assertEqual(scope.game_functions(ws), G)
        self.assertEqual(scope.extra_functions(ws), [E1, E2, E3, E4])
        self.assertEqual(scope.scope_functions(ws), G + [E1, E2, E3, E4])
        self.assertEqual(sysassign.game_functions(ws), G)  # the re-exports are the same definitions
        self.assertIs(sysassign.in_scope, scope.in_scope)
        self.assertEqual(scope.missing_extras(ws), [])
        none = sys_ws("sets0", extras=None)
        self.assertEqual(scope.scope_functions(none), G)  # control: without the key, the game only


class ExtraSystems(unittest.TestCase):
    def test_extras_get_their_own_systems(self):
        ws = sys_ws("xsys")
        self.assertEqual(sysassign.unassigned(ws)[0], [E1, E2, E3, E4])
        self.assertEqual(sysassign.extras_order(ws, EXTRAS), [E1, E2, None, E3, None, E4])
        p = sysassign.plan(ws)
        self.assertEqual((p["before"], p["extras"]), (4, 4))
        self.assertEqual(p["new"], [{"id": "S03", "hint": "extra", "functions": [E1, E2, E4]},
                                    {"id": "S04", "hint": "extra", "functions": [E3]}])
        self.assertTrue(all(x["how"] == "new" for x in p["assign"].values()))
        lines = sysassign.report(p)
        self.assertIn("0 game function(s) and 4 extra function(s)", lines[0])
        self.assertIn("new system S03 (extra) (3)", "\n".join(lines))
        before = json.loads((ws / "state/systems.json").read_text(encoding="utf-8"))["systems"]
        sysassign.apply(ws)
        after = {s["id"]: s for s in json.loads((ws / "state/systems.json").read_text(encoding="utf-8"))["systems"]}
        for s in before:  # the game systems never change
            self.assertEqual(after[s["id"]]["functions"], s["functions"])
        self.assertEqual(sysassign.unassigned(ws)[0], [])  # no function in no system
        self.assertEqual(sysassign.apply(ws)["assign"], {})  # idempotent

    def test_a_later_extra_joins_by_call_and_never_a_game_system(self):
        ws = sys_ws("xsys2")
        sysassign.apply(ws)
        put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000",
                                 "extra_functions": EXTRAS + [E5]})
        cat = json.loads((ws / "state/catalog.json").read_text(encoding="utf-8"))
        put(ws, "state/catalog.json", {"functions": cat["functions"] + [{"addr": E5}]})
        with open(ws / "state/graph.tsv", "a", encoding="utf-8") as f:
            f.write(f"{E5}\t{E3},{G[2]}\t\t\t\n")  # calls E3 (an extra system) and a game function
        p = sysassign.plan(ws)
        self.assertEqual(p["assign"], {E5: {"system": "S04", "how": "call"}})

    def test_attach_stays_within_the_extras(self):
        ws = sys_ws("xsys3")
        sysassign.apply(ws)
        put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000",
                                 "extra_functions": EXTRAS + [E5]})
        cat = json.loads((ws / "state/catalog.json").read_text(encoding="utf-8"))
        put(ws, "state/catalog.json", {"functions": cat["functions"] + [{"addr": E5}]})
        p = sysassign.plan(ws, attach=True)  # E5 follows E4 directly: E4's system, never a game one
        self.assertEqual(p["assign"], {E5: {"system": "S03", "how": "adjacent"}})

    def test_a_connected_library_splits_into_communities(self):
        xs = [f"8c0800{i:x}0" for i in range(8)]  # two call cliques of four, one call between them, all adjacent
        ws = sys_ws("xcomm", extras=xs, more=xs)
        rows = [f"{a}\t{','.join(b for b in grp if b != a)}\t\t\t" for grp in (xs[:4], xs[4:]) for a in grp]
        put(ws, "state/graph.tsv", "\n".join(rows + [f"{xs[3]}\t{xs[4]}\t\t\t"]) + "\n")
        p = sysassign.plan(ws)
        self.assertEqual([s["functions"] for s in p["new"]], [xs[:4], xs[4:]])  # clusters would make one of eight
        g = sysassign._clusters(xs, {a: i for i, a in enumerate(xs)}, xs, {a: set(xs) - {a} for a in xs})
        self.assertEqual(len(g), 1)  # control: the game's rule chains them

    def test_control_without_the_key(self):
        ws = sys_ws("xsys0", extras=None)
        p = sysassign.plan(ws)
        self.assertEqual((p["before"], p["assign"], p["new"], p["extras"]), (0, {}, [], 0))
        self.assertEqual(sysassign.report(p), ["0 game function(s) in no system (of 2 existing systems)",
                                               "nothing to assign"])


class Doctor(unittest.TestCase):
    def test_extras_in_no_system_and_unknown_extras(self):
        ws = sys_ws("doc", extras=EXTRAS + ["8c0799f0"])
        ok, lines, facts = doctor.check(ws)
        text = "\n".join(lines)
        self.assertFalse(ok)
        self.assertIn("1 project.json extra_functions address(es) not functions of the catalog: 8c0799f0", text)
        self.assertIn("4 game function(s) in no system (4 of them extra functions)", text)
        self.assertEqual((facts["unassigned"], facts["extras_not_functions"]), (4, 1))
        put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000", "extra_functions": EXTRAS})
        sysassign.apply(ws)
        ok, lines, facts = doctor.check(ws)
        self.assertTrue(ok, lines)
        self.assertEqual((facts["unassigned"], facts["extras_not_functions"]), (0, 0))


def image_ws(name, extras):
    """A 0x100-byte image: game 8c010000-8c010080, an extra at 8c0100c0 with a literal pool word, SDK at 8c010080."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix=name, dir=TMP))
    img = bytearray(0x100)
    struct.pack_into("<H", img, 0xC0, 0xD001)  # mov.l @(4,PC),r0 at 8c0100c0: reads 8c0100c8 (outside the body)
    (ws / "game.bin").write_bytes(bytes(img))
    pj = {"target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin", "game_end": "8c010080"}
    put(ws, "project.json", dict(pj, extra_functions=extras) if extras else pj)
    put(ws, "state/catalog.json", {"functions": [{"addr": a, "bytes": 4} for a in ("8c010000", "8c010080",
                                                                                  "8c0100c0")]})
    put(ws, "state/code_ranges.txt", "8c010000 8c010004\n8c010080 8c010084\n8c0100c0 8c0100c4\n")
    put(ws, "state/function_bodies.txt", "8c010000 8c010000 8c010004\n8c010080 8c010080 8c010084\n"
                                         "8c0100c0 8c0100c0 8c0100c4\n")
    return ws


class Bytes(unittest.TestCase):
    def test_extras_bytes_line_apart(self):
        ws = image_ws("bytes", ["8c0100c0"])
        img = (ws / "game.bin").read_bytes()
        self.assertEqual(completeness.extras_bytes(img, 0x8C010000, ws / "state/function_bodies.txt", {"8c0100c0"}),
                         (1, 4, 4))
        r = subprocess.run([sys.executable, "-W", "error", str(TOOL / "tools/completeness.py"), "--project", str(ws)],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"unexplained [\d,]+ in \d+ runs \| stale \d+")  # the final audit's line, unchanged
        self.assertIn("extra functions (project.json extra_functions, outside the game ranges): 1 of 1 listed in the "
                      "catalog, code 4 bytes, literal pools 4 bytes", r.stdout)
        self.assertIn("code 4 pool 0 ", r.stdout)  # game-range classes: the extra's bytes are not mixed in
        cov = subprocess.run([sys.executable, "-W", "error", str(TOOL / "targets/dreamcast_sh4/tools/coverage.py"),
                              str(ws)], capture_output=True, text=True, timeout=300)
        self.assertEqual(cov.returncode, 0, cov.stderr)
        self.assertIn("extra functions (outside the game ranges): 1 of 1 listed in the catalog, 4 code bytes",
                      cov.stdout)

    def test_control_without_the_key(self):
        ws = image_ws("bytes0", None)
        r = subprocess.run([sys.executable, "-W", "error", str(TOOL / "tools/completeness.py"), "--project", str(ws)],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("extra functions", r.stdout + (ws / "COMPLETENESS.md").read_text(encoding="utf-8"))
        cov = subprocess.run([sys.executable, "-W", "error", str(TOOL / "targets/dreamcast_sh4/tools/coverage.py"),
                              str(ws)], capture_output=True, text=True, timeout=300)
        self.assertNotIn("extra functions", cov.stdout)


class CrossProject(unittest.TestCase):
    def test_in_scope_and_is_game(self):
        ws = image_ws("xm", ["8c0100c0"])
        img = xmatch.Image(ws)
        self.assertTrue(img.is_game("8c010000") and not img.is_game("8c0100c0"))  # the code ranges: unchanged
        self.assertTrue(img.in_scope("8c0100c0") and img.in_scope("8c010000") and not img.in_scope("8c010080"))
        self.assertFalse(xmatch.Image(image_ws("xm0", None)).in_scope("8c0100c0"))


if __name__ == "__main__":
    unittest.main(warnings="error")
