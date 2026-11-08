"""One command from an Xbox 360 disc (or default.xex) to a workspace ready to prepare: the hand steps of the Too Human
proof (PROOF_PLAN.md) in order, the decode audit through the Ghidra broker like every other Ghidra run.

  setup <disc.iso | default.xex | WORKSPACE> <workspace> [--name NAME] [--roots FILE | --pick] [--no-ghidra]
      1. catalog_inputs.py into the workspace: image.bin, image.json, catalog_inputs.json, code_ranges.txt; from an
         existing workspace of the same title, those four files are copied (no retail key, no decrypt);
      2. the roots (--roots: a JSON {"functions": [...]} or one address per line; --pick: proof_pick.py's 50, written
         to proof_set.json and proof_set.txt) are copied to setup_roots.json; without roots the whole catalog is set up;
      3. ghidra_setup.py's list beside image.bin (ghidra_setup.txt): the roots and what they reach in 4 calls, or every
         function;
      4. project.json (never overwritten: an existing one stops setup) and oracle/TcInputs.java from the template;
      5. unless --no-ghidra, the `ghidra` step below.
  ghidra <workspace> [--audit all|roots]
      decomp.py's setup (import, TcSetup, TcCatalog), then TcDecodeAudit over every function the setup made (default:
      the setup list, i.e. the roots and what they reach in 4 calls, which the oracle runs through, and the thunks
      and helpers TcSetup adds; the hand step's set), or over the roots only (--audit roots, a quick look), checked
      as gate G2 (decode_audit.py) on the words of the functions the target prepares (catalog_inputs "refuse"
      empty): the disagreements in refused functions (VMX, atomics, ...) and in the import records the loader
      overwrites (an import thunk's first two words) are counted apart and expected. Writes state/decode_audit.tsv;
      exit 1 when G2 fails.

The project.json rules (each from image.json, the same for every title):
- binary image.bin and its SHA-256; ghidra_name: the name's letters and digits;
- game_ranges: the code sections (one range per run of adjacent code sections);
- global_ranges: .data, the only writable section the vectors randomise (.tls, .idata and the rest are the
  runtime's or the loader's);
- fixed_regs r13: the kernel block's base (target.json memory "kernel"), as the processor block pointer;
- priority: the roots, in their order; port 8700 + a CRC of the name (one Ghidra server per workspace);
- ghidra_setup_timeout_s 1800 with roots, 7200 for the whole catalog.

Bound it like every Ghidra command: python tools/bounded.py SECONDS -- python targets/xbox360_ppc/tools/x360setup.py ...
"""
import argparse
import bisect
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import zlib

HERE = pathlib.Path(__file__).resolve().parent
TARGET = HERE.parent
TOOL = TARGET.parents[1]
sys.path.insert(0, str(HERE))
import catalog_inputs  # noqa: E402
import decode_audit  # noqa: E402
import ghidra_setup  # noqa: E402
import proof_pick  # noqa: E402


INPUTS = ("image.bin", "image.json", "catalog_inputs.json", "code_ranges.txt")  # catalog_inputs.build's output


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_roots(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    try:
        items = json.loads(text)["functions"]
    except (ValueError, KeyError, TypeError):
        items = [ln.split("#")[0].strip() for ln in text.splitlines()]
    return [(x["addr"] if isinstance(x, dict) else x).lower() for x in items if x]


def code_runs(sections):
    runs = []
    for s in sorted((s for s in sections if s["code"]), key=lambda s: s["start"]):
        if runs and s["start"] <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], s["end"])
        else:
            runs.append([s["start"], s["end"]])
    return [[f"{lo:08x}", f"{hi:08x}"] for lo, hi in runs]


def project_fields(ws, name, desc, roots):
    tj = json.loads((TARGET / "target.json").read_text(encoding="utf-8"))
    data = [s for s in desc["sections"] if s["name"] == ".data"]
    cfg = {"name": name, "target": "xbox360_ppc", "ghidra_name": re.sub(r"[^A-Za-z0-9]", "", name) or "title",
           "binary": "image.bin", "binary_sha256": sha256(ws / "image.bin"),
           "game_ranges": code_runs(desc["sections"]),
           "global_ranges": [[f"{s['start']:08x}", f"{s['end']:08x}"] for s in data],
           "fixed_regs": {"r13": tj["memory"]["kernel"][0]}}
    if roots:
        cfg["priority"] = roots
    cfg.update({"references": [], "port": 8700 + zlib.crc32(name.encode()) % 100,
                "ghidra_setup_timeout_s": 1800 if roots else 7200})
    return cfg


