"""Match the compiled Sonic SDK functions against a game's catalog functions, relocations masked.

usage: python smatch.py TABLE.json CONFIGDIR OUT.json --ws WS [--names DIR] [--fid tc_fid.tsv] --dp DP_CHECKOUT
A row of TABLE is an exported symbol's span; statics are split out with the repo's symbols/splits when the object's
code section has the same size as the Sonic image's. An exact match: every unmasked byte equal from the function's
entry to its end, every masked word an address, each symbol one value within the function.
Nearest existing: xproject.py match - compares two game images; this compares compiled objects with relocations masked.
"""
import argparse
import collections
import json
import pathlib
import re
import struct
import sys

IMAGE_BASE = 0x8C010000
MIN_UNMASKED = 16


def lib_of(src):
    p = src.split("/")
    if p[0] == "loader":
        return "loader-sdk"
    if p[0] == "modules":
        return "movie-sdk"
    if p[1] == "ninja":
        return "kamui" if p[-1].endswith("_kamui_lib") or p[-1].startswith("internal_kamui") else "ninja"
    if p[1] == "shinobi":
        return "shinobi-sound" if p[-1].startswith("sd_") else "shinobi"
    return p[1]


def load_splits(cfg):
    """{source without extension: (image, start, end, section)} of each source's code section, and {image: sorted symbols}."""
    splits, syms = {}, {}
    for img in ("1st_read", "movie", "loader"):
        d = pathlib.Path(cfg) / img
        s = []
        for ln in (d / "symbols.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r"0x([0-9a-f]+)\s+(\S+)", ln)
            if m:
                s.append((int(m.group(1), 16), m.group(2)))
        syms[img] = sorted(s)
        for ln in (d / "splits.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r"(\S+)\.(?:c|src)\s+(P\w*)\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)", ln)
            if m and m.group(1) not in splits:
                splits[m.group(1)] = (img, int(m.group(3), 16), int(m.group(4), 16), m.group(2))
    return splits, syms


def split_table(rows, splits, syms):
    """One entry per function: the object's section re-cut at every Sonic symbol when the sizes agree."""
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["source"], r["section"])].append(r)
    out, stats = [], collections.Counter()
    for (src, sec), rs in by.items():
        rs.sort(key=lambda r: r["off"])
        body = bytes.fromhex("".join(r["hex"] for r in rs))
        rel = [[r["off"] + t[0], t[1], t[2], t[3]] for r in rs for t in r["rel"]]
        cuts = {r["off"]: r["name"].lstrip("_") for r in rs if r["name"] != "@start"}
        sp = splits.get(src)
        sized = sp and sp[2] - sp[1] == len(body)
        stats["objects"] += 1
        stats["bytes"] += len(body)
        if sp:
            stats["in_splits"] += 1
            stats["same_size"] += bool(sized)
            stats["same_size_bytes"] += len(body) if sized else 0
        if sized:
            for a, n in syms[sp[0]]:
                if sp[1] <= a < sp[2] and (a - sp[1]) not in cuts and (a - sp[1]) % 2 == 0:
                    cuts[a - sp[1]] = n
        if 0 not in cuts:
            cuts[0] = None
        offs = sorted(cuts)
        for i, o in enumerate(offs):
            e = offs[i + 1] if i + 1 < len(offs) else len(body)
            if cuts[o] is None or e - o < 2:
                continue
            out.append(dict(source=src, lib=lib_of(src), name=cuts[o], off=o, body=body[o:e],
                            rel=[(t[0] - o, t[2], t[3]) for t in rel if o <= t[0] < e], sized=bool(sized)))
    return out, stats


def trim(f):
    """Length without trailing alignment fill (nop or zero words past the last relocation)."""
    b, n = f["body"], len(f["body"])
    floor = max((t[0] + 4 for t in f["rel"]), default=2)
    while n - 2 >= floor and b[n - 2:n] in (b"\x09\x00", b"\x00\x09", b"\x00\x00"):
        n -= 2
    return n


def looks_addr(v):
    return (v >> 24) in (0x8C, 0x0C, 0xAC, 0xA0, 0x80, 0x00 + 0xFF, 0x05, 0xA5) and v != 0


def compare(f, data, a):
    """None, or {symbol+addend: word} when the bytes at address a equal f outside its relocations."""
    off = a - IMAGE_BASE
    n = f["n"]
    if off < 0 or off + n > len(data):
        return None
    pos = 0
    words = {}
    for t in sorted(f["rel"]):
        if t[0] + 4 > n:
            continue
        if data[off + pos:off + t[0]] != f["body"][pos:t[0]]:
            return None
        v = struct.unpack_from("<I", data, off + t[0])[0]
        if not looks_addr(v):
            return None
        k = (t[1], t[2])
        if words.setdefault(k, v) != v:
            return None
        pos = t[0] + 4
    if data[off + pos:off + n] != f["body"][pos:n]:
        return None
    return words


