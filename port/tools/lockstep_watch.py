"""The lockstep check's writer watch (tools/lockstep.py --watch NAME[J]@N): the port logs each write to that spec
word through port frame N (runtime/lockstep_watch.c), and the writes are named here from the build's port.map."""
import bisect
import pathlib
import re

FUNC_REACH = 0x4000  # a code address this far past a symbol is not in it


def exit_line(log):
    """Why the port's run ended: its log's last EXIT line (runtime/main.c port_exit), e.g. "HALT: the original's
    endless loop (tc_halt), called from port.exe+..." for a halt (runtime/sh4_rt.c)."""
    try:
        ends = re.findall(r"^EXIT: (.*)$", open(log, encoding="utf-8", errors="replace").read(), re.M)
    except OSError:
        ends = []
    return ends[-1] if ends else "no EXIT line"


def watch_arg(items, text, layout):
    """The port's --lockstep-watch value (trace word@frame) for "NAME[J]@N" (J defaults to 0)."""
    m = re.fullmatch(r"(\w+)(?:\[(\d+)\])?@(\d+)", text)
    if not m:
        raise SystemExit(f"--watch {text}: not NAME[J]@FRAME")
    name, j, n = m.group(1), int(m.group(2) or 0), int(m.group(3))
    at = next((at for it, at in layout(items, True) if it["name"] == name and j < it["words"]), None)
    if at is None:
        raise SystemExit(f"--watch {text}: no such word in the port's trace")
    return f"{at + j}@{n}"


def read_map(path):
    """(preferred base, [(address, name)] sorted) of an MSVC map."""
    base, syms = 0, []
    for line in pathlib.Path(path).read_text(encoding="ascii", errors="replace").splitlines():
        m = re.match(r"\s*\w{4}:\w{8}\s+(\S+)\s+([0-9a-f]{8})\s", line)
        if m and "<absolute>" not in line:
            syms.append((int(m.group(2), 16), m.group(1)))
        elif "Preferred load address is" in line:
            base = int(line.split()[-1], 16)
    syms.sort()
    return base, syms


def watch_hits(log, map_path, callers=4):
    """The watch's lines from the port's log: (other log lines, [{"value", "after", "in", "callers", "word"}]) in the
    order the writes ran, with the writing function and the likeliest callers (code addresses on the stack, nearest
    first) named from the build's port.map, and the watched word written (its address, from the arming line). A
    word's last write past its share is logged as the watch ends, with its place in the order (seq). The image may load
    away from the map's preferred base."""
    base, syms = read_map(map_path)
    keys = [a for a, _ in syms]

    def name(a):
        i = bisect.bisect_right(keys, a) - 1
        ok = i >= 0 and a - syms[i][0] < FUNC_REACH and not syms[i][1].startswith(("_g_", "??_C", "__imp_"))
        return f"{syms[i][1].lstrip('_')}+{a - syms[i][0]:x}" if ok else None

    image, notes, hits, words, order = base, [], [], [], []
    for line in pathlib.Path(log).read_text(encoding="ascii", errors="replace").splitlines():
        if not line.startswith("lockstep watch:"):
            continue
        p = line.split()
        if "image" in p:
            image = int(p[p.index("image") + 1], 16)
            words = p[p.index("word") + 1:p.index("views" if "views" in p else "image")] if "word" in p else []
            notes.append(line.strip())
        elif "after" in p:
            shift = base - image
            at = int(p[p.index("after") + 1], 16) + shift
            stack = [name(int(w, 16) + shift) for w in p[p.index("stack") + 1:]]
            i = int(p[p.index("word") + 1]) if "word" in p else 0
            order.append(int(p[p.index("seq") + 1]) if "seq" in p else len(order))
            hits.append({"value": p[p.index("write") + 1], "after": f"{at:08x}", "in": name(at) or hex(at),
                         "callers": [s for s in stack if s][:callers], "word": words[i] if i < len(words) else None})
        else:
            notes.append(line.strip())
    return notes, [h for _, h in sorted(zip(order, hits), key=lambda x: x[0])]


def report_hits(log, map_path, callers=4):
    """Print the watch's writes from the port's log: the function that wrote, then the likeliest callers."""
    notes, hits = watch_hits(log, map_path, callers)
    for line in notes[:1]:
        print(line)
    for h in hits:
        print(f"  write {h['value']} to {h['word'] or '?'} in {h['in']}; stack: {', '.join(h['callers']) or '-'}")
    for line in notes[1:]:
        print(line)
    if not hits:
        print("  no write to the word in that frame (the value came from an earlier frame)")
