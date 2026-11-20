"""What each translated function reads before it sets it, for fn_parts.hand_over: {address: ["r11", "f1", ...]} from gen/'s
"read before set" lines. A caller stores those registers in tc_gr / tc_fr before it calls the function, and the function loads them.
Cached in OUT/cache/read_first.json on the newest gen/ file's time (a rewrite keeps the folder's).

Nearest existing: xenontranslate/returns.py cached_kinds - the same kind of whole-game summary, cached beside it, made from the
catalogue rather than from gen/."""
import json
import re


def load(gen, cache):
    p, t = cache / "read_first.json", max((f.stat().st_mtime_ns for f in gen.glob("fn_*.c")), default=0)
    old = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    if old.get("t") != t:
        old = {"t": t, "map": {}}
        for f in gen.glob("fn_*.c"):
            with open(f, encoding="utf-8", errors="replace") as fh:
                m = re.search(r"/\* read before set: ([^*]*)\*/", fh.readline() + fh.readline())
            if m and re.search(r"[rf]\d+", m.group(1)):
                old["map"][f.stem[3:]] = [n for n in m.group(1).split() if re.fullmatch(r"[rf]\d+", n)]
        p.write_text(json.dumps(old), encoding="utf-8")
    return {int(a, 16): v for a, v in old["map"].items()}
