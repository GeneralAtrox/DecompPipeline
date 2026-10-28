"""Display-list digests: what the port hands the PowerVR, vertex by vertex, against what Flycast's TA decoded, on the
fields the hardware uses for each polygon.

tools/hw_check.py already compares each render's strip headers, vertex counts and summed extents. Each list also
gets a vertex digest, from both sides: tools/hw_emu.lua hashes Flycast's decoded primitives (the pvr-primitive
event's vertices); runtime/ta_digest.c decodes the port's lists as Flycast's TA does (core/hw/pvr/ta_vtx.cpp) and
hashes the same fields. A field the polygon's mode does not use is masked (hashed as 0), so a word the game never
stores (what the store queue held) is no difference where the hardware ignores it. The masks, and where each comes
from (Flycast's renderer is the reference the recordings are made with; the TA's PCW and TSP bits as the PowerVR
defines them):

  field              used when                                     source
  x y                always
  z                  always (a sprite's d: from the plane of a b c) ta_vtx.cpp CaclulateSpritePlane: d's z u v are
                                                                    the hardware's, from a b c
  u v                PCW Texture (bit 3)                           rend/gles/gles.cpp: pp_Texture gates every texel
  base colour rgb    not textured, or TSP shading (bits 7:6) not 0 gles.cpp: ShadInstr 0 (decal) colour = texture
  base colour alpha  TSP Use Alpha (bit 20), and not textured or   gles.cpp: pp_UseAlpha 0 sets alpha 1; ShadInstr
                     shading 2 or 3                                 0 and 1 take the texture's alpha
  offset colour rgb  PCW Texture and PCW Offset (bit 2)            gles.cpp: pp_Offset adds it, in textured only
  offset alpha       PCW Texture, PCW Offset and TSP fog (bits     gles.cpp: pp_FogCtrl 1 (per vertex) with
                     23:22) 01                                      pp_Offset mixes the fog colour by offset.a
  colours, flat      PCW Gouraud (bit 1) 0: vertices 0 and 1 of a  rend/sorter.cpp setFirstProvokingVertex: a
                     strip carry none (each triangle's last vertex  triangle's colour is its last vertex's
                     colours it)
  two-volume strips  x y z only (not modelled: no game uses them yet)
  off the screen     x y z only when every vertex's x, or every y,   pixels are sampled at x, y >= 0 (the tiles
                     is negative (-0 not): no pixel drawn; its H hash  start at 0): e.g. TC's list-start dummy
                     is marked "o"                                    sprites (x -1.5..-0.5), colours unstored
Modifier volumes have no vertices in the event: their lists are compared by header and triangle count only.

Per list the digest is "h0 all from1 from2": the first strip's hash, then every strip's hash chained from strip 0,
1 and 2 (a list whose first sprite the console gets twice, hw_check's known resend, compares the emulator's from 2
with the port's from 1). Each hash is two lanes (h * 65599 + x mod 4294967291, h * 31337 + x mod 4294967279), exact in
Lua's doubles. tools/lockstep.py compares them with the RAM, pairing renders as hw_check does.

    python tools/ta_digest.py detail --game DIR --rec REC --build-dir B --frame F --render R [hw_check emu args]

`detail` is the field-by-field view of one divergent render (lockstep.py prints the command): the port runs to its
scene of frame F (lockstep.py's "port frame") and dumps its masked fields (--hw-dump); render R's come from the
recording's LS_HW_DUMP file if it has them, else Flycast replays the recording's input to R and dumps it (hw_check.py
emu --hw-dump, stopped once it is written; REC/detail_R), with the Flycast the recording was made with (its emu.lua
names it) unless --flycast is given; the first differing strip, vertex and fields are named.
"""
import argparse
import pathlib
import struct
import sys
from collections import Counter
from difflib import SequenceMatcher

PARA_SPRITE, PCW_TEXTURE, PCW_VOLUME, PCW_GOURAUD = 5, 0x8, 0x40, 0x2
LIST_OPAQUE, MODVOL_LISTS = 0, (1, 3)
ISP_CULL_BIT, VOLUME_LAST = 0x08000000, 0x04000000
TSP_BLEND, TCW_ADDR = 0xfc000000, 0x001fffff
KIND_SPRITE = 3


def parse_seq(tokens):
    """[(header, vertex count)] from "h pcw isp tsp tcw n n h ..." tokens."""
    out, hdr, i = [], (0, 0, 0, 0), 0
    while i < len(tokens):
        if tokens[i] == "h":
            hdr = tuple(int(x, 16) for x in tokens[i + 1:i + 5])
            i += 5
        else:
            out.append((hdr, int(tokens[i])))
            i += 1
    return out


