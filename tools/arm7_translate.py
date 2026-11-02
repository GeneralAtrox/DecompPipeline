#!/usr/bin/env python3
"""Translates the executed code of an ARMv4 ARM-state program (the AICA sound driver) to C blocks for the AICA ARM7
runtime (port/runtime/arm7_native.h): one C function per basic block, doing exactly what the interpreter
(port/runtime/arm7.c) does for those instructions, with the interpreter's cycle and instruction counts. The
interpreter runs everything the table does not cover.

Input: a snapshot of the sound RAM and the recorder's executed-address file (env DP_ARM_PCS of a replay run, one byte
per RAM word). Output: one C file with the blocks, the words each was translated from, and the table
`arm7_driver_native` (a game lists it in game.json `sources`). The generated file holds the driver's code words, so it
belongs with the game that owns the disc, like its translated C, not in the kit.

    py -3.12 tools/arm7_translate.py --ram DRIVER.ram --pcs DRIVER.pcs --out aica_native.c

Nearest existing: targets/dreamcast_sh4 (the SH-4 to C translator) - another instruction set, and its output
reproduces the SH-4's behaviour, not the ARM7 interpreter's cycle accounting per block; no ARM front end exists
(searched targets/, tools/, core/, every branch and `git log --all -S`).
"""
import argparse
import sys
from pathlib import Path

CONDS = ["a->z", "!a->z", "a->c", "!a->c", "a->n", "!a->n", "a->v", "!a->v", "(a->c && !a->z)", "(!a->c || a->z)",
         "(a->n == a->v)", "(a->n != a->v)", "(!a->z && a->n == a->v)", "(a->z || a->n != a->v)", "1", "0"]
MAX_BLOCK = 48
LOGICAL = {0, 1, 8, 9, 12, 13, 14, 15}
NO_WRITE = {8, 9, 10, 11}


def bit(op, n):
    return (op >> n) & 1


def popcount(x):
    return bin(x).count("1")


class Insn:
    """One decoded instruction: how the translator treats it.

    kind: dp, ldst, branch (inline) or fb (the interpreter's own code, through arm7_step_op).
    ends: the instruction may write the PC or change the PSR, so the block ends after it.
    mem: it may reach the device bus (the block returns after it if it did).
    store: it may write sound RAM (the block returns after it if it wrote into its own words).
    worst: the most cycles it can cost beyond its fetch cycle."""

    def __init__(self, op, pc):
        self.op, self.pc = op, pc
        self.kind, self.ends, self.mem, self.store, self.worst = "fb", False, False, False, 0
        cls = (op >> 25) & 7
        rd = (op >> 12) & 15
        load = bit(op, 20)
        if cls in (0, 1):
            self.decode_dp_family(op, cls, rd, load)
        elif cls == 2 or (cls == 3 and not bit(op, 4)):
            self.decode_single(op, rd, load)
        elif cls == 4:
            self.mem, self.store = True, not load
            self.ends = bool(bit(op, 22)) or bool(load and bit(op, 15))
            self.worst = popcount(op & 0xffff) + 1 + (2 if load and bit(op, 15) else 0)
        elif cls == 5:
            self.kind, self.ends, self.worst = "branch", True, 2
        else:  # coprocessor, SWI, undefined
            self.ends = True

    def decode_dp_family(self, op, cls, rd, load):
        if cls == 0 and ((op & 0x0fc000f0) == 0x00000090 or (op & 0x0f8000f0) == 0x00800090):
            self.worst = 3  # multiply
        elif cls == 0 and (op & 0x0fb00ff0) == 0x01000090:  # swap
            self.mem, self.store, self.worst, self.ends = True, True, 3, rd == 15
        elif cls == 0 and (op & 0x0e000090) == 0x00000090:  # halfword transfers
            self.mem, self.store, self.worst = True, not load, 2
            self.ends = bool(load and rd == 15)
        elif cls == 0 and (op & 0x0ffffff0) == 0x012fff10:  # bx
            self.ends, self.worst = True, 2
        elif (op & 0x0d900000) == 0x01000000:  # PSR transfers (and the undefined MSR forms)
            self.ends = not (cls == 0 and not bit(op, 21))  # MRS only reads
        else:
            opcode, s = (op >> 21) & 15, bit(op, 20)
            writes = opcode not in NO_WRITE
            reg_shift = not bit(op, 25) and bit(op, 4)
            if reg_shift:
                self.worst = 1 + (2 if rd == 15 else 0)
                self.ends = rd == 15 and (writes or s)
            elif rd == 15 and (writes or s):
                self.ends, self.worst = True, 2
            else:
                self.kind = "dp"

    def decode_single(self, op, rd, load):
        self.mem, self.store = True, not load
        self.worst = 2 if load else 1
        self.ends = bool(load and rd == 15)
        if rd != 15 and (not bit(op, 25) or (op & 0xff0) == 0):
            self.kind = "ldst"


