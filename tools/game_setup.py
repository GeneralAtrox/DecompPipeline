"""One command from a Dreamcast, Saturn or GameCube disc to a workspace ready to prepare: the hand steps of a new game
(the target README's disc tool, project.json, the oracle's TcInputs, `decomp.py setup`), then the decode check of
every function Ghidra found (tools/decode_check.py), through the Ghidra broker like every other Ghidra run.

  setup <disc image> <workspace> [--target T] [--name NAME] [--fields FILE] [--no-ghidra]
        [--file NAME@ADDR ...] [--context NAME@ADDR ...] [--first-as-context]   (Saturn)
        [--rels all|none|ID,ID...]                                                (GameCube)
      The console comes from the disc (IP.BIN "SEGA SEGAKATANA" or "SEGA SEGASATURN", the GameCube magic), or
      --target. The disc step writes the binary and a record of where it came from (never over an existing
      project.json):
      - Dreamcast: the boot file IP.BIN names, extracted to <workspace>/disc/ (gdrom.py), with disc/disc.json; a boot
        file linked at 0x0c... (P0) gets "seed_functions" (gdrom.py scan's rule);
      - Saturn: binary/image.bin, a work RAM high image (overlay_image.py) of the first-read file at its IP.BIN
        address, plus each --file (to decompile) and --context file (callees and globals only) at its load
        address, with binary/image.json; project.json gets its image_gaps and game_ranges;
      - GameCube: gcdisc.py setup into <workspace>/disc/ (main.dol, the REL modules and linked.dol, disc.json) and
        project.json's game_ranges, global_ranges and fixed_regs.
      Then project.json (name, ghidra_name: the name's letters and digits, binary, binary_sha256, port: a per-target
      base plus a CRC of the name, references) and oracle/TcInputs.java from the target's template. --fields merges a
      JSON object of the workspace's own decisions into project.json (e.g. catalog_overrides carried over from an
      earlier workspace of the same game); it may not set target, binary or binary_sha256.
      Unless --no-ghidra, the `ghidra` step below.
  ghidra <workspace>
      `decomp.py setup` (import, analysis, TcSetup, the catalog fixups, TcCatalog), then `check`.
  check <workspace>
      tools/ghidra_tools/TcDecodeCheck.java over every function of the master project (read-only), then
      tools/decode_check.py on its output: every instruction is the binary's bytes, decoded, and the target's own
      decoder names it as Ghidra does. Writes state/decode_check.tsv; exit 1 when the check fails.
Each step's time goes to state/game_setup.json.

Bound it like every Ghidra command: python tools/bounded.py SECONDS -- python tools/game_setup.py ...
(The Xbox 360 has its own: targets/xbox360_ppc/tools/x360setup.py.)
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import zlib

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
for _t in ("dreamcast_sh4", "saturn_sh2", "gamecube_ppc"):
    sys.path.insert(0, str(TOOL / "targets" / _t / "tools"))
import decode_check  # noqa: E402

PORT_BASE = {"dreamcast_sh4": 8600, "saturn_sh2": 8900, "gamecube_ppc": 8800}  # gcdisc.py's own rule for GameCube
FIXED = ("target", "binary", "binary_sha256")


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def detect(image):
    """The disc's target, from its boot header."""
    import gcdisc
    import gdrom
    import satdisc
    if pathlib.Path(image).suffix.lower() != ".gdi":
        try:
            d = satdisc.Disc(image)
            try:
                d.ip()
                return "saturn_sh2"
            finally:
                d.f.close()
        except (ValueError, SystemExit, OSError):
            pass
    try:
        if gdrom.Disc(image).ip()["hardware"] == "SEGA SEGAKATANA":
            return "dreamcast_sh4"
    except (ValueError, SystemExit, OSError, IndexError):
        pass
    try:
        gcdisc.Disc(image).close()
        return "gamecube_ppc"
    except (SystemExit, OSError):
        pass
    raise SystemExit(f"{image}: no Dreamcast, Saturn or GameCube boot header (pass --target)")


def title_name(title):
    return " ".join(w.capitalize() for w in title.split("\0")[0].split()) or "game"


def common_fields(target, name, binary_rel, data):
    return {"name": name, "target": target, "ghidra_name": re.sub(r"[^A-Za-z0-9]", "", name) or "game",
            "binary": binary_rel, "binary_sha256": sha256(data),
            "port": PORT_BASE[target] + zlib.crc32(name.encode()) % 100, "references": []}


