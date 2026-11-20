"""The game's C++ throw and catch in the port form (port/games/<game>/eh.json, docs/x360/EXCEPTIONS_AND_LONGJMP.md part (b)). Before it,
a throw stopped the port at RtlRaiseException. A made-up image holds a parent F with a try block whose catches are B, A (by
reference) and catch(...), each a funclet returning the continuation 82010024 (reached in F only through a catch), a middle frame
WORK with two destructors in its unwind map and a try whose catch(...) rethrows, and DEEP with one destructor, which throws.
port/tools/eh_tables.py reads the tables at gen time and validates them; the translator gives F a landing slot and every
function of a group its call sites' addresses; runtime/xb_eh.h walks the frames. Where MSVC is installed (vswhere), F and its catch
funclets are translated, built at the port's flags with the runtime's xb_eh.h on stubs, and run:
- a throw two frames below the try is caught by catch(...) after B and A are skipped (or by A, which gets the object by reference),
  DEEP's and WORK's destructors run once each, innermost first, the catch funclet runs, F goes on at the continuation, the
  exception object is destroyed once, and the call depth, tc_sp and the vector file are put back;
- a rethrow from WORK's catch reaches F's catch(...), with WORK's destructors run after its catch;
- no catch for the type is a stop, with nothing run;
- a __C_specific_handler frame SEHF between WORK and DEEP (__try { __try { call } __finally FIN1 } __finally FIN2, and a third
  scope FIN3 that does not hold the call): FIN1 then FIN2 run after DEEP's destructor and before WORK's, with AbnormalTermination 1
  and SEHF's entry r1 in r4 and r12; FIN3 does not run (before: none ran);
- planted wrong tables (an unwind chain that loops, a call-site IP outside its function, a scope outside its function, an __except
  holding the call) stop with the reason before any destructor runs; gen-time validation refuses those and the other plants (an
  IP-to-state entry outside the functions, a try outside the unwind map, a continuation outside the parent, a __finally scope
  outside its function, a __finally handler that is no function) and passes the good tables.
Without eh.json nothing changes, and the judge's form never has any of it.
usage: python tests/test_x360_eh.py
"""
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import unittest

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path[:0] = [str(X360 / "translator"), str(X360 / "port" / "tools"), str(pathlib.Path(__file__).resolve().parent)]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone: gen_xbox needs a project)
from test_x360_setjmp import vcvars64  # noqa: E402
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, translate_function  # noqa: E402
import eh_tables  # noqa: E402

LOAD = 0x8200FF00
F, WORK, C_ALL, C_A, C_B, DEEP, RETHROW = 0x82010000, 0x82010300, 0x82010400, 0x82010440, 0x82010480, 0x82010500, 0x82010600
D_W0, D_W1, D_D0, DESTROY, THROW, HANDLER, RAISE = 0x82010700, 0x82010720, 0x82010740, 0x82010780, 0x82010F00, 0x82010F40, 0x82010F80
FI_F, U_F, T_F, H_F, IP_F = 0x82011000, 0x82011040, 0x82011060, 0x82011080, 0x820110C0
FI_W, U_W, T_W, H_W, IP_W = 0x82011100, 0x82011140, 0x82011180, 0x820111A0, 0x820111C0
FI_D, U_D, IP_D = 0x82011200, 0x82011240, 0x82011260
TI_A, CTA_A, CT_A, TI_C, CTA_C, CT_C = 0x82011300, 0x82011320, 0x82011330, 0x82011380, 0x820113A0, 0x820113B0
TYPE_A, TYPE_B, TYPE_C, PDATA = 0x82011800, 0x82011820, 0x82011840, 0x82011C00
CONT = 0x82010024
SEHF, FIN1, FIN2, FIN3, CSH, SCOPE = 0x82010800, 0x82010840, 0x82010860, 0x82010880, 0x82010FC0, 0x82011400
SEH_FUNCS = [(SEHF, 16, CSH), (FIN1, 2, 0), (FIN2, 2, 0), (FIN3, 2, 0)]   # (begin, instructions, frame handler)
SCOPES = [(SEHF + 0x10, SEHF + 0x20, FIN1, 0), (SEHF + 0x08, SEHF + 0x30, FIN2, 0), (SEHF + 0x20, SEHF + 0x30, FIN3, 0)]   # call at +0x18

