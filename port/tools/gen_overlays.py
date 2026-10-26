"""Compile a game's overlays side by side (runtime/overlay.c): the units <game>/overlays.json names, each the code a
game loads at run time over guest RAM that another unit may also use.

    python port/tools/gen_overlays.py [--game DIR] [--out DIR]

overlays.json: {"overlays": [{"id": "intro", "decomp": "../decomp/ovl", "ranges": [["<lo>", "<hi>"]]}, ..]}
"ranges" ([lo, hi) hex) defaults to the workspace's project.json game_ranges; "prefix" (default: the workspace's
folder name) names the workspace's symbols. Reads (never writes) the workspaces. Writes into --out (default
<game>/build/gen/overlays); a workspace whose readable pass has not run (no include/types.h) gets no library:
  <prefix>/include/game.h  the workspace's registry, as gen_port.py writes main's
  <prefix>/prefix.h        forced first into each of the workspace's files: its function names become
                           <prefix>__<name>, so two overlays (or an overlay and main) never share a symbol
  <prefix>/stubs.c         logging stubs: functions in its units with no readable file, catalog starts with no
                           declaration
  <prefix>/tramp.c         for each out-of-range function its files name: a naked jump through port_guest_target,
                           which runs what a call to that guest address runs (main, or the unit loaded there)
  overlay_table.c          the units: ranges, identity windows and functions (runtime/overlay.h)
  overlays.cmake           one static library per workspace, linked into the port (the kit's CMakeLists.txt)
Identity windows: OVERLAY_WINDOW bytes of code in the body ranges (state/function_bodies.txt) where the unit differs
from each rival inside the rival's ranges (where loading it writes), by NEED windows (fewer if the unit has fewer code
windows there; none if it has none: that rival never overwrites its code). A pair that cannot be told apart stops
the run. At run time a call resolves among the units with a function at that address (runtime/overlay.c). A game
with no overlays.json gets no overlays.cmake.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import game_config  # noqa: E402

WINDOW = 16       # runtime/overlay.h OVERLAY_WINDOW
MAX_WINDOWS = 8
MIN_WINDOWS = 4   # also for a unit with no rival: RAM holding something else must not match
NEED = 2          # windows that tell each rival apart
IDENT = re.compile(r"[A-Za-z_]\w*")
CNAME = re.compile(r"[A-Za-z_]\w{0,40}")


class OverlayError(Exception):
    pass


def hexrange(r):
    lo, hi = int(r[0], 16), int(r[1], 16)
    if lo >= hi:
        raise OverlayError(f"range {r}: lo must be below hi")
    return lo, hi


def load_units(game_dir):
    """The units of <game>/overlays.json, [] if there is none: {id, ws, prefix, ranges}."""
    spec = game_dir / "overlays.json"
    if not spec.is_file():
        return []
    raw = json.loads(spec.read_text(encoding="utf-8")).get("overlays") or []
    units, prefixes = [], {}
    for u in raw:
        ws = (game_dir / u["decomp"]).resolve()
        if not (ws / "project.json").is_file():
            raise OverlayError(f"overlay {u.get('id')}: no project.json in {ws}")
        prefix = u.get("prefix") or re.sub(r"\W", "_", ws.name)
        if prefixes.setdefault(prefix, ws) != ws:
            raise OverlayError(f"prefix {prefix} names two workspaces; give one a \"prefix\"")
        ranges = u.get("ranges") or json.loads((ws / "project.json").read_text(encoding="utf-8")).get("game_ranges")
        if not ranges or not CNAME.fullmatch(str(u.get("id", ""))) or not CNAME.fullmatch(prefix):
            raise OverlayError(f"overlay {u}: needs a C-name id, ranges (or game_ranges) and a C-name prefix")
        units.append({"id": u["id"], "ws": ws, "prefix": prefix, "ranges": [hexrange(r) for r in ranges]})
    if len({u["id"] for u in units}) != len(units):
        raise OverlayError("overlay ids must be unique")
    return units


def groups(units):
    """Group number per unit: units whose ranges overlap (directly or through others) share one."""
    parent = list(range(len(units)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    for i, a in enumerate(units):
        for j in range(i):
            if any(lo < h and l2 < hi for lo, hi in a["ranges"] for l2, h in units[j]["ranges"]):
                parent[root(i)] = root(j)
    order = {}
    return [order.setdefault(root(i), len(order)) for i in range(len(units))]


def inside(ranges, a, n=WINDOW):
    return any(lo <= a and a + n <= hi for lo, hi in ranges)


def same_code(own, read, blocks, rr):
    """1 if a rival holds the unit's very code wherever its ranges overlap the unit's blocks (only data differs):
    either build of it runs right, so the two need no telling apart."""
    for s, e in blocks:
        for lo, hi in rr:
            a, b = max(s, lo), min(e, hi)
            if a < b and own(a, b - a) != read(a, b - a):
                return 0
    return 1


def pick_windows(uid, own, ranges, blocks, rivals):
    """[(addr, bytes)] for one unit. own(a, n) and each rival's read(a, n) give image bytes; blocks: [(start, end)]
    of the unit's code; rivals: [(id, rival ranges, read)]."""
    cands = []
    for s, e in sorted(blocks):
        for a in range(s, e - WINDOW + 1, WINDOW):
            b = own(a, WINDOW)
            if inside(ranges, a) and len(b) == WINDOW and len(set(b)) > 1:
                cands.append((a, b))
    # a rival whose ranges hold none of the unit's code windows cannot overwrite them: nothing to tell apart
    # and one with the same code there runs right as the unit does
    want = {rid: 0 if same_code(own, read, blocks, rr) else min(NEED, sum(1 for a, _ in cands if inside(rr, a)))
            for rid, rr, read in rivals}
    need = dict(want)
    tells = [{rid for rid, rr, read in rivals if inside(rr, a) and read(a, WINDOW) != b} for a, b in cands]
    chosen = []
    while len(chosen) < MAX_WINDOWS and any(need.values()):
        gain, best = max(((sum(1 for r in t if need[r]), -i) for i, t in enumerate(tells)), default=(0, 0))
        if gain == 0:
            break
        i = -best
        chosen.append(i)
        for r in tells[i]:
            need[r] = max(0, need[r] - 1)
        tells[i] = set()
    short = [(r, want[r] - n) for r, n in need.items() if n]
    if short:
        r, have = short[0]
        raise OverlayError(f"overlays {uid} and {r}: only {have} code window(s) tell them apart (need {want[r]})")
    rest = [i for i in range(len(cands)) if i not in chosen]
    step = max(1, len(rest) // max(1, MIN_WINDOWS))
    for i in rest[::step]:
        if len(chosen) >= MIN_WINDOWS:
            break
        chosen.append(i)
    if len(chosen) < MIN_WINDOWS:
        raise OverlayError(f"overlay {uid}: fewer than {MIN_WINDOWS} code windows in its ranges")
    return sorted(cands[i] for i in chosen)


def prefix_h(prefix, names):
    lines = [f"/* Generated by port/tools/gen_overlays.py: workspace {prefix}'s function names, unique in the port. */"]
    return "\n".join(lines + [f"#define {n} {prefix}__{n}" for n in sorted(names)]) + "\n"


def tramp_c(prefix, targets):
    """targets {name: guest addr}: each becomes a jump to what a call to that address runs, stack untouched."""
    lines = [f"/* Generated by port/tools/gen_overlays.py: workspace {prefix}'s calls out of its overlays. */",
             '#include "rd_rt.h"', "", "typedef void (*PortFn)(void);", "PortFn port_guest_target(u32 addr);"]
    for n, a in sorted(targets.items()):
        lines += ["", f"__declspec(naked) void {prefix}__{n}(void)", "{",
                  f"    __asm {{ push 0x{a} }}", "    __asm { call port_guest_target }", "    __asm { add esp, 4 }",
                  "    __asm { jmp eax }", "}"]
    return "\n".join(lines) + "\n"


def table_c(units, grp, windows, rows):
    """rows[i]: [(addr, symbol, name)] sorted by addr; windows[i]: [(addr, bytes)]."""
    out = ["/* Generated by port/tools/gen_overlays.py: the overlay units (runtime/overlay.h). */",
           '#include "overlay.h"', ""]
    out += [f"extern void {s}(void);" for s in sorted({s for r in rows for _, s, _ in r})]
    for i, u in enumerate(units):
        out += ["", f"static const PortOverlayRange r{i}[] = {{" + ", ".join(
            f"{{0x{lo:08x}u, 0x{hi:08x}u}}" for lo, hi in u["ranges"]) + "};"]
        out += [f"static const PortOverlayWindow w{i}[] = {{"]
        out += [f"    {{0x{a:08x}u, {{" + ", ".join(f"0x{x:02x}" for x in b) + "}}," for a, b in windows[i]]
        out += ["};", f"static const PortDispatch d{i}[] = {{"]
        out += [f'    {{0x{a:08x}u, {s}, "{n}"}},' for a, s, n in rows[i]] or ["    {0, 0, 0},"]
        out += ["};"]
    out += ["", "const PortOverlay port_overlays[] = {"]
    out += [f'    {{"{u["id"]}", {grp[i]}u, r{i}, {len(u["ranges"])}u, w{i}, {len(windows[i])}u, d{i}, '
            f"{len(rows[i])}u}}," for i, u in enumerate(units)]
    out += ["};", f"const int port_noverlays = {len(units)};"]
    return "\n".join(out) + "\n"


def cmake(libs, table):
    """libs: [{prefix, dir, files, include}]: one static library each, with its prefix.h forced first."""
    q = lambda p: '"' + pathlib.Path(p).as_posix() + '"'  # noqa: E731
    lines = ["# Generated by port/tools/gen_overlays.py: the overlays, compiled side by side (CMakeLists.txt)."]
    for L in libs:
        d, t = L["dir"], f"overlay_{L['prefix']}"
        srcs = [q(f) for f in L["files"]] + [q(d / "stubs.c"), q(d / "tramp.c")]
        # The main workspace's readable include follows the unit's own: its types.h until the unit has one.
        inc = [q(d / "include"), q(L["include"]), '"${PORT_READABLE_INCLUDE}"', '"${PORT_GEN}/include"',
               '"${PORT_RD_RT}"', '"${CMAKE_CURRENT_SOURCE_DIR}/runtime"']
        lines += [f"add_library({t} STATIC", *[f"  {s}" for s in srcs], ")",
                  f"target_include_directories({t} BEFORE PRIVATE {' '.join(inc)})",
                  f'target_compile_options({t} PRIVATE ${{PORT_C_FLAGS}} /TC "/FI{(d / "prefix.h").as_posix()}")',
                  f"set_property(TARGET {t} PROPERTY C_STANDARD 11)", f"target_link_libraries(port PRIVATE {t})"]
    lines.append(f"target_sources(port PRIVATE {q(table)})")
    return "\n".join(lines) + "\n"


def worker(ws, ranges, prefix, out):
    """One workspace (its own process: the pipeline's core reads DECOMP_PROJECT once). Writes <out>/<prefix>/ and
    prints {rows, blocks, image, load, files, include, counts} as JSON (files None: no readable pass yet)."""
    import gen_port
    library, paths, readable = gen_port.load_core(ws, game_config.PIPELINE)
    inr = lambda x: any(lo <= int(x, 16) < hi for lo, hi in ranges)  # noqa: E731
    blocks = []
    for ln in paths.FUNCTION_BODIES.read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) == 3 and inr(p[1]):
            blocks.append((int(p[1], 16), int(p[2], 16)))
    cfg = paths.config()
    info = {"blocks": blocks, "image": str(cfg["binary_path"]), "load": int(str(cfg["load_address"]), 16)}
    if not (readable.INCLUDE / "types.h").is_file():  # no readable pass yet: nothing to compile
        print(json.dumps({**info, "rows": [], "files": None, "include": str(readable.INCLUDE), "counts": {}}))
        return
    reg = readable.Registry()
    files = sorted(p.stem for p in readable.SRC.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem) and inr(p.stem))
    sigs, _ = library.raw_declarations(reg, files)
    game_h, _ = gen_port.port_game_h(readable, reg, sigs)
    declared = sorted(set(reg.functions) | set(sigs))
    protos = {x: reg.proto(x, raw_sig=sigs.get(x)) for x in declared}
    names = {x: readable.parse_proto(p)[1] for x, p in protos.items()}
    used = set()
    for f in files:
        used |= set(IDENT.findall((readable.SRC / f"{f}.c").read_text(encoding="utf-8")))
    own = {x for x in declared if inr(x)}
    rename = {names[x] for x in own | {x for x in declared if names[x] in used}} - gen_port.CRT
    tramps = {names[x]: x for x in declared if not inr(x) and names[x] in used and names[x] in rename}
    stubs = [x for x in sorted(own) if x not in files and names[x] not in gen_port.CRT]
    catalog = [f["addr"] for f in json.loads(paths.CATALOG.read_text(encoding="utf-8"))["functions"]]
    bare = sorted(x for x in catalog if inr(x) and x not in names)
    d = out / prefix
    (d / "include").mkdir(parents=True, exist_ok=True)
    gen_port.write_if_changed(d / "include" / "game.h", game_h.replace("gen_port.py", "gen_overlays.py", 1))
    gen_port.write_if_changed(d / "prefix.h", prefix_h(prefix, rename))
    body = ["/* Generated by port/tools/gen_overlays.py: logging stubs. */", '#include "game.h"',
            '#include "port_stub.h"', ""]
    for x in stubs:
        body += gen_port.stub_c(readable, x, protos[x]) + [""]
    body += [f'u32 {prefix}__fn_{x}(void) {{ port_stub(0x{x}u, "fn_{x}"); return 0; }}' for x in bare]
    gen_port.write_if_changed(d / "stubs.c", "\n".join(body) + "\n")
    gen_port.write_if_changed(d / "tramp.c", tramp_c(prefix, tramps))
    rows = [(x, f"{prefix}__{names[x]}", names[x]) for x in sorted(own) if names[x] in rename]
    rows += [(x, f"{prefix}__fn_{x}", f"fn_{x}") for x in bare]
    print(json.dumps({**info, "rows": sorted(rows), "files": [str(readable.SRC / f"{f}.c") for f in files],
                      "include": str(readable.INCLUDE), "counts": {"files": len(files), "stubs": len(stubs),
                      "bare": len(bare), "trampolines": len(tramps), "renamed": len(rename)}}))