def r(n, pc):
    return "0x%xu" % (pc + 8) if n == 15 else "a->r[%d]" % n


def shifted(op, pc, need_carry):
    """C statements computing `opd` (and `cr`, the shifter's carry out) for a data-processing operand without a
    register-specified shift."""
    if bit(op, 25):
        rot = ((op >> 8) & 15) * 2
        imm = op & 0xff
        val = (imm >> rot | imm << (32 - rot)) & 0xffffffff if rot else imm
        out = ["u32 opd = 0x%xu;" % val]
        if need_carry:
            out.append("u32 cr = %s;" % ("%du" % (val >> 31) if rot else "a->c"))
        return out
    rm, typ, n = op & 15, (op >> 5) & 3, (op >> 7) & 31
    out = ["u32 m = %s; (void)m;" % r(rm, pc)]
    if typ == 0:
        if n == 0:
            out += ["u32 opd = m;"] + (["u32 cr = a->c;"] if need_carry else [])
        else:
            out += ["u32 opd = m << %d;" % n] + (["u32 cr = (m >> %d) & 1u;" % (32 - n)] if need_carry else [])
    elif typ == 1:
        if n == 0:
            out += ["u32 opd = 0;"] + (["u32 cr = m >> 31;"] if need_carry else [])
        else:
            out += ["u32 opd = m >> %d;" % n] + (["u32 cr = (m >> %d) & 1u;" % (n - 1)] if need_carry else [])
    elif typ == 2:
        if n == 0:
            out += ["u32 opd = (m >> 31) ? 0xffffffffu : 0u;"] + (["u32 cr = m >> 31;"] if need_carry else [])
        else:
            out += ["u32 opd = (u32)((s32)m >> %d);" % n] + (["u32 cr = (m >> %d) & 1u;" % (n - 1)] if need_carry else [])
    else:
        if n == 0:  # RRX
            out += ["u32 opd = a->c << 31 | m >> 1;"] + (["u32 cr = m & 1u;"] if need_carry else [])
        else:
            out += ["u32 opd = m >> %d | m << %d;" % (n, 32 - n)] + (["u32 cr = (m >> %d) & 1u;" % (n - 1)] if need_carry else [])
    return out


def gen_dp(i):
    op, pc = i.op, i.pc
    opcode, s, rd, rn = (op >> 21) & 15, bit(op, 20), (op >> 12) & 15, (op >> 16) & 15
    logical = opcode in LOGICAL
    need_carry = bool(s and logical)
    out = shifted(op, pc, need_carry)
    uses_rv = opcode not in (13, 15)
    if uses_rv:
        out.append("u32 rv = %s;" % r(rn, pc))
    expr = {
        0: "rv & opd", 1: "rv ^ opd", 8: "rv & opd", 9: "rv ^ opd", 12: "rv | opd", 13: "opd", 14: "rv & ~opd",
        15: "~opd",
        2: "n_add(a, rv, ~opd, 1, %d)" % s, 3: "n_add(a, opd, ~rv, 1, %d)" % s, 4: "n_add(a, rv, opd, 0, %d)" % s,
        5: "n_add(a, rv, opd, a->c, %d)" % s, 6: "n_add(a, rv, ~opd, a->c, %d)" % s, 7: "n_add(a, opd, ~rv, a->c, %d)" % s,
        10: "n_add(a, rv, ~opd, 1, 1)", 11: "n_add(a, rv, opd, 0, 1)",
    }[opcode]
    out.append("u32 res = %s;" % expr)
    if s:
        out.append("n_nz(a, res);")
        if logical:
            out.append("a->c = cr;")
    if opcode not in NO_WRITE:
        out.append("a->r[%d] = res;" % rd)
    return out


SUCC = {}  # address of a block -> its index + 1, set by generate() before the blocks are written


def exit_stmt(pending, next_expr, pc_last, target=None):
    """Leave the block after an instruction: the accounting handed over, the next address set, the next block's
    index returned when the address is a constant that starts a block."""
    return ("a->cycles -= %d + cy; a->executed += %d; a->next = %s; a->r[15] = 0x%xu; return %d;"
            % (pending, pending, next_expr, pc_last + 8, SUCC.get(target, 0) if target is not None else 0))


class Block:
    def __init__(self, addr, insns):
        self.addr, self.insns = addr, insns
        self.words = [i.op for i in insns]
        self.pre_max = sum(1 + i.worst for i in insns[:-1])


