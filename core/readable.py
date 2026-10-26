"""Readable stage: the names registry (function prototypes, named globals) and the headers built from it.

Layout (in the game workspace):
  readable/include/types.h   structs, enums and typedefs (designed per system); includes rd_rt.h
  readable/names/*.json      {"functions": {addr: {"proto": "void Ship_Update(Ship *ship)"}},
                              "globals": {addr: {"decl": "Ship g_ships[8]"}}}  (any number of files, merged)
  readable/src/<addr>.c      one readable function each: #include "game.h", the function, static helpers

A function not in the registry keeps its raw name and Ghidra's prototype (fn_8c012345, u32/s32/f32 types).
Readable code includes game.h. For the port, game.h declares every function and `extern`s every global.
For verification, game.h makes every other function name a pointer to its guest address (the call faults
and is logged) and every global a dereference of its guest address.
"""
import collections
import re

from . import paths

READABLE = paths.ROOT / "readable"
INCLUDE = READABLE / "include"
NAMES = READABLE / "names"
SRC = READABLE / "src"

BASE_KINDS = {"f32": "f", "float": "f", "s8": "c", "char": "c", "signed char": "c", "u8": "C", "unsigned char": "C",
              "s16": "h", "short": "h", "signed short": "h", "u16": "H", "unsigned short": "H", "s32": "i",
              "u32": "i", "int": "i", "unsigned": "i", "unsigned int": "i", "signed int": "i", "long": "i",
              "unsigned long": "i", "uintptr_t": "i", "va_list": "i", "void": "v"}
SIZES = {"f": 4, "c": 1, "C": 1, "h": 2, "H": 2, "i": 4}


class RegistryError(Exception):
    pass


def parse_proto(proto):
    """'void Ship_Update(Ship *ship, f32 dt)' -> (ret type, name, [(type, name), ...]). A variadic prototype's `...`
    (last, after a named parameter) is not in the list: is_variadic tells."""
    m = re.fullmatch(r"\s*(.+?)\s*\b([A-Za-z_]\w*)\s*\((.*)\)\s*;?\s*", proto, re.S)
    if not m:
        raise RegistryError(f"not a prototype: {proto!r}")
    ret, name, plist = m.group(1).strip(), m.group(2), m.group(3).strip()
    params = []
    if plist and plist != "void":
        items = plist.split(",")
        if items[-1].strip() == "...":
            if len(items) == 1:
                raise RegistryError(f"{name}: a variadic function needs a named parameter before `...`")
            items = items[:-1]
        for i, p in enumerate(items):
            pm = re.fullmatch(r"\s*(.+?)\s*\b([A-Za-z_]\w*)\s*", p)
            if not pm or "(" in p or "[" in p or "..." in p:
                raise RegistryError(f"parameter {i + 1} of {name}: write 'type name' (typedef function pointers; "
                                    "`...` only last)")
            params.append((pm.group(1).strip(), pm.group(2)))
    return ret, name, params


def is_variadic(proto):
    """Whether the prototype ends in `...` (SHC passes the last named parameter and the variable ones on the stack)."""
    m = re.search(r"\((.*)\)\s*;?\s*$", proto, re.S)
    return bool(m) and m.group(1).split(",")[-1].strip() == "..."


def raw_proto(meta_sig):
    return meta_sig.replace("UNSUPPORTED", "u32")


