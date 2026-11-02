"""Convert every Sonic SDK object to ELF with the SDK's elfcnv, and write one table of its functions.

usage: python -I stable.py OBJDIR SHCDIR OUT.json
Row per code symbol: source, section, name, global, offset, length, hex bytes, relocated offsets [(off, size, symbol, addend)].
Nearest existing: tools/import_from.py - reads a game workspace, not SYSROF objects through elfcnv.
"""
import json
import os
import pathlib
import struct
import subprocess
import sys


def parse_elf(data):
    if data[:4] != b"\x7fELF":
        raise ValueError("not ELF")
    shoff = struct.unpack_from("<I", data, 32)[0]
    shent, shnum, shstr = struct.unpack_from("<HHH", data, 46)
    secs = []
    for i in range(shnum):
        nm, ty, fl, addr, off, size, link, info, align, ent = struct.unpack_from("<10I", data, shoff + i * shent)
        secs.append(dict(nm=nm, type=ty, flags=fl, off=off, size=size, link=link, info=info, ent=ent))
    names = secs[shstr]
    for s in secs:
        e = data.index(b"\0", names["off"] + s["nm"])
        s["name"] = data[names["off"] + s["nm"]:e].decode("latin-1")
    return secs


def table(path, source):
    data = path.read_bytes()
    secs = parse_elf(data)
    symtab = next((s for s in secs if s["type"] == 2), None)
    rows = []
    if symtab is None:
        return rows, {"nosym": 1}
    strs = secs[symtab["link"]]
    syms = []
    for i in range(symtab["size"] // 16):
        nm, val, size, info, oth, shndx = struct.unpack_from("<IIIBBH", data, symtab["off"] + i * 16)
        e = data.index(b"\0", strs["off"] + nm)
        syms.append(dict(name=data[strs["off"] + nm:e].decode("latin-1"), val=val, size=size, bind=info >> 4,
                         type=info & 15, shndx=shndx))
    relocs = {}
    for s in secs:
        if s["type"] in (4, 9):
            ent = 12 if s["type"] == 4 else 8
            rel = relocs.setdefault(s["info"], [])
            for i in range(s["size"] // ent):
                vals = struct.unpack_from("<III" if ent == 12 else "<II", data, s["off"] + i * ent)
                sym = syms[vals[1] >> 8] if (vals[1] >> 8) < len(syms) else {"name": "?"}
                rel.append((vals[0], vals[1] & 255, sym["name"] or secs[sym.get("shndx", 0)]["name"] if sym.get("shndx", 0) < len(secs) else sym["name"],
                            vals[2] if ent == 12 else 0))
    for si, s in enumerate(secs):
        if s["type"] != 1 or not (s["flags"] & 4):  # PROGBITS, EXECINSTR
            continue
        here = sorted((y for y in syms if y["shndx"] == si and y["name"] and y["type"] != 3), key=lambda y: y["val"])
        body = data[s["off"]:s["off"] + s["size"]]
        if not here or here[0]["val"] > 0:  # code before the first exported symbol: static functions
            here.insert(0, dict(name="@start", val=0, bind=0))
        for j, y in enumerate(here):
            end = here[j + 1]["val"] if j + 1 < len(here) else s["size"]
            if end <= y["val"]:
                continue
            rs = [r for r in relocs.get(si, []) if y["val"] <= r[0] < end]
            rows.append(dict(source=source, section=s["name"], name=y["name"], bind=y["bind"], off=y["val"],
                             hex=body[y["val"]:end].hex(), rel=[[r[0] - y["val"], r[1], r[2], r[3]] for r in rs]))
    return rows, {"secs": [(s["name"], s["type"], s["size"]) for s in secs if s["size"]],
                  "syms": len(syms)}


def main():
    objdir, shc, out = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve(), sys.argv[3]
    rows, fails, first = [], [], None
    for obj in sorted(objdir.rglob("*.obj")):
        elf = obj.with_suffix(".elf")
        if not elf.exists() or elf.stat().st_mtime < obj.stat().st_mtime:
            p = subprocess.run([str(shc / "elfcnv.exe"), obj.name, elf.name], cwd=obj.parent, capture_output=True, timeout=120,
                               stdin=subprocess.DEVNULL)
            if not elf.exists() or b"successful" not in (p.stdout + p.stderr).lower():
                fails.append((str(obj.relative_to(objdir)), (p.stdout + p.stderr).decode("latin-1")[:200]))
                elf.unlink(missing_ok=True)
                continue
        got, info = table(elf, obj.relative_to(objdir).with_suffix("").as_posix())
        rows += got
        first = first or info
    json.dump({"rows": rows}, open(out, "w", encoding="utf-8"))
    print(len(rows), "code symbols;", len(fails), "elfcnv failures", fails[:5])
    print("first object", first)
    if rows:
        r = rows[0]
        print(r["source"], r["section"], r["name"], r["bind"], r["off"], len(r["hex"]) // 2, r["rel"][:4])


if __name__ == "__main__":
    main()
