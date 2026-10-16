"""Header lint: one owner per name under readable/include (refine.py header, and every readable run's snapshot).

Two definitions of one name in the headers under readable/include fail, naming both files and lines:
- a macro (#define, include guards too);
- a typedef name, or a struct/union/enum tag (tags share one namespace);
- an enum constant;
- a global or function declaration (extern objects, prototypes; game.h's come from the names registry).
Identical redefinitions fail too. C allows some of them, but the second copy is a name another header owns, and the
next edit of either copy makes them differ (C4005 under /WX, or a type conflict) and blocks every run. Ordinary
identifiers (typedef names, enum constants, globals, functions) share one namespace and a macro hides any of them,
so a clash across those kinds fails as well. A forward declaration (`struct Toy;`) defines nothing and is free.

compile_game_h: game.h (port form) built at the judge's flags (/W4 /WX) against the include tree.
Generic: nothing here knows a project.
"""
import re

from . import paths, readable, readable_headers

DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(.*(?:\\\n.*)*)", re.M)
GUARD = re.compile(r"^[ \t]*#[ \t]*ifndef[ \t]+([A-Za-z_]\w*)[ \t]*\n(?:[ \t]*\n)*[ \t]*#[ \t]*define[ \t]+\1\b", re.M)
KIND_TEXT = {"macro": "macro", "typedef": "typedef", "tag": "tag", "const": "enum constant", "global": "declaration"}


def blank_comments(text):
    """text with every comment replaced by spaces; newlines are kept, so line numbers stay."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


class Def:
    __slots__ = ("key", "kind", "name", "header", "line", "text")

    def __init__(self, key, kind, name, header, line, text):
        self.key, self.kind, self.name, self.header, self.line = key, kind, name, header, line
        self.text = " ".join(text.replace("\\\n", " ").split())


def _line_of(it, code, name, last=False):
    """File line of the first (or last) code line of item it that names `name`."""
    rows = [k for k, ln in enumerate(code.split("\n")) if re.search(rf"\b{re.escape(name)}\b", ln)]
    return it.line + ((rows[-1] if last else rows[0]) if rows else 0)


def _enum_constants(flat):
    body = re.search(r"\{(.*)\}", flat)
    head = flat[:body.start()] if body else flat
    if not body or not re.match(r"\s*(?:typedef\s+)?enum\b", head):
        return set()
    return {m.group(0) for part in body.group(1).split(",") for m in [re.match(r"[A-Za-z_]\w*", part.strip())] if m}


def _global(flat):
    """(name, declaration) of a global or function declared by a top-level declaration, else None."""
    if not flat.endswith(";") or flat.startswith(("typedef", "#")) or "{" in flat:
        return None
    if re.fullmatch(r"(struct|union|enum)\s+[A-Za-z_]\w*\s*;", flat):  # a forward declaration defines nothing
        return None
    decl = re.sub(r"^extern\s+", "", flat[:-1].strip())
    fp = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(", decl)  # a function pointer object
    if fp:
        return fp.group(1), decl
    if "(" in decl:
        try:
            return readable.parse_proto(decl)[1], decl
        except readable.RegistryError:
            return None
    try:
        return readable.global_name(decl.split("=")[0]), decl
    except readable.RegistryError:
        return None


def definitions(include_dir):
    """Every name the headers under include_dir define, as Def records (in file order)."""
    hs = readable_headers.parse_all(include_dir)
    out = []
    for rel in hs.seen:
        text = (include_dir / rel).read_text(encoding="utf-8", errors="replace")
        for m in GUARD.finditer(blank_comments(text)):
            out.append(Def(("id", m.group(1)), "macro", m.group(1), rel, text[:m.start(1)].count("\n") + 1,
                           f"#define {m.group(1)} (include guard)"))
    for it in hs.items:
        code = blank_comments(it.text)
        if it.kind == "define":
            for m in DEFINE.finditer(code):
                out.append(Def(("id", m.group(1)), "macro", m.group(1), it.header,
                               it.line + code[:m.start()].count("\n"), m.group(0)))
            continue
        flat = " ".join(code.split())
        if it.kind == "other":
            g = _global(flat)
            if g:
                out.append(Def(("id", g[0]), "global", g[0], it.header, _line_of(it, code, g[0], last=True), g[1]))
            continue
        consts = _enum_constants(flat)
        for n in sorted(it.names):
            if ":" in n:
                tag = n.split(":", 1)[1]
                out.append(Def(("tag", tag), "tag", n.replace(":", " "), it.header, _line_of(it, code, tag), flat))
            elif n in consts:
                out.append(Def(("id", n), "const", n, it.header, _line_of(it, code, n), flat))
            else:
                out.append(Def(("id", n), "typedef", n, it.header, _line_of(it, code, n, last=True), flat))
    return out


def lint(include_dir):
    """Problems (text, one per name defined more than once), naming every file and line; [] when clean."""
    by_key = {}
    for d in definitions(include_dir):
        same = by_key.setdefault(d.key, [])
        if not any((o.header, o.line, o.kind) == (d.header, d.line, d.kind) for o in same):
            same.append(d)
    problems = []
    for key, ds in by_key.items():
        if len(ds) < 2:
            continue
        kinds = {d.kind for d in ds}
        where = ", ".join(f"{d.header}:{d.line}" + ("" if len(kinds) == 1 else f" ({KIND_TEXT[d.kind]})") for d in ds)
        how = "identical copies" if len({d.text for d in ds}) == 1 and len(kinds) == 1 else "different definitions"
        what = KIND_TEXT[ds[0].kind] if len(kinds) == 1 else "name"
        problems.append(f"{what} {ds[0].name if key[0] == 'id' else key[1]} is defined {len(ds)} times: {where} "
                        f"({how}; one owner per name: keep the owner's, request it there instead)")
    return problems


def compile_game_h(cfg, include_dir, build_dir):
    """game.h of include_dir built alone at the judge's flags (/W4 /WX); [] when it builds, else the messages."""
    import shutil
    from . import readable_check as rcheck
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    probe = build_dir / "game_h_probe.c"
    probe.write_text('#include "game.h"\n', encoding="utf-8")
    env = rcheck.msvc_env_x86(cfg)
    cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
    if not cl:
        return ["cl.exe not found in the 32-bit Visual Studio environment: game.h could not be built"]
    cmd = [cl, "/nologo", "/c", *rcheck.C_FLAGS, f"/I{include_dir}", f"/I{rcheck.RUNTIME_RD}", f"/I{paths.RUNTIME}",
           f"/Fo{build_dir}\\", str(probe)]
    rc, out, reason = paths.run_bounded(cmd, 180, cwd=build_dir, env=env)
    if rc == 0 and not reason:
        return []
    keep = [ln.strip() for ln in out.splitlines() if "error" in ln or "warning" in ln] or out.splitlines()[-10:]
    prefix = str(include_dir).replace("/", "\\").rstrip("\\") + "\\"
    return [f"game.h does not build at /W4 /WX: {ln.replace(prefix, '')}" for ln in keep[:12]] + \
        ([f"game.h build: {reason}"] if reason else [])
