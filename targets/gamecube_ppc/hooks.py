"""GameCube PowerPC hooks (core/target_hooks): the check, the agent sandbox and the uninitialised-stack scan, which
core implements for the SH-4. The modules beside this file hold the PowerPC versions. The check is the behaviour
check (ppc_check) with the byte-match check (ppc_match) added when the project configures one."""
import pathlib
import sys

_here = str(pathlib.Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

import ppc_agent  # noqa: E402
import ppc_check  # noqa: E402
import ppc_match  # noqa: E402
import ppc_readable  # noqa: E402


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None):
    from core import paths
    res = ppc_check.run_check(cfg, addr, work_file, sets=sets, build_dir=build_dir, harness_dir=harness_dir)
    return ppc_match.attach(cfg, paths.ROOT, addr, work_file, res, build_dir or (paths.BUILD / addr))


def build_sandbox(cfg, addr, meta, summary, prev_work, previous_text):
    return ppc_agent.build_sandbox(cfg, addr, meta, summary, prev_work, previous_text)


def uninit_stack(packet, meta):
    """core/stack_uninit reads SH-4 asm; the PowerPC oracle starts every frame zeroed in both oracle and runner, so a
    read-before-write frame word is equal on both sides and needs no report."""
    return []


def readable_check_raw(cfg, addr, work_file, build_dir, sets=("dev", "hidden"), harness_dir=None):
    """Raw C through the readable runtime (core/readable_check.check_raw): the PowerPC readable runtime's own test."""
    return ppc_readable.check_raw(cfg, addr, work_file, build_dir, sets=sets, harness_dir=harness_dir)


def readable_check(cfg, addr, src_file, build_dir, sets=("dev", "hidden"), reg=None, harness_dir=None, **kw):
    """The readable judge (core/readable_check.check_readable): big-endian MEM1 at its own address, the EABI's
    argument placement, the PowerPC call record."""
    return ppc_readable.check_readable(cfg, addr, src_file, build_dir, sets=sets, reg=reg, harness_dir=harness_dir,
                                       **kw)


def readable_sandbox_decls(reg, addr, meta):
    """The readable sandbox's declarations (core/readable_sandbox.sandbox_decls): the callees' raw signatures."""
    import ppc_readable_sandbox
    return ppc_readable_sandbox.sandbox_decls(reg, addr, meta)


def readable_context_md(reg, addr, meta, cfg=None, raw_text="", types_body=None, decls=None, ext=None, views=None):
    """The readable sandbox's context.md (core/readable_context.context_md) with the PowerPC's argument placement."""
    import ppc_readable_sandbox
    return ppc_readable_sandbox.context_md(reg, addr, meta, cfg, raw_text, types_body, decls, ext, views)
