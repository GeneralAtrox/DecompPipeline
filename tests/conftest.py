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
