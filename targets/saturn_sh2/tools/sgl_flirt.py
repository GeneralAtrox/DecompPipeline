r"""Names the SGL and SBL library functions in a Saturn code image with IDA FLIRT signatures (any Saturn game).

Usage: sgl_flirt.py IMAGE BASE FUNCS BODIES OUT.tsv SIG...: apply IDA FLIRT signatures (CyberWarriorX's Saturn SBL/SGL sets) to a
flat memory image, without Ghidra. Two passes per signature file:
  list: at every function start in FUNCS (one hex address per line, '#' comments), as ApplySig does in Ghidra;
  scan: at every 2-byte-aligned address in the image, to find library functions missing from FUNCS.
OUT is one row per named address in tc_fid.tsv columns (tools/sdk_fid_lib.merge_fid), plus kind.
Signatures: saturnsigs100.zip (SH3/*.sig), from CyberWarriorX's site, now gone; Wayback copy
  https://web.archive.org/web/20070107192409id_/http://www.cyberwarriorx.com:80/files/saturnsigs100.zip
  sha256 b0e7cf6da8838d60d191b455136ab6eb1b6be941810bf12bfac7b9ef332e5af2
A module matches when its pattern tree, its CRC16 over the next crc_length bytes and its tail bytes all match. Every
public name of the module is given at start + its own offset (ApplySig gives them all to the start: a bug).
Referenced-function checks are not made (neither radare2 nor ApplySig makes them), so a name with more than one
candidate is reported as a collision rather than picked.
Nearest existing: ApplySig.py (NWMonster, Jython, Ghidra <= 11; Ghidra 12.1.2 has no Jython), ported to Python 3 here.

Moved here 2026-11-15 from Integrations\SegaSaturn\Robopit\tools\sgl_flirt.py (unchanged code) so
tools/AutoOnboard/autoonboard.py's Saturn symbols stage (tools/AutoOnboard/onboard_sat.py) uses one matcher for every game. Robo Pit's copy is
now a duplicate of this one.
"""
import struct, sys, zlib, io, pathlib, collections

POLY = 0x1021


def _rev(n, bits):
    return int('{:0{}b}'.format(n, bits)[::-1], 2)


_T = []
for _i in range(256):
    c, crc = (_rev(_i, 8) << 8) & 0xFFFF, 0
    for _ in range(8):
        crc = ((crc << 1) ^ POLY if (crc ^ c) & 0x8000 else crc << 1) & 0xFFFF
        c = (c << 1) & 0xFFFF
    _T.append(_rev(crc, 16))


def crc16(data):
    out = 0xFFFF
    for b in data:
        out = (out >> 8) ^ _T[(out ^ b) & 0xFF]
    out ^= 0xFFFF
    return ((out & 0xFF) << 8) | (out >> 8)


def u8(f): return f.read(1)[0]
def u16be(f): return struct.unpack('>H', f.read(2))[0]
def u16le(f): return struct.unpack('<H', f.read(2))[0]
def u32le(f): return struct.unpack('<L', f.read(4))[0]
def u32be(f): return struct.unpack('>L', f.read(4))[0]


def max2(f):
    b = u8(f)
    return ((b & 0x7F) << 8) | u8(f) if b & 0x80 else b


def multi(f):
    b = u8(f)
    if not b & 0x80:
        return b
    if b & 0xC0 != 0xC0:
        return ((b & 0x7F) << 8) | u8(f)
    if b & 0xE0 != 0xE0:
        return ((b & 0x3F) << 24) | (u8(f) << 16) | u16be(f)
    return u32be(f)


Node = collections.namedtuple('Node', 'children modules length mask pattern')
Module = collections.namedtuple('Module', 'crc_len crc length publics tails refs')


def parse(path):
    f = open(path, 'rb')
    assert f.read(6) == b'IDASGN'
    ver = u8(f)
    arch, _ft, _os, _app, feat, _old, _crc = u8(f), u32le(f), u16le(f), u16le(f), u16le(f), u16le(f), u16le(f)
    f.read(12)
    nlen, _ = u8(f), u16le(f)
    nfun = None
    if ver >= 6:
        nfun = u32le(f)
        if ver >= 8:
            u16le(f)
            if ver > 9:
                u16le(f)
    lib = f.read(nlen).decode('latin-1')
    if feat & 0x10:
        f = io.BytesIO(zlib.decompress(f.read()))
    off = (lambda: multi(f)) if ver >= 9 else (lambda: max2(f))

    def public(o):
        o += off()
        b, loc = u8(f), False
        if b < 0x20:
            loc = bool(b & 2)
            b = u8(f)
        name = bytearray()
        while b >= 0x20:
            name.append(b)
            b = u8(f)
        return (name.decode('latin-1'), o, loc), o, b

    def modules():
        out = []
        while True:
            cl, cc = u8(f), u16be(f)
            while True:
                length, pubs, o = off(), [], 0
                while True:
                    p, o, fl = public(o)
                    pubs.append(p)
                    if not fl & 1:
                        break
                tails = []
                if fl & 2:
                    for _ in range(u8(f) if ver >= 8 else 1):
                        tails.append((off(), u8(f)))
                refs = []
                if fl & 4:
                    for _ in range(u8(f) if ver >= 8 else 1):
                        ro, nl = off(), u8(f)
                        nl = nl or multi(f)
                        refs.append((ro, f.read(nl)))
                out.append(Module(cl, cc, length, pubs, tails, refs))
                if not fl & 8:
                    break
            if not fl & 0x10:
                return out

    def tree(root):
        length, mask, pat = 0, [], []
        if not root:
            length = u8(f)
            vm = max2(f) if length < 0x10 else multi(f) if length <= 0x20 else (multi(f) << 32) | multi(f)
            bit = 1 << (length - 1)
            for _ in range(length):
                v = bool(vm & bit)
                mask.append(v)
                pat.append(0 if v else u8(f))
                bit >>= 1
        n = multi(f)
        if n == 0:
            return Node([], modules(), length, mask, pat)
        return Node([tree(False) for _ in range(n)], [], length, mask, pat)

    root = tree(True)
    assert not f.read(1)
    return dict(ver=ver, arch=arch, lib=lib, nfun=nfun, root=root)


