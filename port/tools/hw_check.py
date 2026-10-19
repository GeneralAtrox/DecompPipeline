"""Hardware check: what the port hands the PowerVR, frame by frame, against Flycast from the same input.

The lockstep check (tools/lockstep.py) pairs the port's frames with the emulator's. This adds each render's display
lists and the textures they sample, from both sides (tools/hw_emu.lua, runtime/hw_check.c):
  - per list: each strip's header (pcw isp tsp tcw) and vertex count, exactly; the polygon strips' extents summed,
    within GEOM_REL;
  - per texture the emulator samples (each time its bytes change): their SHA-256 against the port's bytes for the
    same strip's texture at the same frame.
A render is paired with the port scene that matches it within LAGS of its frame: the TA takes a frame's lists one or
more game frames after the game wrote them, and the lag changes at loads. With no match, the scene at the last lag is
its pair and the first difference is reported.

Flycast's decoder rewrites some words (core/hw/pvr/ta_vtx.cpp), so these are compared as it has them: a sprite's ISP
culling bit (flipped), the first pass's opaque blend (one, zero), a modifier volume header (its PCW's volume bit in the
ISP, the other words 0). An untextured strip's TCW is not read. A texture's place in texture memory (the TCW's
address) is the port's allocator's, not Kamui's: it pairs the textures, and is not compared.

The console's TA gets the vertex buffer through the store queues; the port has no store queue model (r14 defect class
c), so a block the game prefs without writing it all is stale RAM in the port and the queue's last contents on the
console. In check runs hw_check.c models it as the block 0x40 back (the same queue), word by word, and counts it;
what that misses (a queue last written elsewhere) stays a difference. A list-start dummy the console gets twice is
counted apart if the model leaves it single.

--baseline FILE (a reference build's OUT/hw_port_BUILD.txt) counts a divergent render whose port scene the reference
has too as shared: a probe build is judged on what the reference build does not do.

It names the first divergent render and texture, and the function that wrote it: the port runs again to find the
guest word of the first divergent strip (--hw-strip) and logs each write to it in that frame (--hw-watch-ram, debug
registers); for a texture, each texture memory write that covers it up to that frame (--hw-watch-vram). The writers
are named from the build's port.map (lockstep_watch.py).

    python tools/hw_check.py run --game DIR --input START@900,A@1500+10 --seconds 900
        [--out DIR] [--build-dir x86-agent] [--no-watch] [--baseline FILE]

`run` records the emulator (OUT/emu.txt with lockstep's frames and draws, OUT/hw_emu.txt; an existing recording is
reused: the emulator is deterministic for the same input), runs the port twice (the lists, then the textures they
pair; each run ends with the emulator's frames) and compares. `emu` only records. The recording serves
tools/lockstep.py too (--emu OUT/emu.txt).
"""
import argparse
import bisect
import hashlib
import pathlib
import re
import struct
import sys

import lockstep as L
from lockstep_frames import build_frames, draws_path, read_draws
from lockstep_watch import report_hits

GEOM_REL, GEOM_ABS = 1e-3, 1e-2  # summed extents this close are float rounding (both sides sum in double)
LAGS = range(-4, 2)              # port frame - emulator frame of a render's pair
PARA_SPRITE, PCW_TEXTURE, PCW_VOLUME = 5, 0x8, 0x40
LIST_OPAQUE, MODVOL_LISTS = 0, (1, 3)
ISP_CULL_BIT, VOLUME_LAST = 0x08000000, 0x04000000
TSP_BLEND, TCW_ADDR = 0xfc000000, 0x001fffff


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


def read_lists(p, lists, emu):
    lst = int(p[1])
    lists[lst] = {"seq": norm(p[7:], lst, emu), "sum": tuple(float(x) for x in p[4:7])}


def read_emu_hw(path):
    """[(context, tick, lists, textures)] and {context: [(tick, label)] by tick}: hw_emu.lua's renders and the labels
    of the contexts as the TA took their first block."""
    renders, labels = [], {}
    for line in open(path, encoding="ascii", errors="replace"):
        p = line.split()
        if not p:
            continue
        if p[0] == "C" and len(p) == 4:
            labels.setdefault(int(p[1], 16), []).append((int(p[2]), int(p[3], 16)))
        elif p[0] == "R" and len(p) == 4:
            renders.append((int(p[1], 16), int(p[2]), {}, []))
        elif p[0] == "L" and renders:
            read_lists(p, renders[-1][2], True)
        elif p[0] == "T" and renders and len(p) == 4:
            renders[-1][3].append((int(p[1], 16), int(p[2], 16), p[3]))
    for v in labels.values():
        v.sort()
    return renders, labels


