"""Xbox 360 hooks (core/target_hooks): the check, the agent sandbox and the uninitialised-stack scan, which core
implements for the SH-4. The modules beside this file hold the Xenon versions. There is no byte-match check:
Xbox 360 titles were built with Microsoft's Xenon compiler, which DP cannot run (DESIGN.md)."""
import pathlib
import sys

_here = str(pathlib.Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

import x360_agent  # noqa: E402
import x360_check  # noqa: E402


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None):
    return x360_check.run_check(cfg, addr, work_file, sets=sets, build_dir=build_dir, harness_dir=harness_dir)


def build_sandbox(cfg, addr, meta, summary, prev_work, previous_text):
    return x360_agent.build_sandbox(cfg, addr, meta, summary, prev_work, previous_text)


def uninit_stack(packet, meta):
    """core/stack_uninit reads SH-4 asm; the oracle starts every frame zeroed in both oracle and runner, so a
    read-before-write frame word is equal on both sides and needs no report."""
    return []
