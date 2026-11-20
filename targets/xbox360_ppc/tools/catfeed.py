"""Feed the port kit's extra function starts into a judge workspace's catalogue, and check that none is missing.

The live port (port/tools/gen_xbox.py) translates more functions than the judge catalogue (catalog_inputs.json) lists:
- holes: code between catalogue functions that pdata never listed (`gen_xbox.py translate --holes`, and the run-time
  missing-function stops), often right after a tail branch or a bctr (Too Human 824e5228, 824f39c0, 824f39d8);
- mid entries: call targets inside a catalogue function's range (Kit.inside: the body runs from there to the end of
  the containing function, after xb_join);
- leaf entries: call targets the catalogue lacks (xb_leaf.body), state.json "leaf_entries".
None of them is judged unless the catalogue lists it. This tool adds each as a catalogue function with the body the
kit translated, so prepare, pointer seeding and the input generator treat it like any other function.

  snapshot <kit out dir> <port_functions.json>     the port's list: gen/ file names and state.json's mid and leaf
                                                    entries (names only, no C is read); run where the kit's out is
  feed <workspace> [<port_functions.json>]         add the port's extra starts to <workspace>/catalog_inputs.json
                                                    (source kit_hole / kit_mid / kit_leaf, "origin" says why), rewrite
                                                    code_ranges.txt, copy the list to <workspace>/port_functions.json
                                                    and write <workspace>/catfeed.json (counts, what was not fed)
  check <workspace> [<port_functions.json>]        the drift test: exit 1 when the port has a function the catalogue
                                                    lacks (default list: <workspace>/port_functions.json)

Entry state and returns, the same for the oracle and the port's C:
- The body is the kit's: a mid entry runs from its address to the end of the (xb_join-joined) catalogue function that
  holds it, a hole or leaf to xb_leaf.body's end (the first blr, or a tail branch back before it or over 0x400 on,
  past every forward branch in it; a bctr through an inline table skips the table). The same modules the kit imports
  compute it, from the same image, so the judge's packet and the port's fn_<addr>.c cover the same words.
- The oracle (TcOracle.setEntry) starts it like every function: r3..r10 and f1..f13 from the vector, r1 = SP,
  LR = SENTINEL, r13 fixed, r12 and the CR fields it reads before setting them seeded (TcFlow.entryReads), all else
  0. The port's C for the same start uses Signature.generic_for (the same r3..r10, f1..f13) and takes a register it
  reads before setting from its caller when the caller hands it over, else as 0 (gen/ "uncarried"): the judge
  compares the 0 case, and a function whose C reads an unseeded register is the judge's NOTRANSLATE as usual.
- It returns at a blr (the PC reaches SENTINEL), or by a tail branch out of its body (TcRun.tailJump: a direct
  branch outside the body or a computed one that does not flow back into it), which the oracle mocks as a call that
  returns to LR; the translator writes the same tail call and return.
- A mid entry shares its words with the function that holds it, and a hole's leaf body can run on into the next
  catalogue function. Ghidra cannot hold two functions over the same words, so ghidra_setup.lines lists a root over
  any function it overlaps and drops the other one (a whole-catalogue setup drops the kit entries that overlap a
  catalogue function): those are prepared in a workspace set up with them as roots.

Nearest existing: catalog_inputs.py / ppcscan.scan - they make the catalogue from the executable alone and cannot
see what the port found at run time; x360setup --roots only picks catalogue functions; plan/extra_seeds.txt is
XenonRecomp's seed list, kept as a cross-check only (REFERENCES.md).
"""
import bisect
import json
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "port" / "tools")]
import catalog_inputs  # noqa: E402
import ppcscan  # noqa: E402
import xb_join  # noqa: E402
import xb_leaf  # noqa: E402

KINDS = ("kit_hole", "kit_mid", "kit_leaf")
BLR, BCTR = 0x4E800020, 0x4E800420


def is_kit(f):
    return f.get("source") in KINDS


