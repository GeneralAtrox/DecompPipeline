"""The oracle hints a game's workspace needs, tracked with the target: targets/xbox360_ppc/games/<game>/hints/<addr>.txt,
the reference set, and games/<game>/game.json {"name", "binary_sha256"}, which says whose image they belong to.

Hints are oracle input data (oracle/hints/<addr>.txt, core/javalint.lint_hints; TcInputs reads them), hand-written per
function where random inputs give too few valid vectors. A workspace held them only on the machine where they were
written: a fresh workspace on another machine prepared Too Human's 824c3530, 824d8420, 82514780, 825ef068, 82bc5158,
82ea7690 and 82eaf930 with too few valid vectors. Now x360setup.py's setup copies the reference set in (install), and
setup and the judge (translator/tools/dp_judge.py) warn when a workspace's set differs from it (differences): a
file missing, extra, or with other content (sha256 of the text with CRLF read as LF, so a checkout's line endings
do not count). A workspace whose binary no game.json names has no reference and is never warned about.

Nearest existing: core/gate.py - it installs one function's watcher-gated hints into a workspace; nothing carried a
game's hints to a new workspace or compared a workspace's set with a reference.
"""
import hashlib
import json
import pathlib
import shutil
import sys

GAMES = pathlib.Path(__file__).resolve().parents[1] / "games"


def text_hash(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def game_of(sha256, games=GAMES):
    """The games/<game> folder whose game.json names this binary, or None."""
    for g in sorted(pathlib.Path(games).glob("*/game.json")):
        if json.loads(g.read_text(encoding="utf-8")).get("binary_sha256", "").lower() == (sha256 or "").lower():
            return g.parent
    return None


def reference(ws, games=GAMES):
    """(the game folder, {file name: text hash}) of the reference set for this workspace's binary, or (None, None)."""
    proj = pathlib.Path(ws) / "project.json"
    sha = json.loads(proj.read_text(encoding="utf-8")).get("binary_sha256") if proj.is_file() else None
    g = game_of(sha, games)
    if g is None:
        return None, None
    return g, {p.name: text_hash(p) for p in sorted((g / "hints").glob("*.txt"))}


def held(ws):
    return {p.name: text_hash(p) for p in sorted((pathlib.Path(ws) / "oracle" / "hints").glob("*.txt"))}


def differences(ws, games=GAMES):
    """How the workspace's hints set differs from the reference: ["missing X", "extra Y", "differs Z"]; [] when equal
    or when there is no reference."""
    g, ref = reference(ws, games)
    if g is None:
        return []
    have = held(ws)
    return ([f"missing {n}" for n in sorted(set(ref) - set(have))] + [f"extra {n}" for n in sorted(set(have) - set(ref))]
            + [f"differs {n}" for n in sorted(set(ref) & set(have)) if ref[n] != have[n]])


def warn(ws, where, games=GAMES, out=None):
    """Print one warning line when the workspace's hints set differs from the reference; returns the differences."""
    d = differences(ws, games)
    if d:
        g, _ = reference(ws, games)
        print(f"WARNING ({where}): {ws} oracle/hints differs from the reference set {g.relative_to(g.parents[2])}"
              f"/hints: {', '.join(d[:12])}{' ...' if len(d) > 12 else ''}", file=out or sys.stderr)
    return d


def install(ws, games=GAMES):
    """Copy the reference set into the workspace's oracle/hints. A file the workspace already holds is kept (a
    watcher-promoted hint is newer than the reference; warn names it). Returns the names copied."""
    g, ref = reference(ws, games)
    if g is None:
        return []
    dst = pathlib.Path(ws) / "oracle" / "hints"
    dst.mkdir(parents=True, exist_ok=True)
    done = []
    for n in ref:
        if not (dst / n).exists():
            shutil.copyfile(g / "hints" / n, dst / n)
            done.append(n)
    return done
