"""Xbox 360 hooks (core/target_hooks): the check, the agent sandbox, the uninitialised-stack scan and the readable
stage (x360_readable), which core implements for the SH-4, and the scope of a scoped oracle change (x360_scope). The modules beside this file hold the Xenon versions. There is no byte-match check:
Xbox 360 titles were built with Microsoft's Xenon compiler, which DP cannot run (DESIGN.md)."""
import pathlib
import sys

_here = str(pathlib.Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

import x360_agent  # noqa: E402
import x360_check  # noqa: E402
import x360_readable  # noqa: E402
import x360_scope  # noqa: E402


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None):
    return x360_check.run_check(cfg, addr, work_file, sets=sets, build_dir=build_dir, harness_dir=harness_dir)


def build_sandbox(cfg, addr, meta, summary, prev_work, previous_text):
    return x360_agent.build_sandbox(cfg, addr, meta, summary, prev_work, previous_text)


def uninit_stack(packet, meta):
    """core/stack_uninit reads SH-4 asm; the oracle starts every frame zeroed in both oracle and runner, so a
    read-before-write frame word is equal on both sides and needs no report."""
    return []


def stamp_scope(cfg, addr, scope):
    return x360_scope.in_scope(cfg, addr, scope)


def readable_check_raw(cfg, addr, work_file, build_dir, sets=("dev", "hidden"), harness_dir=None):
    """Raw C through the readable runtime (core/readable_check.check_raw): the Xbox 360 readable runtime's own test."""
    return x360_readable.check_raw(cfg, addr, work_file, build_dir, sets=sets, harness_dir=harness_dir)


def readable_check(cfg, addr, src_file, build_dir, sets=("dev", "hidden"), reg=None, harness_dir=None, **kw):
    """The readable judge (core/readable_check.check_readable): 64-bit runner, big-endian guest memory at its own
    addresses, the Xenon's argument placement, the Xbox 360 call record."""
    return x360_readable.check_readable(cfg, addr, src_file, build_dir, sets=sets, reg=reg, harness_dir=harness_dir,
                                        **kw)


def readable_sandbox_decls(reg, addr, meta):
    """The readable sandbox's declarations (core/readable_sandbox.sandbox_decls): the callees' raw signatures."""
    import x360_readable_sandbox
    return x360_readable_sandbox.sandbox_decls(reg, addr, meta)


def readable_context_md(reg, addr, meta, cfg=None, raw_text="", types_body=None, decls=None, ext=None, views=None):
    """The readable sandbox's context.md (core/readable_context.context_md) with the Xenon's argument placement."""
    import x360_readable_sandbox
    return x360_readable_sandbox.context_md(reg, addr, meta, cfg, raw_text, types_body, decls, ext, views)
