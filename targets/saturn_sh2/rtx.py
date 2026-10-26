"""The SH-2 raw runtime's names, for the shared readable pass (core/rtx/lift). The SH-2 translator writes the SH-4
runtime's API (runtime/tc_rt.h: rd/wr, TcArgs and tc_call, r15/pr, sh4_r hand-over) without the FPU, so the pass is
the SH-4 one with the Saturn's stack, its code addresses and the SH-2 runtime's hardware calls refused."""
from core.rtx import flow
from core.rtx import source
from core.rtx.flow import Unsupported

SH4 = type(source.spec("dreamcast_sh4"))


class Spec(SH4):
    pure = {"u2f", "f2u", "sx8", "sx16"}
    helper_types = {"u2f": "f32", "f2u": "u32", "sx8": "u32", "sx16": "u32"}
    handover_get = {"sh4_r"}
    handover_set = {"sh4_set_r"}
    sp_init = 0x060FE000
    ram = (0x06000000, 0x06100000)  # high work RAM
    mirrors = {0x26: (0x06, "|", 0x20000000)}  # the cache-through area (target.json address_mirrors 26: 06)
    # status and global-base registers, memory-mapped hardware and the runtime's stops
    refuse = {"sh4_stc_sr", "sh4_ldc_sr", "sh4_stc_gbr", "sh4_ldc_gbr", "tc_hw_read32", "tc_hw_write32",
              "tc_hw_write16", "tc_halt", "tc_spin", "body"}

    def check(self, body):
        from core.rtx.cast import Case
        from core.rtx.lift import all_stmts, calls_used
        bad = calls_used(body.body) & self.refuse
        if bad:
            raise Unsupported(f"runtime state a readable file has no form for: {sorted(bad)}")
        for _, _, s in all_stmts(body.body):
            if isinstance(s, Case) and s.v is not None and flow.is_int(s.v) and 0x06000000 <= s.v.v < 0x06100000:
                raise Unsupported("a computed jump (a switch over code addresses)")


SPEC = Spec()
