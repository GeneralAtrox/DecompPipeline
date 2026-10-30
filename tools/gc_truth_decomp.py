"""A finished public GameCube decomp's decomp-toolkit config, read for tools/gc_truth.py (test truth only): config.yml,
symbols.txt and splits.txt of main.dol and of every REL module, and the main DOL of a disc image. A line reader, not
YAML, so no new dependency; it reads only config/ and never copies a name into DP.

Nearest existing: targets/gamecube_ppc/tools/gcdisc.py - it reads a disc's own files (DOL, RELs, FST), not a decomp's
config; gc_truth.py's readers moved here unchanged so each file stays under the 400-line cap.
"""
import bisect
import pathlib
import re
import struct

SYMBOL = re.compile(r"^(\S+) = ([^:\s]+):0x([0-9A-Fa-f]+); // (.*)$")
CODE_SECTIONS = (".init", ".text")
LANG = {".c": "c", ".cpp": "cpp", ".cp": "cpp", ".cxx": "cpp", ".s": "asm"}


def read_config(path):
    """The few config.yml keys this tool needs: {"object", "hash", "symbols", "splits", "name", "modules": [...]}.
    A line reader, not YAML: top-level scalars and the "modules:" list of "- object:" entries."""
    top, mods, cur, in_mods = {}, [], None, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.split(" #", 1)[0].rstrip()
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "-")):
            in_mods = s.startswith("modules:")
            m = re.match(r"^(\w+):\s*(\S.*)?$", s)
            if m and m.group(2) and not in_mods:
                top[m.group(1)] = m.group(2).strip().strip("'\"")
            continue
        if not in_mods:
            continue
        m = re.match(r"^-\s+(\w+):\s*(\S.*)$", s)
        if m:
            cur = {m.group(1): m.group(2).strip().strip("'\"")}
            mods.append(cur)
            continue
        m = re.match(r"^\s+(\w+):\s*(\S.*)$", s)
        if m and cur is not None and line.startswith("  ") and not line.startswith("    "):
            cur[m.group(1)] = m.group(2).strip().strip("'\"")
    top["modules"] = mods
    return top


def read_symbols(path):
    """[(addr, size, section, name)] of every type:function symbol in a dtk symbols.txt (in a REL's, addr is the
    offset in its section)."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = SYMBOL.match(line.strip())
        if not m or "type:function" not in m.group(4):
            continue
        attrs = dict(a.split(":", 1) for a in m.group(4).split() if ":" in a)
        size = int(attrs.get("size", "0"), 16)
        out.append((int(m.group(3), 16), size, m.group(2), m.group(1)))
    return out


def read_splits(path):
    """[(lo, hi, language)] of the code ranges of each translation unit in a dtk splits.txt."""
    out, unit = [], None
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith(":") and line.strip() != "Sections:":
            unit = line.rstrip()[:-1]
            continue
        m = re.match(r"^\s+(\S+)\s+start:0x([0-9A-Fa-f]+)\s+end:0x([0-9A-Fa-f]+)", line)
        if m and unit and m.group(1) in CODE_SECTIONS:
            out.append((int(m.group(2), 16), int(m.group(3), 16), LANG.get(pathlib.Path(unit).suffix, "other")))
    return sorted(out)


def code_section_names(path):
    """The code sections a dtk splits.txt's "Sections:" block lists, in its order (the module's section order)."""
    out, on = [], False
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip() == "Sections:":
            on = True
            continue
        if on and line and not line[0].isspace():
            break
        m = re.match(r"^\s+(\S+)\s+.*\btype:code\b", line)
        if on and m:
            out.append(m.group(1))
    return out


def versions(decomp):
    return sorted(p.name for p in (decomp / "config").iterdir() if (p / "config.yml").exists())


class Version:
    """One version folder of a decomp: its main DOL's functions and its REL modules."""

    def __init__(self, decomp, version):
        self.decomp, self.version = pathlib.Path(decomp), version
        self.dir = self.decomp / "config" / version
        self.cfg = read_config(self.dir / "config.yml")
        self.symbols_path = self._path(self.cfg.get("symbols"), "symbols.txt")
        self.splits_path = self._path(self.cfg.get("splits"), "splits.txt")
        self.functions = [f for f in read_symbols(self.symbols_path) if f[2] in CODE_SECTIONS]
        self.modules = self.cfg["modules"]

    def _path(self, given, default):
        return self.decomp / given if given else self.dir / default

    def dol_sha1(self):
        return (self.cfg.get("hash") or "").lower()

    def rel_modules(self):
        """[{"object", "functions": [(offset, size, section, name)], "code_sections": [names in order]}] per REL
        module whose symbols.txt exists (its functions only)."""
        out = []
        for m in self.modules:
            p = self.decomp / m["symbols"] if m.get("symbols") else None
            fns = read_symbols(p) if p and p.exists() else []
            splits = self.decomp / m["splits"] if m.get("splits") else None
            out.append({"object": m.get("object", "?"), "functions": fns, "code_sections": code_section_names(splits)})
        return out

    def module_functions(self):
        """[(module object, functions)] counted from each REL's symbols.txt."""
        return [(m["object"], len(m["functions"])) for m in self.rel_modules()]

    def languages(self, modules=False):
        """{"c": n, "cpp": n, "asm": n, "unsplit": n}: main.dol functions (with modules, the REL modules'
        functions) by the language of their translation unit."""
        sets = [(self.splits_path, self.functions)]
        if modules:
            sets = [(self.decomp / m["splits"], read_symbols(self.decomp / m["symbols"])) for m in self.modules
                    if m.get("splits") and m.get("symbols") and (self.decomp / m["symbols"]).exists()]
        out = {}
        for splits, functions in sets:
            spans = read_splits(splits)
            starts = [s[0] for s in spans]
            for addr, _, _, _ in functions:
                i = bisect.bisect_right(starts, addr) - 1
                lang = spans[i][2] if i >= 0 and spans[i][0] <= addr < spans[i][1] else "unsplit"
                out[lang] = out.get(lang, 0) + 1
        return out


# ---- disc and DOL ----

def dol_size(head):
    """Bytes a DOL occupies: the end of its furthest section (7 text + 11 data offsets and sizes)."""
    offs = struct.unpack(">18I", head[0x00:0x48])
    sizes = struct.unpack(">18I", head[0x90:0xD8])
    return max((o + s for o, s in zip(offs, sizes) if s), default=0)


def dol_bytes(path):
    """The main DOL of a GameCube disc image (GCM/ISO: its offset is the u32 at 0x420), or the file itself."""
    with open(path, "rb") as f:
        head = f.read(0x440)
        if len(head) >= 0x440 and head[0x1c:0x20] == b"\xc2\x33\x9f\x3d":  # the GameCube disc magic
            off = struct.unpack(">I", head[0x420:0x424])[0]
            f.seek(off)
            dh = f.read(0x100)
            f.seek(off)
            return f.read(dol_size(dh))
        f.seek(0)
        return f.read()
