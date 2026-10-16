"""A prepared packet's agent-facing files: long asm.txt / ghidra.c split into parts of at most MAX_LINES lines
(moved out of core/ghidra.py in batch 27; ghidra re-exports them)."""
import re


MAX_LINES = 400


def _split(path, is_break):
    """Split a long file into path_1, path_2, ... of at most MAX_LINES lines, cutting at natural breaks."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_LINES:
        return
    budget = MAX_LINES - 2  # room for the continuation header/footer
    parts, start = [], 0
    while start < len(lines):
        end = min(start + budget, len(lines))
        if end < len(lines):
            cut = max((i for i in range(start + budget // 2, end) if is_break(lines[i])), default=end)
            end = cut
        parts.append(lines[start:end])
        start = end
    stem, ext = path.stem, path.suffix
    for n, chunk in enumerate(parts, 1):
        head = [f"{'//' if ext == '.c' else ';'} part {n} of {len(parts)} of {path.name}"]
        tail = [f"{'//' if ext == '.c' else ';'} continued in {stem}_{n + 1}{ext}"] if n < len(parts) else []
        (path.parent / f"{stem}_{n}{ext}").write_text("\n".join(head + chunk + tail) + "\n", encoding="utf-8")
    path.unlink()


def split_packet(packet):
    _split(packet / "asm.txt", lambda line: line.startswith("LAB_"))
    _split(packet / "ghidra.c", lambda line: not line.strip())


def packet_files(packet):
    """The agent-facing source files of a packet, in reading order (asm first)."""
    if not packet.exists():
        return []
    def key(p):
        m = re.search(r"_(\d+)\.", p.name)
        return (0 if p.name.startswith("asm") else 1, int(m.group(1)) if m else 0)
    return sorted([p for p in packet.iterdir() if re.fullmatch(r"(asm(_\d+)?\.txt|ghidra(_\d+)?\.c)", p.name)],
                  key=key)
