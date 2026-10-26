"""The machine readable file of one function for the pipeline (core/draft_run): the translator's raw C of it, lifted
by core/rtx/lift with the target's readable pass (targets/<target>/rtx.py).

The raw C is the translator's own: a translated match's matched/ADDR.c, else the translator stage's last output
(runs/ADDR/translated/work.c), else a fresh translation (core/translate_stage, bounded by its timeout). A function
the translator cannot translate, or whose raw C the lift has no form for, has no machine file (Unsupported says
why); the judge decides the rest, unchanged.
"""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile

from .. import paths, translate_stage
from ..draft import strip_comments
from . import job as rjob
from . import lift
from .flow import Unsupported

_SPECS = {}


def spec(target=None):
    """The target's readable pass (targets/<target>/rtx.py SPEC), or None when it has none."""
    target = target or paths.PROJECT["target"]
    if target not in _SPECS:
        f = paths.TOOL / "targets" / target / "rtx.py"
        sp = None
        if f.is_file():
            s = importlib.util.spec_from_file_location(f"rtx_{target}", f)
            mod = importlib.util.module_from_spec(s)
            s.loader.exec_module(mod)
            sp = mod.SPEC
        _SPECS[target] = sp
    return _SPECS[target]


def raw_c(cfg, addr):
    """The translator's raw C of addr, comments removed."""
    matched = paths.MATCHED / f"{addr}.c"
    if matched.is_file() and paths.read_json(paths.LEDGER, {}).get(addr, {}).get("source") == "translated":
        return strip_comments(matched.read_text(encoding="utf-8", errors="replace"))
    last = paths.RUNS / addr / "translated" / "work.c"
    if last.is_file():
        return strip_comments(last.read_text(encoding="utf-8", errors="replace"))
    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d) / "work.c"
        info = translate_stage.translate(cfg, addr, out)
        if not info.get("ok"):
            raise Unsupported(f"no translation: {info.get('why')}")
        return strip_comments(out.read_text(encoding="utf-8", errors="replace"))


def machine(cfg, reg, addr, meta, raw=None):
    """The machine readable file's text. Raises Unsupported (or readable.RegistryError) when there is none."""
    sp = spec()
    if sp is None:
        raise Unsupported(f"no readable pass for target {paths.PROJECT['target']} (targets/<target>/rtx.py)")
    raw = raw if raw is not None else raw_c(cfg, addr)
    return lift.lift(raw, rjob.make(cfg, reg, addr, meta, raw, sp))
