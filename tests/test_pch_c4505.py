"""The port's C4505 in the readable check (core/pch_warnings).

MSVC reports C4505 (a static function never called) in C only under a precompiled header, which the port's build
uses. One pass and one fail control per change:
1. readable_check.build fails a file the port-form pass reports, and passes it when that pass is clean (wiring);
2. the port-form pass reports C4505 for a static function never called and nothing for one that is called (only
   where Visual Studio's 32-bit tools are found).
Synthetic sources. Run: python -W error tests/test_pch_c4505.py (also from test_tooling.py).
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import pch_warnings, readable_check  # noqa: E402


class PchWiring(unittest.TestCase):
    def build(self, pch):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        src = d / "in.c"
        src.write_text("int f(void) { return 0; }\n", encoding="utf-8")
        (d / "cl.exe").write_text("cl", encoding="utf-8")  # the runner's key reads the compiler's size and time
        with mock.patch.object(readable_check, "msvc_env_x86", lambda cfg: {"PATH": ""}), \
                mock.patch.object(shutil, "which", lambda *a, **k: str(d / "cl.exe")), \
                mock.patch.object(readable_check.paths, "run_bounded", lambda *a, **k: (0, "", None)), \
                mock.patch.object(pch_warnings, "check", lambda *a, **k: pch):
            return readable_check.build({}, [str(src)], "/* thunk */\n", {}, d / "b")

    def test_port_form_failure_fails_the_build(self):
        ok, msgs = self.build(["port-form pass", "work.c(4): warning C4505: 'h': unreferenced function"])
        self.assertFalse(ok)
        self.assertIn("C4505", " ".join(msgs))
        self.assertTrue(self.build([])[0])  # control: a clean port-form pass


def vc_env():
    """The 32-bit MSVC environment (tests/test_lockstep's vcvars32), or None."""
    try:
        from test_lockstep import HAVE_VC, game_config
    except ImportError:
        return None
    if not HAVE_VC:
        return None
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && set"', capture_output=True, text=True, errors="replace",
                       timeout=180)
    return dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln) if r.returncode == 0 else None


ENV = vc_env() if os.name == "nt" else None


@unittest.skipUnless(ENV, "no 32-bit Visual Studio tools (VCVARS32): the C4505 compile test is SKIPPED")
class PchCompile(unittest.TestCase):
    def check(self, code):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "work.c").write_text(code, encoding="utf-8")
        cl = shutil.which("cl.exe", path=next(v for k, v in ENV.items() if k.upper() == "PATH"))
        return pch_warnings.check(cl, list(readable_check.C_FLAGS), [d / "work.c"], d, ENV)

    def test_unused_static(self):
        msgs = self.check("static int helper(int x) { return x + 1; }\nint used(int y) { return y; }\n")
        self.assertTrue(any("C4505" in m and m.startswith("work.c(1)") for m in msgs), msgs)
        self.assertEqual(self.check("static int helper(int x) { return x + 1; }\n"
                                    "int used(int y) { return helper(y); }\n"), [])  # control: called


if __name__ == "__main__":
    unittest.main(verbosity=1)
