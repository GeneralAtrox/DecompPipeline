"""tools/AutoOnboard/autoonboard.py on the GameCube (tools/AutoOnboard/onboard_gc.py, onboard_symbols' GameCube source, gc_names.py), offline:
1. every adapter module imports on its own, before onboard_stages (the stage table is built on first use);
2. project, disc and extract run end to end on a synthetic disc (tests/test_gamecube_disc.make_disc: three REL modules,
   linked by the setup); census stops as heavy work; the symbols stage comes after the census;
3. the census's REL check: a disc whose RELs were not linked is an error naming them;
4. gc_names.py: a CodeWarrior map with its .dol, and an ELF with a symbol table, matched to a workspace's catalog by
   pdb_names.pair(): names, the library rule (SDK, MSL, MetroTRK by library file or by name; the rest the game's own);
5. the GameCube rank: same game ID first, then the nearest SDK OS build; sets with no names or no code listed not
   scored; the symbol-folder error names the GameCube;
6. gaps: the oracle's GameCube refusals are grouped by instruction (mftb at the end of the text, FPSCR moves), and the
   kit section says there is no GameCube kit and lists, from the code, the hardware blocks and SDK calls;
7. boot: no build tried, every missing input named, the missing kit first.
Run: python -W error tests/test_onboard_gc.py   (needs git on PATH for 2)
"""
import contextlib
import io
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools" / "AutoOnboard"), str(TOOL / "tools"), str(TOOL / "tests"), str(TOOL / "targets" / "gamecube_ppc" / "tools")]
import autoonboard as onboard  # noqa: E402
import onboard_gaps as G  # noqa: E402
import onboard_gc as GC  # noqa: E402
import onboard_stages as S  # noqa: E402
import onboard_symbols as SYM  # noqa: E402
from test_gamecube_disc import FILES, make_disc  # noqa: E402

BLR, NOP = 0x4E800020, 0x60000000
BASE = 0x80003100


def be(*ws):
    return struct.pack(f">{len(ws)}I", *ws)


def bl(frm, to):
    return 0x48000001 | ((to - frm) & 0x03FFFFFC)


def dol(text):
    """A .dol with one text section at BASE and one small data section."""
    offs, addrs, sizes = [0] * 18, [0] * 18, [0] * 18
    offs[0], addrs[0], sizes[0] = 0x100, BASE, len(text)
    offs[7], addrs[7], sizes[7] = 0x100 + len(text), 0x80100000, 0x20
    head = struct.pack(">18I18I18I3I", *offs, *addrs, *sizes, 0x80100020, 0x20, BASE).ljust(0x100, b"\0")
    return head + text + b"\x11" * 0x20


def functions(n=60):
    """n functions of 16 bytes: two unique immediates, a call to the next one, blr."""
    words = []
    for i in range(n):
        a = BASE + 16 * i
        words += [0x38600000 | i, 0x38840000 | (3 * i + 1), bl(a + 8, BASE + 16 * ((i + 1) % n)), BLR]
    return be(*words)


def elf(text, syms):
    """A 32-bit big-endian ELF: .text at BASE, .symtab (syms: (name, value, size, info, shndx)), .strtab, .shstrtab."""
    strtab, symtab = bytearray(b"\0"), bytearray(16)
    for name, value, size, info, shndx in syms:
        symtab += struct.pack(">IIIBBH", len(strtab), value, size, info, 0, shndx)
        strtab += name.encode() + b"\0"
    shstr = b"\0.text\0.symtab\0.strtab\0.shstrtab\0"
    body = bytearray(52)
    t_off = len(body)
    body += text
    s_off = len(body)
    body += symtab
    st_off = len(body)
    body += strtab
    sh_off = len(body)
    body += shstr
    shoff = len(body)
    secs = [(0,) * 10, (1, 1, 6, BASE, t_off, len(text), 0, 0, 4, 0), (7, 2, 0, 0, s_off, len(symtab), 3, 1, 4, 16),
            (15, 3, 0, 0, st_off, len(strtab), 0, 0, 1, 0), (23, 3, 0, 0, sh_off, len(shstr), 0, 0, 1, 0)]
    for s in secs:
        body += struct.pack(">10I", *s)
    body[:52] = struct.pack(">16sHHIIIIIHHHHHH", b"\x7fELF\x01\x02\x01" + bytes(9), 2, 20, 1, BASE, 0, shoff, 0, 52, 0,
                            0, 40, len(secs), 4)
    return bytes(body)