F_CODE = [0x7C0802A6,   # mflr r0
          0x9001FFF8,   # stw r0,-8(r1)
          0x9421FF00,   # stwu r1,-0x100(r1)
          0x7C3F0B78,   # mr r31,r1
          0x38600005,   # li r3,5
          0x907F0050,   # stw r3,0x50(r31)
          0x480002E9,   # bl WORK            (state 0: the try)
          0x807F0050,   # lwz r3,0x50(r31)
          0x4800000C,   # b 8201002c
          0x807F0054,   # 82010024: lwz r3,0x54(r31)   the catches' continuation: only a catch reaches it
          0x48000004,   # b 8201002c
          0x38210100,   # addi r1,r1,0x100
          0x8001FFF8,   # lwz r0,-8(r1)
          0x7C0803A6,   # mtlr r0
          0x4E800020]   # blr


def catch_code(first):
    return [0x3BECFF00,   # addi r31,r12,-0x100   (F's r1 from its entry r1)
            first,        # li r3,0x99 / 0xbb, or lwz r3,0x80(r31): the catch object (by reference, at entry r1 - 0x80)
            0x907F0054,   # stw r3,0x54(r31)
            0x3C608201,   # lis r3,0x8201
            0x38630024,   # addi r3,r3,0x24     -> 82010024
            0x4E800020]   # blr


RETHROW_CODE = [0x3BECFF00, 0x38600000, 0x38800000, 0x480008F5, 0]   # addi r31,r12,-0x100; li r3,0; li r4,0; bl THROW (throw;)
FUNCS = [(F, len(F_CODE)), (WORK, 16), (C_ALL, 6), (C_A, 6), (C_B, 6), (DEEP, 16), (RETHROW, len(RETHROW_CODE))]


