"""Every module in tools/AutoOnboard is reachable from the tool's entry point, and the way to use it is written down.
1. an import graph (ast, no imports run) from autoonboard.py and the stage table (onboard_stages.py), following
   `import X` and `from X import ...` of modules in tools/AutoOnboard, inside functions too, reaches every .py there: no
   script is left that nothing calls (new onboarding work is a stage or a fix in a console module, never a new script);
2. AGENTS.md has an "Onboarding a game" section.
Run: py -3.12 -m pytest -q tests/test_autoonboard_reach.py
"""
import ast
import pathlib
import re
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
AO = TOOL / "tools" / "AutoOnboard"
ROOTS = ("autoonboard", "onboard_stages")


def imports(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def reached():
    have = {p.stem for p in AO.glob("*.py")}
    seen, todo = set(), [r for r in ROOTS if r in have]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo += [m for m in imports(AO / f"{n}.py") if m in have and m not in seen]
    return seen, have


class Reach(unittest.TestCase):
    def test_every_module_is_reached_from_the_entry_point(self):
        seen, have = reached()
        self.assertEqual(sorted(have - seen), [], "modules nothing in tools/AutoOnboard imports")

    def test_agents_md_says_how_to_onboard_a_game(self):
        text = (TOOL / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"^#+ Onboarding a game\s*$", text, re.M), "AGENTS.md has no 'Onboarding a game' section")


if __name__ == "__main__":
    unittest.main()
