"""The port base's drift check: a console kit may not do what the base does (port/README.md "The port base").

Scans every console kit's source for what only base/ may own: a window, a pad read, a log, a menu, an audio device, a
presenter, the wait for the real clock, a program entry.
A kit is every folder under port/ except base/ (and the tools, the shaders and the vendored code), and every
`targets/*/port/` folder (a kit kept beside its translator, such as the Xbox 360 kit's). Comments and strings that only
mention a call are not matched: the scan strips comments and looks for the call itself.

Usage: python port/tools/base_drift.py [--root DIR]   (exit 1 and the findings when a kit has drifted)

Nearest existing: tests/test_port_kit.py (compiles the Dreamcast kit but checks nothing about who owns the window) -
this is the one scan every kit goes through, and tests/test_port_base.py runs it on the tree and on a planted copy.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent.parent

# What only base/ may own. Each rule: (what it is, regex on comment-stripped C/C++ source)
RULES = [
    ("a window of its own", re.compile(r"\b(?:CreateWindow(?:Ex)?[AW]?|RegisterClass(?:Ex)?[AW]?|SDL_CreateWindow(?:WithProperties)?)\s*\(")),
    ("a pad read of its own", re.compile(
        r"\b(?:XInputGet(?:State|Capabilities|Keystroke)|GetAsyncKeyState|joyGetPosEx|DirectInput8Create|"
        r"SDL_(?:OpenGamepad|GetGamepads|OpenJoystick|GetJoysticks|GetKeyboardState))\b|\"xinput1_[0-9_]*\.dll\"")),
    ("a log of its own", re.compile(
        r"^\s*(?:static\s+)?(?:void|int)\s+port_log(?:_open|_flush)?\s*\(|\bFILE\s*\*\s*g_port_log\b|\bf?open(?:_s)?\s*\([^;]*\.log\"", re.M)),
    ("a menu of its own", re.compile(r"\b(?:CreateMenu|CreatePopupMenu|AppendMenu[AW]?|InsertMenu[AW]?|SetMenu|TrackPopupMenu[Ex]*)\s*\(")),
    ("an audio device of its own", re.compile(
        r"\b(?:waveOut(?:Open|Write)|SDL_OpenAudioDevice(?:Stream)?|XAudio2Create|DirectSoundCreate8?)\s*\(|\bIMMDeviceEnumerator\b")),
    ("a presenter of its own", re.compile(
        r"\b(?:D3D11CreateDevice(?:AndSwapChain)?|CreateDXGIFactory[12]?|SDL_CreateRenderer|StretchDIBits|SetDIBitsToDevice)\s*\(")),
    ("pacing of its own", re.compile(r"\b(?:CreateWaitableTimer(?:Ex)?[AW]?|Sleep|SwitchToThread)\s*\(")),
    ("a program entry of its own", re.compile(r"^\s*(?:int|void)\s+(?:__cdecl\s+)?(?:w?main|[wW]inMain)\s*\(", re.M)),
]
SOURCE = {".c", ".cpp", ".cc", ".h", ".hpp"}
# A wait on a line carrying this comment is a test aid's or a sampler thread's, not the game's tick (base/pace.c has that)
NOT_THE_TICK = "not the tick"
# port/ folders that are not console kits: the base itself, tools, the renderer's shaders and generated output
NOT_KITS = {"base", "tools", "build", "third_party", "vendor", "vcpkg_installed"}
COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def kit_dirs(root: Path) -> list[Path]:
    """Every console kit folder under root."""
    out = []
    port = root / "port"
    if port.is_dir():
        out += [d for d in sorted(port.iterdir()) if d.is_dir() and d.name not in NOT_KITS and not d.name.startswith(".")]
    targets = root / "targets"
    if targets.is_dir():
        out += [d / "port" for d in sorted(targets.iterdir()) if (d / "port").is_dir()]
    return out


def strip_comments(text: str) -> str:
    """The source without its comments (keeping the newlines, so line numbers stay)."""
    return COMMENTS.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def scan(root: Path) -> list[str]:
    """The findings under root: 'path:line: <what> (<the matched call>)'."""
    found = []
    for kit in kit_dirs(root):
        for p in sorted(kit.rglob("*")):
            if p.suffix.lower() not in SOURCE or not p.is_file() or any(part in NOT_KITS for part in p.relative_to(kit).parts[:-1]):
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()
            text = strip_comments(raw)
            for what, rule in RULES:
                for m in rule.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    if line <= len(lines) and NOT_THE_TICK in lines[line - 1]:
                        continue
                    found.append(f"{p.relative_to(root).as_posix()}:{line}: {what}: {m.group(0).strip()[:60]}")
    return found


def menu_labels(root: Path) -> list[str]:
    """The base's menu bar: the top-level menus and every entry's label, in order, from base/host_menu.cpp (the one
    place a menu is built)."""
    text = (root / "port" / "base" / "host_menu.cpp").read_text(encoding="utf-8")
    labels = []
    for m in re.finditer(r'AppendMenuW\([^;]*?\bL"((?:[^"\\]|\\.)*)"\)', text):
        labels.append(m.group(1).replace("\\t", "\t"))
    return labels


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(TOOL), help="the repository to scan (default: this one)")
    root = Path(ap.parse_args(argv).root)
    found = scan(root)
    for f in found:
        print(f)
    if found:
        print(f"{len(found)} place(s) where a console kit does what port/base/ owns (port/README.md \"The port base\")", file=sys.stderr)
    else:
        print(f"ok: {len(kit_dirs(root))} kit folder(s) hold no window, pad read, log, menu or entry of their own")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
