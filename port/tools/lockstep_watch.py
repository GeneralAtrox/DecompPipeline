"""The lockstep check's writer watch (tools/lockstep.py --watch NAME[J]@N): the port logs each write to that spec
word through port frame N (runtime/lockstep_watch.c), and the writes are named here from the build's port.map."""
import bisect
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
    for line in open(path, encoding="ascii", errors="replace"):
        m = re.match(r"\s*\w{4}:\w{8}\s+(\S+)\s+([0-9a-f]{8})\s", line)
        if m and "<absolute>" not in line:
            syms.append((int(m.group(2), 16), m.group(1)))
        elif "Preferred load address is" in line:
            base = int(line.split()[-1], 16)
    syms.sort()
    return base, syms


def report_hits(log, map_path, callers=4):
    """Print the watch's writes from the port's log: the function that wrote, then the likeliest callers (code
    addresses on the stack, nearest first). The image may load away from the map's preferred base."""
    base, syms = read_map(map_path)
    keys = [a for a, _ in syms]

    def name(a):
        i = bisect.bisect_right(keys, a) - 1
        ok = i >= 0 and a - syms[i][0] < FUNC_REACH and not syms[i][1].startswith(("_g_", "??_C", "__imp_"))
        return f"{syms[i][1].lstrip('_')}+{a - syms[i][0]:x}" if ok else None

    image, hits = base, 0
    for line in open(log, encoding="ascii", errors="replace"):
        if not line.startswith("lockstep watch:"):
            continue
        p = line.split()
        if "image" in p:
            image = int(p[p.index("image") + 1], 16)
            print(line.strip())
        elif "after" in p:
            hits += 1
            shift = base - image
            at = int(p[p.index("after") + 1], 16) + shift
            stack = [name(int(w, 16) + shift) for w in p[p.index("stack") + 1:]]
            up = [s for s in stack if s][:callers]
            print(f"  write {p[p.index('write') + 1]} in {name(at) or hex(at)}; stack: {', '.join(up) or '-'}")
        else:
            print(line.strip())
    if not hits:
        print("  no write to the word in that frame (the value came from an earlier frame)")
