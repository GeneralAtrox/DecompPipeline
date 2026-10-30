"""Translator C in the port (port/tools/tx_port.py) and the translator's port form (sp_input): which judged files a
port may compile (a PASS row whose sha256 is the file now), the mix and only picks, the frame bound of a judged body,
the tail-return check, the build switch, the game's own float maths (gen_port.py OWN_CRT: whole call chain judged),
the functions the glue leaves out (variadic) and the store-queue ones mode only leaves to readable C, and the port
form's body inputs (an argument register DP's signature does not list comes in as in_REG; the judged function passes
0).
Synthetic files and machine code. Run: python -W error tests/test_tx_port.py.
"""
import hashlib
import json
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
import game_config  # noqa: E402
import gen_port  # noqa: E402
import tx_port  # noqa: E402
from sh4translate.translate import Image, Signature, translate_function  # noqa: E402

BASE = 0x8C010000


class Judged(unittest.TestCase):
    def test_pass_and_sha(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            src = d / "translated_sp" / "fx"
            src.mkdir(parents=True)
            rows = []
            for a, verdict, text in (("8c010000", "PASS", "a"), ("8c010010", "FAIL", "b"), ("8c010020", "PASS", "c")):
                (src / f"{a}.c").write_text(text, encoding="utf-8")
                rows.append({"addr": a, "verdict": verdict, "sha256": hashlib.sha256(text.encode()).hexdigest()})
            (src / "8c010020.c").write_text("changed", encoding="utf-8")
            (d / "results_translated_sp_fx_part1of2.json").write_text(json.dumps(rows), encoding="utf-8")
            ok, why = tx_port.judged(d)
            self.assertEqual(list(ok), ["8c010000"])
            self.assertEqual(why["8c010010"], "FAIL")
            self.assertIn("sha256", why["8c010020"])

    def test_all_results(self):
        """dp_judge --all (every function with a harness, not only the matched ones) names its results
        results_translated_sp_<ws>_all[_partKofN].json; the files are still translated_sp/<ws>/."""
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "translated_sp" / "fx").mkdir(parents=True)
            (d / "translated_sp" / "fx" / "8c010000.c").write_text("a", encoding="utf-8")
            rows = [{"addr": "8c010000", "verdict": "PASS", "sha256": hashlib.sha256(b"a").hexdigest()}]
            (d / "results_translated_sp_fx_all_part0of3.json").write_text(json.dumps(rows), encoding="utf-8")
            ok, why = tx_port.judged(d)
            self.assertEqual((list(ok), why), (["8c010000"], {}))


class Pick(unittest.TestCase):
    def test_modes(self):
        readable, passing, tx, platform = {"a", "b", "c"}, {"a"}, {"a", "b", "d", "p"}, {"p"}
        # mix: readable that passes, readable with no translator C (as today), translator C elsewhere
        self.assertEqual(tx_port.pick("mix", readable, passing, tx, platform), (["a", "c"], ["b", "d"]))
        # only: translator C wherever it passes; readable only where it does not
        self.assertEqual(tx_port.pick("only", readable, passing, tx, platform), (["c"], ["a", "b", "d"]))


class FrameBound(unittest.TestCase):
    def test_steps_summed(self):
        text = "r15 = sp;\nr15 -= 0x10u;\nif (x) { r15 -= 8u; }\nr15 += 0x18u;\n/* r15 -= 999u */"
        self.assertEqual(tx_port.frame_bound(text), 0x18 + tx_port.MARGIN)

    def test_dynamic(self):
        self.assertEqual(tx_port.frame_bound("r15 = sp;\nr15 -= r3;"), tx_port.DYNAMIC)
        self.assertEqual(tx_port.frame_bound("r15 = sp;\nr0 = body(r15, pr, r4);"),
                         tx_port.DYNAMIC)


class TailReturn(unittest.TestCase):
    def body(self, tail):
        return "static void body(u32 sp, u32 ret_addr)\n{\n    r0 = tc_call(r2, &a);\n" + tail + "    return;\n}\n"

    def test_last_call(self):
        self.assertTrue(tx_port.tail_return(self.body("    /* 8c010004 add #4,r15 */\n"), False))

    def test_written_after(self):
        self.assertFalse(tx_port.tail_return(self.body("    /* 8c010004 mov #0,r0 */\n"), False))
        self.assertFalse(tx_port.tail_return(self.body("    if (r1) goto L1;\n"), False))


