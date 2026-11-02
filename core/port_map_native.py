"""Which original address each native function implements, what only cites one, and what moved under ported code.

A binding says the build uses this code in place of the address. It is not a proof that it behaves the same; the
port's replays and side-by-side runs prove that.
  hardware  game.json `hardware` {addr: name}, with a definition in a source
  registry  a non-static definition at column 0 named as the registry names the address (port/CONTRACT.md: it replaces
            that function's stub; port/tools/gen_port.defined_names is the same scan the build uses)
  body      a definition called fn_ADDR in a source or in hand/ (Robo Pit's hand-kept bodies)
  tag       a comment `implements ADDR` on a definition or the two lines above it: the way to bind native code that
            has neither a registry name nor a game.json entry
  wrap      game.json `wrap` {addr: name}: wraps the original, which keeps running, so its state stays the decomp's
A citation is an address in a comment with no binding. It is a mention and not a proof: the code may do something else.

Drift: `record` stores what each bound address stands on (raw C, readable C, the harness, the verdicts) in a baseline
file, and `drift` compares. An address with no baseline falls back to git: the last commit of the native lines against
the last commits of the port's own decomp copy (game.json `decomp`). No baseline and no git is reported as such, never
as "no drift".

Nearest existing: port/tools/gen_port.py (finds the same replacements, only to generate stubs at build time, without
addresses, citations or history) and core/callee_drift.py (drift of callee signatures between harnesses, not of decomp
under native code).
"""
import datetime
import json
import pathlib
import re
import subprocess
import sys

from .port_map import _json, _text, text_sha
from .raw_names import mask

DEF_AT = re.compile(r"^(?:[A-Za-z_][\w \t*]*?[\s*])?([A-Za-z_]\w*)\s*\([^;]*$")
TAG = re.compile(r"\bimplements\s+(?:0x)?([0-9a-fA-F]{8})\b")
BODY = re.compile(r"^fn_([0-9a-f]{8})$")
COMMENT_ADDR = re.compile(r"\b(?:0x)?([0-9a-fA-F]{8})u?\b")
NOT_DEFS = ("if", "while", "for", "switch", "return", "sizeof")
GIT_TIMEOUT_S = 30
DECOMP_FIELDS = (("raw", "raw C"), ("readable", "readable C"), ("vectors", "harness vectors"),
                 ("expected", "harness expected results"), ("raw_status", "raw status"),
                 ("raw_verdict", "raw verdict"), ("readable_status", "readable status"),
                 ("readable_verified", "readable verify time"))


