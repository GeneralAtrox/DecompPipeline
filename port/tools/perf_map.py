"""What an offset in a port exe is: its function, the game's original address, its tier and its layer.

Everything comes from the linker map every port build already writes (port.map): the object file that holds a function
says whether it is translated C (raw C), readable C, the kit, the Core (port/base) or the game's own native source, and
carries the original address in its name. No debug file and no game-specific table is read.

Nearest existing: port/tools/symbolize.py - names an offset (its load_map is used here) but gives no object, tier,
layer or original address, and the per-game agg2.py that did (Power Stone 2, build box A only) was never committed.
"""
import bisect
import pathlib
import re

from symbolize import load_map

PORT = pathlib.Path(__file__).resolve().parent.parent

# Tiers, as the swap list names them.
RAW, READABLE, NATIVE, KIT, CORE, LIB = "raw C", "readable C", "native", "emulated hardware / kit", "Core", "library"

# Kit source files by what they stand for (first match wins; the rest of the kit is "kit runtime").
KIT_KINDS = (
    (r"^(arm7|aica|dsp_|xb_xma|xb_audio)", "sound chip and sound CPU"),
    (r"^(pvr|ta_|kamui|gfx_|d3d|xb_d3d|xb_gpu)", "graphics chip and renderer"),
    (r"^(tx_|tc_rt|sh4_|sq_|dispatch|overlay|xb_call|xb_mem)", "translated-code runtime (memory, calls, FPU)"),
    (r"^(gd|cd|disc|adx|sofdec|mpeg|media)", "disc and media"),
    (r"^(sat_vdp|vdp)", "graphics chip and renderer"),
    (r"^(sat_|scu|smpc|scsp)", "console hardware"),
    (r"^(frame_|interp|present)", "in-between frames and present"),
)
SYSTEM_LIBS = re.compile(r"^(msvcrt|ucrt|libucrt|vcruntime|libvcruntime|msvcprt|libcmt|libcpmt|oldnames|kernel32|user32|"
                         r"gdi32|sdl\d*|d3d\w*|dxgi|dxguid|winmm|ole32|shell32|advapi32|ws2_32|xinput\w*|zstd\w*|uuid)$", re.I)
ADDR_IN_OBJ = re.compile(r"(?:^|[_\\/])(?:tx_|fn_)?([0-9a-f]{8})(?:\.c)?\.obj$", re.I)
ADDR_IN_SYM = re.compile(r"^_?(?:fn|sub|tx|xg)_([0-9a-f]{8})$", re.I)
OWN_OBJECT = re.compile(r"^fn_[0-9a-f]{8}\.obj$", re.I)  # the Xbox 360 kit: one object per translated function, no library


def kit_index(port=PORT):
    """{source file stem: (tier, layer, kind)} for the Core and every console kit in this checkout."""
    out = {}
    for d in sorted(p for p in port.rglob("*") if p.is_dir()):
        rel = d.relative_to(port).as_posix()
        if rel.startswith(("tools", "build", "base/template")) or "/build" in rel:
            continue
        for f in list(d.glob("*.c")) + list(d.glob("*.cpp")):
            if rel == "base" or rel.startswith("base/"):
                out.setdefault(f.name.lower(), (CORE, "Core", "window, input, present, audio out, log"))
            else:
                kind = next((k for pat, k in KIT_KINDS if re.match(pat, f.name, re.I)), "kit runtime")
                out.setdefault(f.name.lower(), (KIT, "kit", kind))
    return out


class Map:
    """A port.map, ready to answer `at(offset)`."""

    def __init__(self, path, port=PORT):
        syms = load_map(str(path), objects=True)
        self.kit = kit_index(port)
        self.rvas = []
        self.rows = []      # per distinct rva: the entry dict
        by_rva = {}
        for rva, sym, obj in syms:
            by_rva.setdefault(rva, []).append((sym, obj))
        for rva in sorted(by_rva):
            self.rvas.append(rva)
            self.rows.append(self._entry(rva, by_rva[rva]))
        self.by_addr = {}
        for e in self.rows:
            if e["addr"] is not None:
                self.by_addr.setdefault(e["addr"], e)

    def _entry(self, rva, same):
        # Functions the linker folded together share an address: one entry names the first and counts the rest.
        same = sorted(same, key=lambda s: (not ADDR_IN_OBJ.search(s[1] or ""), s[0]))
        sym, obj = same[0]
        lib, _, fname = (obj or "").rpartition(":")
        name = sym.lstrip("_") if not sym.startswith("?") else sym
        m = ADDR_IN_OBJ.search(fname) or ADDR_IN_SYM.match(sym)
        addr = int(m.group(1), 16) if m else None
        stem = re.sub(r"\.obj$", "", fname, flags=re.I).lower()
        if lib == "txc" or lib.endswith("_tx") or OWN_OBJECT.match(fname):
            tier, layer, kind = RAW, "game logic", "translated function"
        elif lib == "readable" or lib.startswith("overlay_"):
            tier, layer, kind = READABLE, "game logic", "readable function"
        elif SYSTEM_LIBS.match(lib) or fname.lower().endswith(".dll") or not fname:
            tier, layer, kind = LIB, "Core", "C runtime or system library"
        elif stem in self.kit:
            tier, layer, kind = self.kit[stem]
        elif stem.startswith("xb_"):  # the Xbox 360 kit's runtime (targets/xbox360_ppc/port), outside this folder
            tier, layer, kind = KIT, "kit", next((k for pat, k in KIT_KINDS if re.match(pat, stem)), "kit runtime")
        elif re.match(r"^(dispatch|stubs|glue|tc_glue|thunks?)_", stem):
            tier, layer, kind = KIT, "kit", "generated call glue"
        else:
            tier, layer, kind = NATIVE, "game logic", "the game's own native source"
        return {"rva": rva, "name": name, "addr": addr, "tier": tier, "layer": layer, "kind": kind,
                "object": obj, "folded": len(same) - 1}

    def at(self, offset):
        i = bisect.bisect_right(self.rvas, offset) - 1
        return self.rows[i] if i >= 0 else None

    def guest(self, addr):
        """The entry for a game address from a call stack (a name even where the map has no function for it)."""
        e = self.by_addr.get(addr)
        if e:
            return e
        return {"rva": None, "name": f"fn_{addr:08x}", "addr": addr, "tier": "unknown", "layer": "game logic",
                "kind": "called by address, no function in the map", "object": "", "folded": 0}