def dreamcast(image, ws, name):
    import gdrom
    disc = gdrom.Disc(image)
    ip = disc.ip()
    boot = ip["boot_file"]
    found = [(p, lba, size) for p, lba, size in disc.walk() if p == boot or p.split("/")[-1] == boot]
    if not found:
        raise SystemExit(f"{image}: the boot file {boot} is not on the disc")
    p, lba, size = found[0]
    data = disc.read(lba, size)
    _, p1, p0, _ = gdrom.code_score(data)
    out = ws / "disc"
    out.mkdir(parents=True, exist_ok=False)  # a new folder: never overwrite earlier extractions
    (out / p.split("/")[-1]).write_bytes(data)
    (out / "disc.json").write_text(json.dumps({"image": str(pathlib.Path(image).resolve()), "ip": ip,
                                               "boot_file": {"path": p, "size": size, "sha256": sha256(data),
                                                             "pointers_p1": p1, "pointers_p0": p0}}, indent=1) + "\n",
                                   encoding="utf-8", newline="\n")
    cfg = common_fields("dreamcast_sh4", name or title_name(ip["title"]), f"disc/{p.split('/')[-1]}", data)
    if p0 > 4 * max(p1, 1):  # linked at 0x0c... (gdrom.py scan): TcSeed finds the functions
        cfg["seed_functions"] = True
    print(f"{out}: {p} ({size} bytes) from {ip['title']} ({ip['product']} {ip['version']})")
    return cfg


