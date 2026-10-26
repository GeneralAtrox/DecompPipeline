"""The SH-4 raw runtime's names, for the shared readable pass (core/rtx/lift): what in a translator's raw C is
memory, the call block, the stack pointer and the hand-over of the callee-saved registers (runtime/tc_rt.h)."""
from core.rtx import flow
from core.rtx.cast import Call
from core.rtx.flow import Unsupported


class Spec(flow.Target):
    loads = {"rd8": "u8", "rd16": "u16", "rd32": "u32", "rdf32": "f32"}
    stores = {"wr8": "u8", "wr16": "u16", "wr32": "u32", "wrf32": "f32"}
    width = {"rd8": "8", "rd16": "16", "rd32": "32", "rdf32": "f32", "wr8": "8", "wr16": "16", "wr32": "32",
             "wrf32": "f32"}
    # no effect and no memory read: the translator's bit casts, sign extension and the FPU's value operations
    pure = {"u2f", "f2u", "f_abs", "f_dn", "sx8", "sx16", "sh4_ftrc", "sh4_fsrra", "sh4_fsqrt_rm", "sh4_fmac"}
    u2f, f2u = "u2f", "f2u"
    helper_types = {"u2f": "f32", "f2u": "u32", "f_abs": "f32", "f_dn": "f32", "sx8": "u32", "sx16": "u32",
                    "sh4_ftrc": "s32", "sh4_fsrra": "f32", "sh4_fsqrt_rm": "f32", "sh4_fmac": "f32"}
    # the bit casts the lift may add where the raw C had none (the translator's own text, runtime/tc_rt.h)
    helper_src = {"u2f": "static f32 u2f(u32 x) { union { u32 u; f32 f; } v; v.u = x; return v.f; }",
                  "f2u": "static u32 f2u(f32 x) { union { u32 u; f32 f; } v; v.f = x; return v.u; }"}
    args_type = "TcArgs"
    call_fns = ("tc_call", "tc_callf")
    link = "pr"
    sp_reg, sp_init = "r15", 0x8CFFE000
    ram = (0x8C000000, 0x8D000000)  # main RAM
    # target.json address_mirrors: {mirror top byte: (RAM top byte, op, k)}, the mirror is `address op k`
    mirrors = {0x0C: (0x8C, "&", 0x1FFFFFFF)}  # P0 (U0): the address with the top three bits clear
    handover_get = {"sh4_r", "sh4_fr"}
    handover_set = {"sh4_set_r", "sh4_set_fr"}
    # fr12..fr15 are also the vector library's state (fv12, runtime_rd/rd_rt.h): a callee may read or leave values
    # there, so their hand-overs around calls stay (sh4_fr / sh4_set_fr), and a read of them is a read of state
    state_regs = {"fr12", "fr13", "fr14", "fr15"}

    def is_load(self, fn):
        return fn in self.loads or fn == "sh4_fr"
    # what a readable file cannot be: the FPU's banks and modes, store queues, cache and status-register operations
    refuse = {"sh4_frchg", "sh4_fschg", "sh4_fpchg", "sh4_ftrv_fv", "sh4_fipr_fv", "sh4_xf", "sh4_set_xf",
              "sh4_stc_sr", "sh4_ldc_sr", "sh4_stc_gbr", "sh4_ldc_gbr", "sq_start", "sq_put", "sq_in", "body"}

    def handover_name(self, fn, k):
        return ("fr" if fn == "sh4_fr" else "r") + str(k)

    def handover_reg(self, call):
        return self.handover_name("sh4_fr" if call.fn == "sh4_set_fr" else "sh4_r", call.args[0].v)

    def handover_call(self, reg):
        return ("sh4_fr", int(reg[2:])) if reg.startswith("fr") else ("sh4_r", int(reg[1:]))

    def handover_put(self, reg):
        return ("sh4_set_fr", int(reg[2:])) if reg.startswith("fr") else ("sh4_set_r", int(reg[1:]))

    def arg_var(self, storage):
        """The call block's field for a parameter storage (a.r[0] is r4, a.fr[0] fr4, a.s[0] the first stack word,
        a.rlo[0] r0)."""
        if storage.startswith("fr"):
            return f"__a_fr{int(storage[2:]) - 4}"
        if storage.startswith("stack+"):
            return f"__a_s{int(storage[6:]) // 4}"
        if storage.startswith("r"):
            k = int(storage[1:])
            return f"__a_r{k - 4}" if k >= 4 else f"__a_rlo{k}"
        return None

    def check(self, body):
        from core.rtx.lift import calls_used
        bad = calls_used(body.body) & self.refuse
        if bad:
            raise Unsupported(f"runtime state a readable file has no form for: {sorted(bad)}")
        from core.rtx.cast import Case
        from core.rtx.lift import all_stmts
        for _, _, s in all_stmts(body.body):
            if isinstance(s, Case) and s.v is not None and flow.is_int(s.v) and 0x8C000000 <= s.v.v < 0x8D000000:
                raise Unsupported("a computed jump (a switch over code addresses)")


SPEC = Spec()
