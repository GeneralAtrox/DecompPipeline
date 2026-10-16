"""Batch 25a: SDK Function ID tooling, the pure parts (no Ghidra, no SDK tools): library format detection, which
libraries an SDK holds, skip-if-done stamps, command-line chunks, ar members, the InstallShield 5 reader, the FID
result merge and TSV writing, and the SDK / engine / unknown rules."""
import hashlib
import pathlib
import struct
import sys
import tempfile
import unittest
import zlib

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
import is5cab  # noqa: E402
import sdk_classify  # noqa: E402
import sdk_fid_lib as L  # noqa: E402

SYSROF_LIB_BYTES = b"\xe0\x2e\x00" + b"991025145357" + b"\0" * 32
SYSROF_OBJ_BYTES = b"\x80\x21\x00\x80\x00\x80" + b"\0" * 32


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(prefix="b25a_", dir=TOOL / "tests" / ".work")
        self.d = pathlib.Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def put(self, rel, data):
        p = self.d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class Formats(_Tmp):
    def test_detect(self):
        self.assertEqual(L.detect_format(self.put("a.lib", SYSROF_LIB_BYTES)), L.SYSROF_LIB)
        self.assertEqual(L.detect_format(self.put("a.obj", SYSROF_OBJ_BYTES)), L.SYSROF_OBJ)
        self.assertEqual(L.detect_format(self.put("a.elf", b"\x7fELF\x01\x01\x01" + b"\0" * 9)), L.ELF)
        self.assertEqual(L.detect_format(self.put("a.a", b"!<arch>\n" + b"\0" * 8)), L.AR)
        self.assertIsNone(L.detect_format(self.put("b.lib", b"\0" * 16)))  # a VMU (lc86k) library
        self.assertIsNone(L.detect_format(self.put("c.lib", b"\xe0")))  # too short to say
        self.assertIsNone(L.detect_format(self.d / "missing.lib"))

    def test_select(self):
        self.put("Lib/shinobi.lib", SYSROF_LIB_BYTES + b"1")
        self.put("Lib/Kamui2.lib", SYSROF_LIB_BYTES + b"2")
        self.put("Lib/strt1.obj", SYSROF_OBJ_BYTES)
        self.put("Lib/Gnu/libshinobi.a", b"!<arch>\n" + b"\0" * 8)  # another compiler: skipped by folder
        self.put("Lib/Mwerks/kamui2.elf.lib", SYSROF_LIB_BYTES + b"3")  # skipped by folder
        self.put("Sample/x.lib", SYSROF_LIB_BYTES + b"4")  # samples are not libraries
        self.put("Lib/copy/shinobi.lib", SYSROF_LIB_BYTES + b"1")  # byte-identical copy: left out
        self.put("Lib2/shinobi.lib", SYSROF_LIB_BYTES + b"5")  # same family, other bytes: shinobi_2
        self.put("Lib/readme.txt", SYSROF_LIB_BYTES)  # not a library suffix
        got = [(f, p.relative_to(self.d).as_posix(), k) for f, p, k in L.select_libraries(self.d)]
        self.assertEqual(got, [("kamui2", "Lib/Kamui2.lib", L.SYSROF_LIB), ("shinobi", "Lib/shinobi.lib", L.SYSROF_LIB),
                               ("strt1", "Lib/strt1.obj", L.SYSROF_OBJ), ("shinobi_2", "Lib2/shinobi.lib", L.SYSROF_LIB)])
        only = L.select_libraries(self.d, include="^kam", exclude="_d$")
        self.assertEqual([f for f, _, _ in only], ["kamui2"])
        gnu = L.select_libraries(self.d / "Lib" / "Gnu", formats=(L.AR,))
        self.assertEqual([f for f, _, _ in gnu], ["libshinobi"])

    def test_family(self):
        self.assertEqual(L.family_name("X/Kamui2.lib"), "kamui2")
        self.assertEqual(L.family_name("kamui2.elf.lib"), "kamui2")
        self.assertEqual(L.family_name("sg syDds.lib"), "sg_sydds")


