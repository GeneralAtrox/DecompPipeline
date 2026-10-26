"""Batch 20: address constants in raw C (core/raw_consts.py) and the coverage of unnamed pointers, context.md's pointer
and variadic notes, a function or the inside of a global named among the globals (core/readable_context.py), RR5
(readable_lint.pointer_bytes_lint), the callee records compared on the registers the callee reads
(readable_check.callee_reads), callee out-pointers (core/readable_outptr.py), the variadic call audit
(core/varargs_audit.py) and the design requests (core/design_requests.py). Pure functions on synthetic data: no
Ghidra, no compiler. Run: python -W error tests/test_batch20.py (also from tests/test_tooling.py).
"""
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import design_requests, raw_consts, readable_context, readable_outptr, varargs_audit  # noqa: E402
from core.compare import IMAGE_BASE, Memory  # noqa: E402
from core.readable_check import callee_reads  # noqa: E402
from core.readable_lint import pointer_bytes_lint, readable_lint  # noqa: E402

FN, STR, DATA, POOL = 0x8C010100, 0x8C010200, 0x8C010300, 0x8C010040  # a function, a string, data, a pool word


def image():
    """0x400 bytes at IMAGE_BASE: the word 0x4f222fd6 at FN (its first instruction), "%d %s\\0" at STR."""
    b = bytearray(0x400)
    b[FN - IMAGE_BASE:FN - IMAGE_BASE + 4] = (0x4F222FD6).to_bytes(4, "little")
    b[STR - IMAGE_BASE:STR - IMAGE_BASE + 6] = b"%d %s\0"
    b[POOL - IMAGE_BASE:POOL - IMAGE_BASE + 4] = (0x12345678).to_bytes(4, "little")
    return bytes(b)


RAW = ("#include \"tc_rt.h\"\n\nvoid fn_8c010000(void)\n{\n    TcArgs args = {0};\n"
       "    /* 0x8c010380 in a comment is not a use */\n"
       "    args.r[0] = rd32(0x8c010040u);\n    args.r[1] = 0x8c010100u;\n    wr32(0x8c010300u, 0x8c010200u);\n"
       "    (void)tc_call(0x8c010100u, &args);\n}\n")


class Reg:
    """A names registry stand-in: functions, globals and readable.Registry.kind."""

    def __init__(self, functions=None, globals_=None):
        self.functions, self.globals = functions or {}, globals_ or {}

    @staticmethod
    def kind(t):
        return "f" if t in ("f32", "f64") else "i"


