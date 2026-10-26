"""Translate one function and print its C (a look at the output while working on the translator).

usage: python tools/translate_one.py --ws <DP workspace> ADDR [--raw] [--comments] [--no-struct]
  --ws: a DecompPipeline Xbox 360 workspace (project.json, image.json, catalog_inputs.json, harness/<addr>/meta.json)
  --raw: no dataflow passes (every register a local, as lifted)
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from xenontranslate import returns, workspace  # noqa: E402
from xenontranslate.translate import translate_function  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("addr")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--comments", action="store_true")
    ap.add_argument("--no-struct", action="store_true")
    a = ap.parse_args()
    ws = workspace.Workspace(a.ws)
    addr = int(a.addr, 16)
    kinds = returns.analyse(ws, pathlib.Path(__file__).resolve().parents[1] / "out" / "cache")
    src, ft = translate_function(ws.dol, addr, ws.signature(addr), ws.facts, ws.bodies[addr], comments=a.comments,
                                 structured=not a.no_struct, optimise=not a.raw, returns=kinds)
    sys.stdout.write(src)
    for n in ft.notes:
        print("// note:", n, file=sys.stderr)


if __name__ == "__main__":
    main()