def read_port_hw(path):
    """[(paired emulator frame, lists)] and [blocks the store queue model resent]: hw_check.c's scenes."""
    scenes, resent = [], []
    for line in open(path, encoding="ascii", errors="replace"):
        p = line.split()
        if p and p[0] == "R" and len(p) == 3:
            scenes.append((int(p[1]), {}))
            resent.append(int(p[2]))
        elif p and p[0] == "L" and scenes:
            read_lists(p, scenes[-1][1], False)
    return scenes, resent


def emu_renders(frames, renders, labels):
    """[(frame index, render)] in order: a render's frame is the one whose draw has its context's label (the
    context's last first block before the render)."""
    at = {}
    for n, fr in enumerate(frames):
        if fr["draw"] is not None:
            at.setdefault(fr["draw"][0], n)
    out, unlabelled = [], 0
    for r in renders:
        v = labels.get(r[0], [])
        i = bisect.bisect_left(v, (r[1], -1)) - 1
        n = at.get(v[i][1]) if i >= 0 else None
        if n is None:
            unlabelled += 1
        else:
            out.append((n, r))
    return out, unlabelled


def undummy(e, p, lst):
    """e without the list's first parameter's second copy (the store-queue resend), if p has one copy; and whether."""
    if not e or not p or e[0][0] != p[0][0]:
        return e, False
    if lst in MODVOL_LISTS and e[0][1] == p[0][1] + 1:
        return [(e[0][0], p[0][1], e[0][2])] + e[1:], True
    if len(e) > 1 and e[1] == e[0] and e[0][0][0] >> 29 == PARA_SPRITE and (len(p) < 2 or p[1] != p[0]):
        return e[:1] + e[2:], True
    return e, False


def diff(er, pr, amap=None):
    """(None or (list, what, strip, emulator count, port count), known): the first difference of a render's lists.
    amap gets the emulator's texture address -> the port's, from strips that match."""
    known = False
    for lst in sorted(set(er) | set(pr)):
        e, p = er.get(lst), pr.get(lst)
        if e is None or p is None:
            return (lst, "the list only on the " + ("port" if e is None else "emulator"), None, None, None), known
        es, k = undummy(e["seq"], p["seq"], lst)
        known |= k
        for i, (a, b) in enumerate(zip(es, p["seq"])):
            if a[0] != b[0]:
                return (lst, "header " + " ".join(f"{x:x}" for x in a[0]) + " / port "
                        + " ".join(f"{x:x}" for x in b[0]), i, a[1], b[1]), known
            if a[1] != b[1]:
                return (lst, "vertex count", i, a[1], b[1]), known
            if amap is not None and a[2] is not None:
                amap[a[2]] = b[2]
        if len(es) != len(p["seq"]):
            return (lst, f"strips {len(es)} / port {len(p['seq'])}", min(len(es), len(p["seq"])), None, None), known
        if not all(abs(x - y) <= GEOM_ABS + GEOM_REL * max(abs(x), abs(y)) for x, y in zip(e["sum"], p["sum"])):
            return (lst, "extents " + " ".join(f"{x:.6g}" for x in e["sum"]) + " / port "
                    + " ".join(f"{x:.6g}" for x in p["sum"]), None, None, None), known
    return None, known


def pair(renders, scenes, baseline=None):
    """Each emulator render with a port scene (see LAGS). Returns the result and [(port frame, render, port lists)]
    of the pairs. With baseline (a reference build's scenes), a divergent render whose port scene the reference has
    too is counted as shared, not divergent."""
    res = {"paired": 0, "bad": 0, "known": 0, "emu_only": 0, "first": None, "bad_frames": [], "lags": {}, "shared": 0}
    at = {}
    for i, (g, _) in enumerate(scenes):
        at.setdefault(g, []).append(i)
    last, lag, pairs = -1, 0, []
    for f, r in renders:
        hit = None
        for d in sorted(LAGS, key=lambda x: (abs(x - lag), x)):
            for i in at.get(f + d, []):
                if i > last and diff(r[2], scenes[i][1])[0] is None:
                    hit = (i, d)
                    break
            if hit:
                break
        if hit:
            last, lag = hit
            i = last
            res["lags"][lag] = res["lags"].get(lag, 0) + 1
        else:  # the scene at the last lag, for the report; a later render may still match past it
            i = next((i for i in at.get(f + lag, []) if i > last), None)
            if i is None:
                res["emu_only"] += 1
                continue
            g = scenes[i][0]
            d = diff(r[2], scenes[i][1])[0]
            if baseline is not None and i < len(baseline) and baseline[i] == scenes[i]:
                res["shared"] += 1
                d = None
        if not hit and d is not None:
            res["bad"] += 1
            res["bad_frames"].append(g)
            res["first"] = res["first"] or (g, f) + d
        res["paired"] += 1
        g, lists = scenes[i]
        res["known"] += diff(r[2], lists)[1]
        pairs.append((g, r, lists))
    res["port_only"] = len(scenes) - sum(res["lags"].values())  # scenes no render matched
    return res, pairs