class Base(unittest.TestCase):
    def setUp(self):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="onbgc_", dir=TOOL / "tests" / ".work"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def ws(self, text, cat):
        ws = self.d / "proj" / "decomp"
        (ws / "state").mkdir(parents=True)
        (ws / "main.dol").write_bytes(dol(text))
        (ws / "project.json").write_text(json.dumps({"binary": "main.dol", "name": "T"}), encoding="utf-8")
        (ws / "state" / "catalog.json").write_text(json.dumps({"functions": cat}), encoding="utf-8")
        return ws


class Imports(unittest.TestCase):
    def test_each_adapter_alone(self):
        for mod in ("onboard_symbols", "onboard_x360", "onboard_dc", "onboard_gc", "onboard_gaps", "onboard_estimate"):
            r = subprocess.run([sys.executable, "-W", "error", "-c", f"import sys; sys.path[:0] = [{str(TOOL / 'tools' / 'AutoOnboard')!r}, {str(TOOL / 'tools')!r}]; "
                                f"import {mod}; import onboard_stages as S; print(len(S.STAGES))"],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "11"), f"{mod}: {r.stderr[-400:]}")

    def test_order(self):
        self.assertEqual(S.NAMES, ["project", "machine", "disc", "extract", "census", "symbols", "boot", "register",
                                   "estimate", "translate", "gaps", "overview"])
        # machine and register are Saturn's (the GameCube's boot and launcher entry come with its own work)
        self.assertEqual({s.name: s.adapters["gamecube"].kind for s in S.STAGES},
                         {s.name: ("not-applicable" if s.name in ("machine", "register") else "built") for s in S.STAGES})


class Synthetic(Base):
    def test_disc_to_census(self):
        img, main = make_disc()
        (self.d / "game.iso").write_bytes(img)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = onboard.main(["--project", str(self.d), "--console", "gamecube", "--name", "Test GC"])
        self.assertEqual(rc, 2)
        v = onboard.load(self.d)
        self.assertEqual([v[n]["status"] for n in ("project", "disc", "extract", "census")],
                         ["ok", "ok", "ok", "error"], out.getvalue())
        self.assertNotIn("symbols", v)  # after the census now
        n = v["disc"]["numbers"]
        self.assertEqual((n["game ID"], n["title"], n["format"], n["REL modules"], n["main.dol bytes"]),
                         ("GTSE01", "Test Game", "plain", 3, len(main)))
        self.assertEqual((v["extract"]["numbers"]["linked"], v["extract"]["numbers"]["asset files"]), (3, len(FILES)))
        self.assertEqual((self.d / "game-data" / "sys.txt").read_bytes(), FILES["/sys.txt"])
        self.assertIn("heavy work", v["census"]["message"])

    def test_rels_not_linked(self):
        ws = self.d / "decomp"
        (ws / "disc").mkdir(parents=True)
        (ws / "disc" / "disc.json").write_text(json.dumps({"rels": [{"path": "/rel/a.rel"}]}), encoding="utf-8")
        nums, problems = GC.rel_check(ws, [])
        self.assertEqual(nums["linked"], 0)
        self.assertIn("/rel/a.rel", problems[0])


