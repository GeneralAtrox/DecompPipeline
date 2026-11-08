"""decomp.py divergence (core/divergence.py, port/tools/divergence.py, ref_fast.py, the port's --watch-ram):
1. The first divergence: paired frames compared word by word, the words that differ in each of the first --ignore
   frames left out (noise), the game's documented ranges left out, the first other word reported with the
   reference's value and the port's.
2. The writer: the watch's log lines named from port.map, then to the guest function and its readable file by the
   build's dispatch table; a write outside the game's functions has no address.
3. The requeue: the evidence appended once to agent_notes/ADDR.md (a second run adds nothing), only for a readable
   writer.
4. Coverage: functions entered and branch sides taken from the block-start PCs (core/div_coverage.py).
4b. In game (core/in_game.py): a readable function is proven only by a clean scenario that entered it, in a build that
   compiled it (PORT_READABLE) from a file no newer than port.exe; proofs accumulate, a diverged scenario's writer
   loses its entry, and an edited file loses its proof.
5. The fast reference's Flycast settings (research.RamFrames*), the kit runtime's --watch-ram-frame wiring (a flag
   the base's scene_dump does not claim) and the default build's gen folder (tests/test_ref_fast.py: noise globals).
Synthetic RAM frames, logs and maps; no Flycast, no port build. Run: python -W error tests/test_divergence.py
"""
import atexit
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
if not os.environ.get("DECOMP_PROJECT"):  # standalone: a fixture project (tests/test_tooling.py sets its own)
    (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
    _PROJ = tempfile.TemporaryDirectory(prefix="divergence_", dir=TOOL / "tests" / ".work")
    (pathlib.Path(_PROJ.name) / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name":
                                                                     "fixture", "binary": "game.bin"}), encoding="utf-8")
    os.environ["DECOMP_PROJECT"] = _PROJ.name
    atexit.register(_PROJ.cleanup)
sys.path[:0] = [str(TOOL), str(TOOL / "port" / "tools")]

import divergence as kit_div  # noqa: E402  (port/tools/divergence.py)
import ram_delta  # noqa: E402
import ref_fast  # noqa: E402
from core import divergence as core_div, in_game as core_in_game, paths  # noqa: E402

BASE, SIZE, BLOCK = 0x8C000000, 0x1000000, 32