class Registry:
    def __init__(self, names_dir=None, include_dir=None):
        """The registry of names_dir and the typedefs of include_dir (default: NAMES and INCLUDE, which a readable
        run points at its validated header snapshot: core/header_snapshot)."""
        names_dir, include_dir = names_dir or NAMES, include_dir or INCLUDE
        self.functions, self.globals, self.where = {}, {}, {}
        for f in sorted(names_dir.glob("*.json")) if names_dir.exists() else []:
            data = paths.read_json(f)
            for kind, table in (("functions", self.functions), ("globals", self.globals)):
                for addr, e in data.get(kind, {}).items():
                    addr = paths.norm_addr(addr)
                    if addr in table:
                        raise RegistryError(f"{kind} {addr} is in both {self.where[addr]} and {f.name}")
                    table[addr] = e
                    self.where[addr] = f.name
        self.typedefs = parse_typedefs(include_dir / "types.h")
        for h in sorted((include_dir / "sys").glob("*.h")) if (include_dir / "sys").exists() else []:
            self.typedefs.update(parse_typedefs(h))  # the per-system headers types.h includes
        names = [parse_proto(e["proto"])[1] for e in self.functions.values()]
        names += [global_name(e["decl"]) for e in self.globals.values()]
        dup = {n for n, k in collections.Counter(names).items() if k > 1}
        if dup:
            raise RegistryError(f"names used twice: {sorted(dup)}")

    @classmethod
    def empty(cls, names=None):
        """A registry without the project's names and types.h (regression fixtures: raw names and prototypes),
        holding only names: {"functions": {addr: {"proto"}}, "globals": {addr: {"decl"}}} (a readable/names file)."""
        reg = cls.__new__(cls)
        reg.where, reg.typedefs, reg.isolated = {}, {}, True
        reg.functions, reg.globals = ({paths.norm_addr(a): e for a, e in (names or {}).get(k, {}).items()}
                                      for k in ("functions", "globals"))
        return reg

    isolated = False  # True: the project's readable/include is not used (empty())

    def proto(self, addr, meta=None, raw_sig=None):
        """C prototype text of the function at addr: the registry's, else the raw one (raw_sig, or its harness)."""
        if addr in self.functions:
            return self.functions[addr]["proto"]
        if raw_sig:
            return raw_proto(raw_sig)
        meta = meta or paths.read_json(paths.HARNESS / addr / "meta.json")
        if not meta:
            raise RegistryError(f"no prototype for {addr}: not in the registry and no harness")
        return raw_proto(meta["signature"])

    def kind(self, ctype):
        """Argument-register class of a C type: f, c, C, h, H, i (32-bit integer or pointer) or v."""
        t = re.sub(r"\bconst\b|\bvolatile\b", "", ctype).strip()
        t = re.sub(r"\s+", " ", t)
        if "*" in t:
            return "i"
        if t in ("u64", "s64", "uint64_t", "int64_t", "long long", "unsigned long long"):
            return "L"  # a result only (r0:r1): kinds() refuses it as a parameter
        if t.startswith("enum "):
            return "i"
        if t in BASE_KINDS:
            return BASE_KINDS[t]
        if t in self.typedefs:
            target = self.typedefs[t]
            if target in ("struct", "union"):
                raise RegistryError(f"{ctype} is passed by value (a struct/union): SH-4 code passes pointers")
            return "i" if target == "enum" else self.kind(target)
        raise RegistryError(f"unknown type {ctype!r} (not a basic type or a typedef in types.h)")

    def kinds(self, proto):
        """(parameter letters, result letter) for the call trap (RdProto); '*' last marks a variadic prototype."""
        ret, name, params = parse_proto(proto)
        ks = "".join(self.kind(t) for t, _ in params)
        if "L" in ks:
            raise RegistryError(f"{name}: a 64-bit parameter (SH-4 code has none; write two 32-bit halves)")
        return ks + ("*" if is_variadic(proto) else ""), self.kind(ret)


def global_name(decl):
    m = re.match(r".*?\b([A-Za-z_]\w*)\s*(\[[^\]]*\]\s*)*$", decl.strip().rstrip(";"))
    if not m:
        raise RegistryError(f"not a declaration: {decl!r}")
    return m.group(1)


