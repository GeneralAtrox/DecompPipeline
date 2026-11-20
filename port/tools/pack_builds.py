"""Texture packs a game builds from its own sources and the disc, beside port.exe (game.json "pack_builds").

    "pack_builds": {"HD art": {"tool": "../art/hd/tools/hd_pack.py", "inputs": "../art/hd"}}

A pack that mixes a game's own art with the disc's textures can't be committed (the disc's part is the player's), so
the game keeps its sources and a builder, and build.py runs the builder after a successful compile:
    python TOOL --disc DISC --kit-tools DecompPipeline/port/tools --out <build folder>/packs/NAME
DISC is the disc image build.py was given (--disc, else game.json disc): a .cue, .gdi or the data track itself; the
tool finds its track. It runs only when its stamp changed: every file under inputs (path, size, content), the tool,
this kit's texture_pack.py, and the disc's path and size. The tool writes into a fresh folder, which replaces the old
pack only when the tool exits 0, so a failed build or one whose images don't match what the game expects keeps the
pack already there. A built pack is an optional pack (Display > Texture pack, settings.txt texture-pack=NAME), off
until the player picks it (or on until then when game.json texture_pack_choice names it), unless NAME is the folder
name of game.json texture_pack: pvr/pvr_pack.c looks for the
game's default pack in packs/<that name> beside port.exe first, so the built pack is the default, on with no argument. Nothing here ever fails the game's build: a pack that can't be made is reported and skipped.
A pack not built leaves its reason in packs/NAME.skipped (removed once it is built): where there is no pack, the game
log says why the original picture shows (pvr/pvr_pack.c).
Then the build's own port.exe stores each image's mip chain beside it (port.exe --pack-mips DIR, pvr/pvr_pack_png.c:
FILE.mips, the levels the renderer would otherwise build on the frame that first draws the image, from the same code);
it writes only the files missing or out of date, so an unchanged pack costs a read of each file's header.
Nearest existing: package_release.py (puts packs beside port.exe in a release ZIP, from folders that already exist;
it doesn't make them, and the launcher's builds never run it)."""
import hashlib
import pathlib
import re
import shutil
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
STAMP = ".built"  # in the pack's folder: the stamp it was built from
TIMEOUT = 1800


def config(pb, game_dir, error):
    """game.json pack_builds checked, as {name: {"tool": path, "inputs": path}} resolved against game_dir; error(msg) if not."""
    if not isinstance(pb, dict):
        raise error(f"game.json pack_builds: {pb!r} (a map of pack name to {{\"tool\": ..., \"inputs\": ...}})")
    out = {}
    for name, spec in pb.items():
        if (not re.fullmatch(r"[\w .-]+", str(name)) or str(name).strip(". ") != str(name) or str(name).lower() in ("on", "off")
                or not isinstance(spec, dict) or set(spec) != {"tool", "inputs"}):
            raise error(f"game.json pack_builds {name!r}: {spec!r} (a folder name other than on/off, to "
                        "{\"tool\": a builder script, \"inputs\": the folder it builds from}, both relative to game.json)")
        out[str(name)] = {k: (game_dir / spec[k]).resolve() for k in ("tool", "inputs")}
    return out


def stamp(spec, disc):
    h = hashlib.sha256()
    files = [spec["tool"], TOOLS / "texture_pack.py"]
    if spec["inputs"].is_dir():
        files += sorted(p for p in spec["inputs"].rglob("*") if p.is_file() and "__pycache__" not in p.parts
                        and not any(part.startswith("_work") for part in p.relative_to(spec["inputs"]).parts))
    for f in dict.fromkeys(files):
        h.update(f"{f.as_posix()}\0{f.stat().st_size}\0".encode())
        h.update(hashlib.sha256(f.read_bytes()).digest())
    d = pathlib.Path(disc)
    h.update(f"{d.resolve().as_posix()}\0{d.stat().st_size if d.is_file() else -1}".encode())
    return h.hexdigest()[:24]


