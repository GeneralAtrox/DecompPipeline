"""Name the `port.exe+OFFSET` addresses in a port log with the linker map (the function containing each).

    python tools/symbolize.py <port.map> <port.log>
"""
import bisect
import re
import sys


def load_map(path):
    """[(rva, symbol)] sorted, from the map's "Publics by Value" and static-symbol sections."""
    base, syms = 0, []
    for ln in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"\s*Preferred load address is ([0-9a-fA-F]+)", ln)
        if m:
            base = int(m.group(1), 16)
            continue
        m = re.match(r"\s*[0-9a-fA-F]{4}:[0-9a-fA-F]{8}\s+(\S+)\s+([0-9a-fA-F]{8})\s+f?\s", ln)
        if m and int(m.group(2), 16) >= base:
            syms.append((int(m.group(2), 16) - base, m.group(1)))
    syms.sort()
    return syms


def main():
    syms = load_map(sys.argv[1])
    keys = [s[0] for s in syms]
    for ln in open(sys.argv[2], encoding="utf-8", errors="replace"):
        ln = ln.rstrip("\n")
        m = re.search(r"port\.exe\+([0-9a-f]{8})", ln)
        if m:
            off = int(m.group(1), 16)
            i = bisect.bisect_right(keys, off) - 1
            if i >= 0:
                ln += f"  {syms[i][1]}+{off - syms[i][0]:#x}"
        print(ln)


if __name__ == "__main__":
    main()
