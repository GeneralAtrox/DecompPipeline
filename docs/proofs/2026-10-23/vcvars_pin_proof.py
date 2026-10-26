"""Proof M (findings B and D): config "vcvars_args" pins the compiler and the Windows SDK. For vcvars64.bat and
vcvars32.bat (the judge's raw and readable environments, and the port kit's), the environment is loaded with
toolcfg.vcvars_command, without arguments and with each argument string given, and the compiler it gives is reported:
VCToolsVersion, WindowsSDKVersion and the cl.exe it puts on PATH (toolcfg.compiler_identity, the line the judge stamp
now holds). The command without arguments is compared with the one before the change. Nothing is cached: the
environments are loaded into this process only. No path is written to OUT.

usage: python vcvars_pin_proof.py OUT.json "ARGS" ["ARGS" ...]   (DP_TREE: the DP tree; default this file's)
"""
import json
import os
import pathlib
import sys

TREE = pathlib.Path(os.environ.get("DP_TREE") or pathlib.Path(__file__).resolve().parents[3])
sys.path.insert(0, str(TREE))
from core import check, paths, toolcfg  # noqa: E402


def main(out, arg_sets):
    cfg = toolcfg.load(TREE)
    bat64 = toolcfg.need(cfg, "vcvars").replace("/", "\\")
    rows = []
    for bat in (bat64, bat64.replace("vcvars64.bat", "vcvars32.bat")):
        before = f'cmd /d /s /c ""{bat}" >nul 2>&1 && set"'  # the command before finding B (check.py, git show)
        for args in [None, *arg_sets]:
            pin = {"vcvars_args": args} if args else {}
            cmd = toolcfg.vcvars_command(bat, pin)
            rc, text, reason = paths.run_bounded(cmd, 180)
            env = check.captured(text) if rc == 0 and not reason else {}
            rows.append({"script": pathlib.Path(bat).name, "vcvars_args": args, "rc": rc,
                         "command_as_before": cmd == before if args is None else None,
                         "VCToolsVersion": toolcfg._env(env, "VCTOOLSVERSION") or None,
                         "WindowsSDKVersion": toolcfg._env(env, "WINDOWSSDKVERSION").rstrip("\\") or None,
                         "pinned": toolcfg.pinned(env, pin), "compiler": toolcfg.compiler_identity(env)})
            print(rows[-1], flush=True)
    pathlib.Path(out).write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0 if all(r["rc"] == 0 and r["pinned"] and r["command_as_before"] is not False for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
