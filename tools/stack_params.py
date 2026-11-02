"""Pointer types for the parameters callers pass a frame buffer: a machine pass over the lift's files.

The judge makes a mocked callee deliver its out bytes through a pointer parameter of the callee's prototype, never a
raw u32 (core/readable_outptr). A raw signature has only u32s, so a correct readable caller that passes a local buffer
reads garbage back and fails (Vigilante 8: 19 of 110 functions, Matrix_BuildRowAlt(flags, (u32)frame_buf36, ctx)). The
lift's own files say which parameters those are: an argument `(u32)frame_bufN`, the frame address the original passes
(core/rtx/framebuf). Each becomes `void *` in the callee's names entry. Nothing about the judge changes; the vectors
decide on the new files. Readable files that call or define the callee are listed: they need `refine.py verify`.

usage: python tools/stack_params.py --project DIR --lifted OUT/readable/GAME [--write]

Nearest existing: tools/name_pass - it decides names and keeps the raw signature's types by design; core/sig_override
changes return types only.
"""
import argparse
import json
import pathlib
import re
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project  # noqa: E402
from core.rtx.selfcall import split_args  # noqa: E402

BUFFER = re.compile(r"\(u32\)frame_buf\d+(?: \+ \d+)?$")
CALL = re.compile(r"\b([A-Za-z_]\w*)\(")
PROTO = re.compile(r"^(.*?\()(.*)(\)\s*;?\s*)$", re.S)


def buffer_args(text):
    """{(callee, argument index)} of the calls in text that pass a frame buffer."""
    found = set()
    for m in CALL.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        for k, a in enumerate(split_args(text[m.end():i - 1])):
            if BUFFER.match(a):
                found.add((m.group(1), k))
    return found


def retyped(proto, indexes):
    """proto with those parameters `void *` (only a u32 or s32 one changes), and whether anything changed."""
    m = PROTO.match(proto)
    params = [p.strip() for p in m.group(2).split(",")]
    changed = False
    for k in indexes:
        p = re.fullmatch(r"[us]32\s+(\w+)", params[k]) if k < len(params) else None
        if p:
            params[k] = f"void *{p.group(1)}"
            changed = True
    return m.group(1) + ", ".join(params) + m.group(3), changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--lifted", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    ws = use_project(a.project)
    want = {}
    for f in pathlib.Path(a.lifted).glob("*.c"):
        for fn, k in buffer_args(f.read_text(encoding="utf-8")):
            want.setdefault(fn, set()).add(k)
    changed, names_dir = [], ws / "readable" / "names"
    for f in sorted(names_dir.glob("*.json")):
        raw = f.read_bytes()
        d = json.loads(raw.decode("utf-8"))
        hits = 0
        for addr, e in (d.get("functions") or {}).items():
            m = CALL.search(e.get("proto", ""))
            if m and m.group(1) in want:
                proto, ch = retyped(e["proto"], want[m.group(1)])
                if ch:
                    e["proto"] = proto
                    changed.append((addr, m.group(1), proto))
                    hits += 1
        if hits and a.write:
            nl = "\r\n" if b"\r\n" in raw else "\n"
            f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline=nl)
    stale = set()
    for f in (ws / "readable" / "src").glob("*.c"):
        text = f.read_text(encoding="utf-8", errors="replace")
        if any(re.search(rf"\b{fn}\s*\(", text) for _, fn, _ in changed):
            stale.add(f.stem)
    for addr, _, proto in changed:
        print(f"{addr}  {proto}")
    print(f"{len(changed)} prototype(s) {'written' if a.write else 'would change'}; readable files to verify: "
          f"{len(stale)}")
    print(" ".join(sorted(stale)))


if __name__ == "__main__":
    main()