def saturn(image, ws, name, files=(), context=(), first_context=False):
    import overlay_image
    import satdisc
    disc = satdisc.Disc(image)
    try:
        ip = disc.ip()
        listing = disc.walk()
        want = [(ip["first_read_file"], int(ip["first_read_addr"], 16), first_context)]
        for specs, ctx in ((files, False), (context, True)):
            for s in specs:
                f, base = overlay_image.parse(s)
                want.append((str(f), base, ctx))
        parts, record = [], []
        for nm, base, ctx in want:
            key = nm.strip("/").upper()
            hit = [(p, lba, size) for p, lba, size in listing
                   if p.strip("/").upper() == key or p.split("/")[-1].upper() == key]
            if not hit:
                raise SystemExit(f"{image}: {nm} is not on the disc")
            if not overlay_image.LOAD <= base < overlay_image.RAM_END:
                raise SystemExit(f"{nm}: {base:08x} is not in work RAM high")
            p, lba, size = hit[0]
            data = disc.read(lba, size)
            parts.append((base, data, p.split("/")[-1], ctx))
            record.append({"path": p, "address": f"{base:08x}", "size": size, "sha256": sha256(data), "context": ctx})
    finally:
        disc.f.close()
    try:
        img, gaps, ranges = overlay_image.build(parts)
    except ValueError as e:
        raise SystemExit(str(e))
    out = ws / "binary"
    out.mkdir(parents=True, exist_ok=False)
    (out / "image.bin").write_bytes(img)
    (out / "image.json").write_text(json.dumps({"image": str(pathlib.Path(image).resolve()), "ip": ip, "parts": record,
                                                "image_gaps": gaps, "game_ranges": ranges}, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    cfg = common_fields("saturn_sh2", name or title_name(ip["title"]), "binary/image.bin", img)
    cfg.update({"image_gaps": gaps, "game_ranges": ranges})
    print(f"{out / 'image.bin'}: {len(img)} bytes, {len(parts)} file(s) from {ip['title']} "
          f"({ip['product']} {ip['version']})")
    return cfg


def gamecube(image, ws, name, rels="all"):
    import gcdisc
    disc = gcdisc.Disc(image)
    try:
        cfg = gcdisc.setup(disc, ws / "disc", ws, rels)  # writes project.json and oracle/TcInputs.java itself
    finally:
        disc.close()
    if name:
        cfg["name"] = name
    return cfg


def setup(image, ws, target=None, name=None, fields=None, files=(), context=(), first_context=False, rels="all"):
    ws = pathlib.Path(ws)
    if (ws / "project.json").exists():
        raise SystemExit(f"{ws / 'project.json'} exists: setup never overwrites a workspace")
    extra = json.loads(pathlib.Path(fields).read_text(encoding="utf-8")) if fields else {}
    bad = [k for k in FIXED if k in extra]
    if bad:
        raise SystemExit(f"--fields may not set {', '.join(bad)}: they come from the disc")
    target = target or detect(image)
    if target != "saturn_sh2" and (files or context or first_context):
        raise SystemExit("--file, --context and --first-as-context are for Saturn discs")
    ws.mkdir(parents=True, exist_ok=True)
    if target == "dreamcast_sh4":
        cfg = dreamcast(image, ws, name)
    elif target == "saturn_sh2":
        cfg = saturn(image, ws, name, files, context, first_context)
    elif target == "gamecube_ppc":
        cfg = gamecube(image, ws, name, rels)
    else:
        raise SystemExit(f"{target}: not a disc target of this tool")
    cfg.update(extra)
    (ws / "project.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8", newline="\n")
    oracle = ws / "oracle" / "TcInputs.java"
    if not oracle.exists():
        oracle.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TOOL / "targets" / target / "template" / "TcInputs.java", oracle)
    shown = {k: cfg[k] for k in ("name", "binary", "port", "image_gaps", "game_ranges", "global_ranges", "fixed_regs",
                                 "seed_functions", "seed_entries") if k in cfg}
    print(f"{ws / 'project.json'}: {json.dumps(shown)}" + (f"; from --fields: {sorted(extra)}" if extra else ""))
    print(f'To show it on the dashboard (docs/DASHBOARD.md): python tools/dashboard.py add "<its game folder>"')
    return cfg


def timing(ws, step, seconds, **more):
    f = pathlib.Path(ws) / "state" / "game_setup.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    rec = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    rec[step] = {"seconds": round(seconds, 1), **more}
    f.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8", newline="\n")


def ghidra_step(ws):
    ws = pathlib.Path(ws).resolve()
    t = time.perf_counter()
    rc = subprocess.run([sys.executable, str(TOOL / "decomp.py"), "--project", str(ws), "setup"]).returncode
    timing(ws, "ghidra_setup", time.perf_counter() - t, rc=rc)
    if rc:
        print(f"decomp.py setup failed (exit {rc}): no decode check")
        return rc
    return check_step(ws)


def check_step(ws):
    ws = pathlib.Path(ws).resolve()
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(TOOL))
    from core import ghidra, paths  # noqa: E402  (the workspace is chosen by DECOMP_PROJECT at import)
    cfg = paths.config()
    out = paths.STATE / "decode_check.tsv"
    t = time.perf_counter()
    log = ghidra._headless(cfg, ["-process", cfg["binary_path"].name, "-noanalysis", "-readOnly",
                                 "-scriptPath", str(TOOL / "tools" / "ghidra_tools"), "-postScript", "TcDecodeCheck.java",
                                 str(out)], "ghidra_decode_check.log", timeout=ghidra.whole_timeout(cfg))
    ghidra_s = time.perf_counter() - t
    counts, bad, empty = decode_check.check(decode_check.read_rows(out), cfg)
    ok = decode_check.report(counts, bad, empty)
    m = re.search(r"TcDecodeCheck: (\d+) functions", log)
    if not m or int(m.group(1)) != counts["functions"]:
        print(f"decode check: Ghidra listed {m.group(1) if m else '?'} functions, the output holds "
              f"{counts['functions']}: FAILS")
        ok = False
    timing(ws, "decode_check", time.perf_counter() - t, ghidra_s=round(ghidra_s, 1), passes=ok, **counts,
           no_instruction=empty)
    return 0 if ok else 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("image")
    s.add_argument("workspace")
    s.add_argument("--target", choices=sorted(PORT_BASE))
    s.add_argument("--name")
    s.add_argument("--fields")
    s.add_argument("--file", action="append", default=[])
    s.add_argument("--context", action="append", default=[])
    s.add_argument("--first-as-context", action="store_true")
    s.add_argument("--rels", default="all")
    s.add_argument("--no-ghidra", action="store_true")
    for c in ("ghidra", "check"):
        sub.add_parser(c).add_argument("workspace")
    a = ap.parse_args(argv[1:])
    if a.cmd == "setup":
        t = time.perf_counter()
        cfg = setup(a.image, a.workspace, a.target, a.name, a.fields, a.file, a.context, a.first_as_context, a.rels)
        timing(a.workspace, "disc", time.perf_counter() - t, target=cfg["target"])
        return 0 if a.no_ghidra else ghidra_step(a.workspace)
    return (ghidra_step if a.cmd == "ghidra" else check_step)(a.workspace)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
