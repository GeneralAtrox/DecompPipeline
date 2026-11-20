"""pytest: the suite's own Ghidra broker folder (core/ghidra_broker DP_BROKER_DIR), set before any test module loads,
so no test files a ticket in the machine's folder, where a HOLD or other sessions' work could keep it waiting and its
ticket would keep theirs waiting. tests/test_sync.py does the same for unittest runs.
"""
import atexit
import os
import pathlib
import shutil
import tempfile

_work = pathlib.Path(__file__).resolve().parent / ".work"
_work.mkdir(parents=True, exist_ok=True)
_BROKER = tempfile.mkdtemp(prefix="broker_", dir=_work)
atexit.register(shutil.rmtree, _BROKER, ignore_errors=True)
os.environ["DP_BROKER_DIR"] = _BROKER


def pytest_collection_modifyitems(config, items):
    """tests/test_tooling.py imports about 100 files' test classes. Where a class's own file was collected too, drop the
    copy collected through the import, so each test runs once (test_tooling.py's load_tests does this for discover)."""
    own = {i.module.__name__ for i in items if getattr(i, "module", None) is not None}
    dup = [i for i in items if getattr(i, "cls", None) is not None and i.module is not None
           and i.cls.__module__ != i.module.__name__ and i.cls.__module__ in own]
    if dup:
        drop = set(map(id, dup))
        items[:] = [i for i in items if id(i) not in drop]
        config.hook.pytest_deselected(items=dup)