class RawConsts(unittest.TestCase):
    def test_uses(self):
        u = raw_consts.uses(RAW, 0x400)
        self.assertEqual(u[POOL], {"access"})
        self.assertEqual(u[FN], {"value", "call"})  # passed as a pointer and called
        self.assertEqual(u[STR], {"value"})  # stored as a value
        self.assertEqual(u[DATA], {"access"})
        self.assertNotIn(0x8C010380, u)  # comments ignored
        self.assertEqual(raw_consts.values(RAW, 0x400, own=0x8C010000), [FN, STR])

    def test_resolve(self):
        reg = Reg({"8c010100": {"proto": "void Cb_Run(void)"}}, {"8c010200": {"decl": "char g_fmt[8]"}})
        self.assertEqual(raw_consts.resolve(reg, None, FN), ("function", "8c010100", "void Cb_Run(void)"))
        self.assertEqual(raw_consts.resolve(reg, None, STR), ("global", "8c010200", "char g_fmt[8]", 0))
        self.assertIsNone(raw_consts.resolve(reg, None, STR + 2))  # no extents: a global's own address only
        self.assertEqual(raw_consts.resolve(reg, [(STR, 8)], STR + 2), ("global", "8c010200", "char g_fmt[8]", 2))
        self.assertIn("(u8 *)&name + 0x2 bytes", raw_consts.describe(("global", "8c010200", "char g_fmt[8]", 2)))

    def test_pointer_bytes(self):
        img = image()
        self.assertEqual(raw_consts.pointer_bytes(RAW, "Fn(0x4f222fd6u);", img), [(FN, 0x4F222FD6)])
        self.assertEqual(raw_consts.pointer_bytes(RAW, "Fn(Cb_Run);", img), [])
        self.assertEqual(raw_consts.pointer_bytes(RAW + "/* */ x = 0x4f222fd6u;", "Fn(0x4f222fd6u);", img), [])
        self.assertEqual(raw_consts.pointer_bytes(RAW, "x = 0x12345678u;", img), [])  # POOL is an access, not a value
        lint = pointer_bytes_lint("void f(void) { g(0x4f222fd6u); /* 0x4f222fd6 */ }", RAW, img)
        self.assertEqual(len(lint), 1)
        self.assertTrue(lint[0].startswith("RR5: 0x4f222fd6 is the 4 bytes stored at 0x8c010100"))

    def test_unnamed_values(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            d = pathlib.Path(d)
            (d / "game.bin").write_bytes(image())
            (d / "8c010000.c").write_text(RAW.replace("0x8c010200u", "0x8c010390u"), encoding="utf-8")
            catalog = {"functions": [{"addr": "8c010000", "bytes": 0x80}, {"addr": "8c010100", "bytes": 0x20}]}
            bodies = d / "function_bodies.txt"
            bodies.write_text("8c010000 8c010000 8c010060\n8c010100 8c010100 8c010120\n", encoding="utf-8")
            got = raw_consts.unnamed_values({"binary_path": d / "game.bin"}, Reg(), None, ["8c010000"], d, catalog,
                                            bodies)
            self.assertEqual(got["function"], {"8c010100": {"8c010000"}})  # a catalog function used as data
            self.assertEqual(got["data"], {"8c010390": {"8c010000"}})  # an unnamed rodata pointer: counted
            self.assertEqual(got["code"], {})
            named = Reg({"8c010100": {"proto": "void Cb_Run(void)"}}, {"8c010390": {"decl": "char g_s[4]"}})
            got = raw_consts.unnamed_values({"binary_path": d / "game.bin"}, named, None, ["8c010000"], d, catalog,
                                            bodies)
            self.assertEqual(got, {"data": {}, "function": {}, "code": {}})

    def test_region(self):
        bodies = [(0x8C010000, 0x8C010060, 0x8C010100)]
        self.assertEqual(raw_consts.region(0x8C010080, bodies), "code")  # after the body, before the next function
        self.assertEqual(raw_consts.region(0x8C010100, bodies), "data")


class Context(unittest.TestCase):
    def test_split_pointers(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            (pathlib.Path(d) / "game.bin").write_bytes(image())
            cfg = {"binary_path": pathlib.Path(d) / "game.bin"}
            reg = Reg({"8c010100": {"proto": "void Cb_Run(void)"}}, {"8c010200": {"decl": "char g_fmt[8]"}})
            consts = [("8c010040", 0x12345678), ("8c010100", 0x4F222FD6), ("8c010200", 0x25206425)]
            ptrs, rest = readable_context.split_pointers(reg, cfg, RAW, consts, None)
            self.assertEqual(rest, [("8c010040", 0x12345678)])  # a pool word raw.c loads stays a constant
            self.assertEqual([a for a, _ in ptrs], ["8c010100", "8c010200"])
            self.assertIn("`void Cb_Run(void)`", ptrs[0][1])
            self.assertIn("`char g_fmt[8]`", ptrs[1][1])

    def test_global_hint_function(self):
        """An address the oracle lists among the globals that the registry names as a function (an SDK entry PS2
        8c17e960 stores, 8c189540) is that function, not a MISSING GLOBAL."""
        reg = Reg({"8c010100": {"proto": "void Lib_RenderModelB(void *model, void *context)"}},
                  {"8c010000": {"decl": "u8 g_table[0x40]"}})
        hint = readable_context.global_hint(reg, "8c010100")
        self.assertIn("`void Lib_RenderModelB(void *model, void *context)`", hint)
        self.assertIn("MISSING GLOBAL", readable_context.global_hint(Reg(), "8c010100"))  # control: unnamed
        self.assertIn("g_table", readable_context.global_hint(reg, "8c010010"))  # control: a global's inside

    def test_global_hint_extent(self):
        """An address deep inside a declared global (PS2 8c16d9da: 0x8c535ea4 = g_fighters + 0x380c) is named by the
        global's extent, not a MISSING GLOBAL."""
        reg = Reg(globals_={"8c010000": {"decl": "Fighter g_fighters[4]"}})
        hint = readable_context.global_hint(reg, "8c01380c", [(0x8C010000, 0x8000)])
        self.assertIn("`Fighter g_fighters[4]`", hint)
        self.assertIn("offset 0x380c", hint)
        self.assertIn("MISSING GLOBAL", readable_context.global_hint(reg, "8c01380c"))  # control: no extents
        self.assertIn("MISSING GLOBAL", readable_context.global_hint(reg, "8c01380c", [(0x8C010000, 0x100)]))

    def test_f32_literal_extremes(self):
        """FLT_MAX's 8-digit form is above FLT_MAX (struct refuses it): the literal takes more digits, no crash."""
        lit = readable_context.f32_literal(0x7F7FFFFF)
        self.assertEqual(struct.unpack("<I", struct.pack("<f", float(lit[:-1])))[0], 0x7F7FFFFF)
        self.assertEqual(readable_context.f32_literal(0xFF7FFFFF), "-" + lit)
        self.assertEqual(readable_context.f32_literal(0x3F800000), "1.0f")  # control: the shortest form

    def test_mirror_pointers(self):
        """PS2 S20: raw.c writes a pointer in the 0x0c mirror (target.json address_mirrors), a value or a pool word:
        context.md names it like its 0x8c form (it was a constant to copy, or nothing), and RR3 refuses the number."""
        mirror, img = {"0c": "8c"}, bytearray(image())
        img[0x44:0x48] = (0x0C010100).to_bytes(4, "little")  # a pool word holding FN's mirror form
        raw = ("void fn_8c010000(void)\n{\n    TcArgs args = {0};\n    args.r[0] = rd32(0x8c010044u);\n"
               "    args.r[1] = 0x0c010200u;\n    (void)tc_call(0x8c010380u, &args);\n}\n")
        meta = {"addr": "8c010000", "globals": [], "callees": [{"addr": "8c010380"}]}
        reg = Reg({"8c010100": {"proto": "void Cb_Run(void)"}}, {"8c010200": {"decl": "char g_fmt[8]"}})
        self.assertEqual(raw_consts.uses(raw, 0x400, mirror)[STR], {"value"})
        self.assertNotIn(STR, raw_consts.uses(raw, 0x400))  # control: no mirrors, no use
        with tempfile.TemporaryDirectory(dir=TMP) as d, mock.patch.object(readable_context, "own_pool",
                                                                          lambda m: set()):
            (pathlib.Path(d) / "game.bin").write_bytes(bytes(img))
            cfg = {"binary_path": pathlib.Path(d) / "game.bin"}
            consts = readable_context.pool_constants(cfg, meta, raw, mirror)
            self.assertEqual([a for a, _ in consts], ["8c010044", "8c010200"])
            ptrs, rest = readable_context.split_pointers(reg, cfg, raw, consts, None, mirror)
            self.assertEqual((len(ptrs), rest), (2, []))
            self.assertIn("pool word holding the address of function `void Cb_Run(void)`", ptrs[0][1])
            self.assertIn("`char g_fmt[8]`", ptrs[1][1])
            consts = readable_context.pool_constants(cfg, meta, raw, {})  # control: no mirrors (the old reading)
            self.assertEqual(consts, [("8c010044", 0x0C010100)])
            self.assertEqual(readable_context.split_pointers(reg, cfg, raw, consts, None, {}), ([], consts))

        def rr3(src, named=lambda v: raw_consts.resolve(reg, None, v), m=mirror):
            body = f'#include "game.h"\n\nvoid F(void)\n{{\n    {src}\n}}\n'
            return [e for e in readable_lint(body, "F", "void F(void)", named, m) if e.startswith("RR3")]
        self.assertEqual(len(rr3("Cb_Set((void *)(uintptr_t)0x0c010100u);")), 1)
        self.assertIn("Cb_Run", rr3("Cb_Set((void *)(uintptr_t)0x0c010100u);")[0])
        self.assertEqual(rr3("Cb_Set((void *)(uintptr_t)0x0c010390u);"), [])  # control: names nothing
        self.assertEqual(rr3("Cb_Set((void *)(uintptr_t)0x0c010100u);", m={}), [])  # control: no mirrors
        self.assertEqual(len(rr3("Cb_Set((void *)(uintptr_t)0x8c010390u);", m={})), 1)  # the 0x8c rule as before

    def test_raw_word(self):
        self.assertEqual(readable_context.raw_word("r5"), "args.r[1]")
        self.assertEqual(readable_context.raw_word("fr6"), "args.fr[2]")
        self.assertEqual(readable_context.raw_word("stack+8"), "args.s[2]")
        self.assertEqual(readable_context.raw_word("r0"), "r0")

    def test_variadic_note(self):
        reg = Reg({"8c06829c": {"proto": "s32 sprintf(char *buf, const char *fmt, ...)", "varargs": "printf"}})
        note = readable_context.variadic_note(reg, "8c06829c", "s32 sprintf(char *buf, const char *fmt, ...)")
        self.assertIn("buf = args.r[0], fmt = args.s[0]", note)
        self.assertIn("the variable arguments are args.s[1], args.s[2]", note)


class CalleeReads(unittest.TestCase):
    def test_filters_argument_registers(self):
        meta = {"callees": [{"addr": "8c06162c", "regs": ["r4"]}, {"addr": "8c0615c0", "regs": ["r4", "s0"]},
                            {"addr": "8c000100", "regs": ["r5", "fr4", "s1", "r8"]}]}
        got = callee_reads(meta, {"8c06162c": [], "8c000100": ["r5"]})
        self.assertEqual([c["regs"] for c in got["callees"]], [[], ["r4", "s0"], ["r5", "s1", "r8"]])
        self.assertIs(callee_reads(meta, {}), meta)
        self.assertEqual(meta["callees"][0]["regs"], ["r4"])  # the harness meta itself is not changed


class OutPtr(unittest.TestCase):
    def test_pointee(self):
        p = readable_outptr.pointee
        self.assertEqual(p("s32 *"), "s32")
        self.assertEqual(p("GridQuery*"), "GridQuery")
        for t in ("const Vec3 *", "void *", "u8 **", "u32", "SdkProc"):
            self.assertIsNone(p(t), t)

    def test_lines_for(self):
        exp = {0: {"calls": [[0x8C0126EC, 0x8CFFDF00, 0x8CFFDF04, 0, 0, 0] + [0] * 30]},
               1: {"calls": [[0x8C0126EC, 0, 0x8C100000, 0, 0, 0] + [0] * 30]}}
        specs = [("8c0126ec", 1, "s32"), ("8c0126ec", 2, "Vec3")]
        got = readable_outptr.lines_for(exp, specs, {"s32": 4}.get)
        self.assertEqual(got, {0: ["O 0 8c0126ec 1 8cffdf00 4"]})  # Vec3 unsized; vector 1 passes no stack address

    def test_canary_matches_runtime(self):
        a = 0x8CFFDF00
        self.assertEqual(readable_outptr.canary(a), (0x91 + ((a * 37 + (a >> 7) * 11) & 0xFFFFFFFF) % 0x60) & 0xFF)
        self.assertTrue(0x91 <= readable_outptr.canary(a) < 0xF1)

    def test_deliver(self):
        g = 0x8CFFDF00
        mem = Memory(b"", {"patches": [(g + 2, b"\x55\x66")]})
        c0, c1 = readable_outptr.canary(g), readable_outptr.canary(g + 1)
        # byte 0: still the paint -> the vector's 0; byte 1: written by the original -> xx; byte 2: the vector's
        # poked byte -> 55; byte 3: overwritten -> xx
        raw = f"{c0:02x}{(c1 ^ 1):02x}5577"
        raw2 = f"{c0 ^ 0x5A:02x}{(c1 ^ 1):02x}5577"
        self.assertEqual(readable_outptr.deliver([(raw, 0)], g, mem), "00xx55xx")
        self.assertEqual(readable_outptr.deliver([(raw, 0), (raw2, 0x5A)], g, mem), "00xx55xx")
        # a write that equals the first paint (TC 8c04d900): the second paint shows it is a write
        same = f"{c0:02x}{c0:02x}"
        self.assertEqual(readable_outptr.deliver([(same, 0)], g, Memory(b"", {"patches": []})), "00xx")
        self.assertEqual(readable_outptr.deliver([(same, 0), (same, 0x5A)], g, Memory(b"", {"patches": []})), "xxxx")

    def test_deliver_stored_paint(self):
        # TC 8c0117f4: yaw = the painted output of an earlier call + (-2); bytes 1..3 keep the paint in both runs.
        g = 0x8CFFDF00
        paint = [readable_outptr.canary(g + k) for k in range(4)]
        runs = []
        for flip in readable_outptr.FLIPS:
            w = int.from_bytes(bytes(p ^ flip for p in paint), "little") - 2
            runs.append((w.to_bytes(4, "little").hex(), flip))
        none = Memory(b"", {"patches": []})
        self.assertEqual(readable_outptr.deliver(runs, g, none), "xx000000")  # paints alone: read as unwritten
        stored = [((h, "1111"), flip) for h, flip in runs]
        self.assertEqual(readable_outptr.deliver(stored, g, none), "xxxxxxxx")
        # a byte the original never stored to stays the vector's, mask or not
        part = [((h, "1000"), flip) for h, flip in runs]
        self.assertEqual(readable_outptr.deliver(part, g, none), "xx000000")

    def test_parse_ob_and_with_lines(self):
        ob = readable_outptr.parse_ob("V 3\nOB 2 8c0126ec 1 0a0b0c0d\nOB bad\nE\nV 4\nE\n")
        self.assertEqual(ob, {3: {(2, "8c0126ec", 1): "0a0b0c0d"}})
        ob = readable_outptr.parse_ob("V 3\nOB 2 8c0126ec 1 0a0b0c0d 0110\nE\n")
        self.assertEqual(ob, {3: {(2, "8c0126ec", 1): ("0a0b0c0d", "0110")}})
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            src, dst = pathlib.Path(d) / "v.txt", pathlib.Path(d) / "o.txt"
            src.write_text("V 3\nM 8c100000 00\nE\nV 4\nE\n", encoding="utf-8")
            readable_outptr.with_lines(src, {3: ["D 0 8c0126ec 1 00xx"]}, dst)
            self.assertEqual(dst.read_text(encoding="utf-8"), "V 3\nM 8c100000 00\nD 0 8c0126ec 1 00xx\nE\nV 4\nE\n")
            readable_outptr.with_lines(src, {4: ["B 0 8c0126ec 1 8cffdf00 4"]}, dst, after_v=True)
            self.assertEqual(dst.read_text(encoding="utf-8"),
                             "V 3\nM 8c100000 00\nE\nV 4\nB 0 8c0126ec 1 8cffdf00 4\nE\n")


def rec(words):
    """A call record with stack words s0.. = words."""
    r = [0x8C06829C] + [0] * 40
    for j, w in enumerate(words):
        r[13 + j] = w
    return r


class Varargs(unittest.TestCase):
    INFO = (0, 1, "printf")  # sprintf(buf, fmt, ...): fmt in s0, variable words from s1

    def setUp(self):
        self.img = Memory(image(), {"patches": []})
        self.exp = {"writes": {}}

    def test_format_from_image(self):
        self.assertEqual(varargs_audit.check_call(self.INFO, self.img, self.exp, rec([STR, 7, STR])), ([], 0, 1, 0))

    def test_image_before_vector_fill(self):
        vec = Memory(image(), {"patches": [(STR, b"\x01\x02\x03\x04\x05\x06\x07\x08")]})  # random fill over rodata
        self.assertEqual(varargs_audit.check_call(self.INFO, vec, self.exp, rec([STR, 7, 0]), self.img)[0], [])

    def test_s_word_not_an_address_is_counted(self):
        self.assertEqual(varargs_audit.check_call(self.INFO, self.img, self.exp, rec([STR, 7, 0x1234])), ([], 0, 0, 1))

    def test_bad_format_word(self):
        probs = varargs_audit.check_call(self.INFO, self.img, self.exp, rec([7, STR]))[0]
        self.assertEqual(probs, ["format word s0 is 0x00000007, not an address"])

    def test_stack_format_unread(self):
        self.assertEqual(varargs_audit.check_call(self.INFO, self.img, self.exp, rec([0x8CFFDF00, 1])), ([], 1, 0, 0))

    def test_null_rule(self):
        info = (0, 1, "null")
        self.assertEqual(varargs_audit.check_call(info, self.img, self.exp, rec([STR, 5, 0]))[0], [])
        self.assertEqual(varargs_audit.check_call(info, self.img, self.exp, rec([1] * 8))[0],
                         ["no zero word ends the list in s1..s7"])


class Requests(unittest.TestCase):
    def test_parse(self):
        text = ("FAIL: the registry name for 0x8c041014 is missing, so NULL is passed.\n"
                "MISSING FIELDS: RigidBody +0x124 (f32), Viewport +0x1b4\n"
                "The one-parameter prototype Entity_ResetRot(s32 entity_id) cannot carry r5 and r6.\n"
                "MISSING GLOBAL 0x8cffdf00\nRESULT: FAIL")
        got = design_requests.parse(text, "8c000100")
        self.assertIn(("field", "RigidBody", "RigidBody +0x124 (f32)"), got)
        self.assertIn(("field", "Viewport", "Viewport +0x1b4"), got)
        self.assertEqual([k for kind, k, _ in got if kind == "name"], ["8c041014"])  # a stack address is no name
        self.assertEqual([k for kind, k, _ in got if kind == "prototype"], ["Entity_ResetRot"])
        self.assertEqual(design_requests.parse("MISSING FIELDS: none (all fields exist)\n"), [])
        own = design_requests.parse("The prototype cannot carry the second result.", "8c000100")
        self.assertEqual(own[0][:2], ("prototype", "(its own: 8c000100)"))

    def test_report(self):
        groups = {"S01": {"name": {"8c041014": [{"addr": "8c041146", "attempt": 1, "detail": "x"}]}}}
        lines = design_requests.report(groups)
        self.assertEqual(lines[0], "S01: 1 function(s) with requests")
        self.assertEqual(lines[-1], "requests: 0 field key(s), 1 name key(s), 0 prototype key(s)")


if __name__ == "__main__":
    unittest.main()
