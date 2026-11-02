"""Gate 6 of tools/scorecard.py (docs/DEFINITION_OF_DONE.md, docs/RUNTIME_TARGET.md): does a console's reference game run at
144 fps at 2560x1600 with no emulated hardware in its default play path. Not a tool itself; it only reads.

Two sources, each "unmeasured" until it exists (and an unmeasured check never turns the gate green):
- the game log's session summary, the last line a play session writes (rule point 5 of RUNTIME_TARGET.md), read from the
  launcher's own data folder (spec: consoles.KEY.runtime.logs, relative to the projects folder). One line:
      play summary: shown_fps 143.9 output 2560x1600 frames 8640 tick_ms 1.10/2.30/9.10 draw_ms 2.00/3.10/12.00 frame_ms 6.90/7.20/14.00
  the three times are median/99th percentile/worst, in milliseconds. A session counts only at the target output size and
  with at least spec thresholds.runtime.min_frames shown frames; the gate takes the worst session of the logs kept
  (the heaviest section is what the target names).
- the "Emulated layers in the play path" table of RUNTIME_TARGET.md: a layer of this console (spec: runtime.layers) is
  open unless its Status starts with one of thresholds.runtime.layer_done. game.json has no per-game switch for a
  layer yet; when one exists this is where it is read.
- the "Aspect-ratio proof per game" table of RUNTIME_TARGET.md ("Any aspect ratio"): the reference game's row must list all of
  thresholds.runtime.aspect_shapes, say Yes for default on and for gameplay identical (replay, wide view on and off), and
  name evidence paths (relative to the checkout) that exist. No row is unmeasured; a row that falls short fails; nothing
  else can make the check green.
Nearest existing: scorecard_data.py - its readers cover workspaces' state files and the checkout's tools; none reads a
port's play logs or a markdown table.
"""
import glob
import pathlib
import re

from scorecard_data import at_least, at_most, check, unmeasured

LINE = "play summary:"
SUMMARY = re.compile(r"play summary: shown_fps ([\d.]+) output (\d+)x(\d+) frames (\d+)((?: \w+_ms [\d.]+/[\d.]+/[\d.]+)+)")
TABLE_HEAD = "## Emulated layers in the play path"
ASPECT_HEAD = "### Aspect-ratio proof per game"


def parse_summary(text):
    """The last session summary in `text` as {"fps", "w", "h", "frames", "tick_ms": (median, p99, worst), ...}, or None."""
    found = SUMMARY.findall(text)
    if not found:
        return None
    fps, w, h, frames, times = found[-1]
    out = {"fps": float(fps), "w": int(w), "h": int(h), "frames": int(frames)}
    for name, m, p, x in re.findall(r"(\w+_ms) ([\d.]+)/([\d.]+)/([\d.]+)", times):
        out[name] = (float(m), float(p), float(x))
    return out


