"""The SH-4 translator on code whose meaning the program sets at run time (Toy Commander's movie decoder):
1. written_code: a literal-pool word the program writes at run time (TC's fn_8c09ea20 rewrites 8c09f2ac's mov.w
   pool at decoder start) is read from memory where the original reads it, sign-extended as mov.w does; a pool word
   not listed stays a constant; written_code() reads project.json's words and ranges;
2. fpscr_sz_from_caller: a function its callers run with FPSCR.SZ=1 (TC's TM2X handlers, inside 8c09de06's fschg
   pair) gets an SZ=0 body that is the C it gets without the fact, an SZ=1 body whose fmov moves 64-bit pairs, and
   body() choosing by the runtime's sh4_sz(); the runtimes the judge and the port link define sh4_sz.
Synthetic machine code. Run: python -W error tests/test_dc_runtime_code.py.
"""
import pathlib
import re
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from sh4translate.translate import (Image, Signature, sz_from_caller, translate_function,  # noqa: E402
                                    written_code)

BASE = 0x8C010000


def image(words):
    return Image(struct.pack(f"<{len(words)}H", *words), BASE)


POOL = image([0x9102,      # 0 mov.w @(0x8),r1   (ea = 0 + 4 + 2*2)
              0x6013,      # 2 mov r1,r0
              0x000B,      # 4 rts
              0x0009,      # 6 nop
              0xF234])     # 8 the pool word: 0xf234 (negative: mov.w sign-extends)
COPY = image([0xF248,      # 0 fmov.s @r4,fr2   (SZ=1: fmov @r4,dr2, 8 bytes)
              0xF52A,      # 2 fmov.s fr2,@r5   (SZ=1: fmov dr2,@r5)
              0x000B,      # 4 rts
              0x0009])     # 6 nop


def sig_ret():
    return Signature("fn_8c010000", "u32 fn_8c010000(void)", [], "u32")


def sig_copy():
    return Signature("fn_8c010000", "void fn_8c010000(u32 a0, u32 a1)", [("a0", "u32", "r4"), ("a1", "u32", "r5")],
                     "void")


class WrittenCode(unittest.TestCase):
    def test_pool_word_read_at_run_time(self):
        for sp in (False, True):
            folded = translate_function(POOL, BASE, sig_ret(), sp_input=sp)
            read = translate_function(POOL, BASE, sig_ret(), sp_input=sp, written=written_code({"written_code": {
                "8c010008": "the program writes it"}}))
            self.assertIn("0xfffff234u", folded)
            self.assertNotIn("rd16(0x8c010008u)", folded)
            self.assertIn("(u32)(s32)(s16)rd16(0x8c010008u)", read)
            self.assertNotIn("0xfffff234u", read)

    def test_other_words_stay_constants(self):
        src = translate_function(POOL, BASE, sig_ret(), written={BASE + 0x20, BASE + 0x21})
        self.assertEqual(src, translate_function(POOL, BASE, sig_ret()))

    def test_fact_words_and_ranges(self):
        self.assertEqual(written_code({}), set())
        self.assertEqual(written_code({"written_code": {"8c09f320": "x", "8c000010..8c000014": "y"}}),
                         {0x8C09F320, 0x8C09F321, 0x8C000010, 0x8C000011, 0x8C000012, 0x8C000013})


class SzFromCaller(unittest.TestCase):
    def test_a_body_for_each_mode(self):
        for sp in (False, True):
            plain = translate_function(COPY, BASE, sig_copy(), sp_input=sp)
            both = translate_function(COPY, BASE, sig_copy(), sp_input=sp, sz_from_caller=True)
            self.assertNotIn("sh4_sz", plain)
            self.assertIn("if (sh4_sz())", both)
            nm = "body" if sp else "fn_8c010000"
            self.assertEqual(len(re.findall(r"\bbody_sz[01]\(", both)), 4)    # two definitions, two calls
            # SZ=1: an 8-byte pair at each address (DR2: fr2 at +0, fr3 at +4); SZ=0: one word
            sz1 = both[both.index("body_sz1("):both.index("body_sz0(")]
            self.assertRegex(sz1, r"fr3 = rdf32\((r4|a0) \+ 4u\);")
            self.assertRegex(sz1, r"wrf32\((r5|a1) \+ 4u, fr3\);")
            self.assertNotRegex(plain, r"\+ 4u")
            # the SZ=0 body is the plain translation's, renamed
            m = re.search(rf"^(?:static )?\w+ {nm}\((.|\n)*?^\}}\n", plain, re.M)
            self.assertIn(m.group(0).replace(f" {nm}(", " body_sz0(", 1).split("\n", 1)[1], both)

    def test_fact(self):
        self.assertEqual(sz_from_caller({"fpscr_sz_from_caller": {"8c09ce6c": "in 8c09de06's fschg pair"}}),
                         {0x8C09CE6C})
        self.assertEqual(sz_from_caller({}), set())

    def test_runtimes_define_sh4_sz(self):
        h = (ROOT / "targets/dreamcast_sh4/runtime/tc_rt.h").read_text(encoding="utf-8")
        self.assertIn("u32 sh4_sz(void);", h)
        for c in ("targets/dreamcast_sh4/runtime/tc_fpu.c", "port/runtime/sh4_rt.c"):
            self.assertIn("u32 sh4_sz(void) { return (g_mode & MODE_SZ) != 0u; }",
                          (ROOT / c).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