def norm(tokens, lst, emu):
    """[(header as compared, count, texture address or None)] of one list."""
    out = []
    for (pcw, isp, tsp, tcw), n in parse_seq(tokens):
        addr = None
        if lst in MODVOL_LISTS:
            key = (0, isp | (VOLUME_LAST if not emu and pcw & PCW_VOLUME else 0), 0, 0)
        else:
            if emu and pcw >> 29 == PARA_SPRITE:
                isp ^= ISP_CULL_BIT
            if lst == LIST_OPAQUE:
                tsp &= ~TSP_BLEND
            if pcw & PCW_TEXTURE:
                addr, tcw = (tcw & TCW_ADDR) << 3, tcw & ~TCW_ADDR
            else:
                tcw = 0  # not read: what the game left there
            key = (pcw, isp, tsp, tcw)
        out.append((key, n, addr))
    return out


class List(dict):
    """One list of a render as read: "seq" is parsed when asked and not kept (a long recording's lists parsed at once
    take over a gigabyte); "sum" the extents; "dig" the vertex digest, if the line had one."""
    def __missing__(self, key):
        if key != "seq":
            raise KeyError(key)
        return norm(self["tok"].split(), self["lst"], self["emu"])


def read_lists(line, lists, emu):
    """An "L" line into lists; a "D" line (the list's digest) or "H" line (its strips' hashes) onto its list."""
    p = line.split(None, 7)
    lst = int(p[1])
    if p[0] == "D":
        if lst in lists:
            lists[lst]["dig"] = tuple(line.split()[2:6])
        return
    if p[0] == "H":
        if lst in lists:
            lists[lst]["hs"] = (line.split(None, 2) + [""])[2]
        return
    lists[lst] = List(tok=p[7] if len(p) > 7 else "", lst=lst, emu=emu, sum=tuple(float(x) for x in p[4:7]))


def undummy(e, p, lst):
    """e without the list's first parameter's second copy (the store-queue resend), if p has one copy; and whether."""
    if not e or not p or e[0][0] != p[0][0]:
        return e, False
    if lst in MODVOL_LISTS and e[0][1] == p[0][1] + 1:
        return [(e[0][0], p[0][1], e[0][2])] + e[1:], True
    if len(e) > 1 and e[1] == e[0] and e[0][0][0] >> 29 == PARA_SPRITE and (len(p) < 2 or p[1] != p[0]):
        return e[:1] + e[2:], True
    return e, False


def diff(er, pr, amap=None, geom=(1e-3, 1e-2)):
    """(None or (list, what, strip, emulator count, port count), known): the first difference of a render's lists.
    amap gets the emulator's texture address -> the port's, from strips that match. A list both sides digested
    differs on its digest after its headers, counts and extents."""
    known, (rel, ab) = False, geom
    for lst in sorted(set(er) | set(pr)):
        e, p = er.get(lst), pr.get(lst)
        if e is None or p is None:
            return (lst, "the list only on the " + ("port" if e is None else "emulator"), None, None, None), known
        ps = p["seq"]
        es, k = undummy(e["seq"], ps, lst)
        known |= k
        for i, (a, b) in enumerate(zip(es, ps)):
            if a[0] != b[0]:
                return (lst, "header " + " ".join(f"{x:x}" for x in a[0]) + " / port "
                        + " ".join(f"{x:x}" for x in b[0]), i, a[1], b[1]), known
            if a[1] != b[1]:
                return (lst, "vertex count", i, a[1], b[1]), known
            if amap is not None and a[2] is not None:
                amap[a[2]] = b[2]
        if len(es) != len(ps):
            return (lst, f"strips {len(es)} / port {len(ps)}", min(len(es), len(ps)), None, None), known
        if not all(abs(x - y) <= ab + rel * max(abs(x), abs(y)) for x, y in zip(e["sum"], p["sum"])):
            return (lst, "extents " + " ".join(f"{x:.6g}" for x in e["sum"]) + " / port "
                    + " ".join(f"{x:.6g}" for x in p["sum"]), None, None, None), known
        de, dp = e.get("dig"), p.get("dig")
        if de and dp and ((de[0], de[3]) != (dp[0], dp[2]) if k and lst not in MODVOL_LISTS else de[1] != dp[1]):
            return (lst, "vertex digest", None, None, None), known
    return None, known