def setup(src, ws, name=None, roots_file=None, pick=False):
    ws = pathlib.Path(ws)
    if (ws / "project.json").exists():
        raise SystemExit(f"{ws / 'project.json'} exists: setup never overwrites a workspace")
    ws.mkdir(parents=True, exist_ok=True)
    if pathlib.Path(src).is_dir():  # another workspace of this title: its catalog inputs, made once
        for n in INPUTS:
            shutil.copyfile(pathlib.Path(src) / n, ws / n)
        cat = json.loads((ws / "catalog_inputs.json").read_text(encoding="utf-8"))
    else:
        cat = catalog_inputs.build(src, ws)
    desc = json.loads((ws / "image.json").read_text(encoding="utf-8"))
    roots = None
    if pick:
        picks = proof_pick.pick(cat, (ws / "image.bin").read_bytes(), desc["load_address"])
        (ws / "proof_set.json").write_text(json.dumps({"image_sha1": desc["image_sha1"], "functions": picks},
                                                      indent=1) + "\n", encoding="utf-8")
        roots = [p["addr"] for p in picks]
        (ws / "proof_set.txt").write_text("\n".join(roots) + "\n", encoding="utf-8")
    elif roots_file:
        roots = read_roots(roots_file)
    funcs = {f["addr"]: f for f in cat["functions"]}
    if roots:
        missing = [r for r in roots if r not in funcs]
        if missing:
            raise SystemExit(f"roots not in the catalog: {', '.join(missing[:20])}")
        (ws / "setup_roots.json").write_text(json.dumps({"functions": roots}, indent=1) + "\n", encoding="utf-8")
    chosen = ghidra_setup.closure(funcs, roots, 4) if roots else None
    lines = ghidra_setup.lines(cat, chosen)
    with open(ws / "ghidra_setup.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# DecompPipeline Xbox 360 setup list (tools/ghidra_setup.py)\n" + "\n".join(lines) + "\n")
    cfg = project_fields(ws, name or pathlib.Path(src).stem, desc, roots)
    (ws / "project.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    oracle = ws / "oracle" / "TcInputs.java"
    if not oracle.exists():
        oracle.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TARGET / "template" / "TcInputs.java", oracle)
    kinds = {k: sum(1 for t in lines if t[0] == k) for k in "FHI"}
    print(f"{ws}: {len(cat['functions'])} catalog functions, setup list {kinds['F']} functions, {kinds['H']} helper "
          f"families, {kinds['I']} import thunks; roots {len(roots) if roots else 'none (whole catalog)'}")
    print(f"{ws / 'project.json'}: " + json.dumps({k: cfg[k] for k in ("game_ranges", "global_ranges", "fixed_regs",
                                                                         "port")}))
    return cfg


def g2(audit_tsv, ws, base):
    """(words, disagreements in prepared functions, disagreements in refused ones, disagreements in import records),
    streaming the audit file. An import thunk's first two words are the XEX import record (library and ordinal), which
    the loader overwrites with code; the oracle never runs a thunk (an import call is mocked), so those two words are
    counted apart. The thunk's other two (mtctr, bctr) are checked like any code."""
    cat = json.loads((ws / "catalog_inputs.json").read_text(encoding="utf-8"))
    refused = sorted((int(f["addr"], 16), int(f["addr"], 16) + f["size"]) for f in cat["functions"] if f["refuse"])
    starts = [lo for lo, _ in refused]
    records = {e["thunk"] + k for e in cat["imports"] if e["thunk"] for k in (0, 4)}
    image = (ws / "image.bin").read_bytes()
    words, bad, expected, patched = 0, [], 0, 0
    rows = []

    def flush():
        nonlocal expected, patched
        for line in decode_audit.audit(rows, image, base):
            a = int(line[:8], 16)
            i = bisect.bisect_right(starts, a) - 1
            if i >= 0 and refused[i][0] <= a < refused[i][1]:
                expected += 1
            elif a in records:
                patched += 1
            else:
                bad.append(line)
        rows.clear()

    with open(audit_tsv, encoding="utf-8") as fh:
        for line in fh:
            t = line.rstrip("\n").split("\t")
            if len(t) == 3:
                rows.append((int(t[0], 16), int(t[1], 16), t[2]))
                words += 1
                if len(rows) >= 100000:
                    flush()
    flush()
    return words, bad, expected, patched


