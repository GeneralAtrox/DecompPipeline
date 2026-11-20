"""Supervisor and special-purpose-register instructions: mfspr / mtspr, the time base, MSR, the segment
registers, sc and the conditional traps (FunctionTranslator's mixin; the oracle's side is TcSystem.java)."""
from . import expr as X
from .expr import Var, u
from .ir import Assign, Comment
from .regs import CA, OV, R, SO

# MSR at entry (TcSystem.MSR_ENTRY, tc_rt.h TC_MSR_ENTRY): EE ME FP IR DR RI.
MSR_ENTRY = 0xB032


class SystemOps:
    def spr_var(self, spr):
        if spr == 8:
            return "lr"
        if spr == 9:
            return "ctr"
        if 912 <= spr <= 919:
            return f"gqr{spr - 912}"
        if spr in (268, 284):
            return "tbl"
        if spr in (269, 285):
            return "tbu"
        return f"spr{spr}"

    def op_mfspr(self, i):
        if i.spr == 1:
            e = X.bor(X.bor(X.bor(X.shl(X.cast("u32", SO()), 31), X.shl(X.cast("u32", OV()), 30)),
                            X.shl(X.cast("u32", CA()), 29)), Var("xbc", "u32"))
            return [Assign(f"r{i.rd}", e)]
        return [Assign(f"r{i.rd}", Var(self.spr_var(i.spr), "u32"))]

    op_mftb = op_mfspr

    def op_mtspr(self, i):
        rs = R(i.rd)
        if i.spr == 1:
            return [Assign("so", X.band(X.shr(rs, 31), X.ONE)), Assign("ov", X.band(X.shr(rs, 30), X.ONE)),
                    Assign("ca", X.band(X.shr(rs, 29), X.ONE)), Assign("xbc", X.band(rs, u(0x7F)))]
        v = self.spr_var(i.spr)
        if v.startswith("gqr"):
            self.gqr_written.add(int(v[3:]))
        return [Assign(v, rs)]

    def op_mfmsr(self, i):
        return [Assign(f"r{i.rd}", Var("msr", "u32"))]

    def op_mtmsr(self, i):
        return [Assign("msr", R(i.rs))]

    def op_mfsr(self, i):
        return [Assign(f"r{i.rd}", Var(f"sr{i.sr}", "u32"))]

    def op_mtsr(self, i):
        return [Assign(f"sr{i.sr}", R(i.rd))]

    def op_mfsrin(self, i):
        return [Assign(f"r{i.rd}", Var("srin", "u32"))]

    def op_mtsrin(self, i):
        return [Assign("srin", R(i.rd))]

    def op_sc(self, i):
        # The OS's handler at 0x80000c00: mfspr r9,HID0; ori r10,r9,8; mtspr HID0,r10; isync; sync; mtspr HID0,r9;
        # rfi. It leaves HID0 as it was; r9 and r10 are what a program sees (the oracle's TcSystem.sc).
        h = Var(self.spr_var(1008), "u32")
        return [Assign("r9", h), Assign("r10", X.bor(h, u(8)))]

    def op_tw(self, i):
        self.notes.append(f"conditional trap at {i.addr:08x}: not modelled")
        return [Comment("trap (not modelled)")]

    op_twi = op_tw
