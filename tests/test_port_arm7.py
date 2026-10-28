"""The kit's AICA ARM7 (port/runtime/arm7.c, arm7_ls.c) and the chip around it (aica_arm.c, aica_chan.c): single
instructions against results worked out from the ARM Architecture Reference Manual (ARMv4, ARM state), a small
program with timer A's interrupt on the FIQ, and a voice the program keys on through the registers. The instructions
are encoded here from the manual's formats (enc_* below). The C part only where Visual Studio's 32-bit tools are
found (as tests/test_port_kit.py).
Run: python -W error tests/test_port_arm7.py
"""
import pathlib
import struct
import subprocess
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402
from test_port_audio import work_dir  # noqa: E402

AL = 0xE
OPS = {"and": 0, "eor": 1, "sub": 2, "rsb": 3, "add": 4, "adc": 5, "sbc": 6, "rsc": 7, "tst": 8, "teq": 9, "cmp": 10,
       "cmn": 11, "orr": 12, "mov": 13, "bic": 14, "mvn": 15}
SHIFTS = {"lsl": 0, "lsr": 1, "asr": 2, "ror": 3}


def enc_dp(op, rd, rn, *, imm=None, rot=0, rm=0, sh="lsl", amount=0, rs=None, s=False, cond=AL):
    """Data processing: cond 00 I opcode S Rn Rd operand2 (an 8-bit immediate rotated right by 2 * rot, or Rm
    shifted by an immediate amount or by Rs)."""
    w = cond << 28 | OPS[op] << 21 | int(s or OPS[op] in (8, 9, 10, 11)) << 20 | rn << 16 | rd << 12
    if imm is not None:
        return w | 1 << 25 | rot << 8 | imm
    if rs is not None:
        return w | rs << 8 | SHIFTS[sh] << 5 | 1 << 4 | rm
    return w | amount << 7 | SHIFTS[sh] << 5 | rm


def enc_ldst(load, rd, rn, off=0, *, byte=False, pre=True, up=True, wb=False, rm=None, cond=AL):
    """Single data transfer: cond 01 I P U B W L Rn Rd offset (a 12-bit immediate, or Rm unshifted)."""
    w = cond << 28 | 1 << 26 | pre << 24 | up << 23 | byte << 22 | wb << 21 | load << 20 | rn << 16 | rd << 12
    return w | (1 << 25 | rm if rm is not None else off)


def enc_half(load, rd, rn, off, sh, *, pre=True, up=True, wb=False):
    """Halfword transfer: cond 000 P U 1 W L Rn Rd offset-high 1 S H 1 offset-low (S H: 01 H, 10 SB, 11 SH)."""
    return (AL << 28 | pre << 24 | up << 23 | 1 << 22 | wb << 21 | load << 20 | rn << 16 | rd << 12
            | (off >> 4) << 8 | 1 << 7 | sh << 5 | 1 << 4 | (off & 15))


def enc_block(load, rn, regs, *, pre=False, up=True, wb=False, s=False):
    """Block transfer: cond 100 P U S W L Rn register-list."""
    return (AL << 28 | 4 << 25 | pre << 24 | up << 23 | s << 22 | wb << 21 | load << 20 | rn << 16
            | sum(1 << r for r in regs))


def enc_mul(rd, rm, rs, *, acc=None, s=False):
    """MUL, MLA: cond 000000 A S Rd Rn Rs 1001 Rm."""
    return (AL << 28 | (acc is not None) << 21 | s << 20 | rd << 16 | (acc or 0) << 12 | rs << 8 | 9 << 4 | rm)


def enc_mull(lo, hi, rm, rs, *, signed=False, acc=False):
    """UMULL, UMLAL, SMULL, SMLAL: cond 00001 U A S RdHi RdLo Rs 1001 Rm."""
    return AL << 28 | 1 << 23 | signed << 22 | acc << 21 | hi << 16 | lo << 12 | rs << 8 | 9 << 4 | rm


