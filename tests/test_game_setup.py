"""tools/game_setup.py and tools/decode_check.py, offline (no Ghidra):
1. the decode check passes a listing whose bytes are the binary's and whose names the target's decoder agrees with
   (Ghidra's simplified PowerPC forms and SH delay-slot prefixes named as the decoder names them), and fails on any
   other name, bytes that are not the binary's, or a gap inside a function; a function with no instruction at all is
   listed apart and does not fail it;
2. the Saturn disc step builds binary/image.bin from the first-read file and the --file overlays (satdisc.py and
   overlay_image.py), with project.json's image_gaps and game_ranges and the template TcInputs;
3. the Dreamcast disc step extracts the boot file IP.BIN names and marks a P0-linked one for TcSeed;
4. setup never overwrites a workspace, and --fields may not set what the disc gives.
Run: python -W error tests/test_game_setup.py (also from test_tooling.py).
"""
import json
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tools"), str(TOOL / "tests")]
import decode_check  # noqa: E402
import game_setup  # noqa: E402
from test_saturn_sh2 import FILES as SAT_FILES, disc_sectors  # noqa: E402


def rows_of(text):
    path = pathlib.Path(tempfile.mkdtemp(prefix="dc_")) / "check.tsv"
    path.write_text(text, encoding="utf-8")
    try:
        return decode_check.read_rows(path)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


class Names(unittest.TestCase):
    def test_powerpc(self):
        for ghidra, decoder in (("li", "addi"), ("lis", "addis"), ("subic.", "addic."), ("cmplwi", "cmpli"),
                                ("beq", "bc"), ("bne+", "bc"), ("bdnz", "bc"), ("bgel", "bcl"), ("blr", "bclr"),
                                ("beqlr", "bclr"), ("blrl", "bclrl"), ("bctr", "bcctr"), ("bctrl", "bcctrl"),
                                ("b", "b"), ("bl", "bl"), ("bla", "bla"), ("mr", "or"), ("mr.", "or."), ("nop", "ori"),
                                ("clrlwi.", "rlwinm."), ("mflr", "mfspr"), ("mtctr", "mtspr"), ("mtgqr1", "mtspr"),
                                ("trap", "tw"), ("twgti", "twi"), ("stwcx.", "stwcx."), ("lwz", "lwz")):
            self.assertEqual(decode_check.ppc_name(ghidra), decoder, ghidra)

    def test_superh(self):
        self.assertEqual(decode_check.sh_name("_nop"), "nop")
        self.assertEqual(decode_check.sh4_name("fmov"), "fmov.s")
        self.assertEqual(decode_check.sh4_name("_fmov.s"), "fmov.s")


