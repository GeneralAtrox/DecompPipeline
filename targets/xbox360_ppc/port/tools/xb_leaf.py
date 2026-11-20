"""The body of a call target the catalog has no entry for: a leaf the compiler gave no unwind record (pdata lists only functions that
save registers or use a frame). The run called it, so it is code; its body ends at the first unconditional return or tail jump (back
before it, or more than 0x400 on) past every forward branch inside it. A bctr through a table right after it (see inline_table) reaches
every target in the table, and the table's words are skipped.

Nearest existing: gen_xbox.py Kit.inside - that one cuts a catalog function's body; here no catalog function holds the address.
"""


def word(image, base, pc):
    return int.from_bytes(image[pc - base:pc - base + 4], "big")


def inline_table(image, base, addr, pc):
    """(entries) of a bctr at pc through a table of words right after it, or None: the code before it, back to addr, sets a register to
    pc + 4 with addis/addi and bounds the index with cmplwi rI,N. First: Too Human's 82eb5490, `cmplwi r10,3; bgtlr; lis r12; addi r12,
    0x54c0; slwi r0,r10,2; lwzx r0,r12,r0; mtctr r0; bctr`, four cases that each tail jump on."""
    hi = {}
    table = count = None
    for p in range(addr, pc, 4):
        w = word(image, base, p)
        op, rt, ra, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == 15 and ra == 0:                                         # lis rT, hi
            hi[rt] = imm << 16
        elif op == 14 and ra in hi:                                       # addi rT, rA, lo
            v = (hi[ra] + imm - ((imm & 0x8000) << 1)) & 0xFFFFFFFF
            if v == pc + 4:
                table = v
        elif op == 10 and not (w >> 21) & 1:                             # cmplwi crN, rA, N
            count = imm + 1
    if table is None or count is None or count > 256 or table - base + 4 * count > len(image):
        return None
    out = [word(image, base, table + 4 * k) for k in range(count)]
    if any(t & 3 or not (table + 4 * count <= t <= addr + 0x400 + 4 * count) for t in out):
        return None
    return out


def body(image, base, addr):
    """Returns [(addr, end)] or None when no such return is found in 1024 instructions (Too Human's vector leaf 82cbdad0 is about 280)."""
    off = addr - base
    if off < 0 or off + 4 > len(image):
        return None
    reach = addr
    pc = addr
    for _ in range(1024):
        if pc - base + 4 > len(image):
            return None
        w = word(image, base, pc)
        op = w >> 26
        if op in (16, 18) and not w & 1 and not w & 2:      # b / bc, relative, not a call
            d = (w & 0xFFFC) - ((w & 0x8000) << 1) if op == 16 else (w & 0x03FFFFFC) - ((w & 0x02000000) << 1)
            if 0 < d <= 0x400:
                reach = max(reach, pc + d)
            if op == 18 and pc >= reach and (pc + d < addr or d > 0x400):   # or a tail jump out of it: 820a0130 back, 829a4a88 on to 82a6ed88
                return [(addr, pc + 4)]
        if w == 0x4E800420:                                 # bctr
            tab = inline_table(image, base, addr, pc)
            if tab:
                reach = max([reach] + tab)
                pc += 4 + 4 * len(tab)
                continue
        if w == 0x4E800020 and pc >= reach:
            return [(addr, pc + 4)]
        pc += 4
    return None
