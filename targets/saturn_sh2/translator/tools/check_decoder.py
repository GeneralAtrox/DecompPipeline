"""Cross-check the decoder's mnemonics against Ghidra's listings in DP packets (asm.txt), read-only."""
import collections, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sh2translate.decode import decode

ws = pathlib.Path(sys.argv[1])
img = (ws / "binary" / "field.bin").read_bytes() if len(sys.argv) < 3 else pathlib.Path(sys.argv[2]).read_bytes()
BASE = 0x06000000
bad, n = collections.Counter(), 0
ex = {}
for f in ws.glob("packets/*/asm.txt"):
    for ln in f.read_text(errors="replace").splitlines():
        m = re.match(r"^\s+([0-9a-f]{8})\s+(\S+)\s*(.*?)\s*(;.*)?$", ln)
        if not m:
            continue
        a = int(m.group(1), 16)
        gm = m.group(2).lstrip("_").lower()
        w = int.from_bytes(img[a - BASE:a - BASE + 2], "big")
        mt = decode(w, a).text
        mine = mt.split()[0]
        regs_g = re.findall(r"\b(r\d+|gbr|pr|mach|macl|sr|vbr)\b", m.group(3).lower())
        regs_m = re.findall(r"\b(r\d+|gbr|pr|mach|macl|sr|vbr)\b", mt[len(mine):])
        nums_g = [int(x, 16) for x in re.findall(r"0x([0-9a-f]+)", m.group(3).lower())]
        n += 1
        if mine == gm and regs_g != regs_m:
            mine += " REGS"
        if mine == gm and nums_g and decode(w, a).ea and decode(w, a).ea not in nums_g:
            mine += " EA"
        if mine != gm:
            bad[(gm, mine)] += 1
            ex[(gm, mine)] = (hex(a), hex(w), m.group(2), m.group(3), decode(w, a).text)
print(n, "insns,", sum(bad.values()), "mismatches")
for k, c in bad.most_common(40):
    print(c, k, ex[k])