def tex_requests(pairs, path):
    """The port's --hw-tex file: each texture line of each paired render, at the port's address (paired by strip).
    Returns [(frame, port address, size, sha, emulator address)] in the file's order."""
    reqs = []
    for g, r, lists in pairs:
        amap = {}
        diff(r[2], lists, amap)
        reqs += [(g, amap[a], s, h, a) for a, s, h in r[3] if amap.get(a) is not None]
    reqs.sort()
    with open(path, "w", encoding="ascii") as f:
        for g, pa, s, _, _ in reqs:
            f.write(f"{g} {pa:x} {s:x}\n")
    return reqs


def compare_textures(reqs, bin_path):
    """{checked, bad, first, missing}: the port's dumped bytes (hw_check.c) against the emulator's SHA-256."""
    res = {"checked": 0, "bad": 0, "first": None, "missing": 0}
    data = pathlib.Path(bin_path).read_bytes() if pathlib.Path(bin_path).exists() else b""
    got, at = {}, 0
    while at + 12 <= len(data):
        addr, size, frame = struct.unpack_from("<III", data, at)
        got.setdefault((frame, addr, size), hashlib.sha256(data[at + 12:at + 12 + size]).hexdigest())
        at += 12 + size
    for g, pa, s, h, ea in reqs:
        mine = got.get((g, pa, s))
        if mine is None:
            res["missing"] += 1
            continue
        res["checked"] += 1
        if mine != h:
            res["bad"] += 1
            res["first"] = res["first"] or (g, pa, s, ea)
    return res


def watch_strip(run, map_path, bad):
    """Names the writer of the first divergent strip: --hw-strip finds its word, --hw-watch-ram logs the writes."""
    g, _, lst, what, strip, ce, cp = bad
    if strip is None:
        print("  no single strip to watch (a list or extent difference)")
        return
    counts = [x for x in (ce, cp) if x is not None]
    vertex = max(0, min(counts) - 1) if counts and what == "vertex count" else -1  # else the strip's header
    if what.startswith("strips"):
        strip = max(0, strip - 1)  # the last strip both have
    rc, _, log = run(["--hw-strip", f"{lst}:{strip}:{vertex}@{g}"])
    m = re.search(r"hw check: frame \d+ list \d+ strip \d+ vertex -?\d+ at ([0-9a-f]{8})",
                  pathlib.Path(log).read_text(encoding="ascii", errors="replace"))
    if not m:
        print(f"  the port's run found no strip {strip} in list {lst} at frame {g} (exit {rc})")
        return
    print(f"  watching {m.group(1)} (list {lst} strip {strip} vertex {vertex}) in port frame {g}:")
    run(["--hw-watch-ram", f"{m.group(1)}@{g}"])
    report_hits(log, map_path)


def watch_texture(run, map_path, bad):
    g, addr, size, _ = bad
    print(f"  texture memory writes covering {addr:06x}+{size:x} through port frame {g}:")
    rc, _, log = run(["--hw-watch-vram", f"{addr:x}:{size:x}@{g}"])
    report_hits(log, map_path)


