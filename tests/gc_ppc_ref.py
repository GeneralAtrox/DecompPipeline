"""A reference interpreter for the PowerPC 750CL's user-level integer instructions: the answer key for
tests/test_gc_behaviour.py.

Written from the instruction descriptions in "PowerPC Microprocessor Family: The Programming Environments for 32-Bit
Microprocessors" (chapter 8): each instruction is its register-transfer text there, on 32-bit registers, CR, XER
(SO, OV, CA and the 7-bit byte count), LR and CTR. It shares no code with the translator (ppctranslate): it takes
its own fields out of the instruction word.

Not modelled (Unknown): floating point and paired singles, the string instructions, calls (bl, bctrl, blrl), bcctr,
every special-purpose register but XER, LR and CTR, and the supervisor instructions. Undefined results (a divide by
zero, 0x80000000 / -1) raise Skip: the architecture gives no value to compare.
"""
M = 0xFFFFFFFF


class Skip(Exception):
    """The architecture leaves this result undefined."""


class Unknown(Exception):
    """An instruction or form this model does not cover."""


def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


def sext(v, n):
    v &= (1 << n) - 1
    return v - (1 << n) if v >> (n - 1) else v


def rotl(v, n):
    n &= 31
    return ((v << n) | (v >> (32 - n))) & M


def mask(mb, me):
    """Ones from bit mb to bit me (bit 0 the most significant), wrapping when mb > me."""
    x = (M >> mb) & (M << (31 - me)) & M
    return x if mb <= me else (M >> mb) | ((M << (31 - me)) & M)


class Memory:
    """Big-endian bytes; an address nothing wrote reads a fixed byte of its own."""

    def __init__(self):
        self.b = {}
        self.written = {}

    @staticmethod
    def fill(a):
        return ((a * 0x9E3779B1) >> 21) & 0xFF

    def rd(self, a, n):
        v = 0
        for k in range(n):
            x = (a + k) & M
            v = v << 8 | self.b.get(x, self.fill(x))
        return v

    def wr(self, a, n, v):
        for k in range(n):
            x = (a + k) & M
            self.b[x] = self.written[x] = (v >> (8 * (n - 1 - k))) & 0xFF


# opcode 31, by the 10-bit extended opcode
LOGIC = {28: lambda s, b: s & b, 444: lambda s, b: s | b, 316: lambda s, b: s ^ b, 476: lambda s, b: ~(s & b),
         124: lambda s, b: ~(s | b), 284: lambda s, b: ~(s ^ b), 60: lambda s, b: s & ~b, 412: lambda s, b: s | ~b}
LOAD_X = {23: (4, 0, 0), 55: (4, 1, 0), 87: (1, 0, 0), 119: (1, 1, 0), 279: (2, 0, 0), 311: (2, 1, 0),
          343: (2, 0, 1), 375: (2, 1, 1)}                      # size, update, algebraic
STORE_X = {151: (4, 0), 183: (4, 1), 215: (1, 0), 247: (1, 1), 407: (2, 0), 439: (2, 1)}
LOAD_D = {32: (4, 0, 0), 33: (4, 1, 0), 34: (1, 0, 0), 35: (1, 1, 0), 40: (2, 0, 0), 41: (2, 1, 0),
          42: (2, 0, 1), 43: (2, 1, 1)}
STORE_D = {36: (4, 0), 37: (4, 1), 38: (1, 0), 39: (1, 1), 44: (2, 0), 45: (2, 1)}
CR_OPS = {257: lambda a, b: a & b, 449: lambda a, b: a | b, 193: lambda a, b: a ^ b, 225: lambda a, b: 1 ^ (a & b),
          33: lambda a, b: 1 ^ (a | b), 289: lambda a, b: 1 ^ a ^ b, 129: lambda a, b: a & (1 ^ b),
          417: lambda a, b: a | (1 ^ b)}


def swap(v, n):
    return int.from_bytes(v.to_bytes(n, "big"), "little")


