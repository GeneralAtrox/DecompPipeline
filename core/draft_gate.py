"""The adoption gate for machine files (batch 24; readable-tx): no unnamed constants.

Batch 23 adopted every draft that passed the judge and read as named C. A spot-check on TC showed that the short ones
read as well as Haiku's files, while longer ones keep bare numbers the headers already name (`781250u` for
CLOCK_TIMER_HZ, `type == 200` for ANIM_REC_200), meaningless locals and unrolled code. So a passing draft is
adopted only when:
- it has at most `max_lines` non-blank lines, the #include not counted (config `readable_draft_max_lines`,
  default 6: about two statements);
- none of its integer literals (other than 0 and 1) has a value that an integer #define or explicit enum value in
  readable/include names.
Otherwise the draft is not adopted: with `--draft start` it is the agent's starting work.c (it already passes).

readable-tx (the user's decision, 2026-10-23): no length limit by default. A machine file of any length that passes the
judge, reads as named C and names its constants is adopted; an agent reviews it when it next works on the function
(the ledger's readable_by: machine). Config readable_draft_max_lines sets a limit again.
"""
import functools
import re

from . import readable
from .raw_names import mask

MAX_LINES = None  # any length (batch 24's limit was 6)
TRIVIAL = {0, 1}
_NUM = r"(-?(?:0[xX][0-9a-fA-F]+|\d+))[uUlL]*"
_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+\(?[ \t]*" + _NUM + r"[ \t]*\)?[ \t]*$", re.M)
_ENUM = re.compile(r"\b([A-Z_][A-Z0-9_]*)\s*=\s*" + _NUM + r"\s*(?=[,}])")
_LITERAL = re.compile(r"(?<![\w.])(0[xX][0-9a-fA-F]+|\d+)[uUlL]*(?![\w.])")


def _value(s):
    neg = s.startswith("-")
    s = s.lstrip("-")
    v = int(s, 16) if s[:2].lower() == "0x" else int(s, 10)
    return -v if neg else v


def _scan(texts):
    out = {}
    for text in texts:
        code = mask(text)[0]
        for rx in (_DEFINE, _ENUM):
            for m in rx.finditer(code):
                out.setdefault(_value(m.group(2)), []).append(m.group(1))
    return {v: sorted(set(n)) for v, n in out.items()}


@functools.lru_cache(maxsize=8)
def _cached(key):
    return _scan(p.read_text(encoding="utf-8", errors="replace") for p, _ in key)


def named_values(include_dir=None):
    """{value: [names]}: every integer #define and explicit enum value in the headers under readable/include (the
    generated game.h is left out: it names addresses, not constants). Cached while no header changes."""
    root = include_dir or readable.INCLUDE
    files = sorted(p for p in root.rglob("*.h") if p.name != "game.h") if root.exists() else []
    return _cached(tuple((p, p.stat().st_mtime_ns) for p in files))


def lines(text):
    return sum(1 for ln in text.splitlines() if ln.strip() and not re.match(r"\s*#\s*include\b", ln))


def reasons(text, values=None, max_lines=MAX_LINES):
    """Why a passing draft is not adopted ([] when it is): too long, or a literal the headers name."""
    out = []
    n = lines(text)
    if max_lines is not None and n > max_lines:
        out.append(f"{n} lines (drafts are adopted up to {max_lines})")
    values = named_values() if values is None else values
    seen = set()
    for m in _LITERAL.finditer(mask(text)[0]):
        v = _value(m.group(1))
        if v in TRIVIAL or v in seen or v not in values:
            continue
        seen.add(v)
        out.append(f"{m.group(0)} is {' or '.join(values[v][:3])}")
    return out