def build_one(name, spec, disc, bdir, run, say):
    """The pack NAME in bdir/packs/NAME: 'built', 'up to date' or why not. run(cmd, timeout) -> (rc, output)."""
    out = bdir / "packs" / name
    if not disc or not pathlib.Path(disc).is_file():
        return f"not built: no disc image ({disc or 'none given'}; build.py --disc)"
    if not spec["tool"].is_file():
        return f"not built: no {spec['tool']}"
    want = stamp(spec, disc)
    if (out / "index.txt").is_file() and (out / STAMP).is_file() and (out / STAMP).read_text(encoding="utf-8").strip() == want:
        return "up to date"
    say(f"Building the texture pack {name} (only after it changed; this can take a few minutes)...")
    new = out.with_name(name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    rc, text = run([sys.executable, str(spec["tool"]), "--disc", str(disc), "--kit-tools", str(TOOLS), "--out", str(new)], TIMEOUT)
    (bdir / "packs" / f"{name}.log").write_text(text, encoding="utf-8")
    if rc or not (new / "index.txt").is_file():
        shutil.rmtree(new, ignore_errors=True)
        kept = "the pack already there is kept" if (out / "index.txt").is_file() else "there is no pack"
        return f"not built: {spec['tool'].name} exited {rc} ({kept}; its output is in packs/{name}.log)"
    (new / STAMP).write_text(want + "\n", encoding="utf-8")
    old = out.with_name(name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    new.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    return "built"


def store_mips(pack, bdir, run):
    """The build's port.exe --pack-mips over the pack's folder: its last line, or why it did not run. A pack without
    them still works (the renderer builds each chain when it first reads the image, and logs that it did)."""
    exe = pathlib.Path(bdir) / "port.exe"
    if not exe.is_file() or not (pathlib.Path(pack) / "index.txt").is_file():
        return "not stored: no port.exe or no pack"
    rc, text = run([str(exe), "--headless", "--fixed-clock", "1", "--pack-mips", str(pack)], 600)
    last = (text.strip().splitlines() or [""])[-1]
    return last if not rc else f"not stored: port.exe --pack-mips exited {rc} ({last})"


def build_all(cfg, bdir, disc, run, say=print):
    """Every pack of game.json pack_builds; a line each. Never raises for a pack that can't be made."""
    results = []
    for name, spec in (cfg.get("pack_builds") or {}).items():
        try:
            r = build_one(name, spec, disc, bdir, run, say)
            if not r.startswith("not built"):
                say(f"Texture pack {name}: {store_mips(bdir / 'packs' / name, bdir, run)}")
        except OSError as e:
            r = f"not built: {e}"
        results.append((name, r))
        say(f"Texture pack {name}: {r}")
        note = bdir / "packs" / f"{name}.skipped"
        try:
            if r.startswith("not built: "):
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(r[len("not built: "):] + "\n", encoding="utf-8")
            else:
                note.unlink(missing_ok=True)
        except OSError:
            pass
    return results


def vram_init(vi, ConfigError):
    """game.json vram_init (from game_config): bytes the game reads from texture memory that nothing it uploads covers (the
    console holds the frame buffers or leftovers there, which the port keeps elsewhere): {byte offset: hex bytes}."""
    if not isinstance(vi, dict):
        raise ConfigError('game.json vram_init: {"byte offset in texture memory": "hex bytes"}')
    vram_init = []
    for a, h in vi.items():
        if not re.fullmatch(r"[0-9a-fA-F]{1,6}", str(a)):
            raise ConfigError(f"game.json vram_init: {a!r} is not a hex byte offset in the 8 MB texture memory")
        at = int(a, 16)
        if not isinstance(h, str) or not re.fullmatch(r"([0-9a-fA-F]{2})+", h) or at + len(h) // 2 > 0x800000:
            raise ConfigError(f"game.json vram_init {a}: hex bytes inside the 8 MB texture memory, got {h!r}")
        vram_init.append((at, h.lower()))
    return vram_init


def sprite_fields(items, ADDR, ConfigError):
    """game.json sprite_fields (from game_config): a scrolling backdrop's sprites, by texture word, size and period."""
    sprite_fields = []
    for sf in items:
        form = 'game.json sprite_fields: [{"tsp": "8 hex digits", "size": [w, h], "period": [x, y]}]'
        if not isinstance(sf, dict) or set(sf) != {"tsp", "size", "period"} or not ADDR.fullmatch(str(sf["tsp"])):
            raise ConfigError(f"{form}, got {sf!r}")
        for key in ("size", "period"):
            pair = sf[key]
            if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(n, int) and n > 0 for n in pair):
                raise ConfigError(f"{form}, got {key} {pair!r}")
        sprite_fields.append((sf["tsp"], *sf["size"], *sf["period"]))
    return sprite_fields


def texture_pack_choice(choice, ConfigError):
    """game.json texture_pack_choice (from game_config): the optional pack a play run starts with (pvr/pvr_pack.c), or None."""
    if choice is not None and (not isinstance(choice, str) or not re.fullmatch(r"[\w .-]+", choice)
                               or choice.strip(". ") != choice or choice.lower() in ("on", "off")):
        raise ConfigError(f"game.json texture_pack_choice: {choice!r} (the folder name of an optional pack "
                          "in packs\\ beside port.exe, e.g. a pack_builds name, other than on/off)")
    return choice
