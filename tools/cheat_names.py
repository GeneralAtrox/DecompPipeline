"""Cheat codes as naming hints for the readable stage (docs/proposals/learnings_dc_decomp_kickoff.md, idea 4).

A cheat code is a RAM address with a meaning ("Infinite Missiles"). This reads a cheat list, turns each code into its
guest address and width, finds the game functions whose code uses that address (core/xmatch.normalize: literal-pool
words, so a field reached through a base address shows as "base + offset"), the names the readable stage already
gave it (readable/names/*.json globals), and writes the hints for the designers. Hints are checked, never trusted:
a list made for another region of the game usually has other addresses, so a region mismatch is said at the top and
an address no function uses is listed apart.

usage: python tools/cheat_names.py --project WS --cht LIST.cht [--region USA|Europe|Japan] [--out FILE.md]
       [--json FILE] [--near 0x400]
  --cht     a RetroArch/libretro .cht list (address form: cheatN_address is a decimal offset into main RAM,
            cheatN_memory_search_size 3/4/5 = 8/16/32 bits, cheatN_repeat_* for arrays). Entries given only as
            cheatN_code are counted and listed as not decoded.
  --region  the disc region when neither project.json's "disc" nor the port's game.json names it
  --out     default WS/readable/cheat_hints.md
Reads only; downloads nothing (getting a list needs the user's yes).
Nearest existing: core/xmatch (used here to find the functions that use an address) - it reads no cheat list and
knows nothing of the readable stage's names.
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # also under python -I
from core import xmatch  # noqa: E402

RAM_BASE = 0x8C000000
RAM_SIZE = 0x1000000
WIDTH = {"3": 8, "4": 16, "5": 32}  # memory_search_size: 0..2 are 1/2/4-bit, rare for Dreamcast lists
REGIONS = ("USA", "Europe", "Japan")


def parse_cht(text):
    """-> list of {index, desc, addr?, bits?, value?, count, step, code?} in list order."""
    keys = {}
    for line in text.splitlines():
        m = re.match(r'\s*cheat(\d+)_(\w+)\s*=\s*"?(.*?)"?\s*$', line)
        if m:
            keys.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    out, owner = [], None
    for i in sorted(keys):
        k = keys[i]
        c = {"index": i, "desc": k.get("desc", "").strip()}
        if k.get("address", "").strip() == "0":  # no address: a value for the code before it (a flag bit's meaning)
            c["value"] = k.get("value", "")
            c["of"] = owner
        elif k.get("address", "").strip().isdigit():
            off = int(k["address"])
            if off < RAM_SIZE:
                c["addr"] = RAM_BASE + off
                owner = c["addr"]
            c["bits"] = WIDTH.get(k.get("memory_search_size", ""), 0)
            c["value"] = k.get("value", "")
            c["count"] = max(1, int(k.get("repeat_count", "1") or 1))
            c["step"] = int(k.get("repeat_add_to_address", "0") or 0)
        elif k.get("code", "").strip():
            c["code"] = k["code"].strip()
        out.append(c)
    return out


def region_of(text):
    """The first of USA/Europe/Japan named in parentheses in a file name or path, else None."""
    for part in re.findall(r"\(([^)]*)\)", text or ""):
        for r in REGIONS:
            if r in part.split(","):
                return r
    return None


def in_code(img, a):
    return any(lo <= a < hi for lo, hi in img.code)


def holder(img, a):
    """The function whose bytes hold address a, or ''."""
    for f, row in img.functions.items():
        lo = int(f, 16)
        if lo <= a < lo + int(row["bytes"]):
            return f
    return ""


def port_region(ws):
    """The region of the disc the game's port/game.json names (the port folder beside the workspace's parents)."""
    for up in (ws.parent, ws.parent.parent):
        g = up / "port" / "game.json"
        if g.is_file():
            try:
                return region_of(json.loads(g.read_text(encoding="utf-8")).get("disc", ""))
            except ValueError:
                return None
    return None


def references(img):
    """{function addr: [data addresses its literal pools hold]} over the functions the project decompiles (code
    addresses left out: a function pointer is no base of a variable)."""
    refs = {}
    for addr in img.functions:
        if not img.in_scope(addr):
            continue
        n = xmatch.normalize(img, addr)
        if n:
            refs[addr] = sorted({a for a in (s & 0x1FFFFFFF | 0x80000000 for s in n[2] if xmatch.is_guest_addr(s))
                                 if not in_code(img, a)})
    return refs


def users(refs, target, near):
    """-> (exact users, [(function, base, offset)] users through a base address at most `near` below)."""
    exact, based = [], []
    for fn, slots in refs.items():
        if target in slots:
            exact.append(fn)
        else:
            below = [s for s in slots if target - near <= s < target]
            if below:
                based.append((fn, max(below), target - max(below)))
    return sorted(exact), sorted(based)


def known_globals(ws):
    """{guest addr: decl} from readable/names/*.json (the designers' names so far)."""
    names = {}
    for p in sorted((ws / "readable" / "names").glob("*.json")):
        try:
            g = json.loads(p.read_text(encoding="utf-8")).get("globals", {})
        except (ValueError, OSError):
            continue
        for a, v in g.items():
            if re.fullmatch(r"[0-9a-fA-F]{8}", a):
                names[int(a, 16)] = (v or {}).get("decl", "") if isinstance(v, dict) else str(v)
    return names


def named_at(names, target, near):
    """'decl' at the address, 'decl + 0xN' for the nearest named global below it within `near`, else ''."""
    if target in names:
        return names[target]
    below = [a for a in names if target - near <= a < target]
    if not below:
        return ""
    a = max(below)
    return "%s + 0x%x" % (names[a], target - a)


def hints(ws, cheats, near):
    img = xmatch.Image(ws)
    refs = references(img)
    names = known_globals(ws)
    rows = []
    for c in cheats:
        if "addr" not in c:
            rows.append({**c, "used_by": [], "via_base": [], "named": ""})
            continue
        if in_code(img, c["addr"]):
            rows.append({**c, "used_by": [], "via_base": [], "named": "", "in_code": holder(img, c["addr"])})
            continue
        ex, based = users(refs, c["addr"], near)
        rows.append({**c, "used_by": ex, "via_base": based, "named": named_at(names, c["addr"], near)})
    return rows


def render(rows, cht_name, list_region, disc_region):
    lines = ["# Cheat codes as naming hints", "",
             "From `%s` (tools/cheat_names.py). A hint, not a fact: name a global from it only when the code that"
             " uses the address agrees." % cht_name, ""]
    if list_region and disc_region and list_region != disc_region:
        lines += ["**Region mismatch:** the list is for %s, this disc is %s. Its addresses usually differ; trust a"
                  " hint only where the functions below clearly fit its description." % (list_region, disc_region), ""]
    elif not (list_region and disc_region):
        lines += ["Region not known for the list or the disc: check each hint against the code.", ""]
    used = [r for r in rows if r.get("used_by") or r.get("via_base")]
    patches = [r for r in rows if "in_code" in r]
    unused = [r for r in rows if "addr" in r and r not in used and r not in patches]
    coded = [r for r in rows if "code" in r]
    lines += ["| Cheat | Address | Width | Already named | Used by | Through a base |", "|---|---|---|---|---|---|"]
    for r in used:
        arr = " (x%d, step %d)" % (r["count"], r["step"]) if r["count"] > 1 else ""
        base = ", ".join("%s (0x%08x + 0x%x)" % b for b in r["via_base"][:6])
        lines.append("| %s | 0x%08x%s | %s | %s | %s | %s |" % (
            r["desc"].replace("|", "/"), r["addr"], arr, r["bits"] or "?", r["named"] or "-",
            ", ".join(r["used_by"][:8]) or "-", base or "-"))
    if patches:
        lines += ["", "Inside the program code on this disc (in the list's own build such a code changes an instruction;"
                  " it names the function holding it only when both builds have the same code there):", ""]
        lines += ["- %s: 0x%08x, in function %s" % (r["desc"], r["addr"], r["in_code"] or "?") for r in patches]
    if unused:
        lines += ["", "No game function uses these addresses (another region's address, or data reached by a"
                  " pointer):", ""]
        lines += ["- %s: 0x%08x" % (r["desc"], r["addr"]) for r in unused]
    values = [r for r in rows if "of" in r]
    if values:
        lines += ["", "Values with no address of their own: the meaning of a value or flag bit of the code before"
                  " them (name the bits of that word from these):", ""]
        for of in dict.fromkeys(r["of"] for r in values):
            lines.append("- %s: %s" % ("0x%08x" % of if of else "(no code before)", "; ".join(
                r["desc"] + (" (value %s)" % r["value"] if r["value"] not in ("", "0") else "")
                for r in values if r["of"] == of)))
    if coded:
        lines += ["", "%d codes given only as encoded codes (not decoded): %s" % (
            len(coded), "; ".join(r["desc"] for r in coded))]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--cht", required=True)
    ap.add_argument("--region")
    ap.add_argument("--out")
    ap.add_argument("--json")
    ap.add_argument("--near", default="0x400")
    a = ap.parse_args(argv)
    ws = pathlib.Path(a.project).resolve()
    cht = pathlib.Path(a.cht)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    disc_region = a.region or region_of(proj.get("disc", "")) or port_region(ws)
    list_region = region_of(cht.name)
    rows = hints(ws, parse_cht(cht.read_text(encoding="utf-8", errors="replace")), int(a.near, 0))
    out = pathlib.Path(a.out) if a.out else ws / "readable" / "cheat_hints.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, cht.name, list_region, disc_region), encoding="utf-8")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    n_used = sum(1 for r in rows if r.get("used_by") or r.get("via_base"))
    print("%d cheats, %d used by game code, list %s, disc %s -> %s" % (
        len(rows), n_used, list_region or "?", disc_region or "?", out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