def has_digests(path, probe=1 << 24):
    """True when the hw file (hw_emu.txt) has D lines in its first 16 MB: a recording made with digests."""
    path = pathlib.Path(path)
    if not path.exists():
        return False
    with open(path, "rb") as f:
        return b"\nD " in f.read(probe)


def strips(lst_entry, seq):
    """[(header key, count, hash or None)] of a list's strips (hash: its H line's, when the counts agree)."""
    hs = lst_entry.get("hs", "").split()
    hs = hs if len(hs) == len(seq) else [None] * len(seq)
    return [(a[0], a[1], h) for a, h in zip(seq, hs)]


def list_strips(e, p, lst):
    """The emulator's and the port's strips of one list, the emulator's known resend dropped."""
    es, k = undummy(e["seq"], p["seq"], lst)
    eh = e.get("hs", "").split()
    if k and lst not in MODVOL_LISTS and len(eh) == len(es) + 1:
        e = {"hs": " ".join(eh[:1] + eh[2:])}
    a, b = strips(e, es), strips(p, p["seq"])
    if (a and a[0][2] is None) != (b and b[0][2] is None):  # one side without strip hashes: neither's
        a, b = [x[:2] + (None,) for x in a], [x[:2] + (None,) for x in b]
    return a, b


def score(er, pr):
    """How far a port scene is from a render: the strips not shared (by header, count and hash), any order."""
    n = 0  # a list on one side only outweighs any strips
    for lst in set(er) | set(pr):
        if lst not in er or lst not in pr:
            n += 1 << 20
            continue
        a, b = list_strips(er[lst], pr[lst], lst)
        common = sum((Counter(a) & Counter(b)).values())
        n += len(a) + len(b) - 2 * common
    return n


def strip_diff(e, p, lst, old=False):
    """[(emulator strip, port strip, what)] of one list's strips that differ once the two are aligned (difflib): what
    is "header", "vertex count", "fields" (the vertex digest), or the side a strip is only on (its index None). With
    old (a recording from before the off-screen mask), a strip the port marks off the screen ("o": no pixel drawn)
    whose hash differs is "off the screen", not a difference."""
    a, b = list_strips(e, p, lst)
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        for d in range(max(i2 - i1, j2 - j1) if tag != "equal" else 0):
            i, j = (i1 + d if i1 + d < i2 else None), (j1 + d if j1 + d < j2 else None)
            what = ("only on the port" if i is None else "only in Flycast" if j is None else "header"
                    if a[i][0] != b[j][0] else "vertex count" if a[i][1] != b[j][1] else "off the screen"
                    if old and (b[j][2] or "").endswith("o") else "fields")
            out.append((i, j, what))
    if not out and diff({lst: e}, {lst: p}, geom=(1.0, float("inf")))[0]:  # no strip hashes (or not one per strip)
        out.append((None, None, "vertex digest"))
    return out


def check(hw_emu, hw_port, frames, out=None):
    """(rc, lines): the port's display lists against the recording's (rc 0 equal, 4 divergent, None not compared),
    renders paired as hw_check pairs them (the nearest scene, by score, if none matches). out gets a line per
    divergent list: port frame, emulator frame, render, list, strips differing, emulator:port:what of the first 8."""
    import hw_check as H
    if not has_digests(hw_emu):
        return None, ["display lists: not compared (the recording has no vertex digests: RAM only)"]
    if not pathlib.Path(hw_port).exists():
        return None, [f"display lists: not compared (no port lists in {hw_port})"]
    with open(hw_emu, "rb") as fh:
        old = not fh.read(64).startswith(b"X offscreen")
    renders, unlabelled = H.emu_renders(frames, *H.read_emu_hw(hw_emu))
    rr, pairs = H.pair(renders, H.read_port_hw(hw_port)[0], score=score)
    lags = ", ".join(f"{d:+d}: {n}" for d, n in sorted(rr["lags"].items()))
    whats, bad, first, f_of, offs = Counter(), 0, None, {id(r): f for f, r in renders}, 0
    sink = open(out, "w", encoding="ascii") if out else None
    for g, r, lists in pairs:
        any_bad = False
        for lst in sorted(set(r[2]) | set(lists)):
            if lst not in r[2] or lst not in lists:
                d = [(None, None, "the list only on the " + ("port" if lst in lists else "emulator"))]
            else:
                d = strip_diff(r[2][lst], lists[lst], lst, old)
                offs += sum(w == "off the screen" for _, _, w in d)
                d = [x for x in d if x[2] != "off the screen"]
            if not d:
                continue
            any_bad = True
            whats.update(w for _, _, w in d)
            first = first or (g, f_of[id(r)], r[4] if len(r) > 4 else None, lst, d)
            if sink:
                sink.write(f"{g} {f_of[id(r)]} {r[4] if len(r) > 4 else '-'} {lst} {len(d)} "
                           + " ".join(f"{i}:{j}:{w.replace(' ', '_')}" for i, j, w in d[:8]) + "\n")
        bad += any_bad
    sink and sink.close()
    lines = [f"display lists: {rr['paired']} renders paired (exactly at lags {lags or '-'}), {rr['emu_only']} "
             f"emulator only, {unlabelled} with no frame label; {rr['known']} with the known list-start resend; "
             f"divergent: {bad}" + (f"; strips differing: " + ", ".join(f"{w} {n}" for w, n in whats.most_common())
                                    if whats else "")
             + (f"; {offs} strips off the screen differ (no pixel drawn; the recording predates their mark)"
                if offs else "")]
    if not first:
        return 0, lines + ["display lists: every paired render equal on the fields the hardware uses"]
    g, f, render, lst, d = first
    lines += [f"FIRST DIVERGENT DISPLAY LIST: port frame {g} (emulator frame {f}, render {render}) list {lst}: "
              f"{len(d)} strips differ, the first " + (f"emulator strip {d[0][0]} / port strip {d[0][1]}: {d[0][2]}"),
              f"  detail: tools/ta_digest.py detail --frame {g} --render {render} (with the run's --game, --rec, "
              "--build-dir and the recording's hw_check emu arguments)"]
    return 4, lines