class Glue(unittest.TestCase):
    def test_store_queue_glued(self):
        """A store-queue function has glue (its RAM model is the port's area-3 window); mix takes it where no readable
        file passes, and only leaves it to a readable file where there is one."""
        self.assertTrue(tx_port.store_queues("    sq_start(&sq, 0u);"))
        self.assertFalse(tx_port.store_queues("    wr32(r4, r5);"))
        readable, passing, tx, sq = {"a", "q"}, {"a"}, {"a", "q", "s"}, {"q", "s"}
        self.assertEqual(tx_port.pick("mix", readable, passing, tx, set(), sq), (["a"], ["q", "s"]))
        self.assertEqual(tx_port.pick("only", readable, passing, tx, set(), sq), (["q"], ["a", "s"]))

    def test_variadic_left_out(self):
        with self.assertRaisesRegex(tx_port.GlueError, "variadic"):
            tx_port.inbound_c(None, None, "8c010000", "void fn_8c010000(char *f, ...)", {}, pathlib.Path("x.c"), "")

    def test_definitions(self):
        text = ("static u32 body(u32 sp, u32 ret_addr, u32 a0, u32 in_r5)\n{\n}\n"
                "u32 fn_8c010000(u32 a0)\n{\n    return body(0x8cffe000u, 0x8cfff000u, a0, 0u);\n}\n")
        (bret, bparams), (oret, oname, _) = tx_port.definitions(text)
        self.assertEqual((bret, bparams, oret, oname), ("u32", [("u32", "a0"), ("u32", "in_r5")], "u32",
                                                        "fn_8c010000"))


class Settings(unittest.TestCase):
    def test_switch(self):
        self.assertIsNone(tx_port.settings({}))
        self.assertEqual(tx_port.settings({"translator_c": {"dir": "tx"}})[1], "mix")
        self.assertEqual(tx_port.settings({"translator_c": {"dir": "tx", "mode": "mix"}}, None, "only")[1], "only")
        with self.assertRaises(SystemExit):
            tx_port.settings({}, "tx", "all")