def parse_typedefs(path):
    """{name: target}: target is a basic type, another typedef, 'struct', 'union', 'enum' or a pointer type."""
    if not path.exists():
        return {}
    from .lint import strip_comments
    src = strip_comments(path.read_text(encoding="utf-8"))
    out = {}
    for m in re.finditer(r"\btypedef\b([^;{]*)(\{)?", src):
        head = m.group(1).strip()
        if m.group(2):  # typedef struct/union/enum ... { ... } Name;
            depth, i = 0, m.end(2) - 1
            while i < len(src):
                depth += {"{": 1, "}": -1}.get(src[i], 0)
                if depth == 0:
                    break
                i += 1
            tail = src[i + 1:src.find(";", i)]
            kw = head.split()[0] if head else "struct"
            for n in re.findall(r"([*\s]*)([A-Za-z_]\w*)", tail):
                out[n[1]] = "u32 *" if "*" in n[0] else kw
            continue
        fp = re.fullmatch(r"(.+?)\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(.*\)", head, re.S)
        if fp:
            out[fp.group(2)] = "void *"
            continue
        dm = re.fullmatch(r"(.+?)\s*\b([A-Za-z_]\w*)", head, re.S)
        if dm:
            target = dm.group(1).strip()
            out[dm.group(2)] = re.sub(r"^(struct|union|enum)\s+\w+$", r"\1", target)
    return out


def fn_pointer_type(proto):
    ret, _, params = parse_proto(proto)
    types = [t for t, _ in params] + (["..."] if is_variadic(proto) else [])
    return f"{ret} (*)({', '.join(types) or 'void'})"


def game_h(reg, verify_self=None, addrs=None, raw_sigs=None, aliases=None, lean_globals=None):
    """game.h text. verify_self: the address under test (verification build); None for the port header.

    addrs: functions to declare besides the registry's; raw_sigs: {addr: raw signature} for those. aliases: {addr:
    raw signature} of named functions readable C may still call by their raw name fn_ADDR with the raw arguments
    (readable_abi.raw_alias_ok: the registry's placement starts with the raw parameters'); verification build only.
    lean_globals: a lean game.h (readable_lean): only the functions in addrs, and only these globals."""
    lines = ["/* Generated from readable/names/*.json: do not edit. */", "#ifndef GAME_H", "#define GAME_H", "",
             '#include "rd_rt.h"', '#include "types.h"', ""]
    lean = lean_globals is not None
    fns = sorted(set(addrs or ()) if lean else set(reg.functions) | set(addrs or ()))
    globs = sorted((a, e) for a, e in reg.globals.items() if not lean or a in lean_globals)
    protos = {a: reg.proto(a, raw_sig=(raw_sigs or {}).get(a)) for a in fns}
    lines += [f"{protos[a]};" for a in fns]
    lines.append("")
    if verify_self is None:
        lines += [f"extern {e['decl']};" for _, e in globs]
    else:
        for a in fns:
            if a != verify_self:
                name = parse_proto(protos[a])[1]
                lines.append(f"#define {name} (({fn_pointer_type(protos[a])})0x{a}u)")
        for a, sig in sorted((aliases or {}).items()):
            raw = raw_proto(sig)
            name = parse_proto(raw)[1]
            if a != verify_self and name != parse_proto(protos.get(a) or reg.proto(a))[1]:
                lines.append(f"#define {name} (({fn_pointer_type(raw)})0x{a}u) /* raw name */")
        for a, e in globs:
            name = global_name(e["decl"])
            td = re.sub(rf"\b{name}\b", f"rdg_{name}_t", e["decl"].rstrip(";"), count=1)
            lines += [f"typedef {td};", f"#define {name} (*(rdg_{name}_t *)0x{a}u)"]
    lines += ["", "#endif", ""]
    return "\n".join(lines)


# Where each argument lives (placement by storage, the entry thunk, the call trap's regs): readable_abi.
from .readable_abi import (callee_regs, name_storage, own_storages, param_storage, storages,  # noqa: E402,F401
                           thunk_c)