def image(plant=None):
    """The made-up image (bytes, meta): code, .pdata with the handler words before each function, and the tables."""
    img = bytearray(0x82012000 - LOAD)

    def put(a, words):
        for k, w in enumerate(words):
            struct.pack_into(">I", img, a - LOAD + 4 * k, w & 0xFFFFFFFF)
    put(F, F_CODE)
    put(C_ALL, catch_code(0x38600099))
    put(C_A, catch_code(0x807F0080))
    put(C_B, catch_code(0x386000BB))
    put(RETHROW, RETHROW_CODE)
    for k, (a, n) in enumerate(FUNCS):
        put(PDATA + 8 * k, [a, (n << 8) | (1 << 31)])
        put(a - 8, [HANDLER, FI_W if a in (WORK, RETHROW) else FI_D if a == DEEP else FI_F])
    for k, (a, n, h) in enumerate(SEH_FUNCS):
        put(PDATA + 8 * (len(FUNCS) + k), [a, (n << 8) | ((1 << 31) if h else 0)])
        if h:
            put(a - 8, [h, SCOPE])
    put(SCOPE, [len(SCOPES)] + [w for s in SCOPES for w in s])
    # F: states 0 (the try) and 1 (its catches); one try with catch B, catch A& (object at entry r1 - 0x80), catch(...)
    put(FI_F, [0x19930522, 2, U_F, 1, T_F, 3, IP_F, 0, 1])
    put(U_F, [-1, 0, -1, 0])
    put(T_F, [0, 0, 1, 3, H_F])
    put(H_F, [0, TYPE_B, 0, C_B, 8, TYPE_A, -0x80, C_A, 0x40, 0, 0, C_ALL])
    put(IP_F, [F + 0x18, 0, F + 0x1C, -1, C_ALL, 1])
    # WORK: 0 (an object, D_W0), 1 (another, D_W1), 2 (a try), 3 (its catch(...), which rethrows)
    put(FI_W, [0x19930522, 4, U_W, 1, T_W, 3, IP_W, 0, 1])
    put(U_W, [-1, D_W0, 0, D_W1, 1, 0, 1, 0])
    put(T_W, [2, 2, 3, 1, H_W])
    put(H_W, [0x40, 0, 0, RETHROW])
    put(IP_W, [WORK + 0x18, 2, WORK + 0x1C, -1, RETHROW, 3])
    # DEEP: one object (D_D0) live at its throw
    put(FI_D, [0x19930522, 1, U_D, 0, 0, 2, IP_D, 0, 1])
    put(U_D, [-1, D_D0])
    put(IP_D, [DEEP + 0x18, 0, DEEP + 0x1C, -1])
    # the throw types: A and C, each a 4-byte object destroyed by DESTROY
    for ti, cta, ct, ty in ((TI_A, CTA_A, CT_A, TYPE_A), (TI_C, CTA_C, CT_C, TYPE_C)):
        put(ti, [0, DESTROY, 0, cta])
        put(cta, [1, ct])
        put(ct, [0, ty, 0, -1, 0, 4, 0])
    if plant == "unwind_loop":       # WORK's state 1 goes to itself
        put(U_W + 8, [1])
    elif plant == "ip_outside":      # an IP-to-state entry of F's that no function of the group holds
        put(IP_F + 8, [0x82010200])
    elif plant == "try_outside":     # F's try reaches past its unwind map
        put(T_F, [0, 5, 6])
    elif plant == "cont_outside":    # catch(...) continues at 82010900, outside F
        put(C_ALL + 16, [0x38630900])
    elif plant == "seh_outside":     # SEHF's first scope ends past the function
        put(SCOPE + 8, [SEHF + 0x200])
    elif plant == "seh_handler":     # its __finally handler is no function
        put(SCOPE + 12, [0x82010900])
    meta = {"load_address": LOAD, "sections": [{"name": ".text", "start": LOAD, "raw_end": 0x82011000, "code": True},
                                               {"name": ".pdata", "start": PDATA, "raw_end": PDATA + 8 * (len(FUNCS) + len(SEH_FUNCS)), "code": False}]}
    return bytes(img), meta


def groups(plant=None):
    data, meta = image(plant)
    return eh_tables.scan(data, meta, HANDLER, [THROW], seh=CSH)


def translate(addr, eh, port=True, sig=None):
    data, _ = image()
    facts = Facts(port=port)
    if eh is not None:
        facts.eh = eh
    sig = sig or Signature(f"fn_{addr:08x}", f"u64 fn_{addr:08x}(void)", [], "u64", returns={})
    n = dict(FUNCS)[addr]
    src, _ = translate_function(XexImage(data, LOAD), addr, sig, facts, [(addr, addr + 4 * n)])
    return src


def f_sig():
    return Signature("fn_82010000", "u64 fn_82010000(u64 arg_r3)", [("arg_r3", "u64", "r3", -1)], "u64", returns={WORK: "v"})


def judge_sig():
    s = Signature.generic_for(F)     # the judge's harness hands a function every argument register its code reads
    s.returns = {WORK: "v"}
    return s