def gen_ldst(i, k, done, blk_lo, blk_len, last):
    """The C for an inline LDR/STR/LDRB/STRB; both the sound-RAM path and the device-bus path."""
    op, pc = i.op, i.pc
    rn, rd, load = (op >> 16) & 15, (op >> 12) & 15, bit(op, 20)
    pre, up, byte, wb = bit(op, 24), bit(op, 23), bit(op, 22), (not bit(op, 24)) or bit(op, 21)
    wb = wb and rn != 15
    off = "0x%xu" % (op & 0xfff) if not bit(op, 25) else r(op & 15, pc)
    pend = k + 1 - done
    out = ["u32 base = %s, moved = base %s %s;" % (r(rn, pc), "+" if up else "-", off)]
    out.append("u32 addr = %s;" % ("moved" if pre else "base"))
    wbs = "a->r[%d] = moved;" % rn if wb else ""
    flush = "a->cycles -= %d + cy; a->executed += %d; a->next = 0x%xu; a->r[15] = 0x%xu;" % (pend, pend, pc + 4, pc + 8)
    if load:
        rd_ = "n_rd8(a, addr)" if byte else "n_rd32(a, addr)"
        slow_rd = "arm7_read(a, addr, 1)" if byte else "arm7_read(a, addr & ~3u, 4)"
        rot = "" if byte else "if (addr & 3u) v = v >> ((addr & 3u) * 8u) | v << (32 - (addr & 3u) * 8u);"
        out.append("if (addr < a->ram_size) {")
        out.append("    u32 v = %s; %s a->r[%d] = v; cy += 2;" % (rd_, wbs, rd))
        out.append("} else {")
        out.append("    %s" % flush)
        out.append("    { u32 v = %s; %s %s a->r[%d] = v; a->cycles -= 2; }" % (slow_rd, rot, wbs, rd))
        out.append("    return 0;")
        out.append("}")
        # the word path of the fast branch has an aligned-address check of its own inside n_rd32
    else:
        val = r(rd, pc)
        wr_fast = "n_wr8(a, addr, %s);" % val if byte else "n_wr32(a, addr & ~3u, %s);" % val
        wr_slow = "arm7_write(a, addr, %s & 0xffu, 1);" % val if byte else "arm7_write(a, addr & ~3u, %s, 4);" % val
        out.append("if (addr < a->ram_size) {")
        out.append("    %s %s cy += 1;" % (wr_fast, wbs))
        if not last:
            out.append("    if ((%s - 0x%xu) < %du) { %s }" % ("addr" if byte else "(addr & ~3u)", blk_lo, blk_len,
                                                               exit_stmt(pend, "0x%xu" % (pc + 4), 0 + pc, None)))
        out.append("} else {")
        out.append("    %s" % flush)
        out.append("    %s %s a->cycles -= 1; return 0;" % (wr_slow, wbs))
        out.append("}")
    return out


def gen_block(name, blk):
    lo, n = blk.addr, len(blk.insns)
    lines = ["static u32 %s(Arm7 *a)" % name, "{", "    s32 cy = 0;"]
    done = 0
    for k, i in enumerate(blk.insns):
        last = k == n - 1
        cond = (i.op >> 28) & 15
        if cond == 15 and i.kind != "fb":
            lines.append("    /* %08x: never */" % i.pc)
            if last:
                lines.append("    " + exit_stmt(k + 1 - done, "0x%xu" % (i.pc + 4), i.pc, i.pc + 4))
            continue
        lines.append("    /* %08x: %08x */" % (i.pc, i.op))
        body = []
        if i.kind == "dp":
            body = gen_dp(i)
        elif i.kind == "ldst":
            body = gen_ldst(i, k, done, lo, 4 * n, last)
        elif i.kind == "branch":
            off = ((i.op & 0xffffff) << 8)
            off = (off - (1 << 32) if off & 0x80000000 else off) >> 6
            target = (i.pc + 8 + off) & 0xffffffff
            if bit(i.op, 24):
                body.append("a->r[14] = 0x%xu;" % (i.pc + 4))
            body.append("cy += 2;")
            if off < 0:
                body.append("if (a->fast) arm7_scan_probe(a, 0x%xu);" % target)
            body.append(exit_stmt(k + 1 - done, "0x%xu" % target, i.pc, target))
        if i.kind != "fb":
            if cond == 14:
                lines += ["    {"] + ["        " + b for b in body] + ["    }"]
            else:
                lines += ["    if (%s) {" % CONDS[cond]] + ["        " + b for b in body] + ["    }"]
            if i.kind == "branch" and cond != 14:
                lines.append("    " + exit_stmt(k + 1 - done, "0x%xu" % (i.pc + 4), i.pc, i.pc + 4))
            elif last and i.kind != "branch":
                lines.append("    " + exit_stmt(k + 1 - done, "0x%xu" % (i.pc + 4), i.pc, i.pc + 4))
            continue
        # the interpreter's own code for this instruction, after the accounting so far is handed over
        pend = k - done
        lines.append("    a->cycles -= %d + cy; a->executed += %d; cy = 0;" % (pend, pend))
        call = "arm7_step_op(a, 0x%08xu, 0x%xu);" % (i.op, i.pc)
        if i.mem or i.store:
            lines.append("    { u64 b0 = a->bus; %s if (a->bus != b0) return 0; }" % call)
        else:
            lines.append("    " + call)
        done = k + 1
        if i.ends:
            lines.append("    return 0;")
            break
        if i.store and not last:
            lines.append("    if (n_dirty(a, 0x%xu, %d, blk_words_%08x)) return 0;" % (lo, n, lo))
    last_insn = blk.insns[-1]
    if last_insn.kind == "fb" and not last_insn.ends:  # the block ran into whatever follows it
        lines.append("    return %d;" % SUCC.get(last_insn.pc + 4, 0))
    lines.append("}")
    return lines