def read_dump(path, render=None):
    """[(list, strip, kind, count, pcw, tsp, fields)]: a --hw-dump file, or render's part of an LS_HW_DUMP one; None
    if the file has no END (or not that render)."""
    out, done, on = [], False, render is None
    with open(path, encoding="ascii", errors="replace") as f:
        for p in (line.split() for line in f):
            if p and p[0] == "R":
                on = render is None or int(p[1]) == render
                out = [] if render is not None and on else out
            elif p and p[0] == "S" and on:
                out.append(tuple(int(x) for x in p[1:5]) + (int(p[5], 16), int(p[6], 16), []))
            elif p and p[0] == "V" and on and out:
                out[-1][6].extend(int(x, 16) for x in p[1:])
            elif p and p[0] == "END":
                done = True
    return out if done and (out or render is None) else None


def field_names(kind, count, pcw):
    """The names of a strip's fields in the digest's order (ta_digest.c, hw_emu.lua)."""
    tex = bool(pcw & PCW_TEXTURE)
    xyz = lambda v, z=True: [f"{v}.{c}" for c in ("xyz" if z else "xy")] + ([f"{v}.u", f"{v}.v"] if tex and z else [])
    if kind == KIND_SPRITE:
        return xyz("a") + xyz("b") + xyz("c") + xyz("d", False) + ["base", "offset"]
    if pcw & PCW_VOLUME:
        return [f"{i}.{c}" for i in range(count) for c in "xyz"]
    out = []
    for i in range(count):
        out += xyz(str(i)) + ([f"{i}.base", f"{i}.offset"] if pcw & PCW_GOURAUD or i >= 2 else [])
    return out


def show(name, w):
    if name.endswith(("base", "offset")):
        return f"{w:08x}"
    return f"{w:08x}({struct.unpack('<f', struct.pack('<I', w))[0]:.6g})"


