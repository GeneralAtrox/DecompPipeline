"""Declared extents of the named globals: sizeof of each names-registry declaration, as the headers define it.

One compile (/c, the judge's 32-bit compiler and flags) of a probe that includes types.h, typedefs each declaration
as game.h's verification build does (`typedef Vec3 rdx_7_t[24];`) and lists sizeof of each in a constant table
between two marker words; the table is read back from the object file (nothing is linked or run). A declaration
the compiler refuses (an incomplete type, an unknown name) is dropped and reported, and the rest compile again.

refine.py coverage uses the extents: an address referenced by raw code counts as named only inside a named global's
extent [base, base + sizeof), and no two named globals may overlap.
"""
import re
import shutil
import struct
import threading

from . import paths, readable

MAGIC = (0x52445845, 0x5854454E)  # "EXDR", "NETX": the table's first two words
LINE = re.compile(r"probe\.c\((\d+)\)\s*:\s*(?:fatal )?(?:error|warning)\s+(\w+):\s*(.*)")


def probe_text(globs):
    """(C text, {line number: global address}) of the probe for globs [(addr, decl)]."""
    lines, at = ['#include "rd_rt.h"', '#include "types.h"'], {}
    for i, (a, decl) in enumerate(globs):
        name = readable.global_name(decl)
        lines.append("typedef " + re.sub(rf"\b{re.escape(name)}\b", f"rdx_{i}_t", decl.strip().rstrip(";"), count=1)
                     + ";")
        at[len(lines)] = a
    lines.append("const unsigned rdx_extents[] = {")
    lines.append(f"    0x{MAGIC[0]:08x}u, 0x{MAGIC[1]:08x}u, {len(globs)}u,")
    for i, (a, _) in enumerate(globs):
        lines.append(f"    (unsigned)sizeof(rdx_{i}_t), /* {a} */")
        at[len(lines)] = a
    lines.append("};")
    return "\n".join(lines) + "\n", at


def read_table(obj_bytes):
    """The extents listed after the marker words in the object file."""
    k = obj_bytes.find(struct.pack("<II", *MAGIC))
    if k < 0:
        raise RuntimeError("the extents table is not in the probe's object file")
    n = struct.unpack_from("<I", obj_bytes, k + 8)[0]
    return list(struct.unpack_from(f"<{n}I", obj_bytes, k + 12))


def extents(cfg, reg, build_dir, include_dir=None):
    """({global addr: size in bytes}, {global addr: why its size is unknown})."""
    from . import readable_check as rcheck
    include_dir = include_dir or readable.INCLUDE
    globs = sorted((a, e["decl"]) for a, e in reg.globals.items())
    return sizes(cfg, globs, build_dir, (include_dir, rcheck.RUNTIME_RD, paths.RUNTIME))


def sizes(cfg, globs, build_dir, include_dirs):
    """({key: sizeof}, {key: why the compiler refused it}) of declarations [(key, decl)] (a declarator per decl:
    `Vec3 g_x[24]`, `GridQuery v`), in one probe compile with types.h from include_dirs."""
    from . import readable_check as rcheck
    env = rcheck.msvc_env_x86(cfg)
    cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
    if not cl:
        raise RuntimeError("cl.exe not found in the 32-bit Visual Studio environment")
    unknown = {}
    for _ in range(6):
        shutil.rmtree(build_dir, ignore_errors=True)
        build_dir.mkdir(parents=True)
        text, at = probe_text(globs)
        (build_dir / "probe.c").write_text(text, encoding="utf-8")
        cmd = [cl, "/nologo", "/c", "/Od", *rcheck.C_FLAGS, *(f"/I{d}" for d in include_dirs),
               f"/Fo{build_dir}\\probe.obj", str(build_dir / "probe.c")]
        rc, out, reason = paths.run_bounded(cmd, 300, cwd=build_dir, env=env)
        if reason:
            raise RuntimeError(f"the extents probe: {reason}")
        if rc == 0:
            got = read_table((build_dir / "probe.obj").read_bytes())
            return dict(zip((a for a, _ in globs), got)), unknown
        bad = {}
        for m in LINE.finditer(out):
            a = at.get(int(m.group(1)))
            if a and m.group(2) != "C2220":  # C2220: "the following warning is treated as an error"
                bad.setdefault(a, m.group(3).strip())
        if not bad:  # an error outside the declarations (the headers themselves): nothing to drop
            raise RuntimeError("the extents probe does not build: " + " | ".join(out.strip().splitlines()[-4:]))
        unknown.update(bad)
        globs = [(a, d) for a, d in globs if a not in bad]
    raise RuntimeError("the extents probe still does not build after dropping declarations: "
                       + "; ".join(f"{a}: {w}" for a, w in sorted(unknown.items())[:8]))


_CACHE, _LOCK = {}, threading.Lock()


def cached(cfg, reg, build_dir):
    """Sorted [(base, size)] of the named globals (a declaration the compiler refused covers its own address only),
    computed once per include tree and registry globals in this process; None when the probe cannot be built (the
    callers then match a named global's own address only)."""
    key = (str(readable.INCLUDE), tuple(sorted((a, e["decl"]) for a, e in reg.globals.items())))
    with _LOCK:
        if key not in _CACHE:
            try:
                ext_by, unknown = extents(cfg, reg, build_dir)
                _CACHE[key] = sorted([(int(a, 16), s) for a, s in ext_by.items()]
                                     + [(int(a, 16), 1) for a in unknown])
            except RuntimeError:
                _CACHE[key] = None
        return _CACHE[key]


def for_process(cfg, reg):
    """cached() with this process's own probe folder (parallel refine runs and verify workers never share one)."""
    import os
    return cached(cfg, reg, paths.BUILD / f"rd_extents_{os.getpid()}")


def containing(ext, addr):
    """(base, size) of the named global whose extent holds addr (int), else None. ext: sorted [(base, size)]."""
    best = None
    for base, size in ext:
        if base > addr:
            break
        if addr < base + max(size, 1):
            best = (base, size)
    return best


def preceding(ext, addr):
    """(base, size) of the nearest named global at or below addr, else None."""
    below = [(b, s) for b, s in ext if b <= addr]
    return below[-1] if below else None


def overlaps(ext):
    """[(base a, size a, base b)] of named globals b that start inside an earlier global a's extent."""
    out = []
    for i, (a, sa) in enumerate(ext):
        for b, _ in ext[i + 1:]:
            if b >= a + sa:
                break
            out.append((a, sa, b))
    return out
