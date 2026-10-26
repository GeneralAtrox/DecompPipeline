"""A memory card image both sides of a check start from (tools/hw_check.py --vmu): the image itself is only read.

Flycast gets a copy in a folder of the run's own (Dreamcast.VMUPath, given on the command line, which Flycast does not
write back to emu.cfg), named as Flycast names a game's card: the product number from the disc's IP.BIN, then
_vmu_save_A1.bin, with a memory card put in the controller's first slot (lockstep.emu_run leaves it empty, as the
port's --vmu none does). Flycast's own cards are not touched. The port gets another copy as its save folder's
vmu_save_A1.bin (--vmu A1 --save-dir), made again before each port run, since a run may save to it. A card the port
made before sdk/vmu_flash.c wrote its root block as the BIOS does gets that block fixed in both copies (as
vmfs_fix_root does when the port loads one): Flycast's game finds no save on it otherwise.
"""
import pathlib
import shutil

IP_MAGIC = b"SEGA SEGAKATANA "
PRODUCT = slice(0x40, 0x4a)
ROOT = 255 * 512
ROOT_WORDS = ((0x42, 0), (0x44, 255), (0x52, 31), (0x56, 0x80))   # sdk/vmu_flash.c vmfs_format
FLYCAST_BAD = ' /\\:*?|<>"'     # characters Flycast turns into '_' in the card's name (core/oslib/oslib.cpp)


def first_hd_track(disc):
    """(file, sector size) of the high-density area's first track: a .gdi's track 3, or a .cue's third FILE."""
    disc = pathlib.Path(disc)
    lines = disc.read_text(errors="replace").splitlines()
    if disc.suffix.lower() == ".gdi":
        for line in lines[1:]:
            f = line.split()
            if len(f) >= 5 and f[0] == "3":
                return disc.parent / f[4].strip('"'), int(f[3])
    else:
        files = [line.split('"')[1] for line in lines if line.strip().startswith("FILE") and '"' in line]
        sizes = [2352 if "/2352" in line else 2048 for line in lines if line.strip().startswith("TRACK")]
        if len(files) >= 3 and len(sizes) >= 3:
            return disc.parent / files[2], sizes[2]
    raise SystemExit(f"{disc}: no high-density track 3")


def product_number(disc):
    path, size = first_hd_track(disc)
    with open(path, "rb") as f:
        sector = f.read(size)
    ip = sector[16:] if size == 2352 else sector
    if not ip.startswith(IP_MAGIC):
        raise SystemExit(f"{path}: no IP.BIN in its first sector")
    name = ip[PRODUCT].decode("ascii", "replace").rstrip()
    return "".join("_" if c in FLYCAST_BAD else c for c in name)


def copy_card(image, dst):
    """Copies the card image to dst, its root block fixed if an older port made it (root word 0x42 holding 255)."""
    card = bytearray(pathlib.Path(image).read_bytes())
    word = lambda at: int.from_bytes(card[ROOT + at:ROOT + at + 2], "little")
    if word(0x42) == 255 and word(0x44) == 0:
        for at, value in ROOT_WORDS:
            card[ROOT + at:ROOT + at + 2] = value.to_bytes(2, "little")
    pathlib.Path(dst).write_bytes(card)


def emu_entries(disc, image, out):
    """The Flycast -config entries for a run from a copy of `image` in OUT/vmu_emu."""
    folder = pathlib.Path(out) / "vmu_emu"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    copy_card(image, folder / f"{product_number(disc)}_vmu_save_A1.bin")
    return [f"config:Dreamcast.VMUPath={folder.as_posix()}", "input:device1.1=1"]     # 1: MDT_SegaVMU


def port_args(image, out):
    """The port's arguments for a run from a fresh copy of `image` in OUT/vmu_port."""
    folder = pathlib.Path(out) / "vmu_port"
    folder.mkdir(parents=True, exist_ok=True)
    copy_card(image, folder / "vmu_save_A1.bin")
    return ["--vmu", "A1", "--save-dir", str(folder)]