DRIVER = r"""#include <windows.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tc_rt.h"
u8 *tc_base;
__declspec(thread) u32 tc_sp, tc_r12, tc_r13;
__declspec(thread) const u64 *tc_nv;
__declspec(thread) u32 tc_vr[128][4];
__declspec(thread) u64 tc_gr[32];
__declspec(thread) f64 tc_fr[32];
__declspec(thread) u64 tc_resv_v;
__declspec(thread) u32 tc_resv_s;
static __declspec(thread) unsigned s_depth;
static __declspec(thread) u32 s_stack[256];
static __declspec(thread) const TcArgs *s_frame[256];
typedef struct { u32 begin, end, funcinfo, parent; const char *bad; } XbEhFn;
typedef struct { u32 begin, end, scope; const char *bad; } XbSeh;
#include "eh_table.inc"
static char said[512];
void xb_stop(const char *kind, const char *detail, u32 value) { printf("STOP %s %08x %s | log [%s]\n", kind, value, detail, said); fflush(stdout); exit(3); }
void port_log(const char *fmt, ...) { (void)fmt; }   /* the runtime logs a catch and an unwind action; the stub drops the line */
unsigned xb_frame_number(void) { return 0; }
#include "xb_eh.h"
u64 fn_82010000(u64 arg_r3);
u64 fn_82010400(void);
u64 fn_82010440(void);
u64 fn_82010480(void);
static int mode;
static u32 w_entry, d_entry, s_entry, obj;
static void say(const char *s, int ok) { strcat(said, s); if (!ok) strcat(said, "!r12"); strcat(said, " "); }
static u32 raise_record(u32 at, u32 o, u32 ti)
{
    u32 v[8] = {0xE06D7363u, 1, 0, 0, 3, 0x19930520u, o, ti}, k;
    for (k = 0; k < 8; k++) wr32(at + 4 * k, v[k]);
    return at;
}
static u64 call_from(u32 ip, u32 r1, u32 t, u32 r3)   /* a call of a function with EH tables: TcArgs.ip is its own address */
{
    TcArgs b;
    memset(&b, 0, sizeof b);
    b.r[0] = r3, b.sp[0] = r1, b.ip[0] = ip;
    return tc_call(t, &b);
}
static u64 run(u32 t, const TcArgs *a)
{
    switch (t) {
    case 0x82010000u: return fn_82010000(a->r[0]);
    case 0x82010400u: return fn_82010400();
    case 0x82010440u: return fn_82010440();
    case 0x82010480u: return fn_82010480();
    case 0x82010300u:   /* WORK */
        w_entry = tc_sp, tc_vr[70][0] = 0xbeefu;
        (void)call_from(0x82010318u, tc_sp - 0x100u, mode >= 7 ? 0x82010800u : 0x82010500u, 0);
        return 0;
    case 0x82010800u:   /* SEHF: __try { __try { DEEP } __finally FIN1 } __finally FIN2 */
        s_entry = tc_sp;
        (void)call_from(0x82010818u, tc_sp - 0x60u, 0x82010500u, 0);
        return 0;
    case 0x82010840u: say("fin1", tc_r12 == s_entry && a->r[0] == 1 && a->r[1] == s_entry); return 0;
    case 0x82010860u: say("fin2", tc_r12 == s_entry && a->r[0] == 1 && a->r[1] == s_entry); return 0;
    case 0x82010880u: say("fin3", 1); return 0;
    case 0x82010500u: { /* DEEP: throws its object, at r1 + 0x40 */
        u32 r1 = tc_sp - 0x80u;
        d_entry = tc_sp, obj = r1 + 0x40u, tc_vr[70][1] = 0xcafeu;
        wr32(obj, 0x0b1ec7u);
        if (mode == 0) return 0;
        (void)call_from(mode == 5 ? 0x82010600u : 0x82010518u, r1, 0x82010F80u, raise_record(r1 + 0x10u, obj, mode == 2 ? 0x82011300u : 0x82011380u));
        say("returned", 1);
        return 0;
    }
    case 0x82010600u:   /* WORK's catch(...): throw; */
        say("rethrow", tc_r12 == w_entry);
        (void)call_from(0x8201060cu, tc_sp - 0x70u, 0x82010F80u, raise_record(tc_sp - 0x60u, 0, 0));
        say("returned", 1);
        return 0;
    case 0x82010F80u:   /* RtlRaiseException */
        if (!xb_eh_raise(a)) { printf("STOP import\n"); exit(3); }
        return 0;
    case 0x82010700u: say("dW0", tc_r12 == w_entry); return 0;
    case 0x82010720u: say("dW1", tc_r12 == w_entry); return 0;
    case 0x82010740u: say("dD0", tc_r12 == d_entry); return 0;
    case 0x82010780u: say(a->r[0] == obj ? "destroy" : "destroy!obj", 1); return 0;
    }
    printf("unexpected call %08x\n", t);
    exit(4);
}
u64 tc_call(u32 t, const TcArgs *a)   /* xb_call.c's tc_call_inner, as far as the walk sees it */
{
    u32 saved = tc_sp;
    u64 r;
    tc_sp = a->sp[0], tc_r12 = a->r12[0], tc_nv = a->nv;
    s_frame[s_depth & 255] = a;
    s_stack[s_depth++ & 255] = t;
    r = run(t, a);
    s_depth--, tc_sp = saved;
    return r;
}
f64 tc_callf(u32 t, const TcArgs *a) { (void)tc_call(t, a); return 0.0; }
u64 tc_lastr(void) { return 0; }
f64 tc_lastf(void) { return 0.0; }
int main(int argc, char **argv)
{
    FILE *f;
    TcArgs m;
    u64 r;
    mode = atoi(argv[1]);
    tc_base = (u8 *)VirtualAlloc(NULL, 0x100000000ull, MEM_RESERVE, PAGE_NOACCESS);
    VirtualAlloc(tc_base + 0x00080000u, 0x00090000u, MEM_COMMIT, PAGE_READWRITE);
    VirtualAlloc(tc_base + 0x8200F000u, 0x00004000u, MEM_COMMIT, PAGE_READWRITE);
    if (fopen_s(&f, argv[2], "rb") || !f) return 5;
    fread(tc_base + 0x8200FF00u, 1, 0x2100, f);
    fclose(f);
    if (mode != 6) wr32(0x82011100u + 12, 0);       /* WORK without its try: its catch is for the rethrow run */
    if (mode == 3) wr32(0x82011060u + 12, 2);       /* F's catches B and A only: a C finds none */
    if (mode == 4) wr32(0x82011140u + 8, 1);        /* plant: WORK's state 1 goes to itself */
    if (mode == 8) wr32(0x82011400u + 8, 0x82010a00u);   /* plant: SEHF's first scope ends past the function */
    if (mode == 9) wr32(0x82011400u + 32, 0x82010828u);  /* plant: SEHF's second scope is an __except (a target) */
    tc_sp = 0x00100000u, s_depth = 5, tc_vr[70][0] = 0x1111u, tc_vr[70][1] = 0x2222u;
    memset(&m, 0, sizeof m);
    m.sp[0] = 0x00100000u;
    r = tc_call(0x82010000u, &m);
    printf("mode %d r %llx obj %s depth %u sp %x vr %x %x log [%s]\n", mode, r, r == obj ? "yes" : "no", s_depth, tc_sp, tc_vr[70][0], tc_vr[70][1], said);
    return 0;
}
"""