class Stamps(_Tmp):
    def test_skip_if_done(self):
        src = self.put("a.lib", SYSROF_LIB_BYTES)
        want = {"source": L.file_digest(src), "kind": L.SYSROF_LIB}
        stamp = self.d / "split" / "a" / ".done.json"
        self.assertFalse(L.stamp_matches(stamp, want))  # nothing done yet
        L.write_stamp(stamp, want)
        self.assertTrue(L.stamp_matches(stamp, want))  # done: the step is skipped
        src.write_bytes(SYSROF_LIB_BYTES + b"changed")
        self.assertFalse(L.stamp_matches(stamp, {"source": L.file_digest(src), "kind": L.SYSROF_LIB}))  # redo
        stamp.write_text("{broken", encoding="utf-8")
        self.assertFalse(L.stamp_matches(stamp, want))  # an unreadable stamp means not done

    def test_chunks(self):
        items = [f"../work/R10.1/split/lib{i:03d}" for i in range(100)]
        cs = L.chunks(items, max_chars=1000, max_items=40)
        self.assertEqual(sum(cs, []), items)
        self.assertTrue(all(sum(len(x) + 3 for x in c) <= 1000 for c in cs))
        self.assertEqual(L.chunks([]), [])
        self.assertEqual(len(L.chunks(items, max_chars=10 ** 6, max_items=40)), 3)

    def test_ar_members(self):
        def member(name, data):
            return f"{name:<16}{0:<12}{0:<6}{0:<6}{644:<8}{len(data):<10}`\n".encode() + data + b"\n" * (len(data) & 1)
        longnames = b"a_very_long_object_name.o/\n"
        blob = b"!<arch>\n" + member("/", b"\0\0\0\0") + member("//", longnames) + member("x.o/", b"\x7fELFx") \
            + member("/0", b"\x7fELFyz")
        self.assertEqual(L.ar_members(blob), [("x.o", b"\x7fELFx"), ("a_very_long_object_name.o", b"\x7fELFyz")])
        with self.assertRaises(ValueError):
            L.ar_members(b"nope")


def _is5(files):
    """A minimal InstallShield 5 header + cabinet holding `files` [(group, dir, name, data, compressed)]."""
    cdo, ft = 0x14, 0x200
    strings, table, descs, cab = bytearray(), [], bytearray(), bytearray(b"CAB0" * 4)
    dirs = sorted({d for _, d, _, _, _ in files})
    blob = bytearray(0x1000)

    def add_str(s):
        off = 0x800 + len(strings)
        strings.extend(s.encode() + b"\0")
        return off
    table = [add_str(d) for d in dirs]
    for i, (_g, d, name, data, comp) in enumerate(files):
        stored = struct.pack("<H", len(zlib.compress(data)[2:-4])) + zlib.compress(data)[2:-4] if comp else data
        q = 0x400 + 0x40 * i
        struct.pack_into("<IIHII", blob, cdo + ft + q, add_str(name), dirs.index(d), 4 if comp else 0, len(data), len(stored))
        struct.pack_into("<I", blob, cdo + ft + q + 18 + 0x14, len(cab))
        blob[cdo + ft + q + 22 + 0x14:cdo + ft + q + 22 + 0x14 + 16] = hashlib.md5(data).digest()
        table.append(q)
        cab += stored
    struct.pack_into("<5I", blob, 0, is5cab.SIGNATURE, 0x01005201, 0, cdo, 0x100)
    struct.pack_into("<I", blob, cdo + 0x0C, ft)
    struct.pack_into("<I", blob, cdo + 0x0C + 0x10, len(dirs))
    struct.pack_into("<I", blob, cdo + 0x0C + 0x1C, len(files))
    struct.pack_into(f"<{len(table)}I", blob, cdo + ft, *table)
    group = 0xC00  # one file group named after the first file's group, holding every file
    struct.pack_into("<III", blob, cdo + group, 0, group + 0x20, 0)
    struct.pack_into("<I", blob, cdo + group + 0x20, ft + add_str(files[0][0]))
    blob[cdo + ft + 0x800:cdo + ft + 0x800 + len(strings)] = strings
    struct.pack_into("<II", blob, cdo + group + 0x20 + 4 + 0x48, 0, len(files) - 1)
    struct.pack_into("<I", blob, cdo + 0x30 + 0x0E, group)
    return bytes(blob), bytes(cab)


