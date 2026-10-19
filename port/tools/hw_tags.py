"""Tags the game's functions that drive the console's hardware, by what each readable file reaches itself:
  pvr    the PowerVR through Kamui (the kit's kamui2_* modules: surfaces, textures, scenes, display)
  sq     the store queues / TA pointer (sh4_r(14), the 0xe0000000 window, QACR), and the game's hardware functions
         that write TA parameters
  aica   the sound processor through the audio libraries (ac_digi, am_*) and sound RAM writes
  maple  the Maple bus (maple_pad, bu_vmu, vmu_flash)
  gdrom  the GD-ROM (gdfs)
  g2     the G2 bus (sound RAM, the G2 DMA registers)
  dma    DMA (SH-4 DMAC, the PVR channel-2 / sort DMA registers, a cache write-back ahead of one)
A file is tagged when it calls a kit function of that module (by the name the kit defines), a game hardware function
(game.json "hardware") of that class, or has the class's register addresses or idioms. tools/hw_check.py checks pvr and
sq per frame (display lists and textures against the emulator); the others are tagged here, not yet checked per frame.

    python tools/hw_tags.py --game DIR [--out FILE]
"""
import argparse
import pathlib
import re
import sys
import time

from game_config import SDK_DIR, load as load_game
from gen_port import defined_names

MODULES = {"pvr": ("kamui2_misc", "kamui2_scene", "kamui2_tex"), "aica": ("ac_digi", "am_sys", "am_heap"),
           "maple": ("maple_pad", "bu_vmu", "vmu_flash"), "gdrom": ("gdfs",)}
PATTERNS = {
    "sq": r"sh4_r\(14\)|0xe0000000|0xff00003[8c]|PVR_PCW_",
    "pvr": r"0x(?:a0|00)5f8[0-9a-f]{3}\b",
    "aica": r"0x(?:a0|00)7[01][0-9a-f]{4}\b|0x(?:a0|00)8[0-9a-f]{5}\b",
    "maple": r"0x(?:a0|00)5f6c[0-9a-f]{2}\b",
    "g2": r"0x(?:a0|00)5f78[0-9a-f]{2}\b|SoundRam_",
    "dma": r"0xffa0[0-9a-f]{4}\b|0x(?:a0|00)5f68[0-9a-f]{2}\b|[Dd]ma|DMA",
}
HARDWARE = {"sq": r"Vtx|Strip|Poly", "pvr": r"Pvr|Display", "aica": r"Sound", "g2": r"SoundRam",
            "dma": r"Dma|DMA|Cache_WriteBack"}


def kit_names():
    return {cls: defined_names([SDK_DIR / f"{m}.c" for m in mods if (SDK_DIR / f"{m}.c").exists()])
            for cls, mods in MODULES.items()}


def call_re(names):
    return re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\s*\(") if names \
        else None


def tag(game):
    cfg = load_game(game)
    src = pathlib.Path(cfg["decomp"]) / "readable" / "src"
    calls = {cls: call_re(n) for cls, n in kit_names().items()}
    hw = {cls: [n for n in cfg["hardware"].values() if re.search(p, n)] for cls, p in HARDWARE.items()}
    for cls, names in hw.items():
        if names:
            extra = call_re(names)
            calls[cls] = re.compile(calls[cls].pattern + "|" + extra.pattern) if calls.get(cls) else extra
    pats = {cls: re.compile(p) for cls, p in PATTERNS.items()}
    tags = {}
    for f in sorted(src.glob("*.c")):
        text = f.read_text(encoding="utf-8", errors="replace")
        got = {cls for cls, r in calls.items() if r and r.search(text)}
        got |= {cls for cls, r in pats.items() if r.search(text)}
        if got:
            tags[f.stem] = sorted(got)
    return tags, len(list(src.glob("*.c")))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", required=True)
    ap.add_argument("--out", default=None, help="default: GAME/build/hw_tags.txt")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    tags, files = tag(pathlib.Path(a.game))
    out = pathlib.Path(a.out or pathlib.Path(a.game) / "build" / "hw_tags.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{k} {' '.join(v)}\n" for k, v in tags.items()), encoding="ascii")
    counts = {}
    for v in tags.values():
        for cls in v:
            counts[cls] = counts.get(cls, 0) + 1
    print(f"hardware functions: {len(tags)} of {files} readable files ({time.monotonic() - t0:.1f} s); {out}")
    for cls in ("pvr", "sq", "aica", "maple", "gdrom", "g2", "dma"):
        print(f"  {cls}: {counts.get(cls, 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