class Check(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="dc_"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def saturn(self, words):
        (self.d / "image.bin").write_bytes(b"".join(struct.pack(">H", w) for w in words))
        return {"target": "saturn_sh2", "binary_path": self.d / "image.bin", "load_address": "0x06000000"}

    def test_sh2_agrees_and_fails(self):
        cfg = self.saturn([0x000B, 0x0009, 0x6013])  # rts; nop (delay slot); mov r1,r0
        good = "F\t06000000\n06000000\t000b\trts\n06000002\t0009\t_nop\n06000004\t6013\tmov\n"
        counts, bad, empty = decode_check.check(rows_of(good), cfg)
        self.assertEqual((bad, empty, counts["instructions"], counts["functions"]), ([], [], 3, 1))
        for text, kind in ((good.replace("\tmov\n", "\tadd\n"), "name"),
                           (good.replace("6013\tmov", "6014\tmov"), "bytes"),
                           (good + "06000006\t0000\t??\n", "gap")):
            counts, bad, _ = decode_check.check(rows_of(text), cfg)
            self.assertEqual((len(bad), counts[kind]), (1, 1), kind)

    def test_function_with_no_instruction(self):
        cfg = self.saturn([0x000B, 0x0009, 0x0000])
        text = "F\t06000000\n06000000\t000b\trts\n06000002\t0009\t_nop\nF\t06000004\n06000004\t00\t??\n" \
               "F\t060fc000\n060fc000\t-\t??\n"
        counts, bad, empty = decode_check.check(rows_of(text), cfg)
        self.assertEqual((bad, empty, counts["instructions"], counts["functions"]),
                         ([], ["06000004", "060fc000"], 2, 3))
        self.assertTrue(decode_check.report(counts, bad, empty, out=open(self.d / "out.txt", "w", encoding="utf-8")))

    def test_gamecube_dol(self):
        dol = bytearray(0x100 + 8)
        struct.pack_into(">I", dol, 0x00, 0x100)          # text section 0: file offset,
        struct.pack_into(">I", dol, 0x48, 0x80003100)     # load address
        struct.pack_into(">I", dol, 0x90, 8)              # and size
        struct.pack_into(">2I", dol, 0x100, 0x38600001, 0x4E800020)  # li r3,1; blr
        (self.d / "main.dol").write_bytes(bytes(dol))
        cfg = {"target": "gamecube_ppc", "binary_path": self.d / "main.dol", "load_address": "0x80003100"}
        text = "F\t80003100\n80003100\t38600001\tli\n80003104\t4e800020\tblr\n"
        counts, bad, _ = decode_check.check(rows_of(text), cfg)
        self.assertEqual((bad, counts["instructions"]), ([], 2))
        counts, bad, _ = decode_check.check(rows_of(text.replace("\tblr", "\tbctr")), cfg)
        self.assertEqual(counts["name"], 1)


class Setup(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="gs_"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_saturn(self):
        iso = self.d / "game.iso"
        iso.write_bytes(b"".join(disc_sectors()))
        self.assertEqual(game_setup.detect(iso), "saturn_sh2")
        ws = self.d / "ws"
        cfg = game_setup.setup(iso, ws, files=["FIELD.BIN@0x2602c000"])
        image = (ws / "binary" / "image.bin").read_bytes()
        first, field = SAT_FILES["/0"], SAT_FILES["/FIELD/FIELD.BIN"]
        self.assertEqual(image[0xA000:0xA000 + len(first)], first)
        self.assertEqual(image[0x2C000:], field)
        self.assertEqual(cfg["image_gaps"], ["06000000-0600a000", f"{0x0600a000 + len(first):08x}-0602c000"])
        self.assertEqual(cfg["game_ranges"], [["0600a000", f"{0x0600a000 + len(first):08x}"],
                                              ["0602c000", f"{0x0602c000 + len(field):08x}"]])
        pj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        self.assertEqual((pj["target"], pj["binary"], pj["binary_sha256"]),
                         ("saturn_sh2", "binary/image.bin", game_setup.sha256(image)))
        self.assertEqual((ws / "oracle" / "TcInputs.java").read_bytes(),
                         (TOOL / "targets" / "saturn_sh2" / "template" / "TcInputs.java").read_bytes())
        with self.assertRaises(SystemExit):  # never over an existing workspace
            game_setup.setup(iso, ws)
        ctx = game_setup.setup(iso, self.d / "ws2", files=["FIELD.BIN@0x0602c000"], first_context=True)
        self.assertEqual(ctx["game_ranges"], [["0602c000", f"{0x0602c000 + len(field):08x}"]])
        with self.assertRaises(SystemExit):
            game_setup.setup(iso, self.d / "ws3", files=["NOPE.BIN@0x06040000"])

    def test_fields(self):
        f = self.d / "fields.json"
        f.write_text(json.dumps({"binary_sha256": "0" * 64}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            game_setup.setup(self.d / "none.iso", self.d / "ws", fields=f)
        iso = self.d / "game.iso"
        iso.write_bytes(b"".join(disc_sectors()))
        f.write_text(json.dumps({"name": "Kept", "catalog_overrides": {"data": ["0600a010"]}}), encoding="utf-8")
        cfg = game_setup.setup(iso, self.d / "ws", fields=f)
        self.assertEqual((cfg["name"], cfg["catalog_overrides"]), ("Kept", {"data": ["0600a010"]}))

    def test_dreamcast(self):
        boot = struct.pack("<8I", *[0x0C010010] * 8) + b"\x22\x4f" * 64  # P0 pointers: a program linked at 0x0c...

        class Disc:
            def __init__(self, image):
                pass

            def ip(self):
                return {"hardware": "SEGA SEGAKATANA", "boot_file": "1ST_READ.BIN", "title": "SOME GAME",
                        "product": "T-0000", "version": "V1.000"}

            def walk(self):
                return [("1ST_READ.BIN", 45100, len(boot)), ("DATA/X.BIN", 45200, 4)]

            def read(self, lba, size):
                return boot if lba == 45100 else bytes(size)

        with mock.patch("gdrom.Disc", Disc):
            ws = self.d / "ws"
            self.assertEqual(game_setup.detect(self.d / "game.gdi"), "dreamcast_sh4")
            cfg = game_setup.setup(self.d / "game.gdi", ws)
        self.assertEqual((ws / "disc" / "1ST_READ.BIN").read_bytes(), boot)
        self.assertEqual((cfg["name"], cfg["ghidra_name"], cfg["binary"], cfg.get("seed_functions")),
                         ("Some Game", "SomeGame", "disc/1ST_READ.BIN", True))
        rec = json.loads((ws / "disc" / "disc.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["boot_file"]["sha256"], game_setup.sha256(boot))


if __name__ == "__main__":
    unittest.main()