class Cabinet(_Tmp):
    def test_versions_and_chunks(self):
        self.assertEqual(is5cab.major_version(0x01005201), 5)
        self.assertEqual(is5cab.major_version(0x0200020C), 5)  # 524 // 100
        data = b"shinobi" * 300
        comp = zlib.compress(data)[2:-4]
        self.assertEqual(is5cab.inflate_chunks(struct.pack("<H", len(comp)) + comp, len(data)), data)

    def test_extract_and_skip(self):
        files = [("Libraries", "", "shinobi.lib", SYSROF_LIB_BYTES * 20, True),
                 ("Libraries", "Gnu", "libx.a", b"!<arch>\n" + b"z" * 50, False)]
        hdr, cab = _is5(files)
        h = self.put("data1.hdr", hdr)
        self.put("data1.cab", cab)
        info = is5cab.read_header(hdr)
        self.assertEqual([is5cab.file_path(e) for e in info["files"]], ["Libraries/shinobi.lib", "Libraries/Gnu/libx.a"])
        out = self.d / "out"
        self.assertEqual(is5cab.extract(h, out, log=lambda m: None), {"written": 2, "kept": 0, "skipped": 0, "bad": 0})
        self.assertEqual((out / "Libraries" / "shinobi.lib").read_bytes(), SYSROF_LIB_BYTES * 20)
        self.assertEqual(is5cab.extract(h, out, log=lambda m: None)["kept"], 2)  # done already: left alone
        (out / "Libraries" / "shinobi.lib").write_bytes(b"damaged")
        self.assertEqual(is5cab.extract(h, out, only=r"\.lib$", log=lambda m: None)["written"], 1)  # redone
        with self.assertRaises(ValueError):
            is5cab.read_header(b"\0" * 64)


def raw(addr, tag, status, name="", fam="", obj="", score="0", units="20"):
    return {"addr": addr, "tag": tag, "status": status, "code_units": units, "name": name, "family": fam,
            "version": tag, "object": obj, "overall": score}


class Merge(_Tmp):
    def test_merge_fid(self):
        rows = [raw("8c070000", "R9", "match", "_kmBeginScene", "kamui2", "/kamui2/a_.elf", "40"),
                raw("8c070000", "R10.1", "match", "_kmBeginScene", "kamui2_d", "/kamui2_d/a_.elf", "40"),
                raw("8c070100", "R9", "match", "_memset", "sh4nlfzz", "/s/m_.elf", "20"),
                raw("8c070100", "R9", "match", "_memclr", "sh4nlfzz", "/s/c_.elf", "16"),
                raw("8c070200", "R9", "match", "_tiny", "x", "/x/t_.elf", "12"),
                raw("8c070280", "R9", "match", "_sin", "m", "/m/sin.elf", "30"),
                raw("8c070280", "R9", "match", "_cos", "m", "/m/cos.elf", "30"),
                raw("8c070300", "R9", "nomatch"), raw("8c070400", "R9", "small", units=""),
                raw("8c020000", "R9", "match", "_strlen", "sh4nlfzz", "/s/l_.elf", "30")]
        got = {r["addr"]: r for r in L.merge_fid(rows, {"8c070000": "Km_Begin"}, 0x8c068000)}
        a = got["8c070000"]
        self.assertEqual((a["fid_names"], a["unique"], a["candidates"], a["release"], a["our_name"]),
                         ("kmBeginScene", "yes", 2, "R10.1|R9", "Km_Begin"))
        self.assertEqual(a["library_object"], "kamui2:/kamui2/a_.elf|kamui2_d:/kamui2_d/a_.elf")
        b = got["8c070100"]
        self.assertEqual((b["fid_names"], b["unique"], b["score"]), ("memset|memclr", "best", "20.00"))
        self.assertEqual((got["8c070280"]["fid_names"], got["8c070280"]["unique"]), ("cos|sin", "no"))  # a tie
        self.assertEqual(got["8c070200"]["status"], "below")  # under 14.6: not a name
        self.assertNotIn("fid_names", got["8c070200"])
        self.assertEqual([got[x]["status"] for x in ("8c070300", "8c070400")], ["nomatch", "small"])
        self.assertEqual(got["8c020000"]["region"], "game")

    def test_tsv_round_trip(self):
        p = self.d / "t.tsv"
        L.write_tsv(p, ["addr", "why"], [{"addr": "8c0", "why": "a\tb\nc"}, ["8c1", None]])
        self.assertEqual(p.read_text(encoding="utf-8"), "addr\twhy\n8c0\ta b c\n8c1\t\n")
        self.assertEqual(L.read_tsv(p), [{"addr": "8c0", "why": "a b c"}, {"addr": "8c1", "why": ""}])