def enc_b(at, target, link=False, cond=AL):
    """B, BL: cond 101 L offset, the offset in words from the instruction's address + 8."""
    return cond << 28 | 5 << 25 | link << 24 | ((target - at - 8) >> 2) & 0xffffff


def enc_mrs(rd, spsr=False):
    return AL << 28 | 0x010F0000 | spsr << 22 | rd << 12


def enc_msr(rm, fields=9, spsr=False):
    """MSR register form; fields: bit 0 control, bit 3 flags."""
    return AL << 28 | 0x0120F000 | spsr << 22 | fields << 16 | rm


def enc_swp(rd, rm, rn, byte=False):
    return AL << 28 | 1 << 24 | byte << 22 | rn << 16 | rd << 12 | 9 << 4 | rm


def enc_swi(n=0):
    return AL << 28 | 0xF << 24 | n


HALT = 0xEAFFFFFE  # b . : the harness stops on it

ARM_MAIN_C = r"""#include <stdio.h>
#include <stdlib.h>
#include "arm7.h"

static u8 g_ram[0x200000];
void port_log(const char *fmt, ...) { (void)fmt; }
static u32 dev_read(Arm7 *a, u32 addr, u32 size) { (void)a; (void)size; return addr ^ 0x5a5a0000u; }
static void dev_write(Arm7 *a, u32 addr, u32 value, u32 size) { (void)a; printf("W %08x %08x %u\n", addr, value, size); }

int main(int argc, char **argv)
{
    Arm7 a;
    FILE *f = fopen(argv[1], "rb");
    size_t n = fread(g_ram, 1, sizeof g_ram, f);
    fclose(f);
    (void)argc, (void)n;
    a.ram = g_ram;
    a.ram_mask = sizeof g_ram - 1u;
    a.ram_size = 0x800000u;
    a.rd = dev_read;
    a.wr = dev_write;
    a.fiq = 0;
    arm7_reset(&a);
    for (int i = 0; i < 10000; i++) {
        u32 pc = a.next;
        if ((u32)(g_ram[pc] | g_ram[pc + 1] << 8 | g_ram[pc + 2] << 16 | (u32)g_ram[pc + 3] << 24) == 0xeafffffeu) break;
        arm7_run(&a, 1);
    }
    for (int i = 0; i < 15; i++) printf("%08x ", a.r[i]);
    printf("%08x %08x\n", a.next, arm7_cpsr(&a));
    return 0;
}
"""

# timer A every 4 samples (prescale 2 -> 1 << 2), count from 0xfe: an overflow every 8 samples; the FIQ handler counts
# them in RAM 0x100, takes the interrupt (M bit 0) and clears timer A's pending bit (SCIRE). Voice 3 keys on a
# 16-bit constant sample at 0x1000, looped, DISDL 15, DIPAN 0, TL 0, AR 31 (instant).
CHIP_MAIN_C = r"""#include <stdio.h>
#include <stdlib.h>
#include "game.h"
#include "aica_arm.h"

static u8 g_sound[AICA_RAM_SIZE];
void *host_map_fixed(u32 addr, u32 size) { (void)addr, (void)size; return g_sound; }
void port_log(const char *fmt, ...) { (void)fmt; }
void cdda_read(s16 *stereo, u32 frames) { for (u32 i = 0; i < 2u * frames; i++) stereo[i] = 0; }
void audio_start(void) {}
static int g_waits;
void port_wait_vblank(void) { g_waits++; }
u32 port_vblank_phase(int look) { (void)look; return 0; }

int main(int argc, char **argv)
{
    static s16 out[2 * 800];
    FILE *f = fopen(argv[1], "rb");
    size_t n = fread(g_sound, 1, sizeof g_sound, f);
    fclose(f);
    (void)argc, (void)n;
    for (u32 i = 0; i < 0x400u; i += 2) {
        g_sound[0x1000u + i] = 0x00;
        g_sound[0x1001u + i] = 0x40;
    }
    aica_arm_sh4_write(AICA_ARM_RESET, 1);
    aica_arm_sh4_write(AICA_ARM_RESET, 0);
    for (int i = 0; i < 4; i++) aica_arm_poll(); /* 64 samples ahead of the mix */
    u32 count_ahead = g_sound[0x100];
    aica_mix(out, 800);
    printf("%u %u %u %d %d %d %u %u %d\n", count_ahead, g_sound[0x100], (u32)aica_voice_playing(3), out[2 * 700],
           out[2 * 700 + 1], g_waits, aica_arm_sh4_read(0x2810) >> 13 & 3u, aica_arm_sh4_read(0x2890) & 0xffu,
           out[0]);
    return 0;
}
"""


