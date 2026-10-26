"""A DecompPipeline GameCube workspace, read only: the executable, the target facts, the function bodies and the
harness signatures (harness/<addr>/meta.json)."""
from __future__ import annotations

import json
import pathlib

from .image import Dol
from .translate import Facts, Signature

TARGET = pathlib.Path(__file__).resolve().parents[2] / "target.json"  # targets/gamecube_ppc, which holds this translator


class Workspace:
    def __init__(self, root, target=TARGET):
        self.root = pathlib.Path(root)
        self.project = json.loads((self.root / "project.json").read_text())
        self.binary = (self.root / self.project["binary"]).resolve()
        self.dol = Dol(self.binary.read_bytes())
        tj = json.loads(pathlib.Path(target).read_text())
        self.facts = Facts.from_target(tj, self.project.get("fixed_regs", {}))
        self.bodies = {}
        for ln in (self.root / "state" / "function_bodies.txt").read_text().splitlines():
            t = ln.split()
            if len(t) == 3:
                self.bodies.setdefault(int(t[0], 16), []).append((int(t[1], 16), int(t[2], 16)))

    def meta(self, addr):
        if not hasattr(self, "_harnessed"):  # one listing, not a file check per function (returns.analyse asks all)
            self._harnessed = {p.parent.name for p in (self.root / "harness").glob("*/meta.json")}
        p = self.root / "harness" / f"{addr:08x}" / "meta.json"
        return json.loads(p.read_text()) if p.parent.name in self._harnessed else None

    def signature(self, addr):
        m = self.meta(addr)
        return Signature.from_meta(m) if m else Signature.generic_for(addr)

    def matched(self):
        return sorted(int(p.stem, 16) for p in (self.root / "matched").glob("*.c"))