def project_names(ws, names_dir=None):
    d = pathlib.Path(names_dir) if names_dir else pathlib.Path(ws) / "readable" / "names"
    out = {}
    for p in sorted(d.glob("*.json")):
        for addr, e in json.loads(p.read_text(encoding="utf-8")).get("functions", {}).items():
            m = re.search(r"(\w+)\s*\(", e.get("proto", "") or "") or re.match(r"(\w+)", e.get("name", "") or "")
            if m:
                out[addr.lower()] = (m.group(1), p.stem)
    return out


def read_fid(path):
    out = {}
    if not path or not pathlib.Path(path).is_file():
        return out
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    for ln in lines[1:]:
        r = dict(zip(head, ln.split("\t")))
        if r.get("fid_names"):
            out[r["addr"].lower()] = (set(re.split(r"[|,; ]+", r["fid_names"])), r.get("unique"))
    return out


def placeholder(n):
    return bool(re.match(r"(Lib_|FUN_|sub_|fn_|func_|lib_)", n)) or bool(re.search(r"[0-9a-f]{8}$", n))


def near_search(game_funcs, data, sonic, near_min=0.8):
    """Most similar Sonic function for each game function that has no exact match: word 4-grams, then difflib."""
    sys.path.insert(0, DP)
    from core import xmatch
    prior, index = {}, collections.defaultdict(set)
    for i, f in enumerate(sonic):
        if f["n"] < 16:
            continue
        w = masked_words(f["body"][:code_end(f)])
        prior[i] = (tuple(w),)
        for s in xmatch.shingles(prior[i][0]):
            index[s].add(i)
    out = {}
    for a, nbytes in game_funcs:
        off = a - IMAGE_BASE
        if nbytes < 16 or off < 0 or off + nbytes > len(data):
            continue
        w = tuple(masked_words(data[off:off + nbytes]))
        votes = collections.Counter()
        for s in xmatch.shingles(w):
            for c in index.get(s, ()):
                votes[c] += 1
        if not votes:
            continue
        ranked = [c for c, _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
        best = xmatch.best_near(prior, ranked, w, near_min)
        if best:
            out[a] = best
    return out


def code_end(f):
    """The function's words before its pool: up to the last rts/rte/jmp's delay slot, else everything."""
    b, n = f["body"], f["n"]
    last = n
    first_rel = min((t[0] for t in f["rel"]), default=n)
    return min(last, first_rel)


def masked_words(b):
    """16-bit words with call/branch displacements and PC-relative displacements masked, as xmatch does for leaving ones."""
    out = []
    for i in range(0, len(b) - 1, 2):
        w = b[i] | b[i + 1] << 8
        top = w >> 12
        if top in (0xA, 0xB):
            w &= 0xF000
        elif top in (0x9, 0xD) or (w & 0xFF00) == 0xC700:
            w &= 0xFF00
        out.append(w)
    return out


DP = None  # the DecompPipeline checkout, from --dp


def main():
    global DP
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("config")
    ap.add_argument("out")
    ap.add_argument("--ws", required=True)
    ap.add_argument("--names")
    ap.add_argument("--fid")
    ap.add_argument("--dp", required=True)
    ap.add_argument("--no-near", action="store_true")
    a = ap.parse_args()
    DP = a.dp
    sys.path.insert(0, DP)
    ws = pathlib.Path(a.ws)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    data = (ws / proj["binary"]).resolve().read_bytes()
    cat = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]
    ranges = proj.get("game_ranges") or [[f"{IMAGE_BASE:08x}", proj["game_end"]]]
    ranges = [(int(x, 16), int(y, 16)) for x, y in ranges]
    extra = proj.get("extra_functions") or []
    if isinstance(extra, str):
        extra = [ln.split("#")[0].strip() for ln in (ws / extra).read_text(encoding="utf-8").splitlines()]
    extra = {x.lower().removeprefix("0x") for x in extra if x}
    splits, syms = load_splits(a.config)
    sonic, stats, seen = [], {}, set()
    for tbl in a.table.split(","):
        rows = json.load(open(tbl, encoding="utf-8"))["rows"]
        got, st = split_table(rows, splits, syms)
        stats[pathlib.Path(tbl).stem] = dict(st)
        for f in got:
            k = (f["source"], f["name"], f["body"], tuple(t[0] for t in f["rel"]))
            if k not in seen:
                seen.add(k)
                f["compiler"] = pathlib.Path(tbl).stem.replace("table_", "")
                sonic.append(f)
    for f in sonic:
        f["n"] = trim(f)
        f["unmasked"] = f["n"] - 4 * sum(1 for t in f["rel"] if t[0] + 4 <= f["n"])

    entries = {int(f["addr"], 16): int(f["bytes"]) for f in cat}
    idx = collections.defaultdict(list)
    for e in entries:
        o = e - IMAGE_BASE
        if 0 <= o and o + 4 <= len(data):
            idx[data[o:o + 4]].append(e)
    names, fid = project_names(ws, a.names), read_fid(a.fid)

    hits = collections.defaultdict(list)  # game addr -> Sonic functions
    for i, f in enumerate(sonic):
        if f["n"] < 4 or any(t[0] < 4 for t in f["rel"]):
            continue
        for e in idx.get(f["body"][:4], ()):
            w = compare(f, data, e)
            if w is not None:
                hits[e].append((i, w))
    # symbol -> address votes over all exact matches
    votes = collections.defaultdict(collections.Counter)
    for e, hs in hits.items():
        for i, w in hs:
            if sonic[i]["unmasked"] >= MIN_UNMASKED:
                for k, v in w.items():
                    votes[k][v] += 1

    # object-run corroboration: another function of the same object matched here, at the same distance
    placed = collections.defaultdict(set)
    for e, hs in hits.items():
        for i, w in hs:
            if sonic[i]["unmasked"] >= MIN_UNMASKED:
                placed[(sonic[i]["source"], e - sonic[i]["off"])].add(e)
    named_elsewhere = collections.Counter(sonic[i]["name"] for e, hs in hits.items() for i, _ in hs)

    out, c = [], collections.Counter()
    for e, hs in sorted(hits.items()):
        addr = f"{e:08x}"
        lib_region = not any(lo <= e < hi for lo, hi in ranges) and addr not in extra
        distinct = sorted({sonic[i]["name"] for i, _ in hs})
        i, w = max(hs, key=lambda h: sonic[h[0]]["unmasked"])
        f = sonic[i]
        status = []
        if f["unmasked"] < MIN_UNMASKED:
            status.append("short")
        if len(distinct) > 1:
            status.append("ambiguous")
        if not lib_region:
            status.append("game-range")
        if any(votes[k].most_common(1)[0][0] != v and votes[k].most_common(1)[0][1] > 1 for k, v in w.items() if votes.get(k)):
            status.append("symbol-conflict")
        run = len(placed.get((f["source"], e - f["off"]), ())) - (f["unmasked"] >= MIN_UNMASKED)
        if run < 1 and f["unmasked"] < 64:
            status.append("no-object-run")
        ours = names.get(addr)
        fd = fid.get(addr)
        prior = "unnamed"
        if ours and not placeholder(ours[0]):
            prior = "registry"
        if fd:
            prior = prior + "+fid" if prior != "unnamed" else "fid"
        agree = None
        if ours and not placeholder(ours[0]):
            agree = ours[0] in distinct
        fid_agree = None
        if fd:
            fid_agree = bool(fd[0] & (set(distinct) | {"_" + d for d in distinct}))
        out.append(dict(addr=addr, name=f["name"], others=distinct if len(distinct) > 1 else [], lib=f["lib"], source=f["source"],
                        n=f["n"], unmasked=f["unmasked"], sized=f["sized"], compiler=f["compiler"], run=run, method="bytes-reloc-masked", status=status,
                        prior=prior, ours=ours[0] if ours else None, ours_file=ours[1] if ours else None,
                        fid=sorted(fd[0])[:4] if fd else None, fid_unique=fd[1] if fd else None, agree=agree, fid_agree=fid_agree,
                        words={f"{k[0]}+{k[1]}": f"{v:08x}" for k, v in w.items()}))
    lib_addrs = [e for e in entries if not any(lo <= e < hi for lo, hi in ranges) and f"{e:08x}" not in extra]
    unnamed_lib = [e for e in lib_addrs if not (f"{e:08x}" in names and not placeholder(names[f"{e:08x}"][0])) and f"{e:08x}" not in fid]
    unnamed_bytes = sum(entries[e] for e in unnamed_lib)
    near = {}
    if not a.no_near:
        exact = {x["addr"] for x in out}
        todo = [(e, n) for e, n in entries.items() if f"{e:08x}" not in exact]
        near = near_search(todo, data, sonic)
    result = dict(game=proj.get("name"), catalog=len(cat), sonic_functions=len(sonic), stats=stats,
                  library=len(lib_addrs), unnamed_library=len(unnamed_lib), unnamed_library_bytes=unnamed_bytes,
                  named_or_fid=len(lib_addrs) - len(unnamed_lib),
                  exact=out, near=[dict(addr=f"{e:08x}", name=sonic[v[0]]["name"], lib=sonic[v[0]]["lib"], ratio=round(v[1], 3),
                                        region="game" if any(lo <= e < hi for lo, hi in ranges) else "library",
                                        prior=("named" if f"{e:08x}" in names and not placeholder(names[f"{e:08x}"][0]) else "fid" if f"{e:08x}" in fid else "unnamed"))
                                   for e, v in sorted(near.items())])
    json.dump(result, open(a.out, "w", encoding="utf-8"), indent=0)
    good = [x for x in out if not x["status"]]
    print(proj.get("name"), "catalog", len(cat), "library", result["library"], "unnamed-lib", len(unnamed_lib), "bytes", unnamed_bytes, "sonic fn", len(sonic))
    print(" exact candidates", len(out), "clean", len(good), "new", sum(x["prior"] == "unnamed" for x in good),
          "status", collections.Counter(s for x in out for s in x["status"]).most_common(),
          "agree registry", collections.Counter(x["agree"] for x in good).most_common(),
          "agree fid", collections.Counter(x["fid_agree"] for x in good).most_common(), "near", len(near), "near lib unnamed", sum(1 for e in near if e in set(unnamed_lib)))


if __name__ == "__main__":
    main()
