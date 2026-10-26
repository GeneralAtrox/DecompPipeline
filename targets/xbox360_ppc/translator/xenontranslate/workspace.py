"""A DecompPipeline Xbox 360 workspace, read only: the loaded image (image.bin + image.json), the target facts
(targets/xbox360_ppc/target.json), the function bodies and frame helpers DP's catalog found
(catalog_inputs.json), and the harness signatures (harness/<addr>/meta.json) once DP's proof has made them."""
from __future__ import annotations

import json
import pathlib

from .image import XexImage
from .translate import Facts, Signature

TARGET = pathlib.Path(__file__).resolve().parents[2] / "target.json"  # targets/xbox360_ppc, which holds this translator


class Workspace:
    def __init__(self, root, target=TARGET):
        self.root = pathlib.Path(root)
        self.project = json.loads((self.root / "project.json").read_text())
        self.binary = (self.root / self.project["binary"]).resolve()
        ij = json.loads((self.root / "image.json").read_text())
        secs = [(s["start"], s["end"], bool(s.get("code"))) for s in ij.get("sections", [])]
        self.dol = XexImage(self.binary.read_bytes(), ij.get("load_address", 0x82000000), secs)
        tj = json.loads(pathlib.Path(target).read_text())
        cat = json.loads((self.root / "catalog_inputs.json").read_text())
        self.facts = Facts.from_target(tj, self.project.get("fixed_regs", {}), cat.get("helper_ranges", []))
        self.catalog = {int(f["addr"], 16): f for f in cat["functions"]}
        self.bodies = {a: [(a, a + f["size"])] for a, f in self.catalog.items() if f["kind"] == "function"}
        self.imports = cat.get("imports", [])

    def harnessed(self):
        """The addresses with a harness meta.json, the only functions meta() can answer for (one listing)."""
        if not hasattr(self, "_harnessed"):
            self._harnessed = {int(p.parent.name, 16) for p in (self.root / "harness").glob("*/meta.json")}
        return self._harnessed

    def meta(self, addr):
        p = self.root / "harness" / f"{addr:08x}" / "meta.json"
        return json.loads(p.read_text()) if addr in self.harnessed() else None

    def signature(self, addr):
        m = self.meta(addr)
        return Signature.from_meta(m) if m else Signature.generic_for(addr)

    def refused(self, addr):
        """DP's phase-1 refusal for the function (catalog 'refuse'), or None."""
        f = self.catalog.get(addr)
        return f.get("refuse") if f else None