def snapshot(out_dir, path):
    """The port's function list from a kit out dir (gen/fn_*.c names, state.json): written to path, returned."""
    out = pathlib.Path(out_dir)
    gen = sorted({p.name[3:11] for p in (out / "gen").glob("fn_*.c")})
    sp = out / "state.json"
    st = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    port = {"from": str(out), "gen": gen, "mid_entries": st.get("mid_entries", {}),
            "leaf_entries": st.get("leaf_entries", {})}
    pathlib.Path(path).write_text(json.dumps(port, indent=0) + "\n", encoding="utf-8")
    return port


def load_port(path):
    port = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    for k in ("gen", "mid_entries", "leaf_entries"):
        port.setdefault(k, [] if k == "gen" else {})
    return port


def port_starts(port):
    """Every start the port translated: gen/ addresses (the mid and leaf lists are only fed when gen/ has them)."""
    return {int(a, 16) for a in port["gen"]}


def entry_after(word):
    """What the word before a start (or before the padding in front of it) is: how the code before it ends."""
    if word == BLR:
        return "blr"
    if word == BCTR:
        return "bctr"
    if (word & 0xFC000003) == 0x48000000:
        return "tail_branch"
    if word in (0, 0x60000000):
        return "padding"
    return "fallthrough"


def kit_body(addr, spans, starts, image, base):
    """(kind, end, parent) as gen_xbox.Kit.translate finds the body of a start the catalogue lacks: inside a
    catalogue function (a mid entry, to that function's end), else xb_leaf.body (hole or leaf); None if neither."""
    i = bisect.bisect_right(starts, addr) - 1
    if i >= 0:
        lo, hi = spans[starts[i]]
        if lo <= addr < hi:
            return "mid", hi, starts[i]
    lb = xb_leaf.body(image, base, addr)
    return ("leaf", lb[0][1], None) if lb else None