class OwnFloatMaths(unittest.TestCase):
    """The console's float maths (gen_port.py OWN_CRT): the game's own C of a CRT name only with its whole call chain
    judged, port_config.h's PORT_OWN_CRT_name for each, and runtime/port_libm.c giving way on each."""
    NAMES = {"8c010000": "sinf", "8c010100": "powf", "8c010200": "memset", "8c010300": "Fx_Kernel",
             "8c010400": "Fx_Fails", "8c010500": "cosf"}
    CALLS = {"8c010000": ["8c010300"], "8c010100": ["8c010300", "8c010400"], "8c010300": ["8c010000"],
             "8c010500": ["8c010300"]}

    def meta(self, x):
        return {"callees": [{"addr": c} for c in self.CALLS.get(x, [])]} if x != "8c010500" else None

    def test_whole_chain(self):
        have = {"8c010000", "8c010100", "8c010200", "8c010300"}  # 8c010400 (powf's kernel) has no judged C
        self.assertEqual(gen_port.own_float_maths(self.NAMES, have, self.meta), {"8c010000"})  # a cycle ends
        self.assertEqual(gen_port.own_float_maths(self.NAMES, have | {"8c010500"}, self.meta), {"8c010000", "8c010500"})
        self.assertEqual(gen_port.own_float_maths(self.NAMES, have - {"8c010300"}, self.meta), set())

    def test_config_and_libm(self):
        cfg = {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
               "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}
        self.assertNotIn("PORT_OWN_CRT", game_config.port_config_h(cfg))
        self.assertIn("#define PORT_OWN_CRT_sinf 1\n", game_config.port_config_h(cfg, {"sinf"}))
        libm = (ROOT / "port" / "runtime" / "port_libm.c").read_text(encoding="utf-8")
        for n in gen_port.OWN_CRT:
            self.assertRegex(libm, rf"#ifndef PORT_OWN_CRT_{n}\nf32 {n}\(")
        self.assertNotIn("PORT_OWN_CRT_sqrtf", libm)
        self.assertNotIn("#include <math.h>", libm)
        for f in (ROOT / "port" / "runtime").glob("*.c"):  # an inline <math.h> powf would win the game's powf link
            if f.name != "port_libm.c":
                self.assertIsNone(re.search(r"\bpowf\s*\(", f.read_text(encoding="utf-8")), f.name)


class PortForm(unittest.TestCase):
    def test_in_regs(self):
        img = Image(struct.pack("<4H", 0x6053, 0x000B, 0x0009, 0x0009), BASE)  # mov r5,r0; rts; nop
        sig = Signature("fn_8c010000", "u32 fn_8c010000(u32 a0)", [("a0", "u32", "r4")], "u32")
        c = translate_function(img, BASE, sig, sp_input=True)
        c = c if isinstance(c, str) else c[0]
        self.assertRegex(c, r"static u32 body\(u32 sp, u32 ret_addr, u32 a0, [^)]*\bu32 in_r5\b")
        self.assertNotIn("in_r4", c)
        self.assertIn("r5 = in_r5;", c)
        self.assertRegex(c, r"return body\(0x8cffe000u, 0x8cfff000u, a0(, 0(\.0f|u))+\);")
        plain = translate_function(img, BASE, sig)
        self.assertNotIn("in_r5", plain if isinstance(plain, str) else plain[0])

    def test_in_regs_handed_back(self):
        """An r4..r7 the function's callers read at its exit (meta r_exports, TcOut) that is no parameter: the port
        form takes it as the runtime holds it (sh4_r), as the plain form does, so a path that leaves it hands back
        the caller's value (Spawn fe 8c4c796c: r7 0 instead of the caller's; 63 such FAILs in Spawn)."""
        img = Image(struct.pack("<4H", 0x6053, 0x000B, 0x0009, 0x0009), BASE)  # mov r5,r0; rts; nop
        meta = {"params": [{"name": "a0", "kind": "u32", "storage": "r4"}], "r_exports": ["r7"]}
        sig = Signature("fn_8c010000", "u32 fn_8c010000(u32 a0)", [("a0", "u32", "r4")], "u32",
                        r_out=frozenset({"r7"}))
        c = translate_function(img, BASE, sig, sp_input=True)
        c = c if isinstance(c, str) else c[0]
        self.assertRegex(c, r"return body\(0x8cffe000u, 0x8cfff000u, a0, [^;]*sh4_r\(7\)[^;]*\);")
        self.assertRegex(c, r"return body\([^;]*0u[^;]*\);")    # in_r5 (not handed back) still enters as 0
        self.assertNotIn("sh4_r(5)", c)
        reg = type("Reg", (), {"kind": staticmethod(lambda t: "i")})
        abi = type("Abi", (), {"own_storages": staticmethod(lambda *a: ["r4"])})
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "8c010000.c"
            f.write_text(c, encoding="utf-8")
            glue = tx_port.inbound_c(reg, abi, "8c010000", "u32 Fx_Get(u32 a0)", meta, f, c)
            no = tx_port.inbound_c(reg, abi, "8c010000", "u32 Fx_Get(u32 a0)", {"params": meta["params"]}, f, c)
        self.assertIn("sh4_r(7)", glue)
        self.assertNotIn("sh4_r(5)", glue)
        self.assertNotIn("sh4_r(7)", no)

    def test_register_named_past_meta(self):
        """A body judged where the meta listed its argument registers (param_r4, param_r5) glues when this meta lists
        none: each is the registry's argument in that register, as an in_REG one is (it was refused before: "meta has
        0 parameters, the judged body 2")."""
        text = ("static u32 body(u32 sp, u32 ret_addr, u32 param_r4, u32 param_r5, u32 in_r0, f32 in_fr4)\n{\n"
                "    return param_r4 + param_r5;\n}\n\nu32 fn_8c010000(u32 param_r4, u32 param_r5)\n{\n"
                "    return body(0x8cffe000u, 0x8cfff000u, param_r4, param_r5, 0u, 0.0f);\n}\n")
        reg = type("Reg", (), {"kind": staticmethod(lambda t: "v" if t == "void" else "i")})
        abi = type("Abi", (), {"own_storages": staticmethod(lambda *a: ["r4", "r5"])})
        glue = tx_port.inbound_c(reg, abi, "8c010000", "void Fx_Init(u32 a, u32 b)", {"params": []},
                                 pathlib.Path("8c010000.c"), text)
        self.assertIn("u32 r = body(sp, 0x8cfff000u, a0, a1, lo[0], sh4_fr(4));", glue)
        with self.assertRaisesRegex(tx_port.GlueError, "meta has 0 parameters, the judged body 1"):  # not a register
            tx_port.inbound_c(reg, abi, "8c010000", "void Fx_Init(u32 a, u32 b)", {"params": []},
                              pathlib.Path("8c010000.c"), text.replace("param_r5", "param_2"))

    def test_float_argument_read_from_fpu(self):
        """A registry float argument the judged body reads from the FPU state (fr4 = sh4_fr(4), PS2's Mtx_Translate
        8c17c220: ftrv on fr4..fr7) is written there before the body runs, since the port's callers pass it in C
        only (it was dropped as (void)a0, so the translation was lost); one the body never reads stays (void)."""
        text = ("static u32 body(u32 sp, u32 ret_addr, u32 in_r0)\n{\n    f32 fr4 = 0.0f, fr5 = 0.0f;\n"
                "    fr4 = sh4_fr(4);\n    fr5 = sh4_fr(5);\n    (void)fr5;\n    return in_r0 + (u32)fr4;\n}\n\n"
                "void fn_8c010000(void)\n{\n    body(0x8cffe000u, 0x8cfff000u, 0u);\n}\n")
        reg = type("Reg", (), {"kind": staticmethod(lambda t: "v" if t == "void" else "f" if t == "f32" else "i")})
        abi = type("Abi", (), {"own_storages": staticmethod(lambda *a: ["fr4", "fr5", "fr6"])})
        glue = tx_port.inbound_c(reg, abi, "8c010000", "void Fx_Move(f32 x, f32 y, f32 z)", {"params": []},
                                 pathlib.Path("8c010000.c"), text)
        self.assertIn("    sh4_set_fr(4, a0);\n    sh4_set_fr(5, a1);\n    (void)a2;\n", glue)
        self.assertLess(glue.index("sh4_set_fr(4, a0)"), glue.index("body(sp, 0x8cfff000u"))
        self.assertIn("port_tx_fpu_whole(1)", glue)
        # the translator's compact form (fr4=sh4_fr(4); Spawn's Lib_RenderModelAlpha 8c05fec2: its alpha was dropped,
        # so every model drew with face alpha 0) is read the same
        compact = text.replace("fr4 = sh4_fr(4);\n    fr5 = sh4_fr(5);", "fr4=sh4_fr(4);fr5=sh4_fr(5);")
        self.assertEqual(tx_port.inbound_c(reg, abi, "8c010000", "void Fx_Move(f32 x, f32 y, f32 z)",
                                           {"params": []}, pathlib.Path("8c010000.c"), compact), glue)

    def test_never_returns(self):
        """A main loop (bra to itself): its glue compiles at the port's flags with zero warnings (C4702 after the
        body's call, which never returns)."""
        img = Image(struct.pack("<2H", 0xAFFE, 0x0009), BASE)  # bra .; nop
        sig = Signature("fn_8c010000", "void fn_8c010000(void)", [], "void")
        c = translate_function(img, BASE, sig, sp_input=True)
        c = c if isinstance(c, str) else c[0]
        reg = type("Reg", (), {"kind": staticmethod(lambda t: "v")})
        abi = type("Abi", (), {"own_storages": staticmethod(lambda *a: [])})
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "8c010000.c").write_text(c, encoding="utf-8")
            glue = tx_port.inbound_c(reg, abi, "8c010000", "void Fx_Main(void)", {"params": []}, d / "8c010000.c", c)
            (d / "tx_8c010000.c").write_text(glue, encoding="utf-8")
            inc = [ROOT / "port" / "runtime" / "tx", ROOT / "targets" / "dreamcast_sh4" / "runtime"]
            cl = ("cl /nologo /c /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 "
                  + " ".join(f'/I"{p}"' for p in inc) + " tx_8c010000.c")
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())