def refuse_gaps(ws, bad):
    """The functions of the prepared set that hold a word Ghidra decoded no instruction for: write their addresses to
    decode_gaps.json and refuse them in catalog_inputs.json (the other disagreements stay failures of the gate).
    Returns the addresses refused now."""
    gaps = sorted({int(line[:8], 16) for line in bad if line.endswith("Ghidra decoded no instruction")})
    if not gaps:
        return []
    cat_path = ws / "catalog_inputs.json"
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    funcs = sorted((int(f["addr"], 16), f) for f in cat["functions"])
    starts = [a for a, _ in funcs]
    hit = set()
    for a in gaps:
        i = bisect.bisect_right(starts, a) - 1
        if i >= 0 and funcs[i][0] <= a < funcs[i][0] + funcs[i][1]["size"]:
            hit.add(funcs[i][1]["addr"])
    old = ws / "decode_gaps.json"
    prior = set(json.loads(old.read_text(encoding="utf-8"))) if old.exists() else set()
    old.write_text(json.dumps(sorted(prior | hit)), encoding="utf-8")
    for _, f in funcs:
        if f["addr"] in hit and not f.get("refuse"):
            f["refuse"] = "unsupported: embedded data"
    cat_path.write_text(json.dumps(cat, separators=(",", ":")), encoding="utf-8")
    return sorted(hit)


def audit_args(ws, audit):
    """TcDecodeAudit's function arguments: none (every function in the program) for "all", the roots for "roots"."""
    roots = pathlib.Path(ws) / "setup_roots.json"
    return read_roots(roots) if audit == "roots" and roots.exists() else []


def ghidra_step(ws, audit="all"):
    ws = pathlib.Path(ws).resolve()
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(TOOL))
    from core import ghidra, paths  # noqa: E402  (the workspace is chosen by DECOMP_PROJECT at import)
    cfg = paths.config()
    ghidra.setup(cfg)
    roots = audit_args(ws, audit)
    said = []  # TcDecodeAudit's count line: how many functions "every function" was
    out = paths.STATE / "decode_audit.tsv"
    ghidra._headless(cfg, ["-process", cfg["binary_path"].name, "-noanalysis", "-readOnly",
                           "-scriptPath", str(paths.sync_scripts()), "-postScript", "TcDecodeAudit.java", str(out),
                           *roots], "ghidra_decode_audit.log", on_line=lambda s: said.extend(
                               re.findall(r"TcDecodeAudit: (\d+) functions", s)), timeout=ghidra.whole_timeout(cfg))
    base = int(json.loads((ws / "image.json").read_text())["load_address"])
    words, bad, expected, patched = g2(out, ws, base)
    gaps = refuse_gaps(ws, bad)
    if gaps:
        words, bad, expected, patched = g2(out, ws, base)
        print(f"{len(gaps)} function(s) with a body word Ghidra holds as data (jump tables, or an address the constant "
              f"analysis took for data): refused as \"unsupported: embedded data\" (decode_gaps.json)")
    for b in bad[:200]:
        print(b)
    print(f"decode audit: {words} words of {said[-1] if said else len(roots) or 'every'} function(s); {len(bad)} "
          f"disagreement(s) in functions the target prepares, {expected} in refused functions and {patched} in import "
          f"records (expected): gate G2 {'FAILS' if bad else 'passes'}")
    return 1 if bad or not words else 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("source")
    s.add_argument("workspace")
    s.add_argument("--name")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--roots")
    g.add_argument("--pick", action="store_true")
    s.add_argument("--no-ghidra", action="store_true")
    h = sub.add_parser("ghidra")
    h.add_argument("workspace")
    h.add_argument("--audit", choices=("all", "roots"), default="all")
    a = ap.parse_args(argv[1:])
    if a.cmd == "setup":
        setup(a.source, a.workspace, a.name, a.roots, a.pick)
        return 0 if a.no_ghidra else ghidra_step(a.workspace)
    return ghidra_step(a.workspace, a.audit)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
