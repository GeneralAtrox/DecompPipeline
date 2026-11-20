"""tools/AutoOnboard/autoonboard.py's Xbox 360 stages and the phase-4 changes, with no Ghidra, no real disc and no real key (the
synthetic XEX2 and XDVDFS builders of test_xbox360_ppc.py, encrypted with a stand-in master key):
1. the overview keeps the table to short numbers and puts every message and number under it in full, never cut;
2. disc: a retail-encrypted XEX on a disc with no key here stops naming "xex_retail_key" (and never prints a key);
   with the key it opens; --inputs WS is checked against the XEX header (another title is refused); a title update
   (delta patch) and an LZX XEX are refused; a bare .xex in the root is taken;
3. extract: a workspace of another XEX, or one an earlier run left half made, stops the stage;
4. census: the count is labelled as found by analysis with Too Human's shortfall; gate G2 failing is an error; a
   Ghidra folder under a '.' path element is refused;
5. symbols: the folders (a named one absent is an error; none at all is an error), the candidates (a PDB or MAP with
   its own XEX; archives counted), the rank (same title first) and the accept rule on pdb_names.verify's numbers;
   verify() itself agrees on a right pairing and not on its wrong control;
6. gaps: refusals grouped by the instruction (oracle words; the catalog scan's own words via ppcscan.refusing_ops);
   kit_imports reads the kit's ordinals; the "not found" note is Too Human's;
7. boot: the inputs a bring-up needs are listed, each with what supplies it;
8. the stage table wires every Xbox 360 stage (symbols heavy), and --host passes --inputs on.
Run: python -W error tests/test_onboard_x360.py
"""
import json
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import types
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
X360 = TOOL / "targets" / "xbox360_ppc"
sys.path[:0] = [str(TOOL / "tools" / "AutoOnboard"), str(TOOL / "tools"), str(TOOL / "tests")]
import autoonboard as onboard  # noqa: E402
import onboard_stages as S  # noqa: E402
import onboard_symbols as SYM  # noqa: E402
import onboard_x360 as X  # noqa: E402
from test_xbox360_ppc import TEST_MASTER, synthetic_image, synthetic_xex  # noqa: E402
import xex  # noqa: E402  (test_xbox360_ppc put the target's tools on the path)
import xiso  # noqa: E402


def disc_with(xex_bytes, extra=b"hello"):
    """An XDVDFS disc: default.xex (the given bytes) and a.bin in the root."""
    sec = xiso.SECTOR

    def entry(left, right, start, size, attr, name):
        e = struct.pack("<HHIIBB", left, right, start, size, attr, len(name)) + name.encode()
        return e + b"\xff" * (-len(e) % 4)
    first = entry(0, 0, 34, len(xex_bytes), 0, "default.xex")
    root = entry(len(first) // 4, 0, 34, len(xex_bytes), 0, "default.xex") + entry(0, 0, 35 + len(xex_bytes) // sec,
                                                                                    len(extra), 0, "a.bin")
    vd = bytearray(sec)
    vd[0:20] = xiso.MAGIC
    struct.pack_into("<II", vd, 20, 33, len(root))
    vd[0x7EC:0x7EC + 20] = xiso.MAGIC
    out = bytearray(34 * sec)
    out[32 * sec:33 * sec], out[33 * sec:33 * sec + len(root)] = vd, root
    out += xex_bytes + bytes(-len(xex_bytes) % sec) + extra + bytes(-len(extra) % sec) + bytes(4 * sec)
    return bytes(out)


class Base(unittest.TestCase):
    def setUp(self):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="onbx_", dir=TOOL / "tests" / ".work"))
        (self.d / S.STATE_DIR).mkdir(parents=True)
        self.key = None
        self._rk = xex.retail_key
        xex.retail_key = lambda root=None: self.key  # stands in for config.local.json: no real key is read

    def tearDown(self):
        xex.retail_key = self._rk
        shutil.rmtree(self.d, ignore_errors=True)

    def ctx(self, **kw):
        c = types.SimpleNamespace(tool=S.TOOL, project=self.d, console="xbox360", name="Synthetic", disc=None,
                                  inputs=None, verdicts={}, seconds=2900, here=True, host=None, dry=False)
        c.__dict__.update(kw)
        return c

    def error(self, adapter, ctx):
        with self.assertRaises(S.StageError) as cm:
            adapter.run(ctx)
        return cm.exception