def node_ok(node, buf, at):
    if at + node.length > len(buf):
        return False
    for i in range(node.length):
        if not node.mask[i] and buf[at + i] != node.pattern[i]:
            return False
    return True


def mod_ok(m, buf, at):
    if at + m.crc_len > len(buf) or (m.crc_len and crc16(buf[at:at + m.crc_len]) != m.crc):
        return False
    return all(at + m.crc_len + o < len(buf) and buf[at + m.crc_len + o] == v for o, v in m.tails)


def walk(node, buf, at, hits):
    if not node_ok(node, buf, at):
        return
    for ch in node.children:
        walk(ch, buf, at + node.length, hits)
    for m in node.modules:
        if mod_ok(m, buf, at + node.length):
            hits.append(m)


def index(root):
    # root children by their first two bytes (None where masked) so a scan tries only a few at each address
    idx = collections.defaultdict(list)
    for ch in root.children:
        k = None if ch.length < 2 or ch.mask[0] or ch.mask[1] else (ch.pattern[0], ch.pattern[1])
        idx[k].append(ch)
    return idx


def match_at(idx, buf, at):
    hits = []
    for ch in idx.get((buf[at], buf[at + 1]), []) + idx.get(None, []):
        walk(ch, buf, at, hits)
    return hits


def bodies_of(path):
    # state/function_bodies.txt: "entry start end" per block (hex), or code_ranges.txt "start end"
    out = []
    for l in open(path):
        p = l.split()
        if len(p) >= 2 and not l.startswith('#'):
            out.append((int(p[-2], 16), int(p[-1], 16)))
    return out


def merge(rows, sset, bodies):
    """One row per named address, in tools/sdk_fid_lib.merge_fid's tc_fid.tsv columns (so tools/sdk_classify.py and
    the design stage read it as they read FID's), plus kind: function (a catalogue start), inside_function (inside a
    catalogue function's body: functions the catalogue joined), outside_catalogue (even, in no body: a start the
    catalogue lacks, or a library data symbol) or data (odd address)."""
    by = collections.defaultdict(lambda: collections.defaultdict(set))
    for a, n, tag, scope, _found, ln, _c in rows:
        by[a][n].add((tag, scope, ln))
    out = []
    for a in sorted(by):
        names = sorted(by[a], key=lambda n: (-len(by[a][n]), n))
        top = by[a][names[0]]
        inside = any(s <= a < e for s, e in bodies)
        kind = 'function' if a in sset else 'data' if a & 1 else 'inside_function' if inside else 'outside_catalogue'
        out.append({'addr': '%08x' % a, 'region': 'library', 'our_name': '', 'status': 'match',
                    'code_units': str(max(t[2] for t in top)), 'fid_names': '|'.join(n.lstrip('_') for n in names),
                    'unique': 'yes' if len(names) == 1 else 'best' if len(top) > len(by[a][names[1]]) else 'no',
                    'candidates': str(len(names)), 'score': '', 'library_object': 'flirt:' + sorted(top)[0][1],
                    'release': '|'.join(sorted({t[0] for t in top})), 'kind': kind})
    return out


def main():
    image, base, funcs, bodies, out, *sigs = sys.argv[1:]
    buf, base = pathlib.Path(image).read_bytes(), int(base, 16)
    starts = sorted(int(l.split()[0], 16) for l in open(funcs) if l.strip() and not l.startswith('#'))
    sset = set(starts)
    rows = []
    for s in sigs:
        sig = parse(s)
        idx = index(sig['root'])
        tag = pathlib.Path(s).stem
        nl = ns = 0
        for at in range(0, len(buf) - 2, 2):
            hits = match_at(idx, buf, at)
            if not hits:
                continue
            a = base + at
            names = sorted({(n, o, loc, m.length) for m in hits for (n, o, loc) in m.publics})
            for n, o, loc, ln in names:
                if n == '?':
                    continue
                rows.append((a + o, n, tag, 'local' if loc else 'public', 'list' if a in sset else 'scan', ln,
                             len({x[0] for x in names if x[1] == o})))
            nl += a in sset
            ns += 1
        print(f'{tag}: {sig["lib"]!r} v{sig["ver"]} arch {sig["arch"]}, {sig["nfun"]} names; matched at {nl} listed '
              f'starts, {ns} addresses in all', flush=True)
    m = merge(rows, sset, bodies_of(bodies))
    head = list(m[0])
    with open(out, 'w', newline='\n') as f:
        f.write('\t'.join(head) + '\n')
        for r in m:
            f.write('\t'.join(r[k] for k in head) + '\n')
    c = collections.Counter(r['kind'] for r in m)
    print(len(m), 'named addresses ->', out, dict(c), 'ambiguous', sum(r['unique'] != 'yes' for r in m))


if __name__ == '__main__':
    main()