class Classify(unittest.TestCase):
    def test_rules(self):
        lib = ["8c070000", "8c070010", "8c070020", "8c070030", "8c070040", "8c070050", "8c070060", "8c070070"]
        fid = {"8c070000": {"status": "match", "unique": "yes", "fid_names": "a", "library_object": "k:/k/x_.elf"},
               "8c070020": {"status": "match", "unique": "yes", "fid_names": "b", "library_object": "k:/k/x_.elf"},
               "8c070040": {"status": "match", "unique": "yes", "fid_names": "c", "library_object": "k_d:/k_d/y_.elf"},
               "8c070070": {"status": "match", "unique": "no", "candidates": "2", "fid_names": "d|e",
                            "library_object": "m:/m/z_.elf"}}
        names = {"8c070060": "Lib_Engine", "8c070050": "Shc_Div"}
        edges = {"8c070000": {"8c070050"}, "8c070040": {"8c070060"}, "8c010000": {"8c070060"}}
        got = sdk_classify.classify(lib, fid, names, {"8c070050", "8c070060"}, edges, ["8c010000"], 0x8c068000)
        self.assertEqual(got["8c070000"][:2], ("sdk", "fid"))
        self.assertEqual(got["8c070070"][:2], ("sdk", "fid"))  # ambiguous: still SDK
        self.assertEqual(got["8c070010"][:2], ("sdk", "object"))  # between two functions of object x_
        self.assertEqual(got["8c070030"][:2], ("sdk", "library"))  # between x_ and y_ of library k (k_d is k)
        self.assertEqual(got["8c070050"][:2], ("sdk", "named"))  # no unique FID name after it: the registry decides
        self.assertNotIn("8c070060", got)  # a Lib_ name the game calls: no SDK evidence

    def test_named_and_callers(self):
        lib = ["8c070000", "8c070010", "8c070020"]
        names = {"8c070000": "kmFoo", "8c070020": "Lib_Bar"}
        edges = {"8c070000": {"8c070010"}, "8c070020": set()}
        got = sdk_classify.classify(lib, {}, names, {"8c070000", "8c070020"}, edges, [], 0x8c068000)
        self.assertEqual(got["8c070000"][:2], ("sdk", "named"))
        self.assertEqual(got["8c070010"][:2], ("sdk", "callers"))  # only kmFoo calls it
        self.assertNotIn("8c070020", got)

    def test_reach_stops_at_sdk(self):
        edges = {"8c010000": {"8c070000", "8c070010"}, "8c070000": {"8c070020"}, "8c070010": {"8c070030"}}
        self.assertEqual(sdk_classify.reach(["8c010000"], edges, {"8c070010"}, 0x8c068000),
                         {"8c070000", "8c070010", "8c070020"})
