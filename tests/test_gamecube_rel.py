"""GameCube REL modules linked onto main.dol (targets/gamecube_ppc/tools/gcrel.py), offline, on synthetic modules
written here from the REL format (no game has any on this machine: Skies of Arcadia has none):
- placement: every module's code, then data, then bss, aligned, from the end of main.dol, never inside the scratch
  window, the generator heap or the stack; a window skipped starts a new DOL section; too many runs for main.dol's
  free slots, or too little MEM1, refused;
- relocations against main.dol, the module itself and another module: REL24 calls, ADDR16_HA/LO pairs, ADDR32
  pointers (into code, data and bss), R_DOLPHIN_SECTION switches and NOP steps past 64 KB; against a module not
  linked, a call goes to the module's "unresolved" function and other words stay as the file has them; every type's
  bit layout (ADDR16/HI/HA/LO, ADDR24, ADDR14, REL24, REL14);
- the linked DOL reads the same in every DOL reader (gcdisc, ppc_compare, ppc-translate's image), identities
  (module, section, offset) round-trip with addresses, seeds are the bl targets and prolog/epilog/unresolved;
- ppc-translate, through the translator stage's adapter, translates a REL function whose calls the link resolved;
- TcSetup.java (the seeds argument) compiles against the Ghidra jars (skipped without ghidra_home and a JDK).

usage: python tests/test_gamecube_rel.py
"""
import io
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
PPC = TOOL / "targets" / "gamecube_ppc"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(PPC / "tools"), str(PPC), str(PPC / "translator")]
import gcdisc  # noqa: E402
import gcrel  # noqa: E402
import ppc_compare  # noqa: E402
from ppctranslate.image import Dol  # noqa: E402
from test_gamecube_disc import BLR, DATA, TEXT, be, make_dol, tmpdir  # noqa: E402
from test_tail import CP, jdk  # noqa: E402
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import paths, translate_stage  # noqa: E402
from core.lint import lint  # noqa: E402

R = gcrel
MAIN_FN, MAIN_GLOBAL = TEXT + 0x80, DATA + 0x10
BL0, B0 = 0x48000001, 0x48000000
WINDOWS = [(0x81000000, 0x81040000), (0x817F0000, 0x81800000)]


def make_rel(mid, sections, relocs, version=3, align=32, bss_align=8, entries=((0, 0), (0, 0), (0, 0))):
    """A REL module: sections [("text"|"data", bytes) | ("bss", size)] (section 0 is the null section, these are
    1, 2, ...); relocs {module id: [(section, offset, type, target section, addend)]} (one import each)."""
    head = {1: 0x40, 2: 0x48, 3: 0x4C}[version]
    nsec = len(sections) + 1
    body, table = bytearray(), [(0, 0)]
    at = head + 8 * nsec
    for kind, v in sections:
        if kind == "bss":
            table.append((0, v))
            continue
        pad = -(at + len(body)) % 4
        body += bytes(pad)
        table.append(((at + len(body)) | (kind == "text"), len(v)))
        body += v
    imp_off = at + len(body)
    imports, runs = [], bytearray()
    rel_off = imp_off + 8 * len(relocs)
    for mod, rows in relocs.items():
        imports.append((mod, rel_off + len(runs)))
        sec, pos = None, 0
        for s, off, typ, tsec, addend in sorted(rows):
            if s != sec:
                runs += struct.pack(">HBBI", 0, R.R_DOLPHIN_SECTION, s, 0)
                sec, pos = s, 0
            while off - pos > 0xFFFF:
                runs += struct.pack(">HBBI", 0xFFFF, R.R_DOLPHIN_NOP, 0, 0)
                pos += 0xFFFF
            runs += struct.pack(">HBBI", off - pos, typ, tsec, addend)
            pos = off
        runs += struct.pack(">HBBI", 0, R.R_DOLPHIN_END, 0, 0)
    bss = sum(v for k, v in sections if k == "bss")
    out = bytearray(head) + struct.pack(f">{2 * nsec}I", *[x for e in table for x in e]) + body
    out += b"".join(struct.pack(">2I", *i) for i in imports) + runs
    struct.pack_into(">12I", out, 0, mid, 0, 0, nsec, head, 0, 0, version, bss, rel_off, imp_off, 8 * len(imports))
    (ps, po), (es, eo), (us, uo) = entries
    out[0x30:0x34] = bytes([ps, es, us, 0])
    struct.pack_into(">3I", out, 0x34, po, eo, uo)
    if version >= 2:
        struct.pack_into(">2I", out, 0x40, align, bss_align)
    if version >= 3:
        struct.pack_into(">I", out, 0x48, len(out))
    return bytes(out)


