"""The tool's own settings: config.json (tracked, portable defaults) with config.local.json (git-ignored, this
machine's paths) merged over it, key by key (batch 26).

The machine paths (ghidra_home, java_home, vcvars, sevenzip, vcpkg: a vcpkg checkout for the port kit, flycast: the
workbench emulator port/tools/emu_capture.py drives) are null in config.json; config.local.json gives them.
Code that needs one calls need(cfg, key), which stops with an error naming the key and config.local.json.
No imports from the rest of core, so tools/ scripts can use it alone.
"""
import json
import pathlib
import shutil

LOCAL = "config.local.json"
MACHINE_KEYS = ("ghidra_home", "java_home", "vcvars", "sevenzip", "vcpkg", "flycast")
REGRESS = "regress"  # a workspace's own regression fixtures folder


def load(tool):
    """config.json of the pipeline folder `tool`, with config.local.json (when present) merged over it."""
    tool = pathlib.Path(tool)
    cfg = json.loads((tool / "config.json").read_text(encoding="utf-8"))
    local = tool / LOCAL
    if local.is_file():
        over = json.loads(local.read_text(encoding="utf-8"))
        if not isinstance(over, dict):
            raise ValueError(f"{local}: must hold a JSON object of config keys")
        cfg.update(over)
    return cfg


def need(cfg, key, which=None):
    """cfg[key], a machine path; else (`which`) that program on PATH; else a clear error naming the key and file."""
    value = cfg.get(key)
    if value:
        return str(value)
    found = shutil.which(which) if which else None
    if found:
        return found
    raise RuntimeError(f"config key {key!r} is not set: give this machine's path in DecompPipeline/{LOCAL} "
                       f"(git-ignored, merged over config.json), e.g. {{\"{key}\": \"...\"}}")


def patterns(tests):
    """tests/patterns.json with tests/patterns.local.json's "projects" (this machine's workspace paths, git-ignored)
    merged over its "projects"; a project without a path stays null (regress stops on it when selected).
    Fixtures are game-derived C, so they live with each game, not here: every workspace's own
    regress/fixtures.json ({"fixtures": [...]}, pass/fail named relative to that folder) is added to "fixtures"
    with its project key and absolute paths."""
    tests = pathlib.Path(tests)
    spec = json.loads((tests / "patterns.json").read_text(encoding="utf-8"))
    local = tests / "patterns.local.json"
    if local.is_file():
        over = json.loads(local.read_text(encoding="utf-8"))
        spec["projects"] = dict(spec.get("projects") or {}, **(over.get("projects") or {}))
    fixtures = list(spec.get("fixtures") or [])
    for key, path in spec["projects"].items():
        own = pathlib.Path(path) / REGRESS / "fixtures.json" if path else None
        if own and own.is_file():
            for f in json.loads(own.read_text(encoding="utf-8"))["fixtures"]:
                f = dict(f, project=key)
                for side in ("pass", "fail"):
                    if f.get(side):
                        f[side] = str(own.parent / f[side])
                fixtures.append(f)
    spec["fixtures"] = fixtures
    return spec