class Overview(Base):
    def test_short_cells_full_messages(self):
        long_msg = "the census found " + "many functions and " * 30 + "THE END"
        for n, st in (("project", "ok"), ("disc", "ok"), ("extract", "error")):
            onboard.save(self.d, {"stage": n, "status": st, "console": "xbox360", "name": "X", "machine": "m",
                                  "message": long_msg if n != "project" else "made", "paths": {},
                                  "numbers": {"count": 7, "note": "a long value " * 5, **{f"k{i}": i for i in range(8)}},
                                  "missing": ["the thing"] if st == "error" else [], "supply": "the person", "next": []})
        text = onboard.overview(self.d, onboard.load(self.d), None)
        row = next(ln for ln in text.splitlines() if ln.startswith("| disc |"))
        self.assertNotIn("a long value", row)
        self.assertIn("count 7, k0 0, k1 1, k2 2, k3 3, k4 4 (+4 more below)", row)
        self.assertIn("## Per stage", text)
        self.assertIn(f"- **disc**: {long_msg} Numbers: count 7; note {'a long value ' * 5}".rstrip(), text)
        self.assertIn(f"- extract: {long_msg}. Missing: the thing", text)
        self.assertIn(f"stopped at **extract**: {long_msg}", text)


class Disc(Base):
    def put(self, data, name="game.iso"):
        (self.d / name).write_bytes(data)

    def test_no_key_names_it(self):
        self.put(disc_with(synthetic_xex(synthetic_image())))
        e = self.error(X.Disc(), self.ctx())
        self.assertIn("encrypted with the console's retail key", str(e))
        self.assertIn('"xex_retail_key"', e.missing[0])
        self.assertIn("--inputs WS", e.supply)
        self.assertNotIn(TEST_MASTER.hex(), str(e) + " ".join(e.missing) + e.supply)

    def test_key_opens(self):
        self.key = TEST_MASTER
        self.put(disc_with(synthetic_xex(synthetic_image())))
        got = X.Disc().run(self.ctx())
        self.assertEqual(got["numbers"]["encryption"], "opens with this machine's keys")
        self.assertEqual(got["numbers"]["files"], 2)
        self.assertTrue((self.d / S.STATE_DIR / "default.xex").is_file())
        self.assertNotIn(TEST_MASTER.hex(), json.dumps(got))

    def test_inputs_checked_against_the_header(self):
        img = synthetic_image()
        self.put(disc_with(synthetic_xex(img)))
        ws = self.d / "other"
        ws.mkdir()
        for n in X.INPUTS:
            (ws / n).write_text("{}", encoding="utf-8")
        x = xex.Xex(synthetic_xex(img, encrypt=False))
        (ws / "image.json").write_text(json.dumps(xex.describe(x, img)), encoding="utf-8")
        got = X.Disc().run(self.ctx(inputs=str(ws)))
        self.assertIn("catalog inputs from", got["numbers"]["encryption"])
        self.assertEqual(got["paths"]["inputs"], str(ws))
        desc = json.loads((ws / "image.json").read_text(encoding="utf-8"))
        desc["entry_point"] += 4
        (ws / "image.json").write_text(json.dumps(desc), encoding="utf-8")
        self.assertIn("not this disc's title (entry point", str(self.error(X.Disc(), self.ctx(inputs=str(ws)))))
        (ws / "code_ranges.txt").unlink()
        self.assertIn("lacks code_ranges.txt", str(self.error(X.Disc(), self.ctx(inputs=str(ws)))))

    def test_refusals_and_a_bare_xex(self):
        img = synthetic_image()
        self.put(synthetic_xex(img, compression=xex.COMP_DELTA), "update.xex")
        self.assertIn("delta patch", str(self.error(X.Disc(), self.ctx())))
        (self.d / "update.xex").unlink()
        self.put(synthetic_xex(img, compression=xex.COMP_NORMAL), "game.xex")
        self.assertIn("LZX", str(self.error(X.Disc(), self.ctx())))
        self.put(synthetic_xex(img, encrypt=False), "game.xex")
        got = X.Disc().run(self.ctx())
        self.assertEqual((got["numbers"]["encryption"], got["numbers"]["files"]), ("none", 0))
        (self.d / "game.xex").unlink()
        self.put(bytes(40 * xiso.SECTOR))
        self.assertIn("not a disc image xiso.py reads", str(self.error(X.Disc(), self.ctx())))