def sessions(projects_dir, patterns):
    """[(file, summary)] for the logs matching `patterns` (relative to projects_dir) that carry a summary."""
    out = []
    for pat in patterns:
        for f in sorted(glob.glob(str(pathlib.Path(projects_dir) / pat))):
            try:
                s = parse_summary(pathlib.Path(f).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if s:
                out.append((f, s))
    return out


def layer_rows(doc):
    """[(layer, where, cost, status)] of the emulated-layers table in RUNTIME_TARGET.md's text."""
    if TABLE_HEAD not in doc:
        return []
    rows = []
    for ln in doc.split(TABLE_HEAD, 1)[1].splitlines():
        if ln.startswith("## "):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        if len(cells) == 4 and cells[0] not in ("Layer",) and not set(cells[0]) <= set("-: "):
            rows.append(tuple(cells))
    return rows


def layers_check(doc, keys, done):
    name = "emulated layers in the default play path"
    mine = [r for r in layer_rows(doc or "") if any(k in r[0] for k in keys)]
    if not mine:
        return unmeasured(name, "no row for this console in docs/RUNTIME_TARGET.md's emulated-layers table", "0")
    if all(r[1].startswith("No port kit") for r in mine):
        return unmeasured(name, "no port kit for this console yet (docs/RUNTIME_TARGET.md)", "0")
    open_ = [r[0] for r in mine if not r[3].startswith(tuple(done)) and not r[1].startswith("No port kit")]
    return check(name, "fail" if open_ else "pass", len(open_), "0",
                 "docs/RUNTIME_TARGET.md table" + (": " + "; ".join(open_) if open_ else ""))


def aspect_rows(doc):
    """[(game, shapes, default_on, gameplay_identical, evidence)] of the aspect-ratio proof table in RUNTIME_TARGET.md's text."""
    if ASPECT_HEAD not in doc:
        return []
    rows = []
    for ln in doc.split(ASPECT_HEAD, 1)[1].splitlines():
        if ln.startswith("#"):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        if len(cells) == 5 and cells[0] != "Game" and not set(cells[0]) <= set("-: "):
            rows.append(tuple(cells))
    return rows


def aspect_check(doc, game, shapes, tool):
    """Never green without a proof: a row for the game that lists every shape, says default on and gameplay identical, and
    names evidence (paths in `tool`, comma separated) that all exist. A row that falls short fails; no row is unmeasured."""
    name = "any aspect ratio, on by default"
    target = "yes, at " + ", ".join(shapes)
    mine = [r for r in aspect_rows(doc or "") if game and game.lower() in r[0].lower()]
    if not mine:
        return unmeasured(name, f"no row for {game or 'a reference game'} in docs/RUNTIME_TARGET.md's aspect-ratio proof table"
                          if game else "no reference game for this console", target)
    _, seen, default_on, same, evidence = mine[0]
    have = {s.strip().lower() for s in re.split(r"[,;]", seen)}
    lacking = [f"shape {s}" for s in shapes if s.lower() not in have]
    lacking += [w for w, v in (("not on by default", default_on), ("gameplay not shown identical", same)) if not v.lower().startswith("yes")]
    paths = [e.strip("` ") for e in re.split(r"[,;]", evidence) if e.strip("` ")]
    gone = [p for p in paths if not (pathlib.Path(tool) / p).is_file()]  # a folder that exists shows nothing
    lacking += ["no evidence path"] if not paths else [f"evidence missing: {p}" for p in gone]
    return check(name, "fail" if lacking else "pass", len(shapes) - sum(1 for s in shapes if s.lower() not in have), target,
                 f"{game}, docs/RUNTIME_TARGET.md proof table" + (": " + "; ".join(lacking) if lacking else ""))


def gate_runtime(spec, con, projects_dir, tool):
    th = spec["thresholds"]["runtime"]
    rt = con.get("runtime") or {}
    pats = rt.get("logs") or []
    ref = rt.get("reference") or "a reference game"
    found = sessions(projects_dir, pats)
    want = f"{th['width']}x{th['height']}"
    good = [(f, s) for f, s in found if s["w"] * s["h"] >= th["width"] * th["height"] and s["frames"] >= th["min_frames"]]
    names = ["shown frames per second (heaviest section)", "shown frame time, 99th percentile"]
    if not good:
        why = (f"no '{LINE}' line yet in {', '.join(pats) or 'any log (the spec names none for this console)'}: the kit writes it at "
               f"the end of a play session" if not found else
               f"{len(found)} session(s) in the logs, none at {want} with {th['min_frames']}+ frames: "
               + "; ".join(f"{s['w']}x{s['h']} {s['frames']} frames {s['fps']} fps" for _, s in found[:3]))
        out = [unmeasured(names[0], why, f">= {th['fps']} fps"), unmeasured(names[1], why, f"<= {th['frame_p99_ms']} ms")]
    else:
        src = f"{ref}: {len(good)} session(s) at {want}, the worst counts"
        out = [at_least(names[0], min(s["fps"] for _, s in good), th["fps"], src, " fps"),
               at_most(names[1], max(s["frame_ms"][1] for _, s in good if "frame_ms" in s) if all("frame_ms" in s for _, s in good)
                       else None, th["frame_p99_ms"], src + "; frame_ms p99", " ms")]
    doc = (pathlib.Path(tool) / "docs" / "RUNTIME_TARGET.md")
    text = doc.read_text(encoding="utf-8") if doc.is_file() else None
    out.append(layers_check(text, rt.get("layers") or [], th["layer_done"]) if text is not None else
               unmeasured("emulated layers in the default play path", "docs/RUNTIME_TARGET.md not found", "0"))
    out.append(aspect_check(text, rt.get("reference"), th["aspect_shapes"], tool) if text is not None else
               unmeasured("any aspect ratio, on by default", "docs/RUNTIME_TARGET.md not found", "yes"))
    return out