# module 1: .text (exec), .ctors, .rodata, .data, .bss
M1_TEXT = be(0x7C0802A6, 0x9421FFF0, 0x90010014,
             BL0,  # 0x0c bl MAIN_FN                 (REL24 against main.dol)
             BL0,  # 0x10 bl f1                      (REL24 against itself)
             0x38630001, 0x80010014, 0x7C0803A6, 0x38210010, BLR,
             BL0,  # 0x28 bl into module 99          (not linked: to "unresolved")
             BLR,
             BLR,  # 0x30 unresolved
             BLR,  # 0x34 prolog
             BLR,  # 0x38 epilog
             0x60000000,
             0x3C800000,  # 0x40 f1: lis r4,m2data@ha
             0x80640000,  # 0x44 lwz r3,m2data@l(r4)
             0x3CA00000,  # 0x48 lis r5,MAIN_GLOBAL@ha
             0x38A50000,  # 0x4c addi r5,r5,MAIN_GLOBAL@l
             BLR,
             B0,   # 0x54 b f2 (a tail branch: LK clear, not a seed)
             BLR)
M1_DATA = be(0, 0, 0xDEADBEEF, 0)
M1 = make_rel(1, [("text", M1_TEXT), ("data", bytes(4)), ("data", b"\xAA" * 8), ("data", M1_DATA), ("bss", 0x20)], {
    1: [(1, 0x10, R.R_REL24, 1, 0x40), (4, 4, R.R_ADDR32, 1, 0x40), (4, 12, R.R_ADDR32, 5, 4)],
    2: [(1, 0x42, R.R_ADDR16_HA, 2, 0), (1, 0x46, R.R_ADDR16_LO, 2, 0), (1, 0x54, R.R_REL24, 1, 0),
        (4, 0, R.R_ADDR32, 1, 0)],
    0: [(1, 0x0C, R.R_REL24, 0, MAIN_FN), (1, 0x4A, R.R_ADDR16_HA, 0, MAIN_GLOBAL),
        (1, 0x4E, R.R_ADDR16_LO, 0, MAIN_GLOBAL)],
    99: [(1, 0x28, R.R_REL24, 1, 0), (4, 8, R.R_ADDR32, 1, 0x10)],
}, entries=((1, 0x34), (1, 0x38), (1, 0x30)))
# module 2 (version 1: no alignment fields): .text f2 and a call back into module 1, a data section past 64 KB
M2_DATA = bytearray(0x12010)
M2_DATA[0:4] = be(0x1234)
M2 = make_rel(2, [("text", be(0x38600007, BLR, BL0, BLR)), ("data", bytes(M2_DATA))], {
    1: [(1, 8, R.R_REL24, 1, 0x40)],
    0: [(2, 0x12000, R.R_ADDR32, 0, MAIN_FN)],
}, version=1)


def branch_target(word, at):
    d = word & 0x03FFFFFC
    return at + (d - 0x04000000 if d & 0x02000000 else d)


def ha_lo(dol, at_hi, at_lo):
    hi, lo = dol.u32(at_hi) & 0xFFFF, dol.u32(at_lo) & 0xFFFF
    return ((hi << 16) + (lo - 0x10000 if lo & 0x8000 else lo)) & 0xFFFFFFFF


