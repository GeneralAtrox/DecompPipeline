"""The warnings the port's build reports and the judge's plain build does not.

MSVC (19.38, C) reports C4505 (an unreferenced static function, removed) only in a file built with a precompiled
header. The port builds readable C with one (CMake precompile_headers: game.h), the judge without, so a file the
judge passed stopped the port's /W4 /WX build (TC 8c034ae0: `static f32 bits_to_f32` defined, never called).
check() compiles the C under test once more, syntax only (/Zs), under a one-line precompiled header and the judge's
flags. Nothing here is project-specific.
"""
import pathlib
import re

PCH_H = "/* The precompiled header of the judge's port-form pass (core/pch_warnings). */\ntypedef int rd_pch_word;\n"


def check(cl, flags, sources, build_dir, env):
    """[diagnostic] of sources (the C under test) compiled under a precompiled header, as the port compiles them;
    [] when they build clean. flags: the judge's (C_FLAGS and the include folders, no /Fo)."""
    from . import paths
    d = pathlib.Path(build_dir) / "pch"
    d.mkdir(parents=True, exist_ok=True)
    h, c, pch = d / "rd_pch.h", d / "rd_pch.c", d / "rd_pch.pch"
    h.write_text(PCH_H, encoding="utf-8")
    c.write_text("/* Builds rd_pch.pch (the header is forced in). */\n", encoding="utf-8")
    use = [f"/FI{h}", f"/Fp{pch}"]
    rc, out, reason = paths.run_bounded([cl, "/nologo", "/c", "/O1", *flags, f"/Yc{h}", *use, f"/Fo{d}\\", str(c)],
                                        120, cwd=d, env=env)
    if rc != 0 or reason:
        return ["port-form pass (precompiled header): the header did not build:", *out.splitlines()[-8:]]
    cmd = [cl, "/nologo", "/c", "/O1", "/Zs", *flags, f"/Yu{h}", *use, *map(str, sources)]
    rc, out, reason = paths.run_bounded(cmd, 120, cwd=d, env=env)
    if rc == 0 and not reason:
        return []
    keep = [ln for ln in out.splitlines() if "error" in ln or "warning" in ln] or out.splitlines()[-8:]
    return ["port-form pass (precompiled header, as the port builds readable C):",
            *(re.sub(r"^.*[\\/](work\.c|src\d+\.c)", r"\1", ln) for ln in keep[:12])]
