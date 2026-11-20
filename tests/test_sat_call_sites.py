"""The real return address per call in Saturn ports (save anywhere, stage 1, 2026-11-18).

The translated C pushed the judge's return sentinel 060ff000 where the SH-2 pushes PR, and the kit kept no record of where
an open frame resumes, so a save state could not say where to carry on. Now the translator marks each call with its
return address (overlay.py --call-sites, a comment: the C is otherwise the same), gen_saturn.py turns the mark into
sat_at(ADDR, target) and the entry's `pr = 0x060ff000u` into sat_pr_in(), and the kit (sat_call.c) keeps the address and
the caller's r8..r14 per frame.
Controls (each fails on the code before this change):
1. a call (bsr) gets `/*@ret A+4*/` with call_sites on, and none with it off;
2. the C with the marks removed is byte-identical to the C without them: the judged code cannot change;
3. a jump (bra out of the body) gets no mark;
4. gen_saturn turns a marked call into sat_at(...), the entry PR into sat_pr_in(), the recursive wrapper's sentinel too,
   strips a mark left on a jump, and leaves C without marks unchanged apart from the entry PR;
5. the kit takes the address at tc_call's entry and keeps it per frame (sat_call.c), and sat_task.c keeps it per context.
Planted wrong answers (the check must reject each): a mark at the call's own address, a mark on a jump, a gen_saturn that
drops the target. Run: python -W error tests/test_sat_call_sites.py.
Nearest existing: tests/test_sat_exit_regs.py - gen_saturn's rewrites of one function; this one starts at the translator.
"""
import pathlib
import re
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "targets" / "saturn_sh2" / "translator"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
from sh2translate.translate import FunctionTranslator, Image, Signature, TargetFacts  # noqa: E402
import gen_saturn  # noqa: E402

SITE_MARK = re.compile(r"\s*/\*@ret [0-9a-f]{8}\*/")


def rewrite(text):
    """gen_saturn's rewrite (the code before it had none: the text comes back as it was)"""
    return getattr(gen_saturn, "call_sites", lambda t: t)(text)

BASE = 0x06000000
# 06000000 sts.l pr,@-r15 / bsr 06000012 / nop / lds.l @r15+,pr / bra 06000024 / nop ... 06000010 rts / nop
CODE = [0x4F22, 0xB006, 0x0009, 0x4F26, 0xA00C, 0x0009, 0x0009, 0x0009, 0x000B, 0x0009, 0x0009, 0x0009,
        0x0009, 0x0009, 0x0009, 0x0009, 0x000B, 0x0009]


def translate(sites):
    img = Image(b"".join(struct.pack(">H", w) for w in CODE), BASE)
    ft = FunctionTranslator(img, BASE, Signature.generic(BASE), TargetFacts(), [(BASE, BASE + 0x0B)], {BASE, 0x06000012, 0x06000024},
                            comments=False)
    ft.call_sites = sites
    return ft.translate()


class Translator(unittest.TestCase):
    def test_mark_on_call_only(self):
        on, off = translate(True), translate(False)
        self.assertIn("tc_call(0x06000024u, &a);", on)  # the jump out of the body: no mark
        marks = re.findall(r"/\*@ret ([0-9a-f]{8})\*/", on)
        self.assertEqual(marks, ["06000006"], on)  # bsr at 06000002: PR = 06000006
        self.assertNotIn("@ret", off)
        self.assertEqual(SITE_MARK.sub("", on), off)  # the judged C is the same code

    def test_planted(self):
        on = translate(True)
        wrong_addr = on.replace("@ret 06000006", "@ret 06000002")
        self.assertNotEqual(re.findall(r"@ret ([0-9a-f]{8})", wrong_addr), ["06000006"])
        jump_marked = on.replace("tc_call(0x06000024u", "tc_call(0x06000024u /*@ret 0600000c*/")
        self.assertNotEqual(re.findall(r"@ret ([0-9a-f]{8})", jump_marked), ["06000006"])


class GenSaturn(unittest.TestCase):
    TEXT = ("u32 fn_06000000(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    u32 r0 = 0, pr = 0;\n    pr = 0x060ff000u;\n"
            "    r0 = tc_call(0x06000010u /*@ret 06000006*/, &a);\n    r0 = tx_call(r1 /*@ret 06000008*/, r0, r1, r2, r3, r4, r5, r6, r7, r15);\n"
            "    return tc_tail_sp(0x06000020u /*@ret 0600000c*/, &a, r15);\n}\n"
            "u32 fn_06000100(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    return body(sat_entry_sp, 0x060ff000u, a0);\n}\n")

    def test_rewrites(self):
        t = rewrite(self.TEXT)
        self.assertIn("pr = sat_pr_in();", t)
        self.assertIn("tc_call(sat_at(0x06000006u, 0x06000010u), &a)", t)
        self.assertIn("tx_call(sat_at(0x06000008u, r1), r0,", t)
        self.assertIn("tc_tail_sp(0x06000020u, &a, r15)", t)
        self.assertIn("body(sat_entry_sp, sat_pr_in(), a0)", t)
        self.assertNotIn("@ret", t)
        self.assertNotIn("0x060ff000u", t)
        # planted: a rewrite that loses the target would not keep the call's address
        self.assertNotIn("tc_call(sat_at(0x06000006u, ), &a)", t)

    def test_compact_form(self):
        t = rewrite("pr=0x060ff000u;r0=tc_call(0x06000010u/*@ret 06000006*/,&a);")
        self.assertEqual(t, "pr = sat_pr_in();r0=tc_call(sat_at(0x06000006u, 0x06000010u),&a);")

    def test_unmarked_unchanged(self):
        plain = "    r0 = tc_call(0x06000010u, &a);\n    r1 = r0;\n"
        self.assertEqual(rewrite(plain), plain)


class Kit(unittest.TestCase):
    def test_kit_keeps_the_site(self):
        call = (ROOT / "port" / "saturn" / "sat_call.c").read_text(encoding="utf-8")
        task = (ROOT / "port" / "saturn" / "sat_task.c").read_text(encoding="utf-8")
        body = call[call.index("u32 tc_call(u32 target, const TcArgs *a)"):]
        first = body.split("\n")[2]
        self.assertIn("u32 site = sat_ret; sat_ret = SAT_PR_NONE;", first)  # before anything that can run guest code
        self.assertIn("frame_site(c, site);", body)
        self.assertIn("sat_ret = keep_ret;", call)  # an interrupt keeps the pending call's address
        self.assertIn("cur->ret_next = sat_ret;", call)  # so does the other CPU's turn
        self.assertIn("memcpy(x->site, c->site", task)
        self.assertIn("memcpy(c->site, x->site", task)


if __name__ == "__main__":
    unittest.main()
