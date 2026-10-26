"""Compile translated C files the way DP's Xbox 360 build does (a quick check while working on the translator).

usage: python tools/cl_one.py FILE.c [...]
"""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile_all  # noqa: E402


def main():
    out = compile_all.HERE / "out"
    out.mkdir(exist_ok=True)
    env = compile_all.msvc_env(out)
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = next(str(pathlib.Path(d) / "cl.exe") for d in path.split(";") if d and (pathlib.Path(d) / "cl.exe").exists())
    runtime = compile_all.DP / "targets" / "xbox360_ppc" / "runtime"
    odir = out / "obj1"
    odir.mkdir(exist_ok=True)
    r = subprocess.run([cl, "/nologo", "/c", "/O1", "/fp:strict", "/W4", "/WX", "/TC", f"/I{runtime}", f"/Fo{odir}\\",
                        *sys.argv[1:]], capture_output=True, text=True, timeout=300, env=env)
    print(r.stdout + r.stderr)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