def feed(ws, port_path=None):
    """Add the port's extra starts to ws/catalog_inputs.json. Idempotent: earlier kit entries are replaced."""
    ws = pathlib.Path(ws)
    port_path = pathlib.Path(port_path) if port_path else ws / "port_functions.json"
    port = load_port(port_path)
    cat_path = ws / "catalog_inputs.json"
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    desc = json.loads((ws / "image.json").read_text(encoding="utf-8"))
    image = (ws / "image.bin").read_bytes()
    base = int(desc.get("load_address", 0x82000000))
    cat["functions"] = [f for f in cat["functions"] if not is_kit(f)]
    have = {int(f["addr"], 16) for f in cat["functions"]}
    # the kit's catalogue bodies: functions only, joined where xb_join joins them (Kit.open)
    bodies = {int(f["addr"], 16): [(int(f["addr"], 16), int(f["addr"], 16) + f["size"])]
              for f in cat["functions"] if f["kind"] == "function"}
    for f, lohi in xb_join.join(bodies, image, base).items():
        bodies[f] = [lohi]
    spans = {a: b[-1] for a, b in bodies.items()}
    starts = sorted(spans)
    im = ppcscan.Image(image, desc)
    helpers = {k: v for k, v in cat.get("helpers", {}).items() if not k.endswith("ambiguous")}
    helper_ranges = [(lo, hi) for _, lo, hi in cat.get("helper_ranges", [])]
    thunks = {e["thunk"]: e for e in cat.get("imports", []) if e.get("thunk")}
    added, unfed = [], {}
    for a in sorted(port_starts(port) - have):
        h = f"{a:08x}"
        if any(lo <= a < hi for lo, hi in helper_ranges):
            unfed[h] = "inside a register save/restore helper family"
            continue
        if a in thunks:
            unfed[h] = "an import thunk"
            continue
        found = kit_body(a, spans, starts, image, base)
        if found is None:
            unfed[h] = "no body: neither inside a catalogue function nor an xb_leaf body"
            continue
        kind, end, parent = found
        if kind == "leaf" and h not in port["leaf_entries"]:
            kind = "hole"
        recorded = port["mid_entries"].get(h) or port["leaf_entries"].get(h)
        f = ppcscan.features(im, a, end - a, "kit_" + kind, helpers, thunks)
        p = a - 4
        while p > a - 0x40 and im.word(p) in (0, 0x60000000):   # alignment padding: what ends the code before it
            p -= 4
        f["origin"] = {"kit": kind, "after": entry_after(im.word(p)), "padded": p != a - 4, "end": f"{end:08x}"}
        if parent is not None:
            f["origin"]["parent"] = f"{parent:08x}"
        if recorded:
            f["origin"]["recorded_end"] = recorded   # state.json's end when the kit first took it (xb_leaf may since differ)
        added.append(f)
    cat["functions"] = sorted(cat["functions"] + added, key=lambda f: int(f["addr"], 16))
    cat["stats"] = ppcscan.stats(cat["functions"], cat.get("mid_entries", []))
    tmp = cat_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cat, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, cat_path)
    (ws / "code_ranges.txt").write_text("".join(f"{lo:08x} {hi:08x}\n" for lo, hi in catalog_inputs.code_ranges(cat)),
                                        encoding="utf-8")
    if port_path.resolve() != (ws / "port_functions.json").resolve():
        shutil.copyfile(port_path, ws / "port_functions.json")
    counts = {k: sum(1 for f in added if f["source"] == k) for k in KINDS}
    report = {"port": str(port_path), "port_functions": len(port["gen"]), "added": counts,
              "refused": sum(1 for f in added if f["refuse"]),
              "recorded_end_differs": sorted(f["addr"] for f in added
                                             if f["origin"].get("recorded_end", f["origin"]["end"]) != f["origin"]["end"]),
              "unfed": unfed}
    (ws / "catfeed.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return report


def missing(ws, port_path=None):
    """{addr: what the port has it as} for every port function the catalogue lacks. A start inside a register
    save/restore helper family is not one: the judge runs those families inline in their callers (ppcscan)."""
    ws = pathlib.Path(ws)
    port = load_port(pathlib.Path(port_path) if port_path else ws / "port_functions.json")
    cat = json.loads((ws / "catalog_inputs.json").read_text(encoding="utf-8"))
    have = {int(f["addr"], 16) for f in cat["functions"]}
    helper_ranges = [(lo, hi) for _, lo, hi in cat.get("helper_ranges", [])]
    gen = port_starts(port)
    out = {}
    for a in sorted(gen - have):
        if any(lo <= a < hi for lo, hi in helper_ranges):
            continue
        h = f"{a:08x}"
        out[h] = "mid entry" if h in port["mid_entries"] else "leaf entry" if h in port["leaf_entries"] else "gen/"
    return out


def main(argv):
    if len(argv) < 3 or argv[1] not in ("snapshot", "feed", "check"):
        print(__doc__.split("\n\n")[1])
        return 2
    cmd = argv[1]
    if cmd == "snapshot":
        port = snapshot(argv[2], argv[3])
        print(f"{argv[3]}: {len(port['gen'])} gen/ functions, {len(port['mid_entries'])} mid entries, "
              f"{len(port['leaf_entries'])} leaf entries")
        return 0
    if cmd == "feed":
        r = feed(argv[2], argv[3] if len(argv) > 3 else None)
        print(f"catfeed: added {r['added']} ({r['refused']} refused by class), {len(r['unfed'])} not fed, "
              f"{len(r['recorded_end_differs'])} whose xb_leaf end differs from state.json's")
        for a, why in list(r["unfed"].items())[:20]:
            print(f"  not fed {a}: {why}")
        return 0
    miss = missing(argv[2], argv[3] if len(argv) > 3 else None)
    for a, why in list(miss.items())[:20]:
        print(f"catfeed check: {a} ({why}) is a port function the judge catalogue lacks")
    print(f"catfeed check: {len(miss)} port function(s) missing from the catalogue: {'FAIL' if miss else 'ok'}")
    return 1 if miss else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
