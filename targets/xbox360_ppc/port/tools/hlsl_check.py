"""Compiles the HLSL files shader_convert.py wrote with the system's Direct3D compiler (d3dcompiler_47.dll, Windows) and
prints the errors, so a conversion that Direct3D 11 would refuse is found at tool time and not at the first draw.

Nearest existing: targets/xbox360_ppc/port/tools/shader_convert.py - it writes the files; this only checks them.

  python hlsl_check.py DIR      # DIR holds vs_*.hlsl and ps_*.hlsl
"""
import ctypes
import pathlib
import sys

d3dc = ctypes.WinDLL("d3dcompiler_47.dll")


def compile_file(path):
    src = path.read_bytes()
    code, err = ctypes.c_void_p(), ctypes.c_void_p()
    prof = b"vs_5_0" if path.name.startswith("vs_") else b"ps_5_0"
    hr = d3dc.D3DCompile(src, len(src), str(path).encode(), None, None, b"main", prof, 0, 0, ctypes.byref(code), ctypes.byref(err))
    msg = ""
    if err.value:
        vt = ctypes.cast(err, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(err)
        size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(err)
        msg = ctypes.string_at(ptr, size).decode("latin1")
    size = 0
    if code.value:
        vt = ctypes.cast(code, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(code)
    return hr & 0xFFFFFFFF, size, msg


if __name__ == "__main__":
    bad = 0
    for f in sorted(pathlib.Path(sys.argv[1]).glob("*.hlsl")):
        hr, size, msg = compile_file(f)
        print("%-20s %s %s" % (f.name, "ok %d bytes" % size if hr == 0 else "FAILED %08x" % hr, msg.strip()[:400]))
        bad += hr != 0
    sys.exit(1 if bad else 0)