class Link(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = make_dol()
        cls.out, cls.rec, cls.seeds = gcrel.link(cls.main, [("b.rel", M2), ("a.rel", M1)], WINDOWS)
        cls.dol = Dol(cls.out)
        cls.at = staticmethod(lambda m, s, o=0: gcrel.address(cls.rec, m, s, o))

    def test_placement(self):
        self.assertEqual([m["module_id"] for m in self.rec["modules"]], [1, 2])  # module-id order
        secs = [(m["module_id"], s) for m in self.rec["modules"] for s in m["sections"]]
        kinds = [("text" if s["exec"] else "bss" if s["bss"] else "data") for _, s in sorted(
            secs, key=lambda x: int(x[1]["addr"], 16))]
        self.assertEqual(kinds, sorted(kinds, key=["text", "data", "bss"].index))  # code, then data, then bss
        self.assertEqual(self.at(1, 1), 0x80010700)  # the end of main.dol's bss, aligned
        self.assertEqual(self.at(2, 1), 0x80010760)  # after module 1's 0x5c bytes, aligned to 32
        self.assertEqual(self.at(1, 5) % 8, 0)  # bss: the module's bss alignment
        spans = sorted((int(s["addr"], 16), int(s["addr"], 16) + s["size"]) for _, s in secs)
        for (a, b), (c, _) in zip(spans, spans[1:]):
            self.assertLessEqual(b, c)  # no two sections overlap
        self.assertGreaterEqual(spans[0][0], gcrel.main_end(self.main))
        self.assertEqual([(d["kind"], d["start"]) for d in self.rec["dol_sections"]],
                         [("text", "80010700"), ("data", "80010780")])

    def test_main_dol_unchanged(self):
        for a, b in ppc_compare.dol_sections(self.main):
            self.assertEqual(self.dol.find(a, len(b))[1], b)
        self.assertEqual(gcdisc.dol_sections(self.out)["bss"], gcdisc.dol_sections(self.main)["bss"])
        self.assertEqual(gcdisc.dol_sections(self.out)["entry"], TEXT)

    def test_readers_agree(self):
        mine = ppc_compare.dol_sections(self.out)
        self.assertEqual(mine, [(a, b) for a, b, _ in self.dol.sections])
        texts = {a for _, _, a, _ in gcdisc.dol_sections(self.out)["text"]}
        self.assertEqual({a for a, _, t in self.dol.sections if t}, texts)
        self.assertTrue(self.dol.is_text(self.at(1, 1, 0x40)) and self.dol.is_text(self.at(2, 1)))
        self.assertFalse(self.dol.is_text(self.at(1, 4)))

    def test_calls(self):
        f0, f1, f2 = self.at(1, 1), self.at(1, 1, 0x40), self.at(2, 1)
        self.assertEqual(branch_target(self.dol.u32(f0 + 0x0C), f0 + 0x0C), MAIN_FN)
        self.assertEqual(branch_target(self.dol.u32(f0 + 0x10), f0 + 0x10), f1)
        self.assertEqual(branch_target(self.dol.u32(f2 + 8), f2 + 8), f1)  # module 2 into module 1, backwards
        self.assertEqual(branch_target(self.dol.u32(f0 + 0x54), f0 + 0x54), f2)
        self.assertEqual(self.dol.u32(f0 + 0x54) & 3, 0)  # b stays b, bl stays bl
        self.assertEqual(self.dol.u32(f0 + 0x0C) & 3, 1)

    def test_data_references(self):
        f0 = self.at(1, 1)
        self.assertEqual(ha_lo(self.dol, f0 + 0x40, f0 + 0x44), self.at(2, 2))  # lis/lwz into module 2's data
        self.assertEqual(ha_lo(self.dol, f0 + 0x48, f0 + 0x4C), MAIN_GLOBAL)  # lis/addi of a main.dol global
        self.assertEqual(self.dol.u32(f0 + 0x44) >> 16, 0x8064)  # the instruction bits are kept
        d = self.at(1, 4)
        self.assertEqual([self.dol.u32(d + k) for k in (0, 4, 12)],
                         [self.at(2, 1), self.at(1, 1, 0x40), self.at(1, 5, 4)])  # into code and bss
        self.assertEqual(self.dol.u32(self.at(2, 2, 0x12000)), MAIN_FN)  # past 64 KB: NOP steps
        self.assertEqual(self.dol.u32(self.at(2, 2)), 0x1234)

    def test_module_not_linked(self):
        f0 = self.at(1, 1)
        self.assertEqual(branch_target(self.dol.u32(f0 + 0x28), f0 + 0x28), f0 + 0x30)  # to "unresolved"
        self.assertEqual(self.dol.u32(self.at(1, 4, 8)), 0xDEADBEEF)  # as the file has it
        st = self.rec["modules"][0]["relocations"]
        self.assertEqual((st["applied"], st["against_missing"], st["redirected_to_unresolved"]), (10, {"99": 2}, 1))
        self.assertEqual(self.rec["modules"][1]["relocations"]["applied"], 2)

    def test_seeds(self):
        f0 = self.at(1, 1)
        want = {f0 + 0x40, f0 + 0x30, f0 + 0x34, f0 + 0x38}  # bl targets in modules; unresolved, prolog, epilog
        self.assertEqual(set(self.seeds), want)  # not main.dol's MAIN_FN, not f2 (only a b and an ADDR32 reach it)
        self.assertEqual(self.rec["modules"][0]["prolog"], f"{f0 + 0x34:08x}")
        self.assertIsNone(self.rec["modules"][1]["prolog"])

    def test_project_ranges(self):
        lay = gcdisc.layout(self.main)["project"]
        game = self.rec["project"]["game_ranges"]
        self.assertEqual(game[:len(lay["game_ranges"])], lay["game_ranges"])
        self.assertIn([f"{self.at(1, 1):08x}", f"{self.at(1, 1) + len(M1_TEXT):08x}"], game)
        glob = self.rec["project"]["global_ranges"]
        self.assertIn([f"{self.at(1, 4):08x}", f"{self.at(1, 4) + 16:08x}"], glob)  # the last data section
        self.assertIn([f"{self.at(1, 5):08x}", f"{self.at(1, 5) + 0x20:08x}"], glob)  # and the bss
        self.assertNotIn([f"{self.at(1, 3):08x}", f"{self.at(1, 3) + 8:08x}"], glob)  # .rodata is not writable

    def test_identity(self):
        for m in self.rec["modules"]:
            for s in m["sections"]:
                for off in (0, s["size"] - 1):
                    a = gcrel.address(self.rec, m["module_id"], s["index"], off)
                    w = gcrel.where(self.rec, a)
                    self.assertEqual((w["module"], w["section"], int(w["offset"], 16)),
                                     (m["module_id"], s["index"], off))
        self.assertEqual(gcrel.where(self.rec, MAIN_FN)["module"], 0)
        with self.assertRaises(gcrel.LinkError):
            gcrel.address(self.rec, 1, 1, len(M1_TEXT))

    def test_fixed(self):  # the same inputs, the same image and record, whatever order the files came in
        out, rec, seeds = gcrel.link(self.main, [("a.rel", M1), ("b.rel", M2)], WINDOWS)
        self.assertEqual((out, rec["linked_sha256"], seeds), (self.out, self.rec["linked_sha256"], self.seeds))


class Limits(unittest.TestCase):
    def test_windows_skipped(self):
        main = make_dol()
        win = [(0x80010760, 0x80010780)] + WINDOWS  # module 2's code would start here
        out, rec, _ = gcrel.link(main, [("a.rel", M1), ("b.rel", M2)], win)
        self.assertEqual(gcrel.address(rec, 2, 1, 0), 0x80010780)
        self.assertEqual([d["kind"] for d in rec["dol_sections"]], ["text", "text", "data"])  # a new run
        for a, b in ppc_compare.dol_sections(out):
            for lo, hi in win:
                self.assertFalse(a < hi and a + len(b) > lo)

    def test_reserved_windows(self):
        self.assertEqual(gcrel.reserved_windows(), WINDOWS)  # the template's heap, target.json's scratch and stack

    def test_no_room(self):
        with self.assertRaisesRegex(gcrel.LinkError, "do not fit"):
            gcrel.link(make_dol(), [("a.rel", M1)], [(0x80010000, 0x81800000)])

    def test_no_free_slot(self):
        main = bytearray(make_dol())
        for k in range(2, 7):  # all seven text slots in use
            struct.pack_into(">I", main, 4 * k, 0x100)
            struct.pack_into(">I", main, 0x48 + 4 * k, 0x80006000 + 0x100 * k)
            struct.pack_into(">I", main, 0x90 + 4 * k, 4)
        with self.assertRaisesRegex(gcrel.LinkError, "free text slot"):
            gcrel.link(bytes(main), [("a.rel", M1)], WINDOWS)

    def test_refused(self):
        with self.assertRaisesRegex(gcrel.LinkError, "module id 1"):
            gcrel.link(make_dol(), [("a.rel", M1), ("c.rel", M1)], WINDOWS)
        with self.assertRaisesRegex(gcrel.LinkError, "main.dol"):
            gcrel.link(make_dol(), [("z.rel", make_rel(0, [("text", be(BLR))], {}))], WINDOWS)
        with self.assertRaisesRegex(gcrel.LinkError, "not a REL"):
            gcrel.link(make_dol(), [("x.rel", b"\0" * 16)], WINDOWS)
        bad = make_rel(5, [("text", be(BLR))], {0: [(1, 0, 77, 0, 0)]})
        with self.assertRaisesRegex(gcrel.LinkError, "type 77"):
            gcrel.link(make_dol(), [("t.rel", bad)], WINDOWS)


class Types(unittest.TestCase):
    """Each relocation type's bits, at P = 0x80001000, value V."""
    P = 0x80001000

    def one(self, typ, word, v, at=0):
        mem = [(self.P, bytearray(struct.pack(">II", word, 0)))]
        gcrel.patch(mem, self.P + at, typ, v, "t")
        return struct.unpack(">I", mem[0][1][:4])[0]

    def test_types(self):
        v = 0x8123ABCD
        self.assertEqual(self.one(R.R_ADDR32, 0, v), v)
        self.assertEqual(self.one(R.R_ADDR24, 0x48000003, 0x00123454), 0x48123457)
        self.assertEqual(self.one(R.R_ADDR16, 0x38600000, v, 2), 0x3860ABCD)
        self.assertEqual(self.one(R.R_ADDR16_LO, 0x38600000, v, 2), 0x3860ABCD)
        self.assertEqual(self.one(R.R_ADDR16_HI, 0x3C600000, v, 2), 0x3C608123)
        self.assertEqual(self.one(R.R_ADDR16_HA, 0x3C600000, v, 2), 0x3C608124)  # lo is negative: +1
        self.assertEqual(self.one(R.R_ADDR16_HA, 0x3C600000, 0x81237FFF, 2), 0x3C608123)
        for t in (R.R_ADDR14, R.R_ADDR14_BRTAKEN, R.R_ADDR14_BRNTAKEN):
            self.assertEqual(self.one(t, 0x41820003, 0x1234), 0x41821237)
        self.assertEqual(self.one(R.R_REL24, 0x48000001, self.P - 8), 0x4BFFFFF9)
        for t in (R.R_REL14, R.R_REL14_BRTAKEN, R.R_REL14_BRNTAKEN):
            self.assertEqual(self.one(t, 0x41820000, self.P + 0x40), 0x41820040)
        with self.assertRaisesRegex(gcrel.LinkError, "cannot reach"):
            self.one(R.R_REL14, 0x41820000, self.P + 0x8000)
        with self.assertRaisesRegex(gcrel.LinkError, "outside"):
            self.one(R.R_ADDR32, 0, 0, 0x100)


class Command(unittest.TestCase):
    def test_link_where_addr(self):
        d = tmpdir("gcrel_")
        try:
            for n, b in (("main.dol", make_dol()), ("a.rel", M1), ("b.rel", M2)):
                (d / n).write_bytes(b)
            with mock.patch("sys.stdout", io.StringIO()):
                gcrel.main(["link", str(d / "main.dol"), str(d / "out"), str(d / "a.rel"), str(d / "b.rel")])
            rec = json.loads((d / "out" / "link.json").read_text(encoding="utf-8"))
            self.assertEqual((d / "out" / "linked.dol").read_bytes(),
                             gcrel.link(make_dol(), [("a.rel", M1), ("b.rel", M2)], WINDOWS)[0])
            self.assertEqual(len((d / "out" / "seeds.txt").read_text(encoding="utf-8").split()), rec["seeds"])
            with mock.patch("sys.stdout", io.StringIO()) as o:
                gcrel.main(["where", str(d / "out" / "link.json"), "80010740"])
            self.assertEqual(json.loads(o.getvalue())["offset"], "0x40")
            with mock.patch("sys.stdout", io.StringIO()) as o:
                gcrel.main(["addr", str(d / "out" / "link.json"), "1", "1", "40"])
            self.assertEqual(o.getvalue().strip(), "80010740")
            (d / "bodies.txt").write_text(  # two ranges of one entry, then a main.dol function
                f"80010740 80010740 80010754\n80010740 80010760 80010768\n{MAIN_FN:08x} {MAIN_FN:08x} "
                f"{MAIN_FN + 36:08x}\n", encoding="utf-8")
            gcrel.main(["ids", str(d / "out" / "link.json"), str(d / "bodies.txt"), str(d / "ids.tsv")])
            self.assertEqual((d / "ids.tsv").read_text(encoding="utf-8").splitlines(), [
                "addr\tmodule_id\tmodule\tsection\toffset", f"{MAIN_FN:08x}\t0\tmain.dol\t\t0x{MAIN_FN:08x}",
                "80010740\t1\ta.rel\t1\t0x40"])
            with self.assertRaises(FileExistsError):  # never over an earlier link
                gcrel.main(["link", str(d / "main.dol"), str(d / "out"), str(d / "a.rel")])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Translate(unittest.TestCase):
    def test_rel_function(self):
        """A workspace on the linked image: ppc-translate, run as the stage runs it, translates module 1's f0, whose
        calls (into main.dol and into its own module) only the link resolved, and f1, which reads module 2's data."""
        out, rec, _ = gcrel.link(make_dol(), [("a.rel", M1), ("b.rel", M2)], WINDOWS)
        f0, f1, m2data = (gcrel.address(rec, *x) for x in ((1, 1, 0), (1, 1, 0x40), (2, 2, 0)))
        d = tmpdir("gcrel_tx_")
        try:
            (d / "linked.dol").write_bytes(out)
            (d / "project.json").write_text(json.dumps({"target": "gamecube_ppc", "binary": "linked.dol"}),
                                            encoding="utf-8")
            (d / "state").mkdir()
            (d / "state" / "function_bodies.txt").write_text(
                f"{MAIN_FN:08x} {MAIN_FN:08x} {MAIN_FN + 0x24:08x}\n{f0:08x} {f0:08x} {f0 + 0x28:08x}\n"
                f"{f1:08x} {f1:08x} {f1 + 0x14:08x}\n", encoding="utf-8")
            metas = {f0: {"name": f"fn_{f0:08x}", "signature": f"u32 fn_{f0:08x}(void)", "params": [], "ret": "u32",
                          "callees": [{"addr": f"{MAIN_FN:08x}", "regs": []}, {"addr": f"{f1:08x}", "regs": []}]},
                     f1: {"name": f"fn_{f1:08x}", "signature": f"u32 fn_{f1:08x}(void)", "params": [], "ret": "u32",
                          "callees": []}}
            srcs = {}
            for a, meta in metas.items():
                (d / "harness" / f"{a:08x}").mkdir(parents=True)
                (d / "harness" / f"{a:08x}" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            for a, meta in metas.items():
                with mock.patch.multiple(paths, ROOT=d, TARGET=PPC, HARNESS=d / "harness"):
                    cmd = translate_stage.command({"binary_path": d / "linked.dol"}, json.loads(
                        (PPC / "target.json").read_text(encoding="utf-8"))["translator"]["command"],
                        str(PPC / "translator"), f"{a:08x}", d / f"{a:08x}.c")
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr)
                srcs[a] = (d / f"{a:08x}.c").read_text(encoding="utf-8")
                self.assertEqual(lint(srcs[a], meta), [])
            self.assertIn(f"tc_call(0x{MAIN_FN:08x}u", srcs[f0])
            self.assertIn(f"tc_call(0x{f1:08x}u", srcs[f0])
            self.assertIn(f"return rd32(0x{m2data:08x}u);", srcs[f1])  # the lis/lwz pair the link filled in
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(CP and jdk("javac"), "no ghidra_home or JDK on this machine")
class Setup(unittest.TestCase):
    def test_tcsetup_compiles(self):
        d = tmpdir("gcrel_java_")
        try:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(d), "-cp", CP, "-sourcepath",
                                str(PPC / "ghidra_scripts"), str(PPC / "ghidra_scripts" / "TcSetup.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Args(unittest.TestCase):
    def test_seed_entries_argument(self):
        from core import ghidra
        with mock.patch.object(paths, "ROOT", pathlib.Path("C:/ws") if sys.platform == "win32" else
                               pathlib.Path("/ws")):
            args = ghidra.setup_script_args({"fixed_regs": {"r2": "80010600"}, "seed_entries": "disc/seeds.txt"})
        self.assertEqual(args[0], "r2:80010600")
        self.assertTrue(args[1].startswith("@") and args[1].replace("\\", "/").endswith("ws/disc/seeds.txt"))
        self.assertEqual(ghidra.setup_script_args({"fixed_regs": {"r2": "80010600"}}), ["r2:80010600"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