def find_blocks(ram, executed):
    insns = {}
    for pc in executed:
        op = int.from_bytes(ram[pc:pc + 4], "little")
        insns[pc] = Insn(op, pc)
    leaders = set()
    for pc, i in insns.items():
        if pc - 4 not in insns:
            leaders.add(pc)
        if i.ends or i.kind == "branch":
            if pc + 4 in insns:
                leaders.add(pc + 4)
        if i.kind == "branch":
            off = ((i.op & 0xffffff) << 8)
            off = (off - (1 << 32) if off & 0x80000000 else off) >> 6
            t = (pc + 8 + off) & 0xffffffff
            if t in insns:
                leaders.add(t)
    blocks = []
    for l in sorted(leaders):
        run = []
        pc = l
        while pc in insns and len(run) < MAX_BLOCK:
            if run and pc in leaders:
                break
            run.append(insns[pc])
            if insns[pc].ends or insns[pc].kind == "branch":
                break
            pc += 4
        blocks.append(Block(l, run))
    return blocks


def generate(ram, pcs, name="arm7_driver_native"):
    executed = [4 * i for i, b in enumerate(pcs) if b and 4 * i + 4 <= len(ram)]
    blocks = find_blocks(ram, executed)
    SUCC.clear()
    SUCC.update({b.addr: idx + 1 for idx, b in enumerate(blocks)})
    out = ['/* Generated by tools/arm7_translate.py from the sound driver\'s executed code: %d blocks, %d instructions. */' % (len(blocks), sum(len(b.insns) for b in blocks)),
           '#include "arm7.h"', '']
    out.append("static inline int n_dirty(Arm7 *a, u32 addr, u32 n, const u32 *words)")
    out.append("{")
    out.append("    const u8 *p = a->ram + (addr & a->ram_mask);")
    out.append("    for (u32 k = 0; k < n; k++, p += 4)")
    out.append("        if (((u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24) != words[k]) return 1;")
    out.append("    return 0;")
    out.append("}")
    out.append("")
    for b in blocks:
        out.append("static const u32 blk_words_%08x[%d] = {%s};" % (b.addr, len(b.words), ", ".join("0x%08xu" % w for w in b.words)))
    out.append("")
    for b in blocks:
        out += gen_block("blk_%08x" % b.addr, b)
        out.append("")
    out.append("static const Arm7Block blocks[%d] = {" % len(blocks))
    for b in blocks:
        out.append("    {0x%xu, %d, blk_words_%08x, %d, blk_%08x}," % (b.addr, len(b.words), b.addr, b.pre_max, b.addr))
    out.append("};")
    first, last = blocks[0].addr >> 2, blocks[-1].addr >> 2
    lookup = [0] * (last - first + 1)
    for idx, b in enumerate(blocks):
        lookup[(b.addr >> 2) - first] = idx + 1
    out.append("static const u16 lookup[%d] = {" % len(lookup))
    for k in range(0, len(lookup), 16):
        out.append("    " + ", ".join(str(v) for v in lookup[k:k + 16]) + ",")
    out.append("};")
    out.append("const Arm7Native %s = {%du, %du, lookup, blocks};" % (name, first, len(lookup)))
    return "\n".join(out) + "\n", len(blocks)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ram", required=True, help="a snapshot of the sound RAM")
    ap.add_argument("--pcs", required=True, help="the recorder's executed-address file (one byte per RAM word)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    ram = Path(args.ram).read_bytes()
    pcs = Path(args.pcs).read_bytes()
    text, n = generate(ram, pcs)
    Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    print(f"{n} blocks, {len(text)} bytes of C -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())