def program(words, at=0):
    """Little-endian words at address `at` of an otherwise zero image."""
    img = bytearray(at + 4 * len(words))
    for i, w in enumerate(words):
        img[at + 4 * i:at + 4 * i + 4] = struct.pack("<I", w & 0xffffffff)
    return bytes(img)


def build(d, main_c, sources, aica_arm=False):
    cfg = {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
           "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": [], "aica_arm": aica_arm}
    (d / "port_config.h").write_text(game_config.port_config_h(cfg), encoding="utf-8")
    (d / "game.h").write_text('#ifndef GAME_H\n#define GAME_H\n#include "rd_rt.h"\n#endif\n', encoding="utf-8")
    (d / "main.c").write_text(main_c, encoding="utf-8")
    rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
    srcs = " ".join(f'"{KIT / "runtime" / s}"' for s in sources) + f' "{d / "main.c"}"'
    cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
          f'/I"{rd}" {srcs} /Fe:t.exe')
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                       errors="replace", timeout=300)
    return r, d / "t.exe"


class Arm7Core(unittest.TestCase):
    exe = None

    @classmethod
    def setUpClass(cls):
        if HAVE_VC:
            cls.dir = work_dir()
            r, cls.exe = build(cls.dir, ARM_MAIN_C, ["arm7.c", "arm7_ls.c"])
            assert r.returncode == 0, r.stdout + r.stderr

    def run_words(self, words, data=()):
        img = bytearray(program(list(words) + [HALT]))
        for addr, w in data:
            img[len(img):addr + 4] = bytes(max(0, addr + 4 - len(img)))
            img[addr:addr + 4] = struct.pack("<I", w)
        path = self.dir / "prog.bin"
        path.write_bytes(bytes(img))
        out = subprocess.run([str(self.exe), str(path)], capture_output=True, text=True, timeout=60).stdout.split("\n")
        regs = [int(x, 16) for x in out[-2].split()]
        return regs[:15], regs[15], regs[16], [line for line in out if line.startswith("W ")]

    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_data_processing_and_flags(self):
        """MOV with a rotated immediate; ADDS to 2^32 (Z and C set, V clear); SUBS 0 - 1 (N set, C clear: a
        borrow); 0x7fffffff + 1 overflows (N and V); ADC adds the carry; RSB; MVN; BIC; ORR; EOR; CMP sets the
        flags only. Results from the manual's definitions."""
        r, pc, cpsr, _ = self.run_words([
            enc_dp("mov", 0, 0, imm=0xff, rot=4),            # r0 = 0xff ror 8 = 0xff000000
            enc_dp("mvn", 1, 0, imm=0),                      # r1 = 0xffffffff
            enc_dp("add", 2, 1, imm=1, s=True),              # r2 = 0, Z C
            enc_dp("adc", 3, 2, imm=5),                      # r3 = 0 + 5 + C = 6
            enc_dp("mov", 4, 0, imm=0x7f, rot=4),            # r4 = 0x7f000000
            enc_dp("orr", 4, 4, imm=0xff, rot=8),            # r4 |= 0x00ff0000
            enc_dp("orr", 4, 4, imm=0xff, rot=12),           # r4 |= 0x0000ff00
            enc_dp("orr", 4, 4, imm=0xff),                   # r4 = 0x7fffffff
            enc_dp("add", 5, 4, imm=1, s=True),              # r5 = 0x80000000, N V, C clear
            enc_dp("rsb", 6, 3, imm=10),                     # r6 = 10 - 6 = 4
            enc_dp("bic", 7, 1, imm=0xf0),                   # r7 = 0xffffff0f
            enc_dp("eor", 8, 7, rm=1),                       # r8 = 0x000000f0
            enc_dp("cmp", 0, 6, imm=4),                      # 4 - 4: Z C, the result dropped
        ])
        self.assertEqual(r[:9], [0xff000000, 0xffffffff, 0, 6, 0x7fffffff, 0x80000000, 4, 0xffffff0f, 0xf0])
        self.assertEqual(cpsr >> 28, 0b0110)  # N 0 Z 1 C 1 V 0
        self.assertEqual(cpsr & 0x1f, 0x13)   # still SVC, the reset's mode
        r, pc, cpsr, _ = self.run_words([enc_dp("mov", 0, 0, imm=0), enc_dp("sub", 1, 0, imm=1, s=True)])
        self.assertEqual((r[1], cpsr >> 28), (0xffffffff, 0b1000))  # N, C clear (a borrow)

    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_shifter(self):
        """LSL/LSR/ASR/ROR by immediates, LSR #32 and ASR #32 (encoded as 0), RRX (ROR #0), and register shifts of
        0, 32 and more (only the low byte of Rs counts); the carry out of MOVS is the last bit shifted out."""
        r, _, cpsr, _ = self.run_words([
            enc_dp("mov", 0, 0, imm=0x81, rot=1),            # r0 = 0x81 ror 2 = 0x40000020
            enc_dp("mov", 1, 0, rm=0, sh="lsl", amount=1),   # 0x80000040
            enc_dp("mov", 2, 0, rm=1, sh="asr", amount=4),   # 0xf8000004
            enc_dp("mov", 3, 0, rm=1, sh="lsr", amount=0),   # LSR #32: 0
            enc_dp("mov", 4, 0, rm=1, sh="asr", amount=0),   # ASR #32: 0xffffffff
            enc_dp("mov", 5, 0, rm=0, sh="ror", amount=8),   # 0x20400000
            enc_dp("mov", 6, 0, imm=33),                     # r6 = 33
            enc_dp("mov", 7, 0, rm=1, sh="lsl", rs=6),       # LSL by 33: 0
            enc_dp("mov", 8, 0, imm=1),
            enc_dp("orr", 8, 8, imm=1, rot=12),              # r8 = 0x100 + 1 = 0x101: the low byte, 1
            enc_dp("mov", 9, 0, rm=1, sh="lsr", rs=8),       # 0x80000040 >> 1 = 0x40000020
            enc_dp("mov", 10, 0, rm=1, sh="lsl", amount=1, s=True),  # 0x80, carry out bit 31 = 1
            enc_dp("mov", 11, 0, rm=0, sh="ror", amount=0, s=True),  # RRX of 0x40000020 with C 1: 0xa0000010, C 0
        ])
        self.assertEqual(r[:12], [0x40000020, 0x80000040, 0xf8000004, 0, 0xffffffff, 0x20400000, 33, 0, 0x101,
                                  0x40000020, 0x80, 0xa0000010])
        self.assertEqual(cpsr >> 28, 0b1000)  # N from 0xa0000010, C 0 (bit 0 of 0x40000020)

    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_loads_and_stores(self):
        """Pre- and post-indexed words and bytes with writeback; a word load from an address 1 past a word reads
        it rotated right by 8 (the ARM7's rule); halfwords and signed loads; a store of the PC stores its address
        + 12; a store above the sound RAM's window goes to the bus."""
        data = [(0x400, 0x11223344), (0x404, 0x8899aabb)]
        r, _, _, writes = self.run_words([
            enc_dp("mov", 0, 0, imm=1, rot=11),              # r0 = 0x400
            enc_ldst(1, 1, 0, 4),                            # r1 = [0x404] = 0x8899aabb
            enc_ldst(1, 2, 0, 1),                            # r2 = [0x400] ror 8 = 0x44112233
            enc_ldst(1, 3, 0, 5, byte=True),                 # r3 = byte at 0x405 = 0xaa
            enc_dp("mov", 4, 0, rm=0),
            enc_ldst(1, 5, 4, 4, pre=False),                 # post-indexed: r5 = [0x400], r4 = 0x404
            enc_half(1, 6, 0, 6, 1),                         # LDRH 0x406: 0x8899
            enc_half(1, 7, 0, 7, 2),                         # LDRSB 0x407: 0xffffff88
            enc_half(1, 8, 0, 4, 3),                         # LDRSH 0x404: 0xffffaabb
            enc_ldst(0, 15, 0, 8),                           # [0x408] = the PC: this instruction (0x24) + 12
            enc_ldst(1, 9, 0, 8),
            enc_dp("mov", 10, 0, imm=2, rot=5),              # r10 = 0x800000: the bus
            enc_ldst(0, 1, 10, 0x10, byte=True),             # a byte write there
            enc_ldst(1, 11, 10, 0x20),                       # a bus read: 0x800020 ^ 0x5a5a0000
            enc_ldst(0, 0, 0, 8, wb=True),                   # [0x408] = 0x400, r0 = 0x408
        ], data)
        self.assertEqual(r[:12], [0x408, 0x8899aabb, 0x44112233, 0xaa, 0x404, 0x11223344, 0x8899, 0xffffff88,
                                  0xffffaabb, 0x30, 0x800000, 0x5ada0020])
        self.assertEqual(writes, ["W 00800010 000000bb 1"])

    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_blocks_multiply_swap(self):
        """STMDB with writeback then LDMIA: the lowest register at the lowest address; MUL, MLA, UMULL, SMLAL;
        SWP exchanges a word with memory."""
        r, _, _, _ = self.run_words([
            enc_dp("mov", 13, 0, imm=2, rot=11),             # sp = 0x800
            enc_dp("mov", 1, 0, imm=7),
            enc_dp("mov", 2, 0, imm=9),
            enc_dp("mvn", 3, 0, imm=0),                      # r3 = -1
            enc_block(0, 13, [1, 2, 3], pre=True, up=False, wb=True),  # [0x7f4..0x7fc] = 7, 9, -1; sp = 0x7f4
            enc_block(1, 13, [4, 5, 6]),                     # r4..r6 = 7, 9, -1
            enc_mul(7, 4, 5),                                # 63
            enc_mul(8, 4, 5, acc=1),                         # 63 + 7 = 70
            enc_mull(9, 10, 3, 3),                           # 0xffffffff^2 = 0xfffffffe_00000001
            enc_dp("mov", 11, 0, imm=0),
            enc_dp("mov", 12, 0, imm=5),
            enc_mull(11, 12, 3, 4, signed=True, acc=True),   # 5 << 32 + (-1 * 7) = 0x4_fffffff9
            enc_swp(0, 2, 13),                               # r0 = [0x7f4] = 7, [0x7f4] = 9
            enc_ldst(1, 14, 13, 0),                          # r14 = 9
        ])
        self.assertEqual(r[:15], [7, 7, 9, 0xffffffff, 7, 9, 0xffffffff, 63, 70, 1, 0xfffffffe, 0xfffffff9, 4,
                                  0x7f4, 9])

    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_branches_conditions_modes(self):
        """BL links the return address and MOV pc, lr returns; a condition that fails skips. MSR into user mode
        banks r13 (SVC's is kept); SWI enters SVC with the user CPSR in the SPSR, and MOVS pc, lr returns to user
        mode."""
        r, _, _, _ = self.run_words([
            enc_b(0x00, 0x10, link=True),                    # 0x00: bl 0x10
            enc_dp("mov", 5, 0, imm=0x55),                   # 0x04: after the return
            enc_b(0x08, 0x20),                               # 0x08: b 0x20
            0,
            enc_dp("mov", 0, 0, imm=1, s=True),              # 0x10: Z clear
            enc_dp("mov", 1, 0, imm=2, cond=0),              # moveq: skipped
            enc_dp("mov", 15, 0, rm=14),                     # 0x18: mov pc, lr
            0,
            enc_dp("mov", 7, 0, imm=0x99),                   # 0x20
        ])
        self.assertEqual((r[0], r[1], r[5], r[7], r[14]), (1, 0, 0x55, 0x99, 4))
        r, _, cpsr, _ = self.run_words([
            enc_b(0x00, 0x20), 0, enc_b(0x08, 0x40), 0, 0, 0, 0, 0,   # reset; the SWI vector (0x08)
            enc_dp("mov", 13, 0, imm=0x77),                  # 0x20: SVC's sp
            enc_dp("mov", 2, 0, imm=0x10),
            enc_msr(2, fields=1),                            # cpsr_c = user mode
            enc_dp("mov", 13, 0, imm=0x66),                  # the user's sp
            enc_swi(7),                                      # 0x30
            enc_mrs(3),                                      # 0x34: back in user mode
            HALT, 0,
            enc_mrs(4, spsr=True),                           # 0x40: the handler, in SVC
            enc_dp("mov", 6, 0, rm=13),                      # SVC's sp
            enc_dp("mov", 15, 0, rm=14, s=True),             # movs pc, lr
        ])
        self.assertEqual((r[3] & 0x1f, r[4] & 0x1f, r[6], r[13], r[14], cpsr & 0x1f), (0x10, 0x10, 0x77, 0x66, 0, 0x10))