class Tables(unittest.TestCase):
    def test_good_tables_pass(self):
        gs = groups()
        self.assertEqual([g["problems"] for g in gs], [[], [], [], []], gs)
        by = {g["funcinfo"] or g["scope"]: g for g in gs}
        self.assertEqual((by[SCOPE]["kind"], by[SCOPE]["functions"], by[SCOPE]["scopes"]), ("seh", [(SEHF, SEHF + 64)], SCOPES))
        self.assertEqual(by[FI_F]["parent"], F)
        self.assertEqual(by[FI_F]["continuations"], {C_ALL: [CONT], C_A: [CONT], C_B: [CONT]})
        self.assertEqual(by[FI_W]["continuations"], {RETHROW: []})     # a catch that rethrows never returns: no landing
        fx = eh_tables.facts(gs)
        self.assertEqual(fx["land"], {F: [CONT]})
        self.assertEqual(fx["fns"], {a for a, _ in FUNCS} | {SEHF})     # SEHF records its call sites: its __finally run
        table = "\n".join(eh_tables.c_table(gs))
        self.assertIn(f"{{0x{F:08x}u, 0x{F + 4 * len(F_CODE):08x}u, 0x{FI_F:08x}u, 0x{F:08x}u, 0}},", table)
        self.assertIn(f"const unsigned xb_eh_fn_count = {len(FUNCS)};", table)
        self.assertIn(f"{{0x{SEHF:08x}u, 0x{SEHF + 64:08x}u, 0x{SCOPE:08x}u, 0}},", table)
        self.assertIn("const unsigned xb_eh_seh_count = 1;", table)
        self.assertIsNone(eh_tables.scan(*image(), HANDLER, [THROW])[-1].get("kind"))   # without the SEH handler: no SEH groups

    def test_planted_wrong_tables_are_refused(self):
        want = {"unwind_loop": (FI_W, "unwind state 1: the toState chain does not end at -1"),
                "ip_outside": (FI_F, "IP-to-state entry 82010200 is outside the functions of this FuncInfo"),
                "try_outside": (FI_F, "try 0: states 0..5, catches to 6: not inside the unwind map of 2 states"),
                "cont_outside": (FI_F, "catch handler 82010400: continuation 82010900 is outside the parent 82010000"),
                "seh_outside": (SCOPE, "scope 0: 82010810..82010a00 is outside the function 82010800..82010840"),
                "seh_handler": (SCOPE, "scope 0: __finally handler 82010900 is not a function")}
        for plant, (fi, why) in want.items():
            gs = {g["funcinfo"] or g["scope"]: g for g in groups(plant)}
            self.assertIn(why, gs[fi]["problems"], (plant, gs[fi]["problems"]))
            self.assertEqual([k for k, g in gs.items() if g["problems"]], [fi], plant)
            if fi == FI_F:
                self.assertEqual(eh_tables.facts(list(gs.values()))["land"], {}, plant)   # no landing on a table that failed
            self.assertIn(why.replace('"', "'"), "\n".join(eh_tables.c_table(list(gs.values()))), plant)   # the runtime stops with it

    def test_too_human_table(self):
        self.assertEqual(eh_tables.load(X360 / "port" / "games" / "too_human"), {"frame_handler": 0x82a4ff28, "throw": [0x82a4fcc0, 0x82ed35f0]})
        self.assertIsNone(eh_tables.load(X360 / "port" / "games" / "mega_man_10"))


