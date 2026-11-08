"""The release ZIP of a kit port: port.exe, the DLLs beside it and its texture packs, laid out as Phangrade extracts and
runs them (port.exe at the top, packs in packs/<folder>, where pvr/pvr_pack.c looks).

    python package_release.py --exe port/build/x86/port.exe --out MyGame-port-win32-v0.1.0.zip
        [--game-pack port/packs/pad_icons] [--pack "Sharp text=C:/art/sharp_font/pack"]...

--game-pack: the pack game.json's texture_pack names, under its own folder name. --pack NAME=DIR: an optional pack the
player can choose (Display > Texture pack), which must carry a NOTICE.txt crediting the owners of what it holds. A
pack takes only index.txt, the images the index names and NOTICE.txt, so nothing else in its folder can ship by
mistake. No disc data goes in: the player's own disc is set up by --setup-only. Publishing is Phangrade's
scripts/publish-release.ps1, which signs this ZIP; run its check-port.ps1 on the build first.

Nearest existing: Phangrade's scripts/publish-release.ps1 - it signs and uploads a ZIP it is given and makes none, and
no kit tool makes one.
"""
import argparse
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import texture_pack as tp  # noqa: E402

RESERVED = {"on", "off"}  # what --texture-pack takes as words


def pack_files(name, folder, notice):
    """(archive name, source file) for a pack; raises ValueError if it is not fit to ship. A NOT_FOR_RELEASE.txt in the
    folder stops it: the pack holds art whose owner hasn't been settled, for the player's own copy only."""
    if (pathlib.Path(folder) / "NOT_FOR_RELEASE.txt").is_file():
        raise ValueError(f"pack {name}: {folder} is marked NOT_FOR_RELEASE.txt: " + (pathlib.Path(folder) / "NOT_FOR_RELEASE.txt").read_text(encoding="utf-8").strip())
    folder = pathlib.Path(folder)
    if name in RESERVED or not name or any(c in name for c in "/\\:#=&"):
        raise ValueError(f"pack name {name!r}: --texture-pack would not read it as a folder of packs/")
    images = sorted(set(tp.read_index(folder).values()))  # raises on an unknown variant word
    wanted = ["index.txt"] + images + (["NOTICE.txt"] if notice else [])
    missing = [f for f in wanted if not (folder / f).is_file()]
    if missing:
        raise ValueError(f"pack {name}: {folder} has no {', '.join(missing)}")
    return [(f"packs/{name}/{f}", folder / f) for f in wanted]


def package(exe, out, game_pack=None, packs=()):
    """Writes the ZIP; returns the names in it."""
    exe = pathlib.Path(exe)
    if not exe.is_file():
        raise ValueError(f"no {exe}")
    files = [(exe.name, exe)] + [(p.name, p) for p in sorted(exe.parent.glob("*.dll"))]
    named = set()
    if game_pack:
        game_pack = pathlib.Path(game_pack)
        named.add(game_pack.name)
        files += pack_files(game_pack.name, game_pack, notice=False)
    for name, folder in packs:
        if name in named:
            raise ValueError(f"pack name {name} used twice")
        named.add(name)
        files += pack_files(name, folder, notice=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, src in files:
            z.write(src, arc)
    return [arc for arc, _ in files]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game-pack")
    ap.add_argument("--pack", action="append", default=[], metavar="NAME=DIR")
    a = ap.parse_args(argv)
    try:
        if any("=" not in p for p in a.pack):
            raise ValueError("--pack takes NAME=DIR")
        names = package(a.exe, a.out, a.game_pack, [tuple(p.split("=", 1)) for p in a.pack])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{a.out}: {len(names)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
