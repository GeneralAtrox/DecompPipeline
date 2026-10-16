"""The decompile scope: which catalog functions a project decompiles. One definition; every consumer uses it.

In scope means: inside the game's code ranges, or listed in project.json "extra_functions".
  game ranges      "game_ranges" ([["8c558000", "8c5f9dd0"], ...], for a combined image of overlays), else the main
                   binary from its load address to "game_end"; a project with neither key: every catalog function.
  extra_functions  library code a range cannot select (e.g. a studio's engine linked between SDK code): a list of hex
                   addresses, or the name of a text file in the workspace (one address per line, '#' starts a
                   comment). An extra inside the game ranges is a game function (counted once, as game). Without the
                   key a project behaves exactly as before. An extra that is not a catalog function is a doctor FAIL.
Everything else outside the ranges (the SDK the port replaces) is never decompiled.

Two meanings, kept apart (see the audit table in STAGE_REPORT_scope.md):
  game_ranges(cfg)       the game's code address ranges: catalog setup (TcRuns, TcCont args), byte coverage
                         (tools/completeness.py, targets/*/tools/coverage.py), SDK-FID's game/library split;
  Scope / in_scope ...   the functions to decompile: queue, tiers, systems, audits, final audit, cross-project import.
Pure (no core.paths): takes project.json dicts and workspace paths.
"""
import pathlib
import re

from . import retire

LOAD = 0x8C010000
KEY = "extra_functions"


def game_ranges(cfg):
    """[(lo, hi)] of the game's code: "game_ranges", else the main binary from its load address to "game_end"."""
    if cfg.get("game_ranges"):
        return [(int(lo, 16), int(hi, 16)) for lo, hi in cfg["game_ranges"]]
    return [(LOAD, int(cfg["game_end"], 16))]


def _norm(x, where):
    a = str(x).strip().lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{1,8}", a):
        raise ValueError(f"{KEY}: {where}: {x!r} is not a hex address")
    return a.rjust(8, "0")


def listed(cfg, ws=None):
    """[addr] that "extra_functions" lists (normalized, file order, duplicates dropped); [] without the key.
    A file name needs ws (relative paths resolve against the workspace). A bad entry or a missing file raises."""
    v = cfg.get(KEY)
    if not v:
        return []
    if isinstance(v, str):
        if ws is None:
            raise ValueError(f"{KEY} names a file ({v}): the workspace is needed to read it")
        path = pathlib.Path(ws) / v
        if not path.exists():
            raise FileNotFoundError(f"project.json {KEY}: {path} does not exist")
        items = [(f"{path.name}:{n}", ln.split("#", 1)[0].strip())
                 for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)]
    elif isinstance(v, list):
        items = [(f"item {n}", x) for n, x in enumerate(v, 1)]
    else:
        raise ValueError(f"project.json {KEY} must be a list of hex addresses or a file name, not {type(v).__name__}")
    out, seen = [], set()
    for where, x in items:
        if x == "":
            continue
        a = _norm(x, where)
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


class Scope:
    """The scope of one project: .ranges (None = every function), .extras (listed, outside the ranges),
    .listed_in_ranges (listed, but game functions anyway)."""

    def __init__(self, cfg, ws=None):
        self.ranges = game_ranges(cfg) if cfg.get("game_ranges") or cfg.get("game_end") else None
        names = listed(cfg, ws)
        self.extras = {a for a in names if not self.in_ranges(a)}
        self.listed_in_ranges = sorted((a for a in names if self.in_ranges(a)), key=lambda a: int(a, 16))

    def in_ranges(self, addr):
        """Inside the game's code ranges (the game's own functions)."""
        return self.ranges is None or any(lo <= int(addr, 16) < hi for lo, hi in self.ranges)

    def is_extra(self, addr):
        return addr in self.extras

    def __contains__(self, addr):
        return addr in self.extras or self.in_ranges(addr)

    def split(self, addrs):
        """(game functions, extra functions) of addrs, each in address order; out-of-scope ones dropped."""
        key = lambda a: int(a, 16)  # noqa: E731
        return (sorted((a for a in addrs if self.in_ranges(a)), key=key),
                sorted((a for a in addrs if a in self.extras), key=key))


def of(ws):
    """The Scope of a workspace (reads its project.json)."""
    pj = retire._read(ws, "project.json", {}) or {}
    return Scope(pj, ws)


def in_scope(cfg, addr, ws=None):
    """addr is a function to decompile: inside the game ranges or listed in "extra_functions"."""
    return addr in Scope(cfg, ws)


def scope_functions(ws):
    """Catalog functions to decompile (game ranges, then extras merged in), in address order."""
    sc = of(ws)
    return sorted((a for a in retire.functions(ws) if a in sc), key=lambda a: int(a, 16))


def game_functions(ws):
    """Catalog functions inside the game ranges, in address order."""
    sc = of(ws)
    return sorted((a for a in retire.functions(ws) if sc.in_ranges(a)), key=lambda a: int(a, 16))


def extra_functions(ws):
    """Catalog functions listed in "extra_functions" outside the game ranges, in address order."""
    sc = of(ws)
    return sorted((a for a in retire.functions(ws) if a in sc.extras), key=lambda a: int(a, 16))


def missing_extras(ws):
    """Listed extras that are not catalog functions (a typo, or a function a catalog rebuild removed)."""
    funcs = retire.functions(ws)
    return sorted((a for a in of(ws).extras if a not in funcs), key=lambda a: int(a, 16))


def describe(cfg):
    """One line for a report: where the scope's extras come from."""
    v = cfg.get(KEY)
    return "" if not v else (f"{KEY} file {v}" if isinstance(v, str) else f"{KEY} list ({len(v)})")
