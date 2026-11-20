"""The tool index (CLAUDE.md "No redundant work": anything new is checked against what exists):
1. every tools/*.py, tools/AutoOnboard/*.py and core/*.py says what it does in a module docstring (a file that predates this check may use a
   leading # comment block instead; core/__init__.py is exempt), so the next agent can find it before writing another;
2. every tools/ or core/ file that is not on master at BASE (when this check landed) names the nearest existing tool
   or module and why it does not fit, in a line "Nearest existing: <tool or module> - <why it doesn't fit>";
3. a new docs/proofs/*/ script without that line is a warning only: proofs are one-off, but most of the 43 before this
   check repeated one of four patterns (a private workspace copy, an A/B judge diff, a cold-vs-served prepare diff,
   their own RAM wait and bounded runner) that tools/ already has or could take.
4. docs/TOOLS.md's tool list, between its tools-index markers, is generated: every tools/*.py with the first line of its
   module docstring (a placeholder when it has none), so no tool goes unlisted again (48 of 84 had by 2026-11-09).
   After adding, removing or redescribing a tool, regenerate it: python tests/test_tool_index.py --write
Needs git for 2 and 3 (skipped without it, or in a clone without BASE). Run: python tests/test_tool_index.py
"""
import ast
import pathlib
import re
import subprocess
import sys
import unittest
import warnings

TOOL = pathlib.Path(__file__).resolve().parents[1]
BASE = "a1b7372cc8e92e129e6a952fd712c5c86a9e3d8e"  # master when the check landed: files there are exempt from 2 and 3
LINE = "Nearest existing:"
EXEMPT = {"core/__init__.py"}
DOC = TOOL / "docs" / "TOOLS.md"
WRITE = "python tests/test_tool_index.py --write"
BEGIN = "<!-- tools-index:begin (generated from each tool's docstring by tests/test_tool_index.py; do not edit by hand) -->"
END = "<!-- tools-index:end -->"


def sources():
    return sorted(p for d in ("tools", "tools/AutoOnboard", "core") for p in (TOOL / d).glob("*.py"))


def rel(p):
    return p.relative_to(TOOL).as_posix()


def head(p):
    """The module docstring, else the leading # comment block (a file that predates the check)."""
    text = p.read_text(encoding="utf-8", errors="replace")
    doc = ast.get_docstring(ast.parse(text))
    if doc:
        return doc, True
    lines = []
    for ln in text.splitlines():
        if not ln.startswith("#"):
            break
        lines.append(ln.lstrip("#").strip())
    return "\n".join(lines), False


def on_base():
    """The paths on master at BASE, or None (no git, or BASE not in this clone)."""
    try:
        r = subprocess.run(["git", "ls-tree", "-r", "--name-only", BASE], cwd=TOOL, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return set(r.stdout.decode().split("\n")) if r.returncode == 0 else None


def summary(p):
    """A tool's line in docs/TOOLS.md: its docstring's first line, less a leading "name.py:". A file without a docstring
    gets a placeholder (followed by the first line of its leading # comment, if any), and so does one that does not
    parse, so neither stops the list being written."""
    try:
        text, is_doc = head(p)
    except (SyntaxError, ValueError):
        return "*(no docstring: the file does not parse)*"
    para = []  # the first paragraph, its wrapped lines joined, cut at the end of its first sentence
    for ln in (ln.strip() for ln in text.splitlines() if not ln.startswith("!")):
        if para and not ln:
            break
        para += [ln] if ln else []
    m = re.match(r"(.+?[^.][.!?])(\s|$)", " ".join(para))  # not a run of dots ("0x0c......")
    first = m.group(1) if m else " ".join(para)
    if len(first) > 160:
        first = first[:160].rsplit(" ", 1)[0] + " ..."
    if not is_doc:
        return "*(no docstring)*" + (f" {first}" if first else "")
    return re.sub(rf"^(tools/)?{re.escape(p.name)}\s*[:-]\s+", "", first) or first


def index_lines():
    """The generated list: every tools/*.py by name, one line each."""
    ps = sorted((TOOL / "tools").glob("*.py"), key=lambda p: p.name) + sorted((TOOL / "tools" / "AutoOnboard").glob("*.py"))
    return [f"- `{p.relative_to(TOOL / 'tools').as_posix()}`: {summary(p)}" for p in ps]


def split_doc(text):
    """docs/TOOLS.md as (the text up to the begin marker, the list's lines, the text from the end marker), or None
    when a marker is missing."""
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        return None
    body = text[start + len(BEGIN):end].strip("\n")
    return text[:start + len(BEGIN)], body.split("\n") if body else [], text[end:]


def write_index():
    """Rewrites the generated list in docs/TOOLS.md, keeping the file's own line endings."""
    raw = DOC.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    parts = split_doc(raw.replace("\r\n", "\n"))
    if parts is None:
        sys.exit(f"{rel(DOC)}: no generated tool list; put the lines\n{BEGIN}\n{END}\nwhere it goes, then run {WRITE}")
    lines = index_lines()
    DOC.write_bytes("\n".join([parts[0], *lines, parts[2]]).replace("\n", nl).encode("utf-8"))
    print(f"{rel(DOC)}: {len(lines)} tools listed")


class Index(unittest.TestCase):
    def test_says_what_it_does(self):
        base = on_base()
        missing = []
        for p in sources():
            if rel(p) in EXEMPT:
                continue
            text, is_doc = head(p)
            old = base is None or rel(p) in base
            if not text.strip() or (not is_doc and not old):
                missing.append(rel(p))
        self.assertEqual(missing, [], "add a module docstring: what the file does and how it is run")

    def test_new_files_name_the_nearest_existing(self):
        base = on_base()
        if base is None:
            self.skipTest(f"git or {BASE[:7]} not available")
        missing = [rel(p) for p in sources() if rel(p) not in base and rel(p) not in EXEMPT
                   and LINE not in head(p)[0]]
        self.assertEqual(missing, [], f'each new tool or module needs a line "{LINE} <tool or module> - <why it '
                                      "doesn't fit>\" in its docstring (CLAUDE.md \"No redundant work\")")

    def test_new_proof_scripts_warn(self):
        base = on_base()
        if base is None:
            self.skipTest(f"git or {BASE[:7]} not available")
        for p in sorted((TOOL / "docs" / "proofs").glob("*/*")):
            if p.suffix in (".py", ".sh", ".ps1") and rel(p) not in base and \
                    LINE not in p.read_text(encoding="utf-8", errors="replace"):
                warnings.warn(f"{rel(p)}: a new proof script without \"{LINE}\" - check tools/ first (regress.py "
                              "--parity/--list, prepare_list.py, oracle_probe.py, tools/bounded.py) and say why "
                              "none fits", stacklevel=1)

    def test_tools_md_lists_every_tool(self):
        parts = split_doc(DOC.read_text(encoding="utf-8"))
        self.assertIsNotNone(parts, f"docs/TOOLS.md lost the markers of its generated tool list: put back {BEGIN!r} "
                                    f"and {END!r}, then run {WRITE}")
        self.assertEqual(parts[1], index_lines(), f"docs/TOOLS.md's tool list is out of date: run {WRITE}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        write_index()
    else:
        unittest.main()
