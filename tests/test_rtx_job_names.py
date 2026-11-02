"""core/rtx/job.py: no function rebinds a name the module imports. make() once defined `def pointer` while the module imports
`pointer` (core/rtx/pointer.py), which made `pointer` a local of make() and turned `pointer.pointer_ca(...)` into a call on the
inner function: AttributeError for every function the readable stage lifted through tools/readable_tx.py. Nothing runs make()
without a registry and a harness, so this checks the source: every name bound at module level by an import must not also be
bound (def, assignment, parameter, import) inside a function of the file.
Run: python -W error tests/test_rtx_job_names.py
"""
import ast
import pathlib
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "core" / "rtx" / "job.py"


def bound_names(fn):
    for n in ast.walk(fn):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
            yield n.name
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            yield n.id
        elif isinstance(n, ast.arg):
            yield n.arg
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                yield (a.asname or a.name).split(".")[0]


class JobNames(unittest.TestCase):
    def test_no_function_shadows_an_imported_module(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        imported = {(a.asname or a.name).split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        self.assertIn("pointer", imported)  # the case this guards
        clashes = sorted((fn.name, name) for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                         for name in set(bound_names(fn)) & imported)
        self.assertEqual(clashes, [])


if __name__ == "__main__":
    unittest.main()
