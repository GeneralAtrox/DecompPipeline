"""FSCA's sine and cosine table as the SH-4 hardware gives it, from tools/fsca_rom.py: the 17-word ROM rebuilt from its
rules (the derived C0 rule, the closed L/E/Q rules, KC) and the datapath model run over all 65,536 angles. No
emulator's table or code is used (tools/fsca_rom.LICENSE: ours, and skmp's MIT notice for the datapath model).

One table per rounding mode: FPSCR.RM = 0 (round to nearest) and RM = 1 (round toward zero, rz=True). The table
follows the rounding the FPU runs in: the oracle reads FPSCR.RM under fpscr_model (without it, its whole FPU rounds to
nearest), and the judge's runtimes, xemu and the port kit read MXCSR.RC (set from FPSCR.RM).

    python -m core.fsca OUT      write the table file (file(): round to nearest's pairs, then toward zero's)

A project's table is state/fsca/<the first 16 hex of FILE_SHA256>.bin (core/paths.fsca_table), written once.
"""
import functools
import hashlib
import importlib.util
import os
import pathlib
import struct
import sys
import threading
import time

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
N = 65536
SIZE = 2 * N * 8  # file(): 2 tables x 65,536 x {sin, cos} u32 (1 MB; the one-table file was 512 KB)
# The generator's two tables (its printed SHA-256 of the 65,536 sines, float32 little-endian), pinned by
# tests/test_tooling.py: both equal skmp's hardware-verified model on every entry, signed zeros included.
SHA256 = {False: "500664c1e60659a3210c020db24e9c534beb6f8c6bea354e052424c2b91669bd",
          True: "1eb396fa419325cafb4130a9d6d47e5deeb13ee40655ba386374f771393d5ef8"}
FILE_SHA256 = "d9dcbed0f8b9b75e8877c219a162910041713ded38b49d428dd56f26ae836aee"  # file(), pinned by test_fsca_rm
_ENSURE = threading.Lock()


def _generator():
    spec = importlib.util.spec_from_file_location("fsca_rom", TOOLS / "fsca_rom.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@functools.lru_cache(maxsize=2)
def sines(rz=False):
    """The 65,536 sines as float32 bits; FSCA's cosine of angle a is sines()[(a + 0x4000) & 0xffff]."""
    gen = _generator()
    return tuple(gen.table(gen.rom(), rz))


def pairs(rz=False):
    """65,536 x (sin bits, cos bits), little-endian: one table of file()."""
    s = sines(rz)
    return struct.pack("<%dI" % (2 * N), *(v for a in range(N) for v in (s[a], s[(a + 0x4000) & 0xffff])))


def file():
    """The table file: pairs() (FPSCR.RM = 0), then pairs(rz=True) (RM = 1). Its readers (TcPrepare, tc_rt.c,
    rd_sh4.c, the port's --check-fsca) refuse any other size, the one-table file included."""
    return pairs() + pairs(rz=True)


def ensure(path):
    """Make path (a project's table: paths.fsca_table) the current file(). Every reader (TcPrepare, the judge's
    runtimes) refuses any other, so a missing or stale one (the one-table 512 KB file of before c346302, which failed
    every prepare until `decomp.py setup` ran again) is rewritten atomically, with one line on stderr and in its
    folder's events.log. Returns that line, or None when the file was already current (a hash, no table build)."""
    path = pathlib.Path(path)
    with _ENSURE:
        old = path.read_bytes() if path.exists() else None
        if old is not None and hashlib.sha256(old).hexdigest() == FILE_SHA256:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".tmp-{path.name}-{os.getpid()}-{threading.get_ident()}")
        tmp.write_bytes(file())
        try:
            os.replace(tmp, path)
        except OSError:  # another process wrote it meanwhile and a reader holds it open: the same table
            tmp.unlink(missing_ok=True)
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != FILE_SHA256:
                raise
            return None
        was = "missing" if old is None else f"{len(old)} bytes, SHA-256 {hashlib.sha256(old).hexdigest()[:16]}"
        line = f"fsca table {path}: {was}, not the current table (core/fsca); rewritten"
        print(line, file=sys.stderr, flush=True)
        with open(path.parent / "events.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')}  {line}\n")
        return line


def table_c(name="port_fsca_sin"):
    """C source defining both tables' sines as `const unsigned int NAME[2][65536]` (the port kit's table): [0] round to
    nearest, [1] round toward zero."""
    lines = ["/* Generated from DecompPipeline tools/fsca_rom.py (core/fsca.py): FSCA's sine for each of the 65,536",
             " * angles as float32 bits, [0] round to nearest and [1] toward zero (FPSCR.RM); the cosine is the sine a",
             " * quarter turn on. Do not edit. */",
             "const unsigned int %s[2][%d] = {" % (name, N)]
    for rz in (False, True):
        s = sines(rz)
        lines += ["    {"] + ["        " + ", ".join("0x%08xu" % v for v in s[i:i + 8]) + "," for i in range(0, N, 8)]
        lines += ["    },"]
    return "\n".join(lines + ["};", ""])


def main(argv):
    if len(argv) != 1:
        raise SystemExit(__doc__)
    pathlib.Path(argv[0]).write_bytes(file())
    print("wrote %s (fsca: round to nearest, sines SHA-256 %s; toward zero, %s)"
          % (argv[0], SHA256[False], SHA256[True]))


if __name__ == "__main__":
    main(sys.argv[1:])