def image_reader(path, load):
    data = pathlib.Path(path).read_bytes()
    return lambda a, n: data[a - load:a - load + n] if a >= load else b""


def run_worker(ws, ranges, prefix, out, timeout):
    cmd = [sys.executable, str(pathlib.Path(__file__).resolve()), "--worker", str(ws), "--ranges",
           json.dumps([[f"{lo:08x}", f"{hi:08x}"] for lo, hi in ranges]), "--prefix", prefix, "--out", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise OverlayError(f"workspace {ws}: {r.stderr[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def generate(game_dir, out, timeout=600):
    """Everything for the game's overlays.json; returns the report lines ([] and no overlays.cmake if it has none)."""
    units = load_units(game_dir)
    if not units:
        (out / "overlays.cmake").unlink(missing_ok=True)
        return []
    out.mkdir(parents=True, exist_ok=True)
    infos, libs = {}, []
    for prefix in dict.fromkeys(u["prefix"] for u in units):
        mine = [u for u in units if u["prefix"] == prefix]
        info = infos[prefix] = run_worker(mine[0]["ws"], [r for u in mine for r in u["ranges"]], prefix, out, timeout)
        if info["files"] is not None:
            libs.append({"prefix": prefix, "dir": out / prefix, "files": info["files"], "include": info["include"]})
    grp = groups(units)
    reads = [image_reader(infos[u["prefix"]]["image"], infos[u["prefix"]]["load"]) for u in units]
    windows, rows = [], []
    for i, u in enumerate(units):
        info, inu = infos[u["prefix"]], lambda a, u=u: any(lo <= a < hi for lo, hi in u["ranges"])
        rivals = [(v["id"], v["ranges"], reads[j]) for j, v in enumerate(units) if j != i and grp[j] == grp[i]]
        windows.append(pick_windows(u["id"], reads[i], u["ranges"], [b for b in info["blocks"] if inu(b[0])], rivals))
        rows.append([(int(a, 16), s, n) for a, s, n in info["rows"] if inu(int(a, 16))])
    table = out / "overlay_table.c"
    text = table_c(units, grp, windows, rows)
    if not table.is_file() or table.read_text(encoding="utf-8") != text:
        table.write_text(text, encoding="utf-8")
    (out / "overlays.cmake").write_text(cmake(libs, table), encoding="utf-8")
    report = [f"overlay units: {len(units)} in {max(grp) + 1} group(s), {len(libs)} of {len(infos)} workspace(s) "
              "compiled"]
    for i, u in enumerate(units):
        inu = [b for b in infos[u["prefix"]]["blocks"] if any(lo <= b[0] < hi for lo, hi in u["ranges"])]
        same = [v["id"] for j, v in enumerate(units) if j != i and grp[j] == grp[i]
                and same_code(reads[i], reads[j], inu, v["ranges"]) and any(
                    max(s, lo) < min(e, hi) for s, e in inu for lo, hi in v["ranges"])]
        report.append(f"  {u['id']}: group {grp[i]}, {len(rows[i])} functions, {len(windows[i])} identity windows"
                      + (f"; same code as {', '.join(same)}" if same else ""))
    report += [f"  {p}: " + (", ".join(f"{k} {v}" for k, v in i["counts"].items()) if i["files"] is not None else
                             f"no readable C yet ({i['include']} has no types.h): its calls find no function")
               for p, i in infos.items()]
    (out / "overlay_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=None, help="the folder with game.json (default: the current folder)")
    ap.add_argument("--out", default=None, help="default: <game>/build/gen/overlays")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--ranges", help=argparse.SUPPRESS)
    ap.add_argument("--prefix", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.worker:
        ranges = [(int(lo, 16), int(hi, 16)) for lo, hi in json.loads(a.ranges)]
        worker(pathlib.Path(a.worker), ranges, a.prefix, pathlib.Path(a.out))
        return
    game_dir = game_config.find_game(a.game)
    out = pathlib.Path(a.out) if a.out else game_dir / "build" / "gen" / "overlays"
    try:
        report = generate(game_dir, out)
    except OverlayError as e:
        raise SystemExit(f"gen_overlays: {e}")
    print("\n".join(report) or "gen_overlays: no overlays.json")


if __name__ == "__main__":
    main()