class Names(Base):
    def setUp(self):
        super().setUp()
        self.text = functions()
        self.cat = [{"addr": f"{BASE + 16 * i:08x}", "bytes": 16} for i in range(60)]

    def test_map_with_dol(self):
        import gc_names
        s = self.d / "set"
        s.mkdir()
        (s / "main.dol").write_bytes(dol(self.text))
        rows = [f"  {16 * i:08x} 000010 {BASE + 16 * i:08x}  4 {'OSFn' if i < 10 else 'GameFn'}{i} \t"
                + ("os.a OSFn.c" if i < 10 else "game.o") for i in range(60)]
        (s / "game.map").write_text(".text section layout\n" + "\n".join(rows) + "\n", encoding="utf-8")
        ws = self.ws(self.text, self.cat)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = gc_names.main(["--set", str(s), "--ws", str(ws), "--out", str(self.d / "o"), "--verify"])
        self.assertEqual(rc, 0)
        names = json.loads((self.d / "o" / "names.json").read_text(encoding="utf-8"))
        self.assertEqual(len(names), 60)
        self.assertEqual((names[f"{BASE:08x}"]["name"], names[f"{BASE:08x}"]["lib"]), ("OSFn0", "os.a"))
        self.assertEqual((names[f"{BASE + 160:08x}"]["name"], names[f"{BASE + 160:08x}"]["lib"]), ("GameFn10", ""))
        ver = json.loads((self.d / "o" / "verify.json").read_text(encoding="utf-8"))
        self.assertTrue(SYM.accept(ver)[0], ver)  # 60 exact pairs whose callees pair too
        self.assertEqual(SYM.keep(names)[0], "game")  # 50 own exact pairs: this game's set

    def test_elf_library_rule(self):
        import gc_names
        syms = [("OSReport.c", 0, 0, 0x04, 0xFFF1), ("OSReport", BASE, 16, 0x02, 1), ("GXInit.c", 0, 0, 0x04, 0xFFF1),
                ("helper", BASE + 16, 16, 0x02, 1), ("memcpy", BASE + 32, 16, 0x12, 1),
                ("TRKInitialize", BASE + 48, 16, 0x12, 1), ("GameMain", BASE + 64, 16, 0x12, 1),
                ("AIController_Update", BASE + 80, 16, 0x12, 1), ("__vt__4Game", 0x80100000, 8, 0x11, 2)]
        p = self.d / "g.elf"
        p.write_bytes(elf(functions(8), syms))
        img, base, funcs, vts = gc_names.from_elf(p)
        self.assertEqual((base, len(img), vts), (BASE, 128, [0x80100000]))
        self.assertEqual({f["name"]: f["lib"] for f in funcs.values()},
                         {"OSReport": "sdk", "helper": "sdk", "memcpy": "msl", "TRKInitialize": "trk", "GameMain": "",
                          "AIController_Update": ""})

    def test_library_runs(self):
        """Plain-named functions inside one library's block are that library's; the game's own stay the game's."""
        import gc_names
        libs = ["", "", "sdk", "", "", "sdk", "", "msl", "", "trk"] + ["sdk"] + [""] * (gc_names.MAX_RUN + 1) + ["sdk"]
        funcs = {0x80003000 + 4 * i: {"name": "f%d" % i, "lib": lib} for i, lib in enumerate(libs)}
        gc_names.library_runs(funcs)
        got = [funcs[a]["lib"] for a in sorted(funcs)]
        self.assertEqual(got[:10], ["", "", "sdk", "sdk", "sdk", "sdk", "", "msl", "", "trk"])
        self.assertEqual(set(got[11:-1]), {""})  # longer than MAX_RUN: the game's own code

    def test_stamp_follows_matcher(self):
        """A kept match is redone when the matcher's source changes, not only when the set's files do."""
        s = self.d / "set"
        s.mkdir()
        (s / "a.elf").write_bytes(b"x")
        (self.d / "m.py").write_text("one", encoding="utf-8")

        class Src(SYM.GameCube):
            tools = (str(self.d / "m.py"),)
        st = SYM.Symbols(Src)
        first = st.stamp({"symbols": str(s)})
        (self.d / "m.py").write_text("two", encoding="utf-8")
        self.assertNotEqual(first, st.stamp({"symbols": str(s)}))


class Rank(Base):
    def test_rank_and_skips(self):
        sets = [{"symbols": "far", "game_id": "GXXE01", "sdk_os_built": "Jan  1 2001 00:00:00", "fn_names": True,
                 "bytes_match": "elf", "lib_obj": "obj"},
                {"symbols": "near", "game_id": "GYYE01", "sdk_os_built": "Sep 27 2002 01:00:00", "fn_names": True,
                 "bytes_match": "dol", "lib_obj": "lib+obj"},
                {"symbols": "same", "game_id": "GEAP8P", "sdk_os_built": None, "fn_names": True, "bytes_match": "elf",
                 "lib_obj": None},
                {"symbols": "nonames", "game_id": "GEAE8P", "sdk_os_built": None, "fn_names": False,
                 "bytes_match": "dol", "lib_obj": None},
                {"symbols": "relonly", "game_id": "GZZE01", "sdk_os_built": None, "fn_names": True,
                 "bytes_match": "rel", "lib_obj": None}]
        r = SYM.gc_rank(None, {"game ID": "GEAE8P", "SDK OS build": "Sep  5 2002 05:33:05"}, sets)
        self.assertEqual([c["symbols"] for c in r], ["same", "near", "far", "nonames", "relonly"])
        self.assertIn("no function names", r[3]["skip"])
        self.assertIn("REL", r[4]["skip"])

    def test_candidates_and_folder_error(self):
        root = self.d / "PDBs"
        for name, files in (("[GC] A [GAAE01]", ("a.elf",)), ("[GC] B [GBBE01]", ("b.map", "main.dol")),
                            ("[GC] C [GCCE01]", ("readme.txt",))):
            (root / name).mkdir(parents=True)
            for f in files:
                (root / name / f).write_bytes(b"x")
        sets, _ = SYM.gc_candidates([(root, "t")], [{"set": "[GC] A [GAAE01]", "game_id": "GAAE01",
                                                    "sdk_os_built": None, "fn_names": True, "bytes_match": "elf",
                                                    "lib_obj": "obj"}])
        self.assertEqual([(pathlib.Path(c["symbols"]).name, c["in_table"], c["bytes_match"]) for c in sets],
                         [("[GC] A [GAAE01]", True, "elf"), ("[GC] B [GBBE01]", False, "dol")])
        ctx = types.SimpleNamespace(project=self.d / "p", tool=self.d, console="gamecube")
        with self.assertRaises(S.StageError) as e:
            SYM.folders(ctx, SYM.GAMECUBE.shared)
        self.assertIn("Integrations\\GameCube", str(e.exception))
        self.assertIn('"gamecube"', e.exception.missing[0])