class Extract(Base):
    def test_other_or_half_made_workspace(self):
        img = synthetic_image()
        (self.d / "game.xex").write_bytes(synthetic_xex(img, encrypt=False))
        dv = {"status": "ok", "numbers": {"files": 0, "GB": 0}, "paths": {"image": str(self.d / "game.xex"),
                                                                         "xex": str(self.d / "game.xex")}}
        ws = self.d / "decomp"
        ws.mkdir()
        (ws / "stray.txt").write_text("x", encoding="utf-8")
        self.assertIn("no project.json", str(self.error(X.Extract(), self.ctx(verdicts={"disc": dv}))))
        desc = xex.describe(xex.Xex(synthetic_xex(img, encrypt=False)), img)
        desc["image_size"] *= 2
        (ws / "project.json").write_text("{}", encoding="utf-8")
        (ws / "image.json").write_text(json.dumps(desc), encoding="utf-8")
        self.assertIn("a workspace of another XEX (image size", str(self.error(X.Extract(),
                                                                               self.ctx(verdicts={"disc": dv}))))


class Census(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.d / "decomp"
        (self.ws / "state").mkdir(parents=True)
        self.cfg = {"ghidra_name": "Syn", "ghidra_dir": str(pathlib.Path(self.d.anchor) / "onboard-test-no-such-dir"),
                    "game_ranges": [["82010000", "82020000"]]}
        (self.ws / "project.json").write_text(json.dumps(self.cfg), encoding="utf-8")
        (self.ws / "catalog_inputs.json").write_text(json.dumps({"functions": [], "stats": {
            "functions": 3, "by_source": {"pdata": 2, "bl": 1}, "refused": {"unsupported: system": 1},
            "mid_entries": 0}}), encoding="utf-8")
        self._run = S.run_tool

    def tearDown(self):
        S.run_tool = self._run
        super().tearDown()

    def fake(self, verdict):
        def run_tool(ctx, stage, argv, seconds, why=None, produces=None):
            S.log_path(ctx, stage).write_text(
                f"decode audit: 12 words of every function(s); {int(verdict != 'passes')} disagreement(s) in functions "
                f"the target prepares, 1 in refused functions and 2 in import records (expected): gate G2 {verdict}\n",
                encoding="utf-8")
            (self.ws / "state" / "catalog.json").write_text(json.dumps({"functions": [
                {"addr": "82010000"}, {"addr": "82010100"}, {"addr": "82030000"}]}), encoding="utf-8")
            (self.ws / "state" / "decode_audit.tsv").write_text("", encoding="utf-8")
            return (0 if verdict == "passes" else 1), []
        S.run_tool = run_tool

    def test_counts_and_g2(self):
        self.fake("FAILS")
        self.assertIn("gate G2 FAILS, 1 disagreement", str(self.error(X.Census(), self.ctx())))
        (S.log_path(self.ctx(), "census")).unlink()
        self.fake("passes")
        got = X.Census().run(self.ctx())
        self.assertEqual((got["numbers"]["catalog"], got["numbers"]["game range"], got["numbers"]["system"]), (3, 2, 1))
        self.assertIn("Not the whole game", got["message"])
        self.assertIn("lacked 6549 entries", got["message"])

    def test_dotted_ghidra_path_refused(self):
        self.cfg.pop("ghidra_dir")
        (self.ws / "project.json").write_text(json.dumps(self.cfg), encoding="utf-8")
        self.assertIn("starts with '.'", str(self.error(X.Census(), self.ctx())))


class Symbols(Base):
    def test_folders(self):
        tool = self.d / "dp"
        tool.mkdir()
        e = self.error(types.SimpleNamespace(run=lambda c: SYM.folders(c)), self.ctx(tool=tool))
        self.assertIn("no symbol folder", str(e))
        (tool / "config.local.json").write_text(json.dumps({"symbol_sets": {"xbox360": [str(self.d / "gone")]}}),
                                                encoding="utf-8")
        e = self.error(types.SimpleNamespace(run=lambda c: SYM.folders(c)), self.ctx(tool=tool))
        self.assertIn("not there", str(e))
        (self.d / "reference").mkdir()
        (self.d / "gone").mkdir()
        self.assertEqual(len(SYM.folders(self.ctx(tool=tool))), 2)

    def test_candidates_and_rank(self):
        ref = self.d / "reference"
        (ref / "a").mkdir(parents=True)
        img = synthetic_image()
        for stem in ("Game-Release", "Other"):
            (ref / "a" / f"{stem}.pdb").write_bytes(b"x")
            (ref / "a" / f"{stem}.xex").write_bytes(synthetic_xex(img, encrypt=False))
        (ref / "a" / "Lonely.map").write_text("x", encoding="utf-8")
        (ref / "a" / "sets.7z").write_bytes(b"7z")
        sets, archives = SYM.candidates([(ref, "test")])
        self.assertEqual((len(sets), archives), (3, 1))
        ranked = SYM.rank(self.ctx(), {"title ID": None, "SDK build": 0}, sets)
        self.assertEqual(ranked[-1]["skip"], "no XEX of the same name beside it")
        self.assertEqual(pathlib.Path(ranked[0]["symbols"]).name, "Game-Release.pdb")

    def test_accept_rule(self):
        def ver(pairs, calls, agree, ctrl):
            return {"tiers": {"exact": {"pairs": pairs, "calls": calls, "agree": agree, "rate": agree / calls}},
                    "control": {"exact": {"rate": ctrl}}}
        self.assertTrue(SYM.accept(ver(400, 900, 880, 0.02))[0])
        self.assertFalse(SYM.accept(ver(20, 900, 880, 0.02))[0])  # too few pairs
        self.assertFalse(SYM.accept(ver(400, 900, 700, 0.02))[0])  # agreement under 90%
        self.assertFalse(SYM.accept(ver(400, 900, 880, 0.60))[0])  # a wrong pairing does nearly as well
        self.assertFalse(SYM.accept({"tiers": {}, "control": {}})[0])
        # another game's verified set gives its SDK library names only (Too Human's PDB on Mega Man 10)
        own = {f"{i:08x}": {"name": f"Game{i}", "tier": "exact", "lib": ""} for i in range(7)}
        lib = {f"{100 + i:08x}": {"name": f"memcpy{i}", "tier": "exact", "lib": "xapilib.lib"} for i in range(60)}
        level, names, n = SYM.keep({**own, **lib})
        self.assertEqual((level, len(names), n), ("libraries", 60, 7))
        own = {f"{i:08x}": {"name": f"Game{i}", "tier": "exact", "lib": ""} for i in range(SYM.MIN_OWN)}
        self.assertEqual(SYM.keep({**own, **lib})[:2], ("game", {**own, **lib}))

    def test_verify_against_a_wrong_pairing(self):
        import pdb_names
        n = 40
        ret = {r: (None, None, [(r + 1) % n, (r + 2) % n]) for r in range(n)}
        dev = {100 + d: (None, None, [100 + (d + 1) % n, 100 + (d + 2) % n]) for d in range(n)}
        pairs = {r: (100 + r, "exact") for r in range(n)}
        v = pdb_names.verify(pairs, ret, dev)
        self.assertEqual(v["tiers"]["exact"]["rate"], 1.0)
        self.assertLess(v["control"]["exact"]["rate"], 0.2)


class Gaps(Base):
    def test_instruction_words(self):
        import onboard_gaps as G
        import ppcscan
        self.assertEqual(ppcscan.mnemonic(0x7C6C42E6), "mftb")
        self.assertEqual(ppcscan.mnemonic(0x7C2007EC | (1 << 21)), "dcbzl")
        img = bytearray(16)
        struct.pack_into(">II", img, 0, 0x7C6C42E6, 0x4E800020)
        self.assertEqual(ppcscan.refusing_ops(bytes(img), 0x82000000, 0x82000000, 8), {"mftb": 1})
        self.assertTrue(G.oracle_refusal("unsupported: system instruction mftb at 82001234").startswith(
            "oracle refused: `mftb`"))
        self.assertIn("`vpkd3d128`", G.oracle_refusal("Direct3D pack/unpack vpkd3d128 at 82000010 (semantics unverified)"))
        self.assertIn("6,549", G.NOT_FOUND["xbox360"])
        self.assertIn("not known", G.kit_section(self.d, "xbox360", [])[0][0])
        # a vector function: no REFUSE class, so its refused vector op (vrefp, an estimate) is named; a plain one is not
        img = bytearray(12)
        struct.pack_into(">III", img, 0, 0x1000010A, 0x10000000 | 1 << 21, 0x4E800020)  # vrefp v0,v0; vaddubm v1,v0,v0
        self.assertEqual(ppcscan.refusing_ops(bytes(img), 0x82000000, 0x82000000, 12), {"vrefp": 1})

    def test_kit_imports(self):
        sys.path.insert(0, str(X360 / "port" / "tools"))
        import kit_imports
        body = "\n  switch (ord) {\n  case 1: case 2:\n    return no_effect(\"x\");\n  case 3: {\n    switch (y) {\n" \
               "    case 9: return 1;\n    }\n    return 0; }\n  default: return other(ord, a);\n  }\n"
        self.assertEqual(kit_imports.cases(body), {1: True, 2: True, 3: False})
        self.assertEqual(kit_imports.routes({"xb_import_call": "KRNL(imp) && k(a); !KRNL(imp) && m(b);",
                                             "m": "default: return n(ord, x);"}), {"k": "krnl", "m": "xam", "n": "xam"})
        f = kit_imports.features()
        self.assertGreater(len(f["libraries"]["xboxkrnl.exe"]), 10)
        self.assertGreater(len(f["libraries"]["xam.xex"]), 10)
        self.assertEqual((kit_imports.lib_key("xboxkrnl.exe"), kit_imports.lib_key("xam.xex"),
                          kit_imports.lib_key("xbdm.xex")), ("xboxkrnl.exe", "xam.xex", None))


class Boot(Base):
    def test_inputs_listed(self):
        ws = self.d / "decomp"
        ws.mkdir()
        (ws / "catalog_inputs.json").write_text(json.dumps({"functions": [{"addr": "82010000"}]}), encoding="utf-8")
        e = self.error(X.Boot(), self.ctx())
        self.assertIn("no bring-up tried", str(e))
        self.assertTrue(any("the Direct3D cut for this game" in m for m in e.missing))
        self.assertTrue(any("game-data" in m for m in e.missing))
        self.assertIn("boundary.py --ws", e.supply)


class Table(Base):
    def test_every_x360_stage_wired(self):
        self.assertEqual({s.name: s.adapters["xbox360"].kind for s in S.STAGES if s.adapters["xbox360"].kind != "built"},
                         {"machine": "not-applicable", "register": "not-applicable"})  # both Saturn's only, so far
        self.assertTrue(S.BY_NAME["symbols"].heavy)
        self.assertEqual(S.BY_NAME["symbols"].adapters["dreamcast"].kind, "not-applicable")

    def test_host_passes_inputs(self):
        import onboard_remote
        seen, old = {}, (onboard_remote.run, onboard_remote.remote_dp, onboard_remote.sync)
        onboard_remote.sync = lambda *x, **k: {"disc": None, "notes": []}
        onboard_remote.remote_dp = lambda host, given, tool: "D:/dp"
        onboard_remote.run = lambda host, dp, project, args, seconds: seen.setdefault("args", args) and {
            "error": "stop", "missing": [], "supply": ""}
        try:
            a = types.SimpleNamespace(host="Build box A", remote_dp=None, remote_project="C:/p", disc=None,
                                      inputs="C:/ws", only=None, seconds=100, late_seconds=0)
            onboard.remote_batch(self.ctx(), a, ["disc"], {})
        finally:
            onboard_remote.run, onboard_remote.remote_dp, onboard_remote.sync = old
        self.assertEqual(seen["args"][seen["args"].index("--inputs") + 1], "C:/ws")
        # the remote stages keep their bounds under the remote wrapper's (seconds), so a budget stop writes its verdict
        self.assertEqual(seen["args"][seen["args"].index("--seconds") + 1], "100")
        self.assertEqual(seen["args"][seen["args"].index("--late-seconds") + 1], "0")  # the retry pass held back there
        import onboard_estimate as E
        tx = E.Translate()
        self.assertIn("--fn-seconds-late 0 ", tx.calls(types.SimpleNamespace(seconds=2400, late_seconds=0))[0])
        self.assertIn(f"--fn-seconds-late {E.FN_LATE_S} ", tx.calls(types.SimpleNamespace(seconds=2400))[0])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
