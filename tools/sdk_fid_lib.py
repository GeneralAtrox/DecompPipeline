"""Pure parts of tools/sdk_fid.py (no Ghidra, no SDK tools): library formats, the libraries an SDK holds, skip-if-done
stamps, command-line chunks, and the FID result merge and TSV writing. Tested by tests/test_batch25a.py."""
import hashlib
import json
import pathlib
import re

SYSROF_LIB, SYSROF_OBJ, ELF, AR = "sysrof_lib", "sysrof_obj", "elf", "ar"
# Folders that hold other compilers' builds, samples or tools, never the SHC libraries a game links.
SKIP_PARTS = {"gnu", "mwerks", "codewarrior", "sample", "samples", "demo", "submit", "vmutool", "utl", "doc", "driver"}
FID_THRESHOLD = 14.6  # Ghidra's FidService.SCORE_THRESHOLD: a match below it is not used


def detect_format(path):
    """A library file's format from its first bytes: Hitachi SYSROF library (record 0xE0) or object (0x80), ELF,
    an ar archive, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head[:4] == b"\x7fELF":
        return ELF
    if head == b"!<arch>\n":
        return AR
    if len(head) >= 8 and head[0] == 0xE0:
        return SYSROF_LIB
    if len(head) >= 8 and head[0] == 0x80:
        return SYSROF_OBJ
    return None


def family_name(path):
    """The FID library family (and project folder) of a library file: its stem, lower case, made folder-safe."""
    stem = pathlib.PurePath(path).name.split(".")[0].lower()
    return re.sub(r"[^a-z0-9_+-]", "_", stem) or "lib"


def select_libraries(root, include=None, exclude=None, formats=(SYSROF_LIB, SYSROF_OBJ)):
    """[(family, path, format)] for every library file under root (a .lib/.obj/.a/.o/.elf whose bytes say it is one
    of `formats`), skipping SKIP_PARTS folders. include/exclude: regexes on the family name. A byte-identical copy
    of a file already taken is left out; other families are unique (a second file with the same family gets _2,
    _3...). Shallower paths first, then by path, so the result is stable."""
    root = pathlib.Path(root)
    inc = re.compile(include, re.I) if include else None
    exc = re.compile(exclude, re.I) if exclude else None
    out, seen, digests = [], {}, set()
    for p in sorted(root.rglob("*"), key=lambda q: (len(q.parts), q.as_posix().lower())):
        if not p.is_file() or p.suffix.lower() not in (".lib", ".obj", ".a", ".o", ".elf"):
            continue
        if any(part.lower() in SKIP_PARTS for part in p.relative_to(root).parts[:-1]):
            continue
        kind = detect_format(p)
        if kind not in formats:
            continue
        fam = family_name(p)
        if (inc and not inc.search(fam)) or (exc and exc.search(fam)):
            continue
        digest = file_digest(p)
        if digest in digests:
            continue
        digests.add(digest)
        n = seen.get(fam, 0) + 1
        seen[fam] = n
        out.append((fam if n == 1 else f"{fam}_{n}", p, kind))
    return out


def file_digest(path):
    """sha1 of a file's bytes (the identity a stamp records)."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stamp_matches(stamp_path, expected):
    """True when stamp_path holds exactly `expected` (a JSON-able dict): that step is done for these inputs."""
    try:
        return json.loads(pathlib.Path(stamp_path).read_text(encoding="utf-8")) == expected
    except (OSError, ValueError):
        return False


def write_stamp(stamp_path, data):
    p = pathlib.Path(stamp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def chunks(items, max_chars=6000, max_items=40):
    """Split arguments into runs whose joined length stays under max_chars (cmd.exe stops at 8191 characters, and a
    .bat launcher passes everything through it)."""
    out, cur, size = [], [], 0
    for it in items:
        n = len(str(it)) + 3
        if cur and (size + n > max_chars or len(cur) >= max_items):
            out.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += n
    if cur:
        out.append(cur)
    return out


def ar_members(data):
    """[(name, bytes)] of a System V / GNU ar archive (the long-name table resolved; symbol tables skipped)."""
    if data[:8] != b"!<arch>\n":
        raise ValueError("not an ar archive")
    p, names, out = 8, b"", []
    while p + 60 <= len(data):
        hdr = data[p:p + 60]
        name = hdr[:16].decode("latin-1").rstrip()
        size = int(hdr[48:58].decode("latin-1").strip() or 0)
        body = data[p + 60:p + 60 + size]
        p += 60 + size + (size & 1)
        if name in ("/", "/SYM64/", "__.SYMDEF", "__.SYMDEF SORTED"):
            continue
        if name == "//":
            names = body
            continue
        if name.startswith("/") and name[1:].isdigit():
            off = int(name[1:])
            name = names[off:names.index(b"\n", off)].decode("latin-1")
        out.append((name.rstrip("/"), body))
    return out


def strip_symbol(name):
    """A C symbol as SHC emits it (_kmBeginScene) as the C name (kmBeginScene)."""
    return name[1:] if name.startswith("_") and len(name) > 1 else name


def read_tsv(path):
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    return [dict(zip(head, ln.split("\t"))) for ln in lines[1:] if ln.strip()]


def write_tsv(path, header, rows):
    """Write rows (dicts or sequences) under header; tabs and newlines inside values become spaces."""
    def cell(v):
        return re.sub(r"[\t\r\n]+", " ", "" if v is None else str(v))
    lines = ["\t".join(header)]
    for r in rows:
        vals = [r.get(h, "") for h in header] if isinstance(r, dict) else list(r)
        lines.append("\t".join(cell(v) for v in vals))
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


TC_FID_HEADER = ["addr", "region", "our_name", "fid_names", "unique", "candidates", "score", "library_object",
                 "release", "status", "code_units"]


def merge_fid(raw_rows, names, game_end, threshold=FID_THRESHOLD):
    """One tc_fid row per function from SdkFidApply rows (any number of releases): the FID names scoring at least
    `threshold` (C names, best score first), unique yes/best/no (see below), the objects and releases that
    gave the best name, and the status (match / below / nomatch / small)."""
    by = {}
    for r in raw_rows:
        by.setdefault(r["addr"], []).append(r)
    out = []
    for addr in sorted(by):
        rows = by[addr]
        units = next((r["code_units"] for r in rows if r.get("code_units")), "")
        matches = [r for r in rows if r["status"] == "match"]
        good = [r for r in matches if float(r["overall"]) >= threshold]
        status = "match" if good else "below" if matches else (
            "nomatch" if any(r["status"] == "nomatch" for r in rows) else "small")
        row = {"addr": addr, "region": "game" if int(addr, 16) < game_end else "library",
               "our_name": names.get(addr, ""), "status": status, "code_units": units}
        if good:
            best = {}
            for r in good:
                n = strip_symbol(r["name"])
                best[n] = max(best.get(n, 0.0), float(r["overall"]))
            order = sorted(best, key=lambda n: (-best[n], n))
            top = [r for r in good if strip_symbol(r["name"]) == order[0]]
            # unique: "yes" one distinct name; "best" several, but the first scores strictly higher (FID's
            # child/parent scores told them apart); "no" a tie at the top.
            uniq = "yes" if len(order) == 1 else "best" if best[order[0]] > best[order[1]] else "no"
            row.update(fid_names="|".join(order), unique=uniq,
                       candidates=len(good), score=f"{best[order[0]]:.2f}",
                       library_object="|".join(sorted({f"{r['family']}:{r['object']}" for r in top})),
                       release="|".join(sorted({r["version"] for r in top})))
        out.append(row)
    return out
