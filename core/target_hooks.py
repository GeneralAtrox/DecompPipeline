"""Per-target Python hooks: targets/<target>/hooks.py, when the target has one, replaces the SH-4 steps it names.

A hook module may define any of
  run_check(cfg, addr, work_file, sets, build_dir, harness_dir)  the official check (core/check.run_check)
  build_sandbox(cfg, addr, meta, summary, prev_work, previous_text)  the agent's sandbox (core/agent.build_sandbox)
  uninit_stack(packet, meta)  the frame words read before any write (core/stack_uninit.scan_packet, SH-4 asm)
Targets without hooks.py (dreamcast_sh4) run the core versions unchanged.
"""
import importlib.util

from . import paths

_loaded = {}


def module():
    """The target's hooks module, loaded once per path, or None."""
    path = paths.TARGET / "hooks.py"
    key = str(path)
    if key not in _loaded:
        mod = None
        if path.exists():
            spec = importlib.util.spec_from_file_location(f"target_hooks_{paths.TARGET.name}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        _loaded[key] = mod
    return _loaded[key]


def get(name):
    """The hook function `name` of the current target, or None."""
    mod = module()
    return getattr(mod, name, None) if mod else None
