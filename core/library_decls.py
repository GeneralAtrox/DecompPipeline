"""The cross-file declaration check of `refine.py library` (core/library.py): one generated translation unit holding
game.h and every readable file's file-scope declarations with external linkage.

Why: each readable file compiles alone, and the archive (lib.exe /WX) catches a symbol two objects define. Neither
sees two files that declare one external name with different types (C has no type-checked linking: file A's local
prototype `f32 sqrtf(f32 x);` and file B's `u32 sqrtf(u32 x);` link without a word). In one unit the compiler must
reconcile them: a conflicting redeclaration is C2371 / C4028 / C4029 (errors at /W4 /WX), as is one that differs
from game.h.

What is collected from each file (comments and literals masked first; readable files have no preprocessor lines
except the game.h include, rule RR1):
- every top-level declaration ending in `;` that is not `static` and not a typedef or a bare tag declaration
  (prototypes as written; objects as `extern` declarations without their initializer: two definitions are the
  archive's finding, a type conflict this unit's);
- every non-static function definition's header, as a prototype (the exported function).
Each is emitted after a `#line N "ADDR.c"` directive, so a diagnostic names the file and line it came from. Static
items have internal linkage and cannot conflict across files. A declaration that names one of the file's own types,
tags or enum constants (a type that exists only in that file) cannot be compiled outside it: it is skipped and
counted (`skipped`), never silently dropped.
"""
import pathlib
import re

from .raw_names import mask

IDENT = re.compile(r"[A-Za-z_]\w*")
KEYWORDS = {"static", "inline", "__inline", "__forceinline", "extern", "const", "volatile", "restrict"}


def top_level(text):
    """[(kind, text, first line)] of the top-level items of C text (masked): kind "def" (a function definition's
    header, before its body), "block" (a typedef/struct/union/enum with a body, up to its `;`) or "decl"."""
    code, _ = mask(text)
    items, depth, start, i, n = [], 0, 0, 0, len(code)
    while i < n:
        c = code[i]
        if c == "{":
            if depth == 0:
                head = code[start:i]
                if re.match(r"\s*(typedef\b|struct\b|union\b|enum\b)", _trim("", head, 0)[1]) and "(" not in head:
                    end = _close(code, i)
                    semi = code.find(";", end)
                    semi = n if semi < 0 else semi
                    items.append(("block", code[start:semi + 1], _line(code, start)))
                    i, start = semi + 1, semi + 1
                    continue
                items.append(("def", head, _line(code, start)))
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                start = i + 1
        elif c == ";" and depth == 0:
            items.append(("decl", code[start:i + 1], _line(code, start)))
            start = i + 1
        i += 1
    return [(k, t, ln) for k, t, ln in (_trim(k, t, ln) for k, t, ln in items) if t.strip()]


def _close(code, i):
    depth = 0
    for j in range(i, len(code)):
        depth += {"{": 1, "}": -1}.get(code[j], 0)
        if depth == 0:
            return j
    return len(code) - 1


def _line(code, pos):
    return code.count("\n", 0, pos) + 1


def _trim(kind, t, line):
    """The item without its leading blank and preprocessor lines, and the line it really starts on."""
    rows = t.split("\n")
    k = 0
    while k < len(rows) and (not rows[k].strip() or rows[k].strip().startswith("#")):
        k += 1
    return kind, "\n".join(rows[k:]), line + k


def _strip_pp(t):
    return "\n".join(ln for ln in t.split("\n") if not ln.strip().startswith("#"))


def local_names(items):
    """Names the file itself defines as types: typedef names, struct/union/enum tags and enum constants."""
    names = set()
    for kind, t, _ in items:
        flat = " ".join(_strip_pp(t).split())
        if kind == "block" or flat.startswith("typedef"):
            names |= set(re.findall(r"\b(?:struct|union|enum)\s+([A-Za-z_]\w*)\s*\{", flat))
            body = re.search(r"\{(.*)\}", flat)
            if body and re.match(r"(typedef\s+)?enum\b", flat):
                names |= {m.group(0) for p in body.group(1).split(",") for m in [IDENT.match(p.strip())] if m}
            if flat.startswith("typedef"):
                tail = flat[body.end():] if body else flat
                fp = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)", tail)
                if fp:
                    names.add(fp.group(1))
                else:
                    names |= set(re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*[,;]", tail))
    return names


def external_decls(text):
    """([(declaration text, line)], [(line, why skipped)]) of one file's external-linkage declarations."""
    items = top_level(text)
    local = local_names(items)
    out, skipped = [], []
    for kind, t, line in items:
        flat = " ".join(_strip_pp(t).split())
        if kind == "block" or not flat or flat.startswith("typedef"):
            continue
        words = IDENT.findall(flat.split("(")[0])
        if "static" in words:
            continue
        if kind == "decl" and re.fullmatch(r"(struct|union|enum)\s+[A-Za-z_]\w*\s*;", flat):
            continue  # a forward declaration of a tag defines nothing
        decl = flat if kind == "decl" else flat + ";"
        if kind == "decl" and ("=" in decl or "(" not in decl or re.search(r"\(\s*\*", decl)):  # an object
            decl = "extern " + re.sub(r"^extern\s+", "", decl.split("=")[0].rstrip(" ;")) + ";"
        uses = (set(IDENT.findall(decl)) - KEYWORDS) & local
        if uses:
            skipped.append((line, f"names the file's own type(s) {', '.join(sorted(uses))}: `{decl[:100]}`"))
            continue
        out.append((decl, line))
    return out, skipped


def combined_unit(files):
    """(C text of the one unit, {"checked": n, "skipped": [(addr, line, why)]}); files: [(addr, path, text)]."""
    lines, checked, skipped = ['#include "game.h"', ""], 0, []
    for addr, path, text in files:
        decls, skip = external_decls(text)
        skipped += [(addr, ln, why) for ln, why in skip]
        for decl, ln in decls:
            lines += [f'#line {ln} "{pathlib.PurePath(path).name}"', decl]
            checked += 1
    return "\n".join(lines) + "\n", {"checked": checked, "skipped": skipped}
