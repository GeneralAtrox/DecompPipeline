"""Proof that a lean sandbox's projected structs keep the full definitions' layout (batch 25).

A projection (core/lean_layout.project) shows some members of a big struct and `u8 _pad_0xOFF[0xLEN];` arrays for
the rest. The probe built here compiles the project's full headers, then each projection as `struct Lean_N`, with
static asserts that it has the full type's size and alignment and that every member it shows has the full type's
offset and size. The judge's compiler and flags (32-bit MSVC, /W4 /WX) decide, so the layout code cannot be wrong
unnoticed: `refine.py verify --lean` and refine's preflight run it, and a failure is a build failure.
Nothing here is project-specific.
"""
import re
import shutil

from . import lean_layout, paths

PROBE = "lean_proof.c"


def projections(views):
    """[(item, layout, kept member indexes)] of the structs views shows projected, in header order."""
    out = []
    for it in sorted(views.items, key=lambda i: i.order):
        lay = views.layout(it)
        if lay is not None and views.projected(lay) and not views.declared_only(it, lay):
            out.append((it, lay, lean_layout.kept(lay, views.keep(it, lay))))
    return out


def type_expr(it):
    """How C names the full type of a struct item: its typedef name, else `struct Tag` / `union Tag`."""
    plain = sorted(n for n in it.names if ":" not in n)
    if it.kind == "typedef" and plain:
        return plain[0]
    kind, tag = next(n for n in sorted(it.names) if ":" in n).split(":")
    return f"{kind} {tag}"


def probe_text(projs):
    """The probe's C text: the full headers, then each projection with its static asserts."""
    lines = ["#include <stddef.h>", '#include "types.h"', ""]
    for n, (it, lay, keep) in enumerate(projs):
        lean, full, what = f"struct Lean_{n}", type_expr(it), re.sub(r"[^\w ]", "_", type_expr(it))
        lines += [f"{lean} {{", *lean_layout.body(lay, keep), "};",
                  f'_Static_assert(sizeof({lean}) == sizeof({full}), "{what}: size");',
                  f'_Static_assert(_Alignof({lean}) == _Alignof({full}), "{what}: alignment");']
        for i in sorted(keep):
            for name in lay.members[i].names:
                lines += [f'_Static_assert(offsetof({lean}, {name}) == offsetof({full}, {name}), '
                          f'"{what}.{name}: offset");',
                          f'_Static_assert(sizeof((({lean} *)0)->{name}) == sizeof((({full} *)0)->{name}), '
                          f'"{what}.{name}: size");']
        lines.append("")
    return "\n".join(lines) + "\n"


def prove(cfg, views, build_dir, include_dir=None):
    """[] when every projection views shows keeps the full layout (or there is none), else the compiler's messages.
    include_dir: the full headers' folder (default readable/include)."""
    from . import readable, readable_check as rcheck
    projs = projections(views) if views is not None else []
    if not projs:
        return []
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    probe = build_dir / PROBE
    probe.write_text(probe_text(projs), encoding="utf-8")
    env = rcheck.msvc_env_x86(cfg)
    cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
    if not cl:
        return ["cl.exe not found in the 32-bit Visual Studio environment: the lean projections could not be proved"]
    cmd = [cl, "/nologo", "/c", *rcheck.C_FLAGS, f"/I{include_dir or readable.INCLUDE}", f"/I{rcheck.RUNTIME_RD}",
           f"/I{paths.RUNTIME}", f"/Fo{build_dir}\\", str(probe)]
    rc, out, reason = paths.run_bounded(cmd, 180, cwd=build_dir, env=env)
    if rc == 0 and not reason:
        return []
    keep = [ln.strip() for ln in out.splitlines() if "error" in ln or "warning" in ln] or out.splitlines()[-10:]
    return [f"lean projection keeps no exact layout: {ln}" for ln in keep[:12]] + \
        ([f"lean projection proof: {reason}"] if reason else [])