def write_raw(path, frames):
    """RAMF records (port/runtime/ram_frames.c) for [(label, emu, {address: u32})]: the first frame in full."""
    ram, prev = bytearray(SIZE), None
    with open(path, "wb") as f:
        for n, (label, emu, words) in enumerate(frames, 1):
            for a, v in words.items():
                ram[a - BASE:a - BASE + 4] = struct.pack("<I", v)
            if prev is None:
                runs = [(0, SIZE)]
            else:
                runs = sorted({(a - BASE) // BLOCK * BLOCK for a in words if ram[(a - BASE) // BLOCK * BLOCK:][:BLOCK]
                               != prev[(a - BASE) // BLOCK * BLOCK:][:BLOCK]})
                runs = [(s, BLOCK) for s in runs]
            f.write(struct.pack("<IIIiI", 0x464D4152, n, label, emu, len(runs)))
            for s, length in runs:
                f.write(struct.pack("<II", s, length))
            for s, length in runs:
                f.write(bytes(ram[s:s + length]))
            prev = bytes(ram)


class First(unittest.TestCase):
    def test_noise_ranges_and_first_word(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            stack, ranged, real = 0x8CFFFFF0, 0x8C100004, 0x8C020000
            ref = [(1, 10, {stack: 1}), (2, 12, {stack: 2}), (3, 14, {ranged: 5}), (4, 16, {real: 5})]
            port = [(1, 0, {stack: 9}), (2, 1, {stack: 8}), (3, 2, {stack: 2, ranged: 6}), (4, 3, {real: 7})]
            write_raw(d / "ref.raw", ref)
            write_raw(d / "port.raw", port)
            ram_delta.pack_raw(d / "ref.raw", d / "ref", 300)
            ram_delta.pack_raw(d / "port.raw", d / "port", 300)
            ranges = [(0x8C100000, 0x8C100010, "a work area the port replaces")]
            first, paired, noise = kit_div.first_divergence(d / "ref", d / "port", None, 2, ranges, [])
            self.assertEqual((paired, noise), (4, 1))
            self.assertEqual((first["port_frame"], first["ref_frame"], first["vblank"], first["count"]), (4, 4, 16, 1))
            self.assertEqual(first["words"][0], {"addr": "8c020000", "name": "", "expected": "00000005",
                                                 "actual": "00000007"})
            # without the documented range the frame-3 word is the divergence
            first, _, _ = kit_div.first_divergence(d / "ref", d / "port", None, 2, [], [])
            self.assertEqual((first["port_frame"], first["words"][0]["addr"]), (3, "8c100004"))
            # --from skips port frames before it (the noise frames come after it)
            first, paired, _ = kit_div.first_divergence(d / "ref", d / "port", None, 1, [], [], start=3)
            self.assertEqual((first["port_frame"], paired), (4, 2))

    def test_float_tolerance_is_counted_apart(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            f, i, real = 0x8C020000, 0x8C020004, 0x8C020008
            ref = [(1, 10, {}), (2, 12, {f: 0x3F800000, i: 0}), (3, 14, {real: 5})]
            port = [(1, 0, {}), (2, 1, {f: 0x3F800003, i: 0}), (3, 2, {real: 7})]
            write_raw(d / "ref.raw", ref)
            write_raw(d / "port.raw", port)
            ram_delta.pack_raw(d / "ref.raw", d / "ref", 300)
            ram_delta.pack_raw(d / "port.raw", d / "port", 300)
            close = {}
            first, _, _ = kit_div.first_divergence(d / "ref", d / "port", None, 0, [], [], ulps=64, close=close)
            self.assertEqual((first["port_frame"], first["words"][0]["addr"]), (3, "8c020008"))
            self.assertEqual((close["frames"], close["words"], close["max_ulps"], close["first"]["port_frame"]),
                             (2, 2, 3, 2))  # the word stays off in frame 3 too
            first, _, _ = kit_div.first_divergence(d / "ref", d / "port", None, 0, [], [])  # no tolerance
            self.assertEqual(first["port_frame"], 2)
            self.assertTrue(kit_div.float_close(0x3F800000, 0x3F800040, 64))
            self.assertFalse(kit_div.float_close(0x3F800000, 0x3F800041, 64))
            self.assertFalse(kit_div.float_close(0x3F800000, 0xBF800000, 64))  # the sign
            self.assertFalse(kit_div.float_close(0x3F7FFFFF, 0x3F800000, 64))  # the exponent
            self.assertFalse(kit_div.float_close(0, 1, 64))  # integers (exponent 0) never

    def test_baseline_leaves_out_what_both_builds_hold(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            kit, bug = 0x8C020000, 0x8C020004  # a kit-side word both builds hold; the word only the tested C gets wrong
            stub = 0x8C010000  # a host jump stub: each executable's own from the first frame (its layout)
            fp = 0x8C020008  # a function pointer the kit stores: the same function at each build's own address
            ref = [(1, 10, {stub: 1}), (2, 12, {kit: 1, fp: 0x8C058002}), (3, 14, {bug: 5}), (4, 16, {})]
            base = [(1, 0, {stub: 0x0173FBE9}), (2, 1, {kit: 2, fp: 0x5A06E740}), (3, 2, {bug: 5}), (4, 3, {})]
            port = [(1, 0, {stub: 0x0173EFE9}), (2, 1, {kit: 2, fp: 0x5A06E6EA}), (3, 2, {bug: 6}), (4, 3, {})]
            maps = ({0x5A06E740: "_App_MainLoopEntry+0"}.get, {0x5A06E6EA: "_App_MainLoopEntry+0"}.get)
            for name, frames in (("ref", ref), ("base", base), ("port", port)):
                write_raw(d / f"{name}.raw", frames)
                ram_delta.pack_raw(d / f"{name}.raw", d / name, 300)
            close = {}
            first, _, _ = kit_div.first_divergence(d / "ref", d / "port", None, 0, [], [], close=close,
                                                   base=d / "base", maps=maps)
            self.assertEqual(close["baseline_layout_words"], 1)
            self.assertEqual((first["port_frame"], first["count"], first["words"][0]["addr"]), (3, 1, "8c020004"))
            first, _, _ = kit_div.first_divergence(d / "ref", d / "port", None, 0, [], [], base=d / "base")
            self.assertEqual(first["words"][0]["addr"], "8c020008")  # without the maps: the pointer
            first, _, _ = kit_div.first_divergence(d / "ref", d / "port", None, 0, [], [])  # no baseline
            self.assertEqual((first["port_frame"], first["words"][0]["addr"]), (1, "8c010000"))
            first, _, _ = kit_div.first_divergence(d / "ref", d / "base", None, 0, [], [], base=d / "base")
            self.assertIsNone(first)  # a build against its own RAM: nothing


MAP = """ Preferred load address is 00400000
 0001:00001000       _Fx_Update                 00401000 f   fx.obj
 0001:00001100       _Fx_Main                   00401100 f   fx.obj
 0001:00002000       _memcpy                    00402000 f   libcmt:memcpy.obj
"""
LOG = """lockstep watch: frame 4 word 8c020000 image 00500000
lockstep watch: frame 4 write 00000007 after 00501020 stack 00000000 00501180 00502010
"""


class Writer(unittest.TestCase):
    def test_named_to_readable_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "port.map").write_text(MAP, encoding="ascii")
            (d / "port.log").write_text(LOG, encoding="ascii")
            gen = d / "game" / "build" / "x86-t_gen"
            gen.mkdir(parents=True)
            (gen / "dispatch_00.c").write_text('    {0x8c011000u, (PortFn)Fx_Main, "Fx_Main"},\n'
                                               '    {0x8c011200u, (PortFn)Fx_Update, "Fx_Update"},\n', encoding="ascii")
            src = d / "decomp" / "readable" / "src"
            src.mkdir(parents=True)
            (src / "8c011200.c").write_text("void Fx_Update(void) {}\n", encoding="ascii")
            notes, hits = kit_div.watch_hits(d / "port.log", d / "port.map", callers=6)
            self.assertEqual(hits, [{"value": "00000007", "after": "00401020", "in": "Fx_Update+20",
                                     "callers": ["Fx_Main+80", "memcpy+10"], "word": "8c020000"}])
            w = kit_div.writer_of(hits, kit_div.dispatch_names(d / "game", "x86-t"), d / "decomp")
            self.assertEqual((w["function"], w["addr"], w["file"]), ("Fx_Update", "8c011200", str(src / "8c011200.c")))
            self.assertEqual([(c["function"], c["addr"]) for c in w["chain"]], [("Fx_Main", "8c011000"),
                                                                                 ("memcpy", None)])
            self.assertIsNone(kit_div.writer_of([], {}, d / "decomp"))
            later = {"value": "00000009", "after": "00401130", "in": "Fx_Main+30", "callers": [], "word": "8c020000"}
            w = kit_div.writer_of(hits + [later], kit_div.dispatch_names(d / "game", "x86-t"), d / "decomp")
            self.assertEqual((w["function"], w["value"]), ("Fx_Main", "00000009"))  # the write the frame shows
            self.assertEqual([x["function"] for x in w["writes"]], ["Fx_Update", "Fx_Main"])

    def test_several_words_earliest_last_write(self):
        log = ("lockstep watch: frame 4 word 8c020000 8c030010 views 2 image 00500000\n"
               "lockstep watch: frame 4 write 00000001 after 00501130 word 0 seq 0 stack 00000000\n"
               "lockstep watch: frame 4 write 00000003 after 00501130 word 0 seq 2 stack 00000000\n"
               # a word's last write past its share comes out as the watch ends: seq gives its place
               "lockstep watch: frame 4 write 00000002 after 00501020 word 1 seq 1 stack 00501180\n")
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "port.map").write_text(MAP, encoding="ascii")
            (d / "port.log").write_text(log, encoding="ascii")
            notes, hits = kit_div.watch_hits(d / "port.log", d / "port.map")
            self.assertEqual([(h["word"], h["in"]) for h in hits], [("8c020000", "Fx_Main+30"),
                                                                     ("8c030010", "Fx_Update+20"),
                                                                     ("8c020000", "Fx_Main+30")])
            w = kit_div.writer_of(hits, {"Fx_Update": "8c011200"}, None)
            # 8c020000's value is the third write's; 8c030010's last write ran before it, so it is nearest the cause
            self.assertEqual((w["function"], w["word"], w["value"]), ("Fx_Update", "8c030010", "00000002"))
            self.assertEqual(core_div.written_word({"words": [{"addr": "8c020000"}, {"addr": "8c030010"}]}, w),
                             {"addr": "8c030010"})

    def test_watch_words_one_per_global_first(self):
        rows = [{"addr": a, "name": n} for a, n in [("8c0a8478", "g_stack+7c8"), ("8c0a847c", "g_stack+7cc"),
                                                    ("8c0d5a88", "g_view+184"), ("8c0d5a90", "g_view+18c"),
                                                    ("8c0fc3d8", "g_dir"), ("8c0fce04", None)]]
        self.assertEqual(kit_div.watch_words({"words": rows}), [0x8C0A8478, 0x8C0D5A88, 0x8C0FC3D8, 0x8C0FCE04])
        self.assertEqual(kit_div.watch_words({"words": rows[:3]}), [0x8C0A8478, 0x8C0D5A88, 0x8C0A847C])

    def test_original_pc_to_catalog_function(self):
        with tempfile.TemporaryDirectory() as d:
            state = pathlib.Path(d) / "state"
            state.mkdir()
            (state / "catalog.json").write_text(json.dumps({"functions": [
                {"addr": "8c011200", "bytes": 64}, {"addr": "8c011000", "bytes": 32}]}), encoding="utf-8")
            funcs = kit_div.catalog_functions(d)
            self.assertEqual(kit_div.function_at(funcs, 0x8C011210), "8c011200")
            self.assertEqual(kit_div.function_at(funcs, 0x0C01101E), "8c011000")  # a mirror
            self.assertIsNone(kit_div.function_at(funcs, 0x8C011020))  # between functions
            self.assertIsNone(kit_div.function_at(funcs, 0x8C010000))
            self.assertEqual(kit_div.catalog_functions(None), [])


class Requeue(unittest.TestCase):
    def result(self, file):
        return {"status": "diverged", "first": {"port_frame": 4, "vblank": 16, "words": [
            {"addr": "8c020000", "name": "g_fx", "expected": "00000005", "actual": "00000007"}]},
            "writer": {"function": "Fx_Update", "addr": "8c0f0f00", "file": file, "value": "00000007",
                       "chain": [{"function": "Fx_Main", "addr": "8c011000"}]}}

    def test_note_once_and_readable_only(self):
        note = paths.AGENT_NOTES / "8c0f0f00.md"
        try:
            note.unlink(missing_ok=True)
            self.assertEqual(core_div.requeue({"s": self.result(None)}, lambda m: None), [])  # not readable: no note
            self.assertFalse(note.exists())
            paths.AGENT_NOTES.mkdir(exist_ok=True)
            note.write_text("An earlier note.\n", encoding="utf-8")
            for _ in range(2):
                self.assertEqual(core_div.requeue({"s": self.result("x.c")}, lambda m: None), ["8c0f0f00"])
            text = note.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("An earlier note.\n\n" + core_div.NOTE_HEAD))
            self.assertEqual(text.count(core_div.NOTE_HEAD), 1)
            self.assertIn("8c020000 (g_fx) holds 00000007 in the port where the original holds 00000005", text)
            self.assertIn("called from Fx_Main (8c011000)", text)
        finally:
            note.unlink(missing_ok=True)


class Coverage(unittest.TestCase):
    def test_functions_and_branch_sides(self):
        from core import div_coverage, mid_entries, sh4_asm
        bodies = mid_entries.Bodies("8c010000 8c010000 8c010020\n8c010040 8c010040 8c010060\n"
                                    "8c010080 8c010080 8c0100a0\n", [0x8C010000, 0x8C010040, 0x8C010080])
        listing = sh4_asm.parse("""
  8c010000  tst r4,r4
  8c010002  bt 0x8c010010
  8c010004  mov #0x1,r0
  8c010006  bf/s 0x8c010014
  8c010008  _nop
""")
        by = {"boot": {0x8C010000, 0x0C010004}, "level": {0x8C010040, 0x8C010014}}  # a P0 mirror folds to P1
        rep = div_coverage.report(by, bodies, {0x8C010000: listing}, lambda e: e != 0x8C010040)
        self.assertEqual(rep["scenarios"]["boot"], {"functions": 1, "entered": ["8c010000"]})
        self.assertEqual(rep["scenarios"]["level"]["entered"], ["8c010000"])  # 8c010040 is outside the scope
        self.assertEqual(rep["functions"], {"in_scope": 2, "entered": 1, "never": ["8c010080"]})
        self.assertEqual(rep["branch_sides"]["in_entered"], 4)
        self.assertEqual([(m["branch"], m["side"]) for m in rep["branch_sides"]["never"]],
                         [("8c010002", "taken"), ("8c010006", "fall-through")])
        self.assertEqual(rep["figure"], {"functions": 0.5, "branch_sides": 0.5})
        self.assertIn("1/2 functions entered, 2/4 branch sides", div_coverage.summary(rep))


class InGame(unittest.TestCase):
    """core/in_game: the stamping rule and readable_proven."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="in_game_", dir=TOOL / "tests" / ".work"))
        self.ws, self.game = self.tmp / "ws", self.tmp / "port"
        src = self.ws / "readable" / "src"
        src.mkdir(parents=True)
        # a: proven; b: entered only in the diverged scenario; c: not in the build; d: newer than port.exe;
        # e: an earlier proof kept; f: an earlier proof whose function is a diverged scenario's writer; g: in the build
        # and clean, but also the diverged scenario's writer this run
        for x in "abcdefg":
            (src / f"8c0000{x}0.c").write_text(f"int f_{x}(void) {{ return 0; }}\n", encoding="utf-8")
        (self.ws / "state").mkdir()
        (self.ws / "state" / "readable.json").write_text(json.dumps(
            {f"8c0000{x}0": {"status": "readable"} for x in "abcdefg"}), encoding="utf-8")
        old = {"sha": "", "scenario": "old", "when": "2026-11-01", "build_dir": "x86-agent"}
        (self.ws / "state" / "in_game.json").write_text(json.dumps(
            {"8c0000e0": dict(old, sha=core_in_game.sha(src / "8c0000e0.c")),
             "8c0000f0": dict(old, sha=core_in_game.sha(src / "8c0000f0.c"))}), encoding="utf-8")
        gen = self.game / "build" / "x86-agent_gen"
        gen.mkdir(parents=True)
        listed = "\n".join(f'  "{(src / f"8c0000{x}0.c").as_posix()}"' for x in "abdg")
        (gen / "port_files.cmake").write_text(
            f"set(PORT_READABLE\n{listed}\n)\nset(PORT_READABLE_NO_PCH\n  \"{(src / '8c0000c0.c').as_posix()}\"\n)\n",
            encoding="utf-8")
        exe = self.game / "build" / "x86-agent" / "Release" / "port.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        os.utime(exe, (2_000_000_000, 2_000_000_000))
        for x in "abcefg":
            os.utime(src / f"8c0000{x}0.c", (1_900_000_000, 1_900_000_000))
        os.utime(src / "8c0000d0.c", (2_100_000_000, 2_100_000_000))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_stamp(self):
        results = {"boot": {"status": "clean"},
                   "level": {"status": "diverged", "writer": {"addr": "8c0000F0", "file": "x.c"}},
                   "menu": {"status": "diverged", "writer": {"addr": "8c0000g0"}},
                   "late": {"status": "error"}}
        cov = {"scenarios": {"boot": {"entered": ["8c0000a0", "8c0000c0", "8c0000d0", "8c0000g0"]},
                             "level": {"entered": ["8c0000b0"]}, "late": {"entered": ["8c0000b0"]}}}
        lines = []
        out = core_in_game.stamp(self.ws, self.game, "x86-agent", results, cov, "2026-11-06 10:00:00", lines.append)
        return out, json.loads((self.ws / "state" / "in_game.json").read_text(encoding="utf-8")), lines

    def test_stamp_rule(self):
        out, led, lines = self.run_stamp()
        self.assertEqual(sorted(led), ["8c0000a0", "8c0000e0"])
        self.assertEqual(led["8c0000a0"], {"sha": core_in_game.sha(self.ws / "readable" / "src" / "8c0000a0.c"),
                                           "scenario": "boot", "when": "2026-11-06 10:00:00", "build_dir": "x86-agent"})
        self.assertEqual(led["8c0000e0"]["scenario"], "old")  # kept from an earlier run
        self.assertEqual((out["proven"], out["newer_than_exe"], out["dropped"]), (1, 1, 2))
        self.assertEqual(len(lines), 1)
        self.assertIn("1 newer than port.exe", lines[0])

    def test_proven_follows_the_file(self):
        self.run_stamp()
        self.assertEqual(core_in_game.readable_proven(self.ws), {"8c0000a0", "8c0000e0"})
        (self.ws / "readable" / "src" / "8c0000a0.c").write_text("int f_a(void) { return 1; }\n", encoding="utf-8")
        rd = self.ws / "state" / "readable.json"
        rd.write_text(json.dumps({"8c0000a0": {"status": "readable"}, "8c0000e0": {"status": "failed"}}),
                      encoding="utf-8")
        self.assertEqual(core_in_game.readable_proven(self.ws), set())  # edited; no longer readable

    def test_no_exe_proves_nothing(self):
        (self.game / "build" / "x86-agent" / "Release" / "port.exe").unlink()
        out, led, lines = self.run_stamp()
        self.assertEqual(sorted(led), ["8c0000e0"])
        self.assertIn("no port.exe", lines[0])


class Wiring(unittest.TestCase):
    def test_flycast_entries(self):
        e = ref_fast.entries(pathlib.Path("C:/r/ram.raw"), 0x8C010000, 0x8C0F0000, pathlib.Path("C:/r"), 120, True)
        self.assertIn("research:RamFramesPath=C:/r/ram.raw", e)
        self.assertIn(f"research:RamFramesPc={0x8C010000}", e)
        self.assertIn(f"research:RamFramesLabel={0x8C0F0000}", e)
        self.assertIn("research:RamFramesVblankBase=120", e)
        self.assertIn("config:Dreamcast.AutoLoadState=yes", e)
        self.assertNotIn("research:DynarecObservation=yes", e)  # the point: a plain dynarec run
        self.assertNotIn("research:BlockCoveragePath", " ".join(e))
        self.assertIn("research:BlockCoveragePath=C:/r/cov.txt",
                      ref_fast.entries(pathlib.Path("C:/r/ram.raw"), 1, 2, pathlib.Path("C:/r"), coverage=pathlib.Path("C:/r/cov.txt")))

    def test_runtime_watch_ram(self):
        import re
        c = (TOOL / "port" / "runtime" / "ram_frames.c").read_text(encoding="utf-8")
        div = (TOOL / "port" / "tools" / "divergence.py").read_text(encoding="utf-8")
        passed = set(re.findall(r'"(--watch-ram[\w-]*)"', div))  # the flag step 3 gives the port
        claimed = {m for f in (TOOL / "port" / "base").glob("*.c")
                   for m in re.findall(r'strcmp\(arg, "(--[\w-]+)"\)', f.read_text(encoding="utf-8"))}
        self.assertIn("--watch-ram", claimed)  # the base's scene_dump, parsed first, exits 2 on A,B@F (558e27ec)
        self.assertEqual(len(passed), 1)
        self.assertFalse(passed & claimed)  # so step 3's flag reaches ram_frames.c
        self.assertIn(f'"{passed.pop()}"', c)
        self.assertIn("lockstep_watch_words(", c)
        self.assertIn('port_exit("ram watch: done", 0)', c)
        self.assertIn('"flycast_divergence"', (TOOL / "config.json").read_text(encoding="utf-8"))


class KitFixes(unittest.TestCase):
    def test_default_build_gen(self):
        import ram_compare
        with tempfile.TemporaryDirectory() as d:
            game = pathlib.Path(d)
            gen = game / "build" / "gen"  # tools/build.py without --build-dir: build/x86 (or x86chk) and build/gen
            (gen / "include").mkdir(parents=True)
            (gen / "include" / "game.h").write_text("#define g_fx (*(rdg_g_fx_t *)0x8c020000u)\n", encoding="ascii")
            (gen / "dispatch_00.c").write_text('    {0x8c011000u, (PortFn)Fx_Main, "Fx_Main"},\n', encoding="ascii")
            for bd in ("x86", "x86chk"):
                self.assertEqual(ram_compare.global_names(game, bd), [(0x8C020000, "g_fx")])
                self.assertEqual(kit_div.dispatch_names(game, bd), {"Fx_Main": "8c011000"})
            self.assertEqual(ram_compare.global_names(game, "x86-div"), [])  # its own: build/x86-div_gen


if __name__ == "__main__":
    unittest.main()