class Translation(unittest.TestCase):
    def test_landing_slot_and_call_sites(self):
        src = translate(F, eh_tables.facts(groups()), sig=f_sig())
        self.assertIn(f"static const u32 ehc_[] = {{0x{CONT:08x}u}};", src, src)
        self.assertIn(f"if (setjmp(*xb_eh_slot(0x{F:08x}u, ehc_, 1u))) {{ ehland = xb_eh_landed(); ehsp = xb_eh_sp(); }}", src, src)
        self.assertTrue(re.search(r"a\.ip\[0\] = [^;]*82010018", src), src)
        self.assertRegex(src, r"if \(ehland != 0u\) \{\s*r1 = \(u64\)ehsp;", src)   # a landing: the frame's own r1, then the branch
        self.assertIn("0x54", src, src)                                # the continuation (lwz r3,0x54(r31)), reached only by a catch, is kept
        decl = [ln for ln in src.splitlines() if re.match(r"    (volatile )?(u64|u32|f64|s64|s32) \w+ = ", ln) and "= tc_" not in ln]
        self.assertTrue(decl and all(ln.startswith("    volatile ") for ln in decl), "\n".join(decl))
        fun = translate(C_ALL, eh_tables.facts(groups()))
        self.assertNotIn("xb_eh_slot", fun)                            # a funclet has no landing: its parent has

    def test_without_the_config_nothing_changes(self):
        fx = eh_tables.facts(groups())
        plain = translate(F, None, sig=f_sig())
        self.assertNotIn("ip[", plain)
        self.assertNotIn("xb_eh", plain)
        self.assertNotIn("volatile", plain)
        self.assertEqual(translate(F, {"fns": set(), "land": {}}, sig=f_sig()), plain)   # a function outside the tables
        judge = [translate(F, e, port=False, sig=judge_sig()) for e in (fx, None)]   # the judge's form, with and without the tables
        self.assertTrue(judge[1].startswith('#include "tc_rt.h"'), judge[1])
        self.assertEqual(judge[0], judge[1])
        self.assertNotIn("ip[", judge[0])

    @unittest.skipUnless(os.name == "nt" and vcvars64(), "no MSVC (vswhere) on this machine")
    def test_built_and_run(self):
        fx = eh_tables.facts(groups())
        rt = [X360 / "runtime", X360 / "port" / "runtime"]
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "fn.c").write_text(translate(F, fx, sig=f_sig()), encoding="utf-8")
            for a in (C_ALL, C_A, C_B):
                (d / f"fn_{a:08x}.c").write_text(translate(a, fx), encoding="utf-8")
            (d / "eh_table.inc").write_text("\n".join(eh_tables.c_table(groups())), encoding="utf-8")
            (d / "drv.c").write_text(DRIVER, encoding="utf-8")
            (d / "image.bin").write_bytes(image()[0])
            cl = "cl /nologo /O1 /fp:strict /W3 /DTC_PORT " + " ".join(f'/I"{p}"' for p in rt) + " fn.c fn_82010400.c fn_82010440.c fn_82010480.c drv.c /Fe:drv.exe"
            r = subprocess.run(f'cmd /d /s /c ""{vcvars64()}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            out = {m: subprocess.run([str(d / "drv.exe"), str(m), str(d / "image.bin")], capture_output=True, text=True, timeout=60).stdout.strip()
                   for m in range(10)}
        back = "depth 5 sp 100000 vr 1111 2222"
        self.assertEqual(out[0], "mode 0 r 5 obj no depth 5 sp 100000 vr beef cafe log []", out)           # no throw
        self.assertEqual(out[1], f"mode 1 r 99 obj no {back} log [dD0 dW1 dW0 destroy ]", out)            # B, A skipped; catch(...)
        self.assertRegex(out[2], rf"^mode 2 r [0-9a-f]+ obj yes {back} log \[dD0 dW1 dW0 destroy \]$")    # A& gets the object
        self.assertEqual(out[6], f"mode 6 r 99 obj no {back} log [dD0 rethrow dW1 dW0 destroy ]", out)    # WORK rethrows to F
        self.assertEqual(out[7], f"mode 7 r 99 obj no {back} log [dD0 fin1 fin2 dW1 dW0 destroy ]", out)  # SEHF's __finally, in order
        self.assertEqual(out[8], "STOP eh 82011400 a __C_specific_handler scope is outside its function | log []", out)  # plant refused
        self.assertEqual(out[9], "STOP eh 82010860 an __except filter on the way of a C++ throw (the console would ask it; "
                                 "the kit does not) | log []", out)                                                     # plant refused
        self.assertEqual(out[3], "STOP eh 82011380 a C++ throw no catch takes (the console's terminate) | log []", out)
        self.assertEqual(out[4], "STOP eh 82011100 an unwind state's toState chain does not end at -1 | log []", out)   # plant refused
        self.assertEqual(out[5], "STOP eh 82010600 a frame's call-site IP is outside its function (not translated with eh.json, "
                                 "or a wrong table) | log []", out)                                                      # plant refused


if __name__ == "__main__":
    unittest.main()