class Gaps(Base):
    def test_refusals_by_instruction(self):
        for text, want in (("cache/system instruction mftb", "mftb"), ("FPSCR move mtfsf (rounding mode)", "mtfsf"),
                           ("CALLOTHER returnFromInterrupt in rfi (no emulator callback)", "rfi"),
                           ("CALLOTHER trapWord in twi", "twi"), ("executes code outside its body at 80001234", None)):
            self.assertEqual(G.instruction(text), want, text)

    def test_kit_section_from_code(self):
        f2 = BASE + 16
        text = be(0x3C60CC00, 0xB0032002, bl(BASE + 8, f2), BLR, NOP, BLR, NOP, NOP)
        ws = self.ws(text, [{"addr": f"{BASE:08x}", "bytes": 16}, {"addr": f"{f2:08x}", "bytes": 8}])
        sym = ws.parent / S.STATE_DIR / "symbols"
        sym.mkdir(parents=True)
        (sym / "names.json").write_text(json.dumps({f"{f2:08x}": {"name": "OSReport", "lib": "sdk", "tier": "exact"}}),
                                        encoding="utf-8")
        lines, nums = G.kit_section(ws, "gamecube", [f"{BASE:08x}", f"{f2:08x}"])
        text = "\n".join(lines)
        self.assertIn("No GameCube port kit exists", text)
        self.assertIn("VI video interface: 1 function(s), 1 register(s) cc002002-cc002002", text)
        self.assertIn("`OSReport` (1 call(s) from 1 function(s))", text)
        self.assertEqual((nums["hardware blocks"], nums["library calls named"]), (1, 1))


class Boot(Base):
    def test_inputs_listed(self):
        ctx = types.SimpleNamespace(project=self.d, tool=TOOL, console="gamecube")
        with self.assertRaises(S.StageError) as e:
            GC.Boot().run(ctx)
        self.assertIn("no GameCube port kit", str(e.exception))
        self.assertIn("a GameCube port kit", e.exception.missing[0])
        self.assertEqual(len(e.exception.missing), 4)


class StoppedTranslate(Base):
    def test_stop_leaves_no_ghidra_lock(self):
        """A translate run stopped at its time bound leaves no Ghidra project locked by the dead run."""
        import json
        from unittest import mock
        import onboard_estimate as E
        ws, gdir = self.d / "decomp", self.d / "gh"
        (ws / "state").mkdir(parents=True)
        (ws / "project.json").write_text(json.dumps({"ghidra_dir": str(gdir)}), encoding="utf-8")
        slot = gdir / "ghidra_slots" / "slot1"
        slot.mkdir(parents=True)
        (slot / "geae8p.lock").write_text("x", encoding="utf-8")
        (slot / "geae8p.lock~").write_text("x", encoding="utf-8")
        (slot / "geae8p.gpr").write_text("x", encoding="utf-8")
        ctx = types.SimpleNamespace(project=self.d, console="gamecube", name="t", seconds=900)
        with mock.patch.object(E.S, "run_tool", return_value=(124, [])), mock.patch.object(E.S, "ws_of", return_value=ws):
            with self.assertRaises(S.StageError):
                E.Translate().run(ctx)
        self.assertEqual(sorted(q.name for q in slot.iterdir()), ["geae8p.gpr"])


if __name__ == "__main__":
    unittest.main()
