"""Exports Ghidra's disassembly and pseudo-C of SDK, engine or hardware functions for reading while the platform
layer is written (<game>/build/ref/<addr>/asm.txt, ghidra.c). The functions are named, not given as addresses, and
at most 5 go to one Ghidra run: Defender flags command lines that carry long lists of hex addresses.

    python port/tools/sdk_ref.py [--game DIR] NAME... [@FILE]      (FILE: one name per line)

Reference only: the platform layer is new source written from what these show, and nothing is copied from them.
The exports hold disc-derived code: they stay in the game's git-ignored build folder.
"""
import argparse
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import game_config  # noqa: E402

CHUNK = 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--game", default=None, help="the folder with game.json (default: the current folder)")
    ap.add_argument("--out", default=None, help="default: <game>/build/ref")
    a = ap.parse_args()
    cfg = game_config.load(game_config.find_game(a.game))
    names = []
    for n in a.names:
        if n.startswith("@"):
            names += [ln.strip() for ln in pathlib.Path(n[1:]).read_text(encoding="utf-8").splitlines() if ln.strip()]
        else:
            names.append(n)
    os.environ["DECOMP_PROJECT"] = str(cfg["decomp"])
    sys.path.insert(0, str(game_config.PIPELINE))
    from core import ghidra, paths, readable  # noqa: E402  (the pipeline's own modules)
    reg = readable.Registry()
    by_name = {readable.parse_proto(reg.proto(x))[1]: x for x in reg.functions}
    by_name.update({n: x for x, n in cfg["hardware"].items()})
    out = pathlib.Path(a.out).resolve() if a.out else cfg["dir"] / "build" / "ref"
    todo, missing = [], []
    for n in names:
        x = by_name.get(n)
        if not x:
            missing.append(n)
        elif not (out / x / "ghidra.c").exists():
            todo.append(x)
    if missing:
        print("unknown names: " + ", ".join(missing))
    pcfg = paths.config()
    for i in range(0, len(todo), CHUNK):
        ghidra.export(pcfg, todo[i:i + CHUNK], out)
    for n in names:
        x = by_name.get(n)
        if x:
            ok = (out / x / "ghidra.c").exists()
            print(f"{n}: {out / x / 'ghidra.c'}" + ("" if ok else "  (not written)"))


if __name__ == "__main__":
    main()