class AicaChip(unittest.TestCase):
    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_timer_fiq_and_key_on(self):
        """A program on the chip: timer A (prescale 2: a count every 4 samples) is set to 0xfe, so it overflows 8
        samples on; the FIQ handler counts the overflows in RAM 0x100, sets timer A back to 0xfe, clears its pending
        bit (SCIRE) and takes the interrupt (M). Overflows come at samples 8, 16, ..., each counted in the next
        sample. The SH-4's four polls run the chip 64 samples ahead of the mix (7 counted); the mix's 800 samples
        run the rest (99 counted by sample 800, where the count has just overflowed again). Voice 3, keyed on
        through KYONEX before the mix, plays its constant 16384 from the mix's first frame at full level on the left
        only (DISDL 15, DIPAN 0x1f: the right side 15 steps down, off). The monitor (MSLC 0) shows voice 0, never
        keyed on: its envelope in the release state (3)."""
        R = 12  # r12: 0x800000, the registers
        vectors = [enc_b(0x00, 0x40)] + [HALT] * 6 + [enc_b(0x1c, 0x104)]  # reset 0x40, FIQ 0x104
        code = [  # 0x40
            enc_dp("mov", R, 0, imm=2, rot=5),                    # 0x800000
            enc_dp("add", 0, R, imm=0x28, rot=12),                # r0 = 0x802800
            enc_dp("mov", 1, 0, imm=0xfe), enc_dp("orr", 1, 1, imm=2, rot=12),  # prescale 2, count 0xfe
            enc_ldst(0, 1, 0, 0x90),                              # TACTL, TIMA
            enc_dp("mov", 1, 0, imm=0x40),                        # timer A's bit
            enc_ldst(0, 1, 0, 0x9c),                              # SCIEB
            enc_ldst(0, 1, 0, 0xa8),                              # SCILV0: level 1
            enc_dp("add", 2, R, imm=0x18, rot=14),                # voice 3's registers, 0x800180
            enc_dp("mov", 1, 0, imm=2, rot=12), enc_ldst(0, 1, 2, 0x00),        # LPCTL, PCM16, SA high 0
            enc_dp("mov", 1, 0, imm=1, rot=10), enc_ldst(0, 1, 2, 0x04),        # SA 0x1000
            enc_dp("mov", 1, 0, imm=0), enc_ldst(0, 1, 2, 0x08),               # LSA 0
            enc_dp("mov", 1, 0, imm=1, rot=12), enc_ldst(0, 1, 2, 0x0c),       # LEA 0x100
            enc_dp("mov", 1, 0, imm=31), enc_ldst(0, 1, 2, 0x10),              # AR 31
            enc_dp("mov", 1, 0, imm=31), enc_ldst(0, 1, 2, 0x14),              # RR 31, KRS 0
            enc_dp("mov", 1, 0, imm=0), enc_ldst(0, 1, 2, 0x18),               # OCT 0 FNS 0: 44,100 Hz
            enc_dp("mov", 1, 0, imm=0xf, rot=12), enc_dp("orr", 1, 1, imm=0x1f), enc_ldst(0, 1, 2, 0x24),
            enc_dp("mov", 1, 0, imm=0), enc_ldst(0, 1, 2, 0x28),               # TL 0
            enc_dp("mov", 1, 0, imm=0xc2, rot=12), enc_ldst(0, 1, 2, 0x00),    # KYONEX, KYONB, LPCTL
            enc_mrs(3), enc_dp("bic", 3, 3, imm=0x40), enc_msr(3, fields=1),    # the FIQ unmasked
            HALT,                                                              # b . from here on
        ]
        fiq = [  # 0x104
            enc_dp("mov", 8, 0, imm=1, rot=12),                   # 0x100
            enc_ldst(1, 9, 8, 0, byte=True), enc_dp("add", 9, 9, imm=1), enc_ldst(0, 9, 8, 0, byte=True),
            enc_dp("mov", 10, 0, imm=2, rot=5), enc_dp("add", 10, 10, imm=0x28, rot=12),  # 0x802800
            enc_dp("mov", 11, 0, imm=0xfe), enc_dp("orr", 11, 11, imm=2, rot=12),
            enc_ldst(0, 11, 10, 0x90),                            # TIMA back to 0xfe
            enc_dp("mov", 11, 0, imm=0x40), enc_ldst(0, 11, 10, 0xa4),  # SCIRE: timer A's bit
            enc_dp("add", 10, 10, imm=0x5, rot=12),               # 0x802d00
            enc_dp("mov", 11, 0, imm=1), enc_ldst(0, 11, 10, 4),  # M: the interrupt taken
            enc_dp("sub", 15, 14, imm=4, s=True),                 # subs pc, lr, #4
        ]
        img = bytearray(0x200)
        for at, words in ((0, vectors), (0x40, code), (0x104, fiq)):
            img[at:at + 4 * len(words)] = program(words)
        self.assertLess(0x40 + 4 * len(code), 0x100)
        d = work_dir()
        (d / "chip.bin").write_bytes(bytes(img))
        r, exe = build(d, CHIP_MAIN_C, ["arm7.c", "arm7_ls.c", "aica.c", "aica_arm.c", "aica_chan.c"], True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = subprocess.run([str(exe), str(d / "chip.bin")], capture_output=True, text=True, timeout=60)
        ahead, total, playing, left, right, waits, sgc, tima, first = (int(x) for x in out.stdout.split())
        self.assertEqual((ahead, total, waits, tima), (7, 99, 0, 0))
        self.assertEqual((playing, first, left, right), (1, 16384, 16384, 0))
        self.assertEqual(sgc, 3)


if __name__ == "__main__":
    unittest.main()