class Cpu:
    def __init__(self, words, base, sp, sentinel, mem=None):
        self.words, self.base, self.sentinel = list(words), base, sentinel
        self.mem = mem or Memory()
        self.r = [0] * 32
        self.r[1] = sp
        self.cr = 0
        self.so = self.ov = self.ca = self.xbc = 0
        self.lr, self.ctr = sentinel, 0
        self.pc = base
        self.reserve = False

    # ------------------------------------------------------------------------------------------- state helpers
    def xer(self):
        return self.so << 31 | self.ov << 30 | self.ca << 29 | self.xbc

    def set_field(self, f, v):
        sh = 28 - 4 * f
        self.cr = (self.cr & ~(0xF << sh) | (v & 0xF) << sh) & M

    def crbit(self, n):
        return (self.cr >> (31 - n)) & 1

    def compare(self, f, x, y):
        self.set_field(f, (8 if x < y else 4 if x > y else 2) | self.so)

    def result(self, reg, v, rc):
        self.r[reg] = v & M
        if rc:
            self.compare(0, s32(v & M), 0)

    def add(self, d, x, y, cin, carry, oe, rc):
        t = x + y + cin
        v = t & M
        if carry:
            self.ca = t >> 32
        if oe:
            self.ov = ((x ^ v) & (y ^ v)) >> 31
            self.so |= self.ov
        self.result(d, v, rc)

    def ea(self, a, off):
        return ((self.r[a] if a else 0) + off) & M

    def load(self, d, a, off, size, update, algebraic):
        if update and (a == 0 or a == d):
            raise Unknown("invalid update form")
        ea = self.ea(a, off)
        v = self.mem.rd(ea, size)
        self.r[d] = sext(v, 16) & M if algebraic else v
        if update:
            self.r[a] = ea

    def store(self, s, a, off, size, update):
        if update and a == 0:
            raise Unknown("invalid update form")
        ea = self.ea(a, off)
        self.mem.wr(ea, size, self.r[s] & ((1 << 8 * size) - 1))
        if update:
            self.r[a] = ea

    def taken(self, bo, bi):
        ctr_ok = True
        if not bo & 4:
            self.ctr = (self.ctr - 1) & M
            ctr_ok = (self.ctr != 0) != bool(bo & 2)
        return ctr_ok and (bool(bo & 16) or self.crbit(bi) == (bo >> 3) & 1)

    # ------------------------------------------------------------------------------------------------ running
    def run(self, limit=20000):
        for _ in range(limit):
            if self.pc == self.sentinel:
                return self
            k = (self.pc - self.base) // 4
            if self.pc & 3 or not 0 <= k < len(self.words):
                raise Unknown(f"left the body at {self.pc:08x}")
            nxt = self.step(self.words[k])
            self.pc = (self.pc + 4) & M if nxt is None else nxt
        raise Unknown("step limit")

    def step(self, w):
        op, d, a, b = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        simm, uimm, rc, r = sext(w, 16), w & 0xFFFF, w & 1, self.r
        if op == 14:
            r[d] = self.ea(a, simm)
        elif op == 15:
            r[d] = self.ea(a, simm << 16)
        elif op in (12, 13):
            self.add(d, r[a], simm & M, 0, True, 0, op == 13)
        elif op == 8:
            self.add(d, ~r[a] & M, simm & M, 1, True, 0, 0)
        elif op == 7:
            r[d] = (s32(r[a]) * simm) & M
        elif op in (10, 11):
            if d & 3:
                raise Unknown("compare with L = 1")
            self.compare(d >> 2, *((s32(r[a]), simm) if op == 11 else (r[a], uimm)))
        elif op in (24, 25):
            r[a] = r[d] | (uimm << 16 if op == 25 else uimm)
        elif op in (26, 27):
            r[a] = r[d] ^ (uimm << 16 if op == 27 else uimm)
        elif op in (28, 29):
            self.result(a, r[d] & (uimm << 16 if op == 29 else uimm), 1)
        elif op in (20, 21, 23):
            m = mask((w >> 6) & 31, (w >> 1) & 31)
            v = rotl(r[d], r[b] if op == 23 else b)
            self.result(a, (v & m) | (r[a] & ~m & M) if op == 20 else v & m, rc)
        elif op == 18:
            if w & 1:
                raise Unknown("bl")
            li = sext(w & 0x03FFFFFC, 26)
            return (li if w & 2 else self.pc + li) & M
        elif op == 16:
            if w & 1:
                raise Unknown("bcl")
            if self.taken(d, a):
                bd = sext(w & 0xFFFC, 16)
                return (bd if w & 2 else self.pc + bd) & M
        elif op == 19:
            return self.op19(w, d, a, b)
        elif op == 31:
            self.op31(w, d, a, b, rc)
        elif op in LOAD_D:
            self.load(d, a, simm, *LOAD_D[op])
        elif op in STORE_D:
            self.store(d, a, simm, *STORE_D[op])
        elif op in (46, 47):                                   # lmw / stmw
            ea = self.ea(a, simm)
            for k in range(d, 32):
                if op == 46:
                    r[k] = self.mem.rd(ea, 4)
                else:
                    self.mem.wr(ea, 4, r[k])
                ea = (ea + 4) & M
        else:
            raise Unknown(f"opcode {op} ({w:08x})")
        return None

    def op19(self, w, d, a, b):
        xo = (w >> 1) & 0x3FF
        if xo == 16:                                            # bclr
            if w & 1:
                raise Unknown("blrl")
            return self.lr & ~3 & M if self.taken(d, a) else None
        if xo == 0:                                             # mcrf
            self.set_field(d >> 2, self.cr >> (28 - 4 * (a >> 2)))
        elif xo in CR_OPS:
            v = CR_OPS[xo](self.crbit(a), self.crbit(b)) & 1
            self.cr = (self.cr & ~(1 << (31 - d)) | v << (31 - d)) & M
        else:
            raise Unknown(f"opcode 19 xo {xo}")
        return None

    def op31(self, w, d, a, b, rc):
        xo, r = (w >> 1) & 0x3FF, self.r
        oe, xo9 = xo >> 9, xo & 0x1FF
        ra, rb, ca, na = r[a], r[b], self.ca, ~r[a] & M
        if xo in LOGIC:
            self.result(a, LOGIC[xo](r[d], rb), rc)
        elif xo in (24, 536):                                   # slw / srw: the count is rB's low 6 bits
            n = rb & 0x3F
            self.result(a, 0 if n > 31 else r[d] << n if xo == 24 else r[d] >> n, rc)
        elif xo in (792, 824):                                  # sraw / srawi
            n = rb & 0x3F if xo == 792 else b
            v = s32(r[d])
            self.ca = int(v < 0 and (n > 31 or r[d] & ((1 << n) - 1) != 0))
            self.result(a, v >> min(n, 31), rc)
        elif xo == 26:
            self.result(a, 32 - r[d].bit_length(), rc)
        elif xo in (922, 954):
            self.result(a, sext(r[d], 16 if xo == 922 else 8), rc)
        elif xo in (0, 32):
            if d & 3:
                raise Unknown("compare with L = 1")
            self.compare(d >> 2, *((s32(ra), s32(rb)) if xo == 0 else (ra, rb)))
        elif xo == 19:
            r[d] = self.cr
        elif xo == 144:                                         # mtcrf
            for k in range(8):
                if (w >> 12) & (0x80 >> k):
                    self.set_field(k, r[d] >> (28 - 4 * k))
        elif xo == 512:                                         # mcrxr
            self.set_field(d >> 2, self.so << 3 | self.ov << 2 | self.ca << 1)
            self.so = self.ov = self.ca = 0
        elif xo in (339, 467):
            self.spr(xo == 467, d, b << 5 | a)
        elif xo in LOAD_X:
            self.load(d, a, rb, *LOAD_X[xo])
        elif xo in STORE_X:
            self.store(d, a, rb, *STORE_X[xo])
        elif xo in (534, 790):                                  # lwbrx / lhbrx
            n = 4 if xo == 534 else 2
            r[d] = swap(self.mem.rd(self.ea(a, rb), n), n)
        elif xo in (662, 918):                                  # stwbrx / sthbrx
            n = 4 if xo == 662 else 2
            self.mem.wr(self.ea(a, rb), n, swap(r[d] & ((1 << 8 * n) - 1), n))
        elif xo == 20:                                          # lwarx
            r[d] = self.mem.rd(self.ea(a, rb), 4)
            self.reserve = True
        elif xo == 150 and rc:                                  # stwcx.: stores when the reservation holds
            if self.reserve:
                self.mem.wr(self.ea(a, rb), 4, r[d])
            self.set_field(0, (2 if self.reserve else 0) | self.so)
            self.reserve = False
        elif xo9 == 266:
            self.add(d, ra, rb, 0, False, oe, rc)
        elif xo9 == 10:
            self.add(d, ra, rb, 0, True, oe, rc)
        elif xo9 == 138:
            self.add(d, ra, rb, ca, True, oe, rc)
        elif xo9 == 234:
            self.add(d, ra, M, ca, True, oe, rc)
        elif xo9 == 202:
            self.add(d, ra, 0, ca, True, oe, rc)
        elif xo9 == 40:
            self.add(d, na, rb, 1, False, oe, rc)
        elif xo9 == 8:
            self.add(d, na, rb, 1, True, oe, rc)
        elif xo9 == 136:
            self.add(d, na, rb, ca, True, oe, rc)
        elif xo9 == 232:
            self.add(d, na, M, ca, True, oe, rc)
        elif xo9 == 200:
            self.add(d, na, 0, ca, True, oe, rc)
        elif xo9 == 104:
            self.add(d, na, 0, 1, False, oe, rc)
        elif xo9 == 235:                                        # mullw
            p = s32(ra) * s32(rb)
            if oe:
                self.ov = int(p != s32(p & M))
                self.so |= self.ov
            self.result(d, p, rc)
        elif xo in (75, 11):                                    # mulhw / mulhwu
            self.result(d, (s32(ra) * s32(rb) if xo == 75 else ra * rb) >> 32, rc)
        elif xo9 in (491, 459) and not oe:                      # divw / divwu
            if rb == 0 or (xo9 == 491 and ra == 0x80000000 and rb == M):
                raise Skip("undefined quotient")
            x, y = (s32(ra), s32(rb)) if xo9 == 491 else (ra, rb)
            q = abs(x) // abs(y)
            self.result(d, -q if (x < 0) != (y < 0) else q, rc)
        else:
            raise Unknown(f"opcode 31 xo {xo}")

    def spr(self, write, reg, n):
        v = self.r[reg]
        if n == 1 and write:
            self.so, self.ov, self.ca, self.xbc = v >> 31, (v >> 30) & 1, (v >> 29) & 1, v & 0x7F
        elif n == 1:
            self.r[reg] = self.xer()
        elif n == 8 and write:
            self.lr = v
        elif n == 8:
            self.r[reg] = self.lr
        elif n == 9 and write:
            self.ctr = v
        elif n == 9:
            self.r[reg] = self.ctr
        else:
            raise Unknown(f"spr {n}")
