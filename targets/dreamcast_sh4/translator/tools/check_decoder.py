"""Cross-check the decoder's mnemonics and registers against Ghidra's listings in DP packets (asm.txt), read-only.

usage: python tools/check_decoder.py <workspace>
"""
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sh4translate.decode import decode  # noqa: E402

ws = pathlib.Path(sys.argv[1])
proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
img = (ws / proj["binary"]).read_bytes()
BASE = 0x8C010000
REG = re.compile(r"\b(r\d+|gbr|pr|mach|macl|sr|vbr|fr\d+|dr\d+|xd\d+|fv\d+|fpul|fpscr)\b")
bad, n = collections.Counter(), 0
ex = {}
seen = set()
for f in ws.glob("packets/*/asm.txt"):
    for ln in f.read_text(errors="replace").splitlines():
        m = re.match(r"^\s+([0-9a-f]{8})\s+(\S+)\s*(.*?)\s*(;.*)?$", ln)
        if not m:
            continue
        a = int(m.group(1), 16)
        if a in seen:
            continue
        seen.add(a)
        gm = m.group(2).lstrip("_").lower().replace("fmov.s", "fmov")
        w = int.from_bytes(img[a - BASE:a - BASE + 2], "little")
        d = decode(w, a)
        mt = d.text
        mine = mt.split()[0].replace("fmov.s", "fmov")
        regs_g = REG.findall(m.group(3).lower())
        regs_m = REG.findall(mt[len(mt.split()[0]):])
        nums_g = [int(x, 16) for x in re.findall(r"0x([0-9a-f]+)", m.group(3).lower())]
        n += 1
        if mine == gm and regs_g != regs_m:
            mine += " REGS"
        if mine == gm and nums_g and d.ea and d.ea not in nums_g:
            mine += " EA"
        if mine != gm:
            bad[(gm, mine)] += 1
            ex[(gm, mine)] = (hex(a), hex(w), m.group(2), m.group(3), mt)
print(n, "insns,", sum(bad.values()), "mismatches")
for k, c in bad.most_common(40):
    print(c, k, ex[k])
