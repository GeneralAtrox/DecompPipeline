"""The tool index (CLAUDE.md "No redundant work": anything new is checked against what exists):
1. every tools/*.py and core/*.py says what it does in a module docstring (a file that predates this check may use a
   leading # comment block instead; core/__init__.py is exempt), so the next agent can find it before writing another;
2. every tools/ or core/ file that is not on master at BASE (when this check landed) names the nearest existing tool
   or module and why it does not fit, in a line "Nearest existing: <tool or module> - <why it doesn't fit>";
3. a new docs/proofs/*/ script without that line is a warning only: proofs are one-off, but most of the 43 before this
   check repeated one of four patterns (a private workspace copy, an A/B judge diff, a cold-vs-served prepare diff,
   their own RAM wait and bounded runner) that tools/ already has or could take.
Needs git for 2 and 3 (skipped without it, or in a clone without BASE). Run: python tests/test_tool_index.py
"""
import ast
import pathlib
import subprocess
import unittest
import warnings

TOOL = pathlib.Path(__file__).resolve().parents[1]
BASE = "a1b7372cc8e92e129e6a952fd712c5c86a9e3d8e"  # master when the check landed: files there are exempt from 2 and 3
LINE = "Nearest existing:"
EXEMPT = {"core/__init__.py"}


def sources():
    return sorted(p for d in ("tools", "core") for p in (TOOL / d).glob("*.py"))


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


if __name__ == "__main__":
    unittest.main()
