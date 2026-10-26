"""The PowerPC raw runtime's names, for the shared readable pass (core/rtx/lift): what in the PPC translator's raw C
is memory and the call block (runtime/tc_rt.h). The translator already keeps the stack and the link register out of
the raw C and holds floats as doubles (f64), as the 750CL does."""
from core.rtx import flow
from core.rtx.cast import Cast, Unary, Var
from core.rtx.flow import Unsupported

F64 = {"px_b2f", "px_ctiw", "px_fdiv", "ppc_frsp", "ppc_fmadds", "ppc_fmsubs", "ppc_fnmadds", "ppc_fnmsubs",
       "ppc_fsel", "ppc_fabs", "ppc_fnabs"}
U32 = {"ppc_single_bits", "ppc_cntlzw", "ppc_rlwinm", "ppc_rlwimi", "ppc_divwu", "ppc_mulhw", "ppc_mulhwu"}
S32 = {"ppc_fctiwz", "ppc_fctiw", "ppc_divw"}


class Spec(flow.Target):
    loads = {"rd8": "u8", "rd16": "u16", "rd32": "u32", "rd64": "u64", "rdf32": "f32", "rdf64": "f64"}
    # wrf32d (stfs of a double) is a store of an f32 only when the double is one exactly (store_value)
    stores = {"wr8": "u8", "wr16": "u16", "wr32": "u32", "wr64": "u64", "wrf32": "f32", "wrf32d": "f32",
              "wrf64": "f64"}
    width = {"rd8": "8", "rd16": "16", "rd32": "32", "rd64": "64", "rdf32": "f32", "rdf64": "f64", "wr8": "8",
             "wr16": "16", "wr32": "32", "wr64": "64", "wrf32": "f32", "wrf32d": "f32", "wrf64": "f64"}
    # no effect and no memory read: the translator's bit casts and the FPU's and integer unit's value operations
    pure = {"u2f", "f2u", "px_f2b"} | F64 | U32 | S32
    u2f, f2u = "u2f", "f2u"
    helper_types = {"u2f": "f32", "f2u": "u32", "px_f2b": "u64", **{f: "f64" for f in F64},
                    **{f: "u32" for f in U32}, **{f: "s32" for f in S32}}
    helper_src = {"u2f": "static f32 u2f(u32 x) { union { u32 u; f32 f; } v; v.u = x; return v.f; }",
                  "f2u": "static u32 f2u(f32 x) { union { u32 u; f32 f; } v; v.f = x; return v.u; }"}
    args_type = "TcArgs"
    call_fns = ("tc_call", "tc_callf")
    link = "lr"
    sp_reg, sp_init = "r1", 0x817FE000  # target.json abi: entry_sp
    sentinel = 0x817FF000  # target.json abi: return_sentinel (the link register at entry)
    stack = (0x817F0000, 0x81800000)
    ram = (0x80000000, 0x81800000)  # MEM1
    mirrors = {0xC0: (0x80, "|", 0x40000000), 0xC1: (0x81, "|", 0x40000000)}  # uncached (address_mirrors c0, c1)
    handover_get, handover_set = set(), set()
    refuse = {"body"}

    def handover_name(self, fn, k):
        raise Unsupported(f"{fn}: no hand-over in the PPC raw C")

    handover_reg = handover_call = handover_name

    def arg_var(self, storage):
        """The call block's field for a parameter storage (a.r[0] is r3, a.f[0] f1, a.s[0] the first stack word)."""
        if storage.startswith("stack+"):
            return f"__a_s{int(storage[6:]) // 4}"
        if storage[:1] == "r" and storage[1:].isdigit() and 3 <= int(storage[1:]) <= 10:
            return f"__a_r{int(storage[1:]) - 3}"
        if storage[:1] == "f" and storage[1:].isdigit() and 1 <= int(storage[1:]) <= 8:
            return f"__a_f{int(storage[1:]) - 1}"
        return None

    def storage_type(self, storage):
        """The C type of a call-block field: the FPRs hold doubles."""
        return "f64" if storage[:1] == "f" and storage[1:].isdigit() else "u32"

    def store_value(self, fn, val):
        """wrf32d's value as an f32: (f64)x of an f32 x (exact, so stfs and C's conversion store the same bits)."""
        if fn != "wrf32d":
            return val
        if isinstance(val, Cast) and val.type == "f64":
            x = val.e
            if isinstance(x, Cast) and x.type == "f32" or \
                    isinstance(x, Unary) and x.op == "*" and isinstance(x.e, Cast) and x.e.type == "f32 *":
                return x
        raise Unsupported("an stfs of a double that may not be single (the 750CL's store conversion)")

    def prepare(self, body):
        """The translator's stack as the passes' stack pointer: it writes r1 as constants (entry_sp 0x817fe000 at
        entry, the return sentinel 0x817ff000 as the saved link register). The back-chain store (the entry r1 at the
        new frame's base) goes; every other stack address becomes r1 + offset, r1 set to entry_sp at the top."""
        from core.rtx.cast import Assign, Bin, Call, ExprS, Num
        from core.rtx.lift import filter_stmts, map_exprs, rewrite

        def stack(e):
            return flow.is_int(e) and self.stack[0] <= e.v < self.stack[1]
        filter_stmts(body.body, lambda s: not (isinstance(s, ExprS) and isinstance(s.e, Call) and s.e.fn == "wr32" and
                                               stack(s.e.args[0]) and flow.is_int(s.e.args[1]) and
                                               s.e.args[1].v == self.sp_init))

        def to_sp(e):
            if not stack(e):
                return e
            if e.v == self.sentinel:
                return Var(self.link)
            d = e.v - self.sp_init
            return Bin("+" if d >= 0 else "-", Var(self.sp_reg), Num(abs(d), f"0x{abs(d):x}u"))
        map_exprs(body.body, lambda e: rewrite(e, to_sp))
        body.body.insert(0, Assign(Var(self.sp_reg), "=", Num(self.sp_init, f"0x{self.sp_init:x}u")))

    def check(self, body):
        from core.rtx.lift import calls_used
        bad = calls_used(body.body) & self.refuse
        if bad:
            raise Unsupported(f"runtime state a readable file has no form for: {sorted(bad)}")


SPEC = Spec()