def report(rr, tr, unlabelled):
    lags = ", ".join(f"{d:+d}: {n}" for d, n in sorted(rr["lags"].items()))
    print(f"renders: {rr['paired']} paired (lags {lags or '-'}), {rr['emu_only']} emulator only, {rr['port_only']} "
          f"port only, {unlabelled} with no frame label; {rr['known']} with the known list-start resend")
    if "resent" in rr:
        print(f"  store queue resends modelled (r14 class c): {rr['resent'][0]} blocks in {rr['resent'][1]} scenes")
    shared = f" ({rr['shared']} more shared with the baseline)" if rr["shared"] else ""
    print(f"  divergent renders: {rr['bad']}{shared}")
    if rr["first"]:
        g, f, lst, what, strip, ce, cp = rr["first"]
        print(f"  first: port frame {g} (emulator frame {f}) list {lst}: {what}"
              + (f" at strip {strip}" if strip is not None else "")
              + (f" (emulator {ce}, port {cp})" if ce is not None else ""))
        b = rr["bad_frames"]
        runs = sum(1 for i, x in enumerate(b) if i == 0 or x > b[i - 1] + 2)
        print(f"  {len(b)} divergent renders in {runs} run(s); last port frame {b[-1]}")
    if tr is not None:
        print(f"textures: {tr['checked']} checked, {tr['bad']} divergent, {tr['missing']} not dumped")
        if tr["first"]:
            g, pa, s, ea = tr["first"]
            print(f"  first: port frame {g} address {pa:06x} size {s:x} (the emulator's {ea:06x})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["run", "emu", "port"])
    ap.add_argument("--game", required=True)
    ap.add_argument("--spec", default=None, help="default: GAME/lockstep.spec")
    ap.add_argument("--input", default="")
    ap.add_argument("--seconds", type=int, default=600, help="the emulator's wall time")
    ap.add_argument("--out", default=None, help="default: GAME/build/hw_check")
    ap.add_argument("--build-dir", default="x86-agent")
    ap.add_argument("--flycast", default=None)
    ap.add_argument("--no-watch", action="store_true", help="do not run the port again to name the writers")
    ap.add_argument("--baseline", default=None, help="a reference build's hw_port file: its divergences are shared")
    a, extra = ap.parse_known_args(argv)
    game = pathlib.Path(a.game).resolve()
    spec = pathlib.Path(a.spec or game / "lockstep.spec").resolve()
    out = pathlib.Path(a.out or game / "build" / "hw_check").resolve()
    out.mkdir(parents=True, exist_ok=True)
    emu_path, hw_emu = out / "emu.txt", out / "hw_emu.txt"
    if a.step == "emu" or not hw_emu.exists():
        wall = L.emu_run(game, spec, emu_path, a.seconds, L.parse_input(a.input), a.flycast, hw=hw_emu)
        emu = L.read_frames(emu_path)
        print(f"emulator: {len(emu)} frames, {(emu[-1][1] / 59.94) if emu else 0:.0f} s played in {wall:.0f} s")
        if a.step == "emu":
            return 0
    emu = L.read_frames(emu_path)
    frames = build_frames(emu, read_draws(draws_path(emu_path)))
    renders, unlabelled = emu_renders(frames, *read_emu_hw(hw_emu))
    played = int(emu[-1][1] / 59.94) + 10 if emu else a.seconds
    hw_port, tex_file = out / f"hw_port_{a.build_dir}.txt", out / f"hw_tex_{a.build_dir}.txt"
    drive, trace = out / "drive.txt", out / f"port_{a.build_dir}.txt"
    extra = [x for x in extra if x != "--"]

    def run(more):
        return L.port_run(game, a.build_dir, spec, drive, trace, played, extra + more)

    L.drive_file(items=L.parse_spec(spec), emu=emu, frames=frames, out=drive)
    rc, wall, log = run(["--hw-out", str(hw_port)])
    print(f"port: exit {rc} ({L.exit_line(log)}), {wall:.0f} s; log {log}")
    scenes, resent = read_port_hw(hw_port)
    rr, pairs = pair(renders, scenes, a.baseline and read_port_hw(a.baseline)[0])
    rr["resent"] = (sum(resent), sum(1 for n in resent if n))
    reqs = tex_requests(pairs, tex_file)
    rc, wall, _ = run(["--hw-tex", str(tex_file)])
    print(f"port (textures): exit {rc}, {wall:.0f} s")
    tr = compare_textures(reqs, f"{tex_file}.bin")
    report(rr, tr, unlabelled)
    if not a.no_watch:
        map_path = game / "build" / a.build_dir / "port.map"
        if rr["first"]:
            print("divergent render, the writer:")
            watch_strip(run, map_path, rr["first"])
        if tr["first"]:
            print("divergent texture, the writer:")
            watch_texture(run, map_path, tr["first"])
    return 1 if rr["bad"] or tr["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
