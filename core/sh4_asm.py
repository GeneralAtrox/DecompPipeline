"""A packet's asm.txt (TcPrepare's listing) as instructions with their control flow, for the static checks of batch 27
(core/ret_use.py: which result registers a caller reads after a call; core/stack_uninit.py: frame words read before
any write; core/data_funcs.py: code addresses used as values). Text only: no Ghidra.

A line is `  ADDR  MNEMONIC OPERANDS   ; NOTE`. A mnemonic starting with `_` is the delay slot of the branch above it.
TcPrepare notes `; call X` on calls and tail calls (bsr, jsr, bra, jmp to a function) and `; = 0xV  (what)` on
literal-pool loads.
"""
import re

LINE = re.compile(r"^\s+([0-9a-f]{8})\s+(\S+)(?:\s+(.*?))?\s*$")
DELAYED = {"bra", "bsr", "braf", "bsrf", "jmp", "jsr", "rts", "rte", "bt/s", "bf/s"}
CALLS = {"bsr", "bsrf", "jsr"}
COND = {"bt", "bf", "bt/s", "bf/s"}
# instructions whose last operand is written without being read
PURE_DEF = re.compile(r"^(mov(\.[bwl])?|mova|movt|fmov(\.s)?|fldi[01]|float|fsts|sts(\.l)?|stc(\.l)?|ext[us]\.[bw]|neg|"
                      r"not|swap\.[bw]|fsca|fcnvds|fcnvsd|lds(\.l)?|ldc(\.l)?)$")
NO_WRITE = re.compile(r"^(cmp/\w+|tst(\.b)?|fcmp/\w+|ftst)$")
REG = re.compile(r"\b(r1[0-5]|r[0-9]|fr1[0-5]|fr[0-9]|dr1[024]|dr[02468]|fv1[02]|fv[048]|xd1[024]|xd[02468])\b")


class Insn:
    __slots__ = ("addr", "mn", "ops", "note", "slot", "idx")

    def __init__(self, addr, mn, ops, note, idx):
        self.addr, self.ops, self.note, self.idx = addr, ops, note, idx
        self.slot = mn.startswith("_")
        self.mn = mn.lstrip("_").lower()

    def call_target(self):
        m = re.search(r"\bcall ([0-9a-f]{8})\b", self.note or "")
        return m.group(1) if m else None

    def pool_value(self):
        m = re.search(r"=\s*0x([0-9a-f]{8})\s*\((\w+)", self.note or "")
        return (int(m.group(1), 16), m.group(2)) if m else None

    def branch_target(self):
        m = re.fullmatch(r"0x([0-9a-f]{8})", (self.ops[0] if self.ops else "").strip())
        return int(m.group(1), 16) if m else None

    def __repr__(self):
        return f"{self.addr:08x} {self.mn} {','.join(self.ops)}"


def split_ops(text):
    """Operands at top-level commas: `@(0x4,r15)` stays one operand."""
    out, depth, cur = [], 0, ""
    for ch in text or "":
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def parse(text):
    """[Insn] in listing order."""
    out = []
    for ln in text.splitlines():
        body, _, note = ln.partition(";")
        m = LINE.match(body)
        if not m:
            continue
        out.append(Insn(int(m.group(1), 16), m.group(2), split_ops(m.group(3)), note.strip(), len(out)))
    return out


def read_packet(packet_dir):
    """The packet's asm text (asm.txt, or its parts asm_1.txt, asm_2.txt ... in order), or None."""
    files = sorted(packet_dir.glob("asm*.txt"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files) if files else None


class Flow:
    """Successors of each instruction, delay slots included: a delayed branch at i runs i+1 first, so i+1's
    successors are the branch's. Unknown flow (braf, a jmp that is no call, a target outside the listing) is None."""

    def __init__(self, insns):
        self.insns = insns
        self.at = {x.addr: x.idx for x in insns}

    def target(self, x):
        t = x.branch_target()
        return self.at.get(t) if t is not None else None

    def succ(self, i):
        """[(next index, kind)] after instruction i; kind 'call' marks the return from a call, 'end' a return or a
        tail call (no successor), None in place of the list: unknown flow."""
        x = self.insns[i]
        if x.slot and i > 0 and self.insns[i - 1].mn in DELAYED:
            return self._after_branch(i - 1)
        if x.mn in DELAYED:
            return [(i + 1, "slot")] if i + 1 < len(self.insns) else None
        if x.mn in ("bt", "bf"):
            t = self.target(x)
            return None if t is None else [(t, "branch"), (i + 1, "fall")]
        return [(i + 1, "fall")] if i + 1 < len(self.insns) else None

    def _after_branch(self, b):
        x, nxt = self.insns[b], b + 2
        if x.mn in ("rts", "rte"):
            return [(None, "end")]
        if x.mn in CALLS:
            return [(nxt, "call")] if nxt < len(self.insns) else None
        if x.mn in ("bra", "jmp") and x.call_target():
            return [(None, "end")]  # a tail call
        if x.mn == "bra":
            t = self.target(x)
            return None if t is None else [(t, "branch")]
        if x.mn in ("bt/s", "bf/s"):
            t = self.target(x)
            return None if t is None else [(t, "branch"), (nxt, "fall")]
        return None  # braf, bsrf, a computed jmp


def regs_in(op):
    return REG.findall(op or "")


def covers(name, reg):
    """Does register `name` (fr0, dr0, fv0, r0, ...) include `reg` (r0 or fr0 ... fr15)?"""
    if name == reg:
        return True
    m, n = re.fullmatch(r"(dr|fv)(\d+)", name), re.fullmatch(r"fr(\d+)", reg)
    if not m or not n:
        return False
    lo, k = int(m.group(2)), int(n.group(1))
    return lo <= k < lo + (2 if m.group(1) == "dr" else 4)


def uses(x, reg):
    """(reads, writes) of register reg by instruction x: reads when it is in a source operand, in an address or in a
    read-modify-write destination; writes when it is the destination."""
    if not x.ops:
        return False, False
    *src, dst = x.ops
    reads = any(covers(r, reg) for o in src for r in regs_in(o))
    if dst.startswith("@"):
        return reads or any(covers(r, reg) for r in regs_in(dst)), "@-" in dst and covers(regs_in(dst)[0], reg)
    in_dst = any(covers(r, reg) for r in regs_in(dst))
    if not in_dst:
        return reads, False
    if NO_WRITE.match(x.mn) or x.mn in DELAYED or x.mn in ("bt", "bf"):
        return True, False
    if len(x.ops) == 1 and x.mn in ("movt", "fldi0", "fldi1"):
        return reads, True
    if PURE_DEF.match(x.mn) and len(x.ops) > 1:
        return reads, True
    return True, True  # a read-modify-write destination (add, or, shlr, fmul, ...)