def compare_dumps(port, emu, limit=12):
    """Lines: per list the strips differing once aligned on count and fields (headers are hw_check's), then the
    first differing in its fields, field by field, and the first in its count or one-sided, with its x y."""
    lines, firsts = [], {}
    for lst in sorted({s[0] for s in port} | {s[0] for s in emu}):
        ps, es = [s for s in port if s[0] == lst], [s for s in emu if s[0] == lst]
        if len(es) == len(ps) + 1 and len(es) > 1 and es[0][6] == es[1][6]:
            es = es[:1] + es[2:]  # the known resend
        key = lambda s: (s[3], tuple(s[6]))
        n = 0
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, [key(s) for s in es], [key(s) for s in ps],
                                                   autojunk=False).get_opcodes():
            for d in range(max(i2 - i1, j2 - j1) if tag != "equal" else 0):
                e, p = (es[i1 + d] if i1 + d < i2 else None), (ps[j1 + d] if j1 + d < j2 else None)
                n += 1
                kind = "fields" if e and p and e[3] == p[3] else "count"
                firsts.setdefault(kind, (e, p))
        lines.append(f"list {lst}: port {len(ps)} strips, emulator {len(es)}; {n} differ once aligned")
    if not firsts:
        return lines + ["no strip differs on the masked fields"]
    for kind in ("fields", "count"):
        if kind not in firsts:
            continue
        e, p = firsts[kind]
        side = lambda s, who: (f"{who} strip {s[1]} (kind {s[2]}, {s[3]} vertices, pcw {s[4]:08x} tsp {s[5]:08x})"
                               if s else f"no {who} strip")
        lines.append(f"first {'field' if kind == 'fields' else 'count or one-sided'} difference: list "
                     f"{(e or p)[0]}: {side(p, 'port')}, {side(e, 'emulator')}")
        if kind == "fields":
            names, n = field_names(p[2], p[3], p[4]), 0
            for i, (a, b) in enumerate(zip(p[6], e[6])):
                if a != b and n < limit:
                    name = names[i] if i < len(names) else f"#{i}"
                    lines.append(f"    {name}: port {show(name, a)} emulator {show(name, b)}")
                    n += 1
        else:
            for s, who in ((p, "port"), (e, "emulator")):
                if s:
                    names = field_names(s[2], s[3], s[4])
                    xy = [show(nm, w) for nm, w in zip(names, s[6]) if nm.endswith((".x", ".y"))]
                    lines.append(f"    {who} x y: " + " ".join(xy[:16]))
    return lines


def main(argv=None):
    import hw_check as H
    import lockstep as L
    from lockstep_frames import build_frames, drive_file, read_frames, read_marks
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["detail"])
    ap.add_argument("--game", required=True)
    ap.add_argument("--rec", required=True, help="the recording's folder (emu.txt, hw_emu.txt)")
    ap.add_argument("--build-dir", default="x86-agent")
    ap.add_argument("--frame", type=int, required=True, help="the port scene's frame (lockstep.py's port frame)")
    ap.add_argument("--render", type=int, required=True, help="its render generation (hw_emu.txt's R line)")
    ap.add_argument("--seconds", type=int, default=900, help="Flycast's wall time limit to reach the render")
    a, emu_args = ap.parse_known_args(argv)
    game, rec = pathlib.Path(a.game).resolve(), pathlib.Path(a.rec).resolve()
    out = rec / f"detail_{a.render}"
    out.mkdir(exist_ok=True)
    spec = game / "lockstep.spec"
    emu = read_frames(rec / "emu.txt")
    frames = build_frames(emu, read_marks(rec / "emu.txt"))
    drive_file(L.parse_spec(spec), emu, frames, out / "drive.txt")
    pdump, edump, render = out / f"port_{a.build_dir}.dump", rec / "hw_emu.txt.dump", a.render
    if not (edump.exists() and read_dump(edump, render) is not None):  # not dumped with the recording
        edump, render = out / "hw_emu.txt.dump", None
    card = [x for x in emu_args if x != "--"]
    vmu = card[card.index("--vmu") + 1] if "--vmu" in card else None
    import emu_card
    rc, wall, log = L.port_run(game, a.build_dir, spec, out / "drive.txt", out / f"port_{a.build_dir}.txt",
                               int(emu[-1][1] / 59.94) + 10, (emu_card.port_args(vmu, out) if vmu else [])
                               + ["--hw-dump", f"{a.frame}:{pdump}"])
    print(f"port: exit {rc}, {wall:.0f} s; log {log}")
    if render is None and not (edump.exists() and read_dump(edump) is not None):
        exe = None if "--flycast" in card else L.recording_flycast(rec / "emu.txt")
        if exe:  # the replay plays the recording's own Flycast (its lockstep hooks), not the toolcfg default
            card += ["--flycast", exe]
        elif "--flycast" not in card:
            print("the recording does not name its Flycast: the toolcfg default replays it (--flycast: one with hooks)")
        card = (["--cable", c] if (c := L.recording_flycast(rec / "emu.txt", "LS_CABLE")) else []) + card  # its cable
        H.main(["emu", "--game", str(game), "--out", str(out), "--seconds", str(a.seconds),
                "--hw-dump", str(a.render), "--hw-dump-stop"] + card)
    p, e = read_dump(pdump) if pdump.exists() else None, read_dump(edump, render) if edump.exists() else None
    if p is None or e is None:
        print(f"no dump: port {'ok' if p is not None else 'missing'}, emulator {'ok' if e is not None else 'missing'}")
        return 2
    print("\n".join(compare_dumps(p, e)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
