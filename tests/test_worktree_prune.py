"""tools/worktree_prune.py on a throwaway repository: only clean, landed, idle worktrees are removable; a dry run
changes nothing (not even the index times it reads); --apply removes exactly those and keeps the branch refs.
Run: python -W error tests/test_worktree_prune.py (also from test_tooling.py).
"""
import contextlib
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import worktree_prune  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60,
                          check=True).stdout


class Prune(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="wtp_"))
        self.repo = self.tmp / "main"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "master")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "a")
        for name in ("landed", "dirty", "ahead", "fresh", "locked"):
            git(self.repo, "worktree", "add", "-q", "-b", name, str(self.tmp / name))
        (self.tmp / "dirty" / "new.txt").write_text("x\n")  # untracked
        (self.tmp / "ahead" / "b.txt").write_text("b\n")
        git(self.tmp / "ahead", "add", "b.txt")
        git(self.tmp / "ahead", "commit", "-q", "-m", "b")  # not in master
        git(self.repo, "worktree", "lock", str(self.tmp / "locked"))
        old = time.time() - 48 * 3600
        for name in ("landed", "dirty", "ahead", "locked"):  # idle two days; "fresh" was just made
            d = worktree_prune.git_dir(self.tmp / name)
            for f in ("index", "HEAD"):
                os.utime(d / f, (old, old))

    def tearDown(self):
        subprocess.run(["git", "-C", str(self.repo), "worktree", "unlock", str(self.tmp / "locked")],
                       capture_output=True, timeout=60)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            worktree_prune.main(["--repo", str(self.repo), *args])
        return out.getvalue()

    def test_dry_run(self):
        d = worktree_prune.git_dir(self.tmp / "landed")
        before = (d / "index").stat().st_mtime
        text = self.run_tool()
        self.assertIn("6 worktrees: 1 removable, 5 kept", text)
        self.assertRegex(text, r"removable: .*landed \[landed\] landed in master")
        for name, why in (("dirty", "1 changed or untracked"), ("ahead", "not landed in master"),
                          ("fresh", "h ago (under 24.0 h)"), ("locked", "locked"), ("main", "the main checkout")):
            self.assertRegex(text, rf"kept: .*{name} \[.*\] .*{re.escape(why)}")
        self.assertTrue((self.tmp / "landed").is_dir())  # nothing removed
        self.assertEqual((d / "index").stat().st_mtime, before)  # its idle clock untouched by the status read

    def test_apply(self):
        text = self.run_tool("--apply")
        self.assertIn("1 removed", text)
        self.assertFalse((self.tmp / "landed").exists())
        for name in ("dirty", "ahead", "fresh", "locked"):
            self.assertTrue((self.tmp / name).is_dir(), name)
        self.assertIn("landed", git(self.repo, "branch", "--list", "landed"))  # the ref stays
        self.assertNotIn(str(self.tmp / "landed").replace("\\", "/").lower(),
                         git(self.repo, "worktree", "list").replace("\\", "/").lower())

    def test_base_ref(self):
        text = self.run_tool("--base", "ahead")  # "ahead" holds master too: landed and ahead both reachable
        self.assertIn("2 removable", text)


if __name__ == "__main__":
    unittest.main()
