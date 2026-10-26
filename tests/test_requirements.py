"""requirements.txt names every package the tracked Python code imports from outside the standard library and this
tree (the second waste audit's finding C: networkx was needed and not listed, so 10 tests failed on a fresh machine).
Run: python -W error tests/test_requirements.py (also from test_tooling.py).
"""
import ast
import pathlib
import re
import subprocess
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
DISTS = {"PIL": "Pillow"}  # an import name whose package has another name


def tracked_py():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"], cwd=TOOL,
                         capture_output=True, text=True, timeout=60).stdout  # new files not yet committed too
    return [TOOL / f for f in out.splitlines() if (TOOL / f).is_file()]


def listed():
    names = set()
    for line in (TOOL / "requirements.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.-]+)", line.split("#", 1)[0])
        if m:
            names.add(m.group(1).lower())
    return names


class Requirements(unittest.TestCase):
    def test_every_import_listed(self):
        files = tracked_py()
        local = {p.stem for p in files} | {p.name for p in TOOL.rglob("*") if p.is_dir()}
        need = {}
        for f in files:
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    tops = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    tops = [node.module.split(".")[0]]
                else:
                    continue
                for top in tops:
                    if top not in sys.stdlib_module_names and top not in local and top != "__future__":
                        need.setdefault(DISTS.get(top, top).lower(), f.relative_to(TOOL).as_posix())
        missing = {k: v for k, v in need.items() if k not in listed()}
        self.assertEqual(missing, {}, "imported but not in requirements.txt (package: first file)")
        self.assertIn("networkx", need)


if __name__ == "__main__":
    unittest.main()
