"""Plants a real fault in the shared kit, builds a real port and checks that port/tools/work_counts.py fails it.

    py -3.12 docs/proofs/work_counts/plant.py toycommander --exe BUILD/port.exe  -- BUILD COMMAND...
    py -3.12 docs/proofs/work_counts/plant.py robopit --exe OUT/robopit.exe -- BUILD COMMAND...

Each fault is one small source edit in this checkout: an added allocation, an extra draw, a repeated upload. The port is
rebuilt, the front-end scene is checked and the row the fault should move has to differ; the edit is then taken out. Last the
clean kit is rebuilt and has to pass again. The results are appended to plant_results.txt beside this file.

Nearest existing: tests/test_work_counts.py - the tool's verdicts on made-up results; this proves real counts catch real faults.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "port" / "tools"))
import work_counts  # noqa: E402  pylint: disable=wrong-import-position

UPLOAD = "ID3D11DeviceContext_Map(g_gfx.ctx, (ID3D11Resource *)b, 0, D3D11_MAP_WRITE_DISCARD, 0, &m)"
FRAME = "ID3D11DeviceContext_Map(cx, (ID3D11Resource *)g_tex, 0, D3D11_MAP_WRITE_DISCARD, 0, &m)"
PVR, CPU, COMPOSE = "port/pvr/pvr_draw.c", "port/base/present_cpu.c", "port/saturn/sat_vdp2.c"
KITS = {
    "dreamcast": [
        ("added allocation", "allocs", PVR, "    D3D11_MAPPED_SUBRESOURCE m;\n    if (FAILED(" + UPLOAD,
         "    D3D11_MAPPED_SUBRESOURCE m;\n    void *volatile planted = malloc(64);\n    free(planted);\n    if (FAILED(" + UPLOAD),
        ("extra draw", "draws", PVR, "ID3D11DeviceContext_Draw(g_gfx.ctx, b->count, b->first);",
         "ID3D11DeviceContext_Draw(g_gfx.ctx, b->count, b->first);\n        ID3D11DeviceContext_Draw(g_gfx.ctx, b->count, b->first);"),
        ("repeated upload", "uploads", PVR, "    if (FAILED(" + UPLOAD,
         "    if (SUCCEEDED(" + UPLOAD + ")) ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)b, 0);\n    if (FAILED(" + UPLOAD),
    ],
    "saturn": [
        ("added allocation", "allocs", COMPOSE, "    work_add(WC_COMPOSES, 1);",
         "    work_add(WC_COMPOSES, 1);\n    {\n        void *volatile planted = malloc(64);\n        free(planted);\n    }"),
        ("extra draw", "draws", CPU, "ID3D11DeviceContext_Draw(cx, 3, 0);",
         "ID3D11DeviceContext_Draw(cx, 3, 0);\n    ID3D11DeviceContext_Draw(cx, 3, 0);"),
        ("repeated upload", "uploads", CPU, "    if (FAILED(" + FRAME,
         "    if (SUCCEEDED(" + FRAME + ")) ID3D11DeviceContext_Unmap(cx, (ID3D11Resource *)g_tex, 0);\n    if (FAILED(" + FRAME),
    ],
}
GAME_KIT = {"toycommander": "dreamcast", "robopit": "saturn"}
SCENE = "front_end"


def edit(path, old, new):
    data = path.read_bytes()
    crlf = b"\r\n" in data
    text = data.decode("utf-8").replace("\r\n", "\n")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: the plant's site is not there exactly once: {old}")
    text = text.replace(old, new)
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def build(game, what, cmd):
    registered = ["py", "-3.12", str(ROOT / "tools/jobreg.py"), "run", "--changed", f"{game}: {what}",
                  "--why", f"work counters' planted fault: {what}", "--"] + cmd
    done = subprocess.run(registered, cwd=ROOT, env=dict(os.environ, DP_ROOT=str(ROOT)), capture_output=True, text=True,
                          timeout=590, check=False)
    if done.returncode:
        raise SystemExit(f"{what}: the build failed:\n{done.stdout[-1500:]}{done.stderr[-500:]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    ap.add_argument("game", choices=sorted(GAME_KIT))
    ap.add_argument("--exe", required=True, type=pathlib.Path)
    ap.add_argument("build", nargs="+")
    args = ap.parse_args()
    exe = args.exe.resolve()
    spec_path = HERE / args.game / "work_counts.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    lines = [f"{args.game} ({GAME_KIT[args.game]} kit), scene {SCENE}"]
    ok = True

    def compare():
        result = work_counts.run_scene(exe, spec_path, spec, spec["scenes"][SCENE], 500)
        return work_counts.differences(spec["scenes"][SCENE], result)

    for name, row, file, old, new in KITS[GAME_KIT[args.game]]:
        path = ROOT / file
        original = path.read_bytes()
        try:
            edit(path, old, new)
            build(args.game, "plant " + name, args.build)
            found = compare()
        finally:
            path.write_bytes(original)
        caught = any(r == row for r, _, _ in found)
        ok &= caught
        rows = ", ".join(f"{r} {a} -> {b}" for r, a, b in found if not r.startswith("peak"))
        lines.append(f"  {name}: {'fails, as it should' if caught else 'NOT CAUGHT'}: {rows}")
        print(lines[-1], flush=True)
    build(args.game, "clean after plants", args.build)
    found = compare()
    ok &= not found
    lines.append(f"  clean kit: {'passes' if not found else 'STILL DIFFERS'}: {found[:6]}")
    print(lines[-1], flush=True)
    with open(HERE / "plant_results.txt", "a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