STACK_DRIVER_C = r"""/* tx_rt.c's tc_call into a translated callee's glue: the callee's frame holds the call's 8 stack words. */
#include <stdarg.h>
#include <stdio.h>
#include <windows.h>
#include "port.h"
#include "port_dispatch.h"
#include "overlay.h"
#include "tx_rt.h"

u32 rd32(u32 addr);
void wr32(u32 addr, u32 value);
u32 tc_call(u32 target, const PortTcArgs *args);

/* the inbound glue of a judged body that takes 2 register arguments and reads its caller's stack word 1 (a va_list
 * pointer its prototype does not name), as tx_port.py writes it */
static u32 callee(u32 a0, u32 a1)
{
    u32 sp = port_tx_enter(16u, 32u), r = rd32(sp + 4u);
    (void)a0, (void)a1;
    port_tx_leave(sp, 32u);
    return r;
}
static void g_callee(const PortTcArgs *a, PortTcRet *o) { o->r0 = callee(a->r[0], a->r[1]); }
static const PortTcRow rows[] = {{0x8c020000u, g_callee}};
static const int nrows = 1;
const PortTcChunk port_tx_chunks[] = {{rows, &nrows}};
const int port_tx_nchunks = 1;
const PortDispatchChunk port_dispatch_chunks[] = {{NULL, &nrows}};
const int port_dispatch_nchunks = 0;

const PortDispatch *overlay_find(u32 addr) { (void)addr; return NULL; }
const PortDispatch *overlay_glue(u32 addr) { (void)addr; return NULL; }
const PortDispatch *port_dispatch_find(u32 addr) { (void)addr; return NULL; }
u32 guest_fold(u32 a) { return (a >> 24) == 0x0cu ? a | 0x80000000u : a; }
int guest_is_ram(u32 a) { return guest_fold(a) - 0x8c000000u < 0x01000000u; }
u32 port_vblank_count(void) { return 0; }
void port_wait_vblank(void) {}
void sh4_set_r(int index, u32 value) { (void)index, (void)value; }
void sh4_set_fr(int index, f32 value) { (void)index, (void)value; }
void port_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); }
void port_exit(const char *why, int code) { printf("EXIT: %s\n", why); ExitProcess((UINT)code); }

int main(void)
{
    PortTcArgs a;
    if (!VirtualAlloc((void *)(UINT_PTR)0x8c000000u, 0x01000000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) return 3;
    memset(&a, 0, sizeof a);
    a.r[0] = 1;
    a.s[0] = 0x8c5f99b8u; /* a format */
    a.s[1] = 0x8c00f3f0u; /* the va_list pointer */
    printf("tc_call %08x\n", tc_call(0x0c020000u, &a));
    wr32(0x8c00f400u - 32u + 4u, 0x5eedu); /* a direct call (readable C): the frame keeps what it held */
    printf("direct %08x\n", callee(1, 0));
    return 0;
}
"""


class StackWords(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "MSVC")
    def test_stack_words_reach_the_frame(self):
        """A callee reached by tc_call finds the call's stack words at its sp, also one its prototype does not name
        (the va_list pointer of a printf-like function, which the judged body reads at sp+4); a direct call from
        readable C leaves the frame as it was."""
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "driver.c").write_text(STACK_DRIVER_C, encoding="utf-8")
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            rt = ROOT / "port" / "runtime"
            inc = [d, rt, rt / "tx", ROOT / "targets" / "dreamcast_sh4" / "runtime_rd"]
            cl = ("cl /nologo /TC /std:c11 /W4 /WX /O1 " + " ".join(f'/I"{p}"' for p in inc)
                  + f' driver.c "{rt / "tx" / "tx_rt.c"}" /Fe:drv.exe /link /LARGEADDRESSAWARE /DYNAMICBASE:NO '
                  "/BASE:0x5a000000")
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            out = subprocess.run([str(d / "drv.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.split("\n")[:2], ["tc_call 8c00f3f0", "direct 00005eed"], out.stdout)


if __name__ == "__main__":
    unittest.main()
