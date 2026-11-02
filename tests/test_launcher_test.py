"""port/tools/launcher_test.py's timeout kill: a run that times out is killed by its own PID and tree, never by image name.
A bystander started from the same executable (stands in for a port.exe the user has open) must survive the timeout.
Run: python -W error tests/test_launcher_test.py
"""
import pathlib
import subprocess
import sys
import time
import unittest

KIT_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "port" / "tools"
sys.path.insert(0, str(KIT_TOOLS))
import launcher_test  # noqa: E402

SLEEP = "import time; time.sleep(60)"


class TimeoutKill(unittest.TestCase):
    def test_a_timeout_kills_only_its_own_process(self):
        bystander = subprocess.Popen([sys.executable, "-c", SLEEP])
        try:
            code, out, err = launcher_test.run(sys.executable, "-c", SLEEP, timeout=2)
            self.assertEqual((code, err), (None, "timed out"))
            time.sleep(0.5)
            self.assertIsNone(bystander.poll(), "the bystander with the same image name was killed")
        finally:
            bystander.kill()
            bystander.wait()

    def test_a_normal_run_returns_its_output(self):
        code, out, err = launcher_test.run(sys.executable, "-c", "print('hi')", timeout=60)
        self.assertEqual((code, out.strip()), (0, "hi"))


if __name__ == "__main__":
    unittest.main()