def _gen_port(kit):
    """The build's own scan of definitions and its C-library names (port/tools/gen_port.py)."""
    tools = str(pathlib.Path(kit) / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import gen_port  # noqa: PLC0415  (port/tools is not a package)
    return gen_port


def def_lines(path):
    """{name: 1-based line} of the functions defined at column 0, static ones too, in a C file."""
    out = {}
    lines = (_text(path) or "").splitlines()
    code = mask("\n".join(lines))[0].split("\n")
    for i, ln in enumerate(code):
        m = DEF_AT.match(ln)
        if m and m.group(1) not in NOT_DEFS and ln[:1].isalpha():
            tail = " ".join(code[i:i + 12])
            if "{" in tail and ";" not in tail.split("{", 1)[0]:
                out.setdefault(m.group(1), i + 1)
    return out


def _sources(port, game):
    """(sources the build compiles, source files in the port's folders it does not), as paths."""
    listed = [port / s for s in game.get("sources") or []]
    extra = [p for d in ("src", "hand") for p in sorted((port / d).glob("*.c")) if p not in listed]
    return listed, extra


class Bindings:
    """native: {addr: [binding]}; kit: {name: {"on", "where"}}; citations: [...]; unbound: native functions with no
    address. Addresses are only bound to functions of the workspace given."""

    def __init__(self, ws, port=None, kit=None):
        self.port = pathlib.Path(port) if port else None
        self.kit_dir = pathlib.Path(kit) if kit else pathlib.Path(__file__).resolve().parents[1] / "port"
        self.game = (_json(self.port / "game.json") or {}) if self.port else {}
        self.native, self.kit, self.citations, self.unbound, self.not_built = {}, {}, [], [], []
        self.kit_addr, self.kit_defs = {}, {}  # kit_addr: game.json `hardware` addresses whose name the kit defines
        self._kit(ws)
        if self.port:
            self._native(ws)
        self.cited_only = {c["addr"] for c in self.citation_only()}

    def _kit(self, ws):
        gen = _gen_port(self.kit_dir)
        on = set(self.game.get("sdk") or [])
        sdk = sorted((self.kit_dir / "sdk").glob("*.c"))
        runtime = sorted((self.kit_dir / "runtime").rglob("*.c"))
        for path in sdk:
            state = path.stem in on or not self.port
            for name in gen.defined_names([path]):
                self.kit_defs[name] = {"on": state, "where": f"port/sdk/{path.name}"}
        for path in runtime:
            for name in gen.defined_names([path]):
                where = path.relative_to(self.kit_dir / "runtime").as_posix()
                self.kit_defs.setdefault(name, {"on": True, "where": f"port/runtime/{where}"})
        for name in set(gen.CRT) | set(gen.OWN_CRT) | set(gen.GUEST_CRT):
            self.kit_defs.setdefault(name, {"on": True, "where": "the host C library (port/tools/gen_port.py)"})
        self.kit = {name: where for name, where in self.kit_defs.items() if name in ws.by_name}

    def _add(self, addr, kind, fn, path, line, defined=True, built=True):
        self.native.setdefault(addr, []).append({"kind": kind, "fn": fn, "file": path.relative_to(self.port).as_posix()
                                                 if path else "game.json", "line": line, "defined": defined,
                                                 "built": built})

    def _native(self, ws):
        listed, extra = _sources(self.port, self.game)
        defs, bound_lines = {}, set()
        for path in listed + extra:
            built = path in listed or path.parent.name == "hand"
            if not built:
                self.not_built.append(path.relative_to(self.port).as_posix())
            for name, line in def_lines(path).items():
                defs.setdefault(name, (path, line, built))
        for addr, name in (self.game.get("hardware") or {}).items():
            addr = addr.lower()
            path, line, built = defs.get(name, (None, 0, False))
            if path is None and name in self.kit_defs:
                self.kit_addr[addr] = self.kit_defs[name]
                continue
            self._add(addr, "hardware", name, path, line, defined=path is not None, built=built)
            bound_lines.add(name)
        for name, (path, line, built) in defs.items():
            m = BODY.match(name)
            addr = m.group(1) if m else ws.by_name.get(name)
            if addr and name not in bound_lines:
                self._add(addr, "body" if m else "registry", name, path, line, built=built)
                bound_lines.add(name)
        for addr, name in (self.game.get("wrap") or {}).items():
            path, line, built = defs.get(name, (None, 0, False))
            self._add(addr.lower(), "wrap", name, path, line, defined=path is not None, built=built)
            bound_lines.add(name)
        for path in listed + extra:
            self._comments(ws, path, def_lines(path), bound_lines)
        self.unbound = sorted(f"{n} ({defs[n][0].relative_to(self.port).as_posix()}:{defs[n][1]})" for n in defs
                              if n not in bound_lines)

    def _comments(self, ws, path, here, bound_lines):
        """Tags bind; every other function address in a comment is a citation until something else binds it."""
        text = _text(path) or ""
        lines = text.split(chr(10))
        by_line = {line: name for name, line in here.items()}
        built = path in _sources(self.port, self.game)[0] or path.parent.name == "hand"
        rel = path.relative_to(self.port).as_posix()
        for start, end in mask(text)[1]:
            first = text.count(chr(10), 0, start)
            for offset, part in enumerate(text[start:end].split(chr(10))):
                number = first + offset + 1
                for m in TAG.finditer(part):
                    addr = ws.function(m.group(1))
                    line = next((j for j in range(number, number + 4) if j in by_line), None)
                    if addr and line:
                        self._add(addr, "tag", by_line[line], path, line, built=built)
                        bound_lines.add(by_line[line])
                for m in COMMENT_ADDR.finditer(part):
                    addr = ws.function(m.group(1))
                    if addr:
                        self.citations.append({"addr": addr, "file": rel, "line": number,
                                               "text": lines[number - 1].strip()[:120]})

    def citation_only(self):
        """Citations of addresses nothing binds. A wrap or a binding of the same address clears them."""
        return [c for c in self.citations if c["addr"] not in self.native]


def native_table(ws, binds):
    """One row per binding, with the decomp's state: which original address each native function implements."""
    from .port_map import classify
    rows = []
    for addr, items in sorted(binds.native.items()):
        row = classify(ws, addr, binds)
        for b in items:
            rows.append(dict(b, addr=addr, name=ws.names.get(addr), decomp=row["decomp"], why=row["why"]))
    return rows


def record(ws, binds, path, at=None):
    """Write a baseline: each bound address's fingerprint and its native file's hash. Writes only `path`."""
    target = pathlib.Path(path).resolve()
    if ws.path.resolve() in target.parents:
        raise ValueError(f"the baseline may not go inside the workspace: {target}")
    functions = {}
    for addr, items in sorted(binds.native.items()):
        functions[addr] = {"native": [{"fn": b["fn"], "file": b["file"], "kind": b["kind"],
                                       "sha": text_sha(binds.port / b["file"]) if b["file"] != "game.json" else None}
                                      for b in items], "decomp": ws.fingerprint(addr)}
    data = {"schema": 1, "project": ws.project.get("name"),
            "recorded": at or datetime.datetime.now().isoformat(timespec="seconds"), "functions": functions}
    target.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return len(functions)


def _git_time(repo, *args):
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def native_time(port, binding):
    """When the native function's lines last changed (git blame, newest line of its body); None: no git history."""
    path = port / binding["file"]
    lines = (_text(path) or "").splitlines()
    start = binding.get("line") or 0
    if not start or not lines:
        return None
    end = next((i + 1 for i in range(start - 1, len(lines)) if lines[i].startswith("}")), len(lines))
    out = _git_time(path.parent, "blame", "--porcelain", "-L", f"{start},{end}", "--", path.name)
    times = [int(x.split()[1]) for x in (out or "").splitlines() if x.startswith("committer-time ")]
    return max(times) if times else None


def decomp_times(port, game, addr):
    """{what: last commit time} of the port's own decomp copy (game.json `decomp`), for what git tracks."""
    base = (port / str(game.get("decomp") or "../decomp")).resolve()
    out = {}
    for what, rel in (("raw C", f"matched/{addr}.c"), ("readable C", f"readable/src/{addr}.c"),
                      ("harness", f"harness/{addr}/meta.json")):
        text = _git_time(base, "log", "-1", "--format=%ct", "--", rel)
        if text and text.strip():
            out[what] = int(text.strip())
    return out


def drift(ws, binds, baseline=None):
    """{"moved", "no_baseline", "no_decomp", "native_changed", "copy_differs"}. moved: the decomp changed under native
    code. no_decomp: this workspace has no raw or readable C of it, so nothing can move. baseline: a file `record`
    wrote; addresses without one use git."""
    old = (_json(baseline) or {}).get("functions", {}) if baseline else {}
    out = {"moved": [], "no_baseline": [], "no_decomp": [], "native_changed": [], "copy_differs": [],
           "baseline": str(baseline) if baseline else None, "bound": len(binds.native)}
    copy = (binds.port / str(binds.game.get("decomp") or "../decomp")).resolve() if binds.port else None
    for addr, items in sorted(binds.native.items()):
        now = ws.fingerprint(addr)
        name = ws.names.get(addr)
        fns = sorted({b["fn"] for b in items})
        if copy and copy != ws.path.resolve():
            for what, rel in (("raw C", f"matched/{addr}.c"), ("readable C", f"readable/src/{addr}.c")):
                a, b = text_sha(copy / rel), text_sha(ws.path / rel)
                if a and b and a != b:
                    out["copy_differs"].append({"addr": addr, "name": name, "what": what})
        if addr in old:
            rec = old[addr]
            moved = [{"what": label, "was": _short(rec["decomp"].get(k)), "now": _short(now.get(k))}
                     for k, label in DECOMP_FIELDS if rec["decomp"].get(k) != now.get(k)]
            if moved:
                out["moved"].append({"addr": addr, "name": name, "native": fns, "by": "baseline", "changes": moved})
            for n in rec.get("native") or []:
                if n.get("sha") and n["sha"] != text_sha(binds.port / n["file"]):
                    out["native_changed"].append({"addr": addr, "fn": n["fn"], "file": n["file"]})
            continue
        if now["raw"] is None and now["readable"] is None:
            out["no_decomp"].append({"addr": addr, "name": name, "native": fns})
            continue
        made = [t for t in (native_time(binds.port, b) for b in items if b["file"] != "game.json") if t]
        times = decomp_times(binds.port, binds.game, addr) if made else {}
        if not made or not times:
            out["no_baseline"].append({"addr": addr, "name": name, "native": fns,
                                       "why": "no git history of the native lines" if not made else
                                       "git tracks none of this address in the port's decomp copy"})
            continue
        written = max(made)
        later = sorted(what for what, t in times.items() if t > written)
        if later:
            out["moved"].append({"addr": addr, "name": name, "native": fns, "by": "git",
                                 "changes": [{"what": w, "was": _day(written), "now": _day(times[w])} for w in later]})
    return out


def _short(value):
    return value[:12] if isinstance(value, str) and len(value) == 64 else value


def _day(stamp):
    return datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
