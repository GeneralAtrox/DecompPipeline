"""The port readiness map (tools/port_map.py) on a small made-up workspace with a known answer for every function, then
the same workspace with wrong answers planted: a faked ready row, a changed readable file, a missing call edge, and a
citation passed off as native code. Each is rejected or reported, and the clean workspace is not.

usage: python -W error tests/test_port_map.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tools"))

from core import port_map as pm  # noqa: E402
from core import port_map_native as pn  # noqa: E402
import port_map as cli  # noqa: E402

ENTRY = "8c010000"
READY, ROW_ONLY, MACHINE, NO_RD, NO_RAW = "8c010100", "8c010200", "8c010300", "8c010400", "8c010500"
KIT_FN, KIT_CHILD, NATIVE_FN, NATIVE_CHILD = "8c010600", "8c010680", "8c010700", "8c010780"
POINTER, WRAPPED, BODY, TAGGED, CITED = "8c010800", "8c010900", "8c010a00", "8c010b00", "8c010c00"
OFF_KIT, OUT = "8c010d00", "8c0f0000"
NAMES = {ENTRY: "Game_Main", READY: "Enemy_Update", ROW_ONLY: "Enemy_Draw", MACHINE: "Hud_Tick", NO_RD: "Hud_Draw",
         KIT_FN: "kmSetThing", KIT_CHILD: "km_inner", NATIVE_FN: "Sound_Init", NATIVE_CHILD: "Sound_Inner",
         POINTER: "Enemy_Callback", WRAPPED: "Scene_Pick", TAGGED: "Tick_Rate", CITED: "Cited_Only", OFF_KIT: "pdOff"}
CALLS = {ENTRY: [READY, ROW_ONLY, MACHINE, NO_RD, NO_RAW, KIT_FN, NATIVE_FN, WRAPPED, BODY, TAGGED, CITED, OFF_KIT, OUT],
         KIT_FN: [KIT_CHILD], NATIVE_FN: [NATIVE_CHILD]}
FUNCS = sorted(set(NAMES) | {NO_RAW, BODY})
PASS = {"status": "readable", "verified": "2026-01-01T00:00:00", "finished": "2026-01-01T00:00:00",
        "attempts": [{"verdict": "PASS"}]}


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def old(path):
    """Before the judged times of PASS, as a judged file is."""
    stamp = time.mktime((2025, 12, 31, 0, 0, 0, 0, 0, -1))
    os.utime(path, (stamp, stamp))


class Fixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="portmap_"))
        self.ws, self.port = self.root / "ws", self.root / "port"
        ledger, readable = {}, {}
        dump(self.ws / "project.json", {"name": "Fixture", "game_ranges": [["8c010000", "8c0e0000"]]})
        dump(self.ws / "state" / "catalog.json", {"functions": [
            {"addr": a, "bytes": 16, "callees": len(CALLS.get(a, [])) - (OUT in CALLS.get(a, [])),
             "indirect_calls": 2 if a == READY else 0, "jump_tables": 1 if a == READY else 0} for a in FUNCS]})
        dump(self.ws / "readable" / "names" / "S1.json", {"functions": {a: {"proto": f"void {n}(void)"}
                                                                        for a, n in NAMES.items()}})
        for a in FUNCS:
            self.packet(a)
            if a == NO_RAW:
                continue
            self.write(self.ws / "matched" / f"{a}.c", f"void fn_{a}(void) {{ }}\n")
            for name in ("vectors.txt", "expected.txt"):
                self.write(self.ws / "harness" / a / name, "1\n")
            dump(self.ws / "harness" / a / "meta.json", {"callees": [{"addr": c} for c in CALLS.get(a, [])]})
            ledger[a] = {"status": "matched", "verdict_detail": "PASS on attempt 1"}
            if a == NO_RD:
                continue
            self.write(self.ws / "readable" / "src" / f"{a}.c", f"#include \"game.h\"\nvoid {NAMES.get(a, 'x')}(void) {{ }}\n")
            readable[a] = dict(PASS)
        readable[MACHINE] = dict(PASS, readable_by="machine")
        dump(self.ws / "state" / "ledger.json", {"functions": ledger})
        dump(self.ws / "state" / "readable.json", {"functions": readable})
        (self.ws / "state" / "graph.tsv").write_text("".join(
            f"{a}\t{','.join(CALLS.get(a, []))}\t{','.join(CALLS.get(a, []) + ([POINTER] if a == READY else []))}\t\t\n"
            for a in FUNCS), encoding="utf-8")
        dump(self.port / "game.json", {"entry": {"addr": ENTRY}, "sources": ["src/hw.c"], "sdk": ["on_mod"], "decomp": "../ws",
                                       "hardware": {NATIVE_FN: "Sound_Init", KIT_CHILD: "shared_in_kit"},
                                       "wrap": {WRAPPED: "Pick_Wrapper"}})
        self.write(self.port / "src" / "hw.c", "\n".join((
            '#include "game.h"',
            f"/* Sound_Init ({NATIVE_FN}). */",
            "void Sound_Init(void)\n{\n}",
            "void Pick_Wrapper(void)\n{\n}",
            f"void fn_{BODY}(void)\n{{\n}}",
            f"/* implements {TAGGED} */",
            "static void rate(void)\n{\n}",
            f"/* Does much what {CITED} does. */",
            "void helper(void)\n{\n}", "")))
        kit = self.root / "kit"
        for name, text in (("sdk/on_mod.c", "void kmSetThing(void)\n{\n}\nvoid shared_in_kit(void)\n{\n}\n"),
                           ("sdk/off_mod.c", "void pdOff(void)\n{\n}\n"), ("runtime/x.c", "void unrelated(void)\n{\n}\n")):
            self.write(kit / name, text)
        shutil.copytree(TOOL / "port" / "tools", kit / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        self.kit = kit

    def packet(self, addr, calls=None):
        lines = [f"; fn_{addr}"]
        lines += [f"  {addr}  jsr @r1      ; call {c}" for c in (CALLS.get(addr, []) if calls is None else calls)]
        if addr == READY:
            lines += [f"  {addr}  jsr @r2", f"  {addr}  mov.l 0x8c000000,r1   ; = 0x{POINTER}  (function {POINTER})"]
        self.write(self.ws / "packets" / addr / "asm.txt", "\n".join(lines) + "\n")

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        old(path)

    def load(self):
        ws = pm.Workspace(self.ws)
        return ws, pn.Bindings(ws, self.port, self.kit)

    def tree(self, **kw):
        ws, binds = self.load()
        tree = pm.walk(ws, ENTRY, binds, **kw)
        return {r["addr"]: r for r in tree["rows"]}, tree


class WithFixture(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        shutil.rmtree(self.f.root, ignore_errors=True)


class Known(WithFixture):
    def setUp(self):
        super().setUp()
        self.rows, self.t = self.f.tree()

    def test_every_state(self):
        want = {READY: pm.READY, ROW_ONLY: pm.READY, MACHINE: pm.NEEDS_READABLE, NO_RD: pm.NEEDS_READABLE,
                NO_RAW: pm.NEEDS_RAW, KIT_FN: pm.KIT, NATIVE_FN: pm.NATIVE, BODY: pm.NATIVE, TAGGED: pm.NATIVE,
                WRAPPED: pm.READY, CITED: pm.READY, OFF_KIT: pm.READY, OUT: pm.OUTSIDE, POINTER: pm.READY}
        self.assertEqual({a: self.rows[a]["state"] for a in want}, want)

    def test_the_walk_stops_at_what_is_replaced(self):
        self.assertNotIn(KIT_CHILD, self.rows)
        self.assertNotIn(NATIVE_CHILD, self.rows)

    def test_the_entry_itself_is_walked_even_when_native(self):
        f = self.f
        game = json.loads((f.port / "game.json").read_text(encoding="utf-8"))
        game["hardware"][ENTRY] = "Sound_Init"
        dump(f.port / "game.json", game)
        rows, _ = f.tree()
        self.assertEqual(rows[ENTRY]["state"], pm.NATIVE)
        self.assertIn(READY, rows)

    def test_a_pointer_is_labelled_and_can_be_left_out(self):
        self.assertEqual(self.rows[POINTER]["reached"], "pointer")
        self.assertNotIn(POINTER, self.f.tree(pointers=False)[0])

    def test_the_tree_never_claims_to_be_complete(self):
        c = self.t["completeness"]
        self.assertFalse(c["complete"])
        self.assertEqual(c["indirect_call_sites"], 2)
        self.assertEqual(c["jump_tables"], 1)
        self.assertEqual(c["edge_gaps"], {})

    def test_name_or_address_as_entry(self):
        ws, _ = self.f.load()
        self.assertEqual(ws.resolve("Game_Main"), ENTRY)
        self.assertEqual(ws.resolve(f"fn_{ENTRY}"), ENTRY)
        self.assertEqual(ws.resolve("0c010000"), ENTRY)  # the P0 mirror
        for bad in ("No_Such_Name", "8c7fffff"):
            with self.assertRaises(ValueError):
                ws.resolve(bad)

    def test_a_kit_module_the_game_leaves_off_is_said_not_counted(self):
        self.assertEqual(self.rows[OFF_KIT]["state"], pm.READY)
        self.assertTrue(any("off_mod" in x for x in self.rows[OFF_KIT]["flags"]))

    def test_hardware_name_defined_in_the_kit_is_the_kits(self):
        ws, binds = self.f.load()
        self.assertNotIn(KIT_CHILD, binds.native)
        self.assertEqual(pm.classify(ws, KIT_CHILD, binds)["state"], pm.KIT)

    def test_each_native_function_names_its_address(self):
        ws, binds = self.f.load()
        got = {(r["addr"], r["fn"], r["kind"]) for r in pn.native_table(ws, binds)}
        self.assertEqual(got, {(NATIVE_FN, "Sound_Init", "hardware"), (WRAPPED, "Pick_Wrapper", "wrap"),
                               (BODY, f"fn_{BODY}", "body"), (TAGGED, "rate", "tag")})
        self.assertIn("helper", " ".join(binds.unbound))

    def test_a_citation_is_not_a_binding(self):
        ws, binds = self.f.load()
        self.assertEqual({c["addr"] for c in binds.citation_only()}, {CITED})
        self.assertEqual(pm.classify(ws, CITED, binds)["state"], pm.READY)
        self.assertIn("cited_not_bound", pm.classify(ws, CITED, binds)["flags"])

    def test_nothing_is_written(self):
        before = sorted((p, p.stat().st_mtime_ns) for p in self.f.root.rglob("*") if "__pycache__" not in str(p))
        self.f.tree()
        ws, binds = self.f.load()
        pn.drift(ws, binds)
        self.assertEqual(before, sorted((p, p.stat().st_mtime_ns) for p in self.f.root.rglob("*")
                                        if "__pycache__" not in str(p)))


class Planted(WithFixture):
    def edit_readable_rows(self, addr, row):
        path = self.f.ws / "state" / "readable.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows["functions"][addr] = row
        dump(path, rows)

    def edit_ledger(self, addr, row):
        path = self.f.ws / "state" / "ledger.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows["functions"][addr] = row
        dump(path, rows)

    def test_faked_ready_rows_are_refused(self):
        plants = {
            "no evidence": ({"status": "readable"}, READY),
            "failed attempts only": ({"status": "readable", "attempts": [{"verdict": "FAIL"}]}, READY),
            "row without a file": (dict(PASS), NO_RD),
        }
        for why, (row, addr) in plants.items():
            self.edit_readable_rows(addr, row)
            self.assertEqual(self.f.tree()[0][addr]["state"], pm.NEEDS_READABLE, why)

    def test_faked_matched_rows_are_refused(self):
        self.edit_ledger(NO_RAW, {"status": "matched", "verdict_detail": "PASS on attempt 1"})
        dump(self.f.ws / "harness" / NO_RAW / "meta.json", {"callees": []})
        self.assertEqual(self.f.tree()[0][NO_RAW]["state"], pm.NEEDS_RAW)  # no matched/ADDR.c
        self.edit_ledger(READY, {"status": "matched"})
        self.assertEqual(self.f.tree()[0][READY]["state"], pm.NEEDS_RAW)  # no PASS on record
        shutil.rmtree(self.f.ws / "harness" / ROW_ONLY)
        self.assertEqual(self.f.tree()[0][ROW_ONLY]["state"], pm.NEEDS_RAW)  # nothing to be judged on

    def test_machine_names_are_not_ready(self):
        reg = self.f.ws / "readable" / "names" / "S1.json"
        data = json.loads(reg.read_text(encoding="utf-8"))
        data["functions"][READY]["proto"] = f"void Stub_{READY}(void)"
        dump(reg, data)
        self.f.write(self.f.ws / "readable" / "src" / f"{ROW_ONLY}.c", f"void Enemy_Draw(void) {{ fn_{NO_RD}(); }}\n")
        rows = self.f.tree()[0]
        self.assertEqual(rows[READY]["state"], pm.NEEDS_READABLE)
        self.assertEqual(rows[ROW_ONLY]["state"], pm.NEEDS_READABLE)  # a raw name of a named function

    def test_a_changed_readable_file_is_not_ready(self):
        (self.f.ws / "readable" / "src" / f"{READY}.c").write_text("void Enemy_Update(void) { /* edited */ }\n", encoding="utf-8")
        row = self.f.tree()[0][READY]
        self.assertEqual(row["state"], pm.NEEDS_READABLE)
        self.assertIn("changed after", row["why"])

    def test_a_moved_harness_is_flagged(self):
        self.edit_ledger(READY, {"status": "matched", "verdict_detail": "PASS", "harness_sha256": {"vectors.txt": "0" * 64}})
        row = self.f.tree()[0][READY]
        self.assertEqual(row["state"], pm.READY)
        self.assertIn("harness_moved_since_match", row["flags"])

    def test_a_missing_edge_in_one_source_is_reported_and_still_walked(self):
        meta = self.f.ws / "harness" / ENTRY / "meta.json"
        dump(meta, {"callees": [{"addr": c} for c in CALLS[ENTRY] if c != READY]})
        rows, t = self.f.tree()
        self.assertIn(READY, rows)
        gap = [g for g in t["completeness"]["gaps"] if g.get("to") == READY]
        self.assertEqual(gap[0]["kind"], "source_disagrees")
        self.assertEqual(gap[0]["missing_from"], ["meta"])

    def test_an_edge_gone_from_every_source_is_found_in_the_text(self):
        kept = [c for c in CALLS[ENTRY] if c != READY]
        dump(self.f.ws / "harness" / ENTRY / "meta.json", {"callees": [{"addr": c} for c in kept]})
        self.f.packet(ENTRY, kept)
        graph = self.f.ws / "state" / "graph.tsv"
        graph.write_text(graph.read_text(encoding="utf-8").replace(f"{READY},", ""), encoding="utf-8")
        self.f.write(self.f.ws / "readable" / "src" / f"{ENTRY}.c", "void Game_Main(void) { Enemy_Update(); }\n")
        rows, t = self.f.tree()
        self.assertEqual(rows[READY]["reached"], "text")
        self.assertTrue(any(g["kind"] == "text_only" and g["to"] == READY for g in t["completeness"]["gaps"]))

    def test_an_edge_gone_from_everything_but_the_catalog_is_counted(self):
        kept = [c for c in CALLS[ENTRY] if c not in (READY, OUT)]
        dump(self.f.ws / "harness" / ENTRY / "meta.json", {"callees": [{"addr": c} for c in kept]})
        self.f.packet(ENTRY, kept)
        graph = self.f.ws / "state" / "graph.tsv"
        graph.write_text(graph.read_text(encoding="utf-8").replace(f"{READY},", "").replace(f",{OUT}", ""), encoding="utf-8")
        _, t = self.f.tree()
        self.assertTrue(any(g["kind"] == "catalog_counts_more" and g["from"] == ENTRY for g in t["completeness"]["gaps"]))

    def test_no_edge_data_is_said(self):
        shutil.rmtree(self.f.ws / "packets" / READY)
        shutil.rmtree(self.f.ws / "harness" / READY)
        graph = self.f.ws / "state" / "graph.tsv"
        graph.write_text("".join(ln for ln in graph.read_text(encoding="utf-8").splitlines(True)
                                 if not ln.startswith(READY)), encoding="utf-8")
        _, t = self.f.tree()
        self.assertEqual(t["completeness"]["edge_gaps"].get("no_edge_data"), 1)

    def test_a_citation_dressed_as_a_binding_does_not_count(self):
        """A comment names the address and a registry-looking helper sits under it: still only a citation."""
        self.f.write(self.f.port / "src" / "hw.c", (self.f.port / "src" / "hw.c").read_text(encoding="utf-8")
                     + f"/* Hud_Tick ({MACHINE}) is done here. */\nvoid hud_tick_native(void)\n{{\n}}\n")
        ws, binds = self.f.load()
        self.assertNotIn(MACHINE, binds.native)
        self.assertIn(MACHINE, {c["addr"] for c in binds.citation_only()})
        self.assertEqual(pm.classify(ws, MACHINE, binds)["state"], pm.NEEDS_READABLE)

    def test_a_binding_with_no_definition_is_not_native(self):
        game = json.loads((self.f.port / "game.json").read_text(encoding="utf-8"))
        game["hardware"][NO_RD] = "Not_Written"
        dump(self.f.port / "game.json", game)
        row = self.f.tree()[0][NO_RD]
        self.assertEqual(row["state"], pm.NEEDS_READABLE)
        self.assertTrue(any("no source defines it" in x for x in row["flags"]))


class Drift(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.base = self.f.root / "baseline.json"
        ws, binds = self.f.load()
        pn.record(ws, binds, self.base, at="2026-01-01T00:00:00")

    def tearDown(self):
        shutil.rmtree(self.f.root, ignore_errors=True)

    def moved(self):
        ws, binds = self.f.load()
        return pn.drift(ws, binds, self.base)

    def test_nothing_moved(self):
        d = self.moved()
        self.assertEqual((d["moved"], d["native_changed"], d["no_baseline"]), ([], [], []))

    def test_a_changed_readable_file_is_reported(self):
        (self.f.ws / "readable" / "src" / f"{NATIVE_FN}.c").write_text("void Sound_Init(void) { /* new */ }\n", encoding="utf-8")
        d = self.moved()
        self.assertEqual([m["addr"] for m in d["moved"]], [NATIVE_FN])
        self.assertEqual([c["what"] for c in d["moved"][0]["changes"]], ["readable C"])

    def test_changed_raw_c_harness_and_verdict_are_reported(self):
        for addr, path in ((BODY, self.f.ws / "matched" / f"{BODY}.c"),
                           (TAGGED, self.f.ws / "harness" / TAGGED / "expected.txt")):
            path.write_text("changed\n", encoding="utf-8")
        ledger = self.f.ws / "state" / "ledger.json"
        rows = json.loads(ledger.read_text(encoding="utf-8"))
        rows["functions"][WRAPPED]["verdict_detail"] = "FAIL on attempt 2"
        dump(ledger, rows)
        got = {m["addr"]: m["changes"][0]["what"] for m in self.moved()["moved"]}
        self.assertEqual(got, {BODY: "raw C", TAGGED: "harness expected results", WRAPPED: "raw verdict"})

    def test_a_line_ending_is_not_a_change(self):
        path = self.f.ws / "matched" / f"{BODY}.c"
        data = path.read_bytes()
        path.write_bytes(data.replace(b"\r\n", b"\n") if b"\r\n" in data else data.replace(b"\n", b"\r\n"))
        self.assertEqual(self.moved()["moved"], [])

    def test_changed_native_code_is_reported_apart(self):
        src = self.f.port / "src" / "hw.c"
        src.write_text(src.read_text(encoding="utf-8") + "/* more */\n", encoding="utf-8")
        d = self.moved()
        self.assertEqual(d["moved"], [])
        self.assertIn("Sound_Init", {c["fn"] for c in d["native_changed"]})

    def test_no_baseline_and_no_git_is_not_called_no_drift(self):
        ws, binds = self.f.load()
        d = pn.drift(ws, binds)
        self.assertEqual(d["moved"], [])
        self.assertEqual(len(d["no_baseline"]), len(binds.native))

    def test_the_baseline_may_not_go_in_the_workspace(self):
        ws, binds = self.f.load()
        with self.assertRaises(ValueError):
            pn.record(ws, binds, self.f.ws / "state" / "b.json")


class Cli(WithFixture):
    def test_commands_run(self):
        out = self.f.root / "tree.json"
        base = ["--ws", str(self.f.ws), "--port", str(self.f.port), "--kit", str(self.f.kit)]
        self.assertEqual(cli.main(["tree", *base, "--out", str(out), "--list", "all"]), 0)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["entry"], ENTRY)
        self.assertEqual(cli.main(["native", *base]), 0)
        baseline = str(self.f.root / "b.json")
        self.assertEqual(cli.main(["record", *base, "--baseline", baseline]), 0)
        self.assertEqual(cli.main(["drift", *base, "--baseline", baseline]), 0)
        (self.f.ws / "matched" / f"{BODY}.c").write_text("changed\n", encoding="utf-8")
        self.assertEqual(cli.main(["drift", *base, "--baseline", baseline]), 1)

    def test_judge_works_on_a_copy(self):
        """The judge keeps caches under its project: it gets a folder of its own, not the workspace."""
        dump(self.f.ws / "project.json", {"binary": "state/catalog.json", "name": "x"})
        cli.private_copy(self.f.ws, [ENTRY], self.f.root / "copy")
        proj = json.loads((self.f.root / "copy" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(proj["binary"]), (self.f.ws / "state" / "catalog.json").resolve())
        self.assertEqual(proj["name"], "x")
        self.assertTrue((self.f.root / "copy" / "harness" / ENTRY / "meta.json").is_file())


if __name__ == "__main__":
    unittest.main()
