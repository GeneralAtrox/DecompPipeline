"""game.json "saturn" keys a launcher's play run needs (port/README.md "Launcher contract"), as sat_game.h lines:

- image_files and image_sha256 -> SAT_IMAGE_FILES and SAT_IMAGE_SHA256: a run without --image makes the code image from
  the player's disc (port/saturn/sat_image.c), since image.bin is game code and never ships in a release; setup and
  verify refuse a disc whose files make another image (exit code 5).
- fallback -> SAT_FALLBACK_DEFAULT: --fallback's default in this build, so a launcher's run, which passes no flags, runs
  as the game's Start Game.bat does.

tools/AutoOnboard/onboard_sat.py writes all three from its disc plan and the flags its boot proved.

Nearest existing: gen_saturn.py's own header lines (product, boot_dir) - these are the same kind, kept in a file of
their own for the 400-line cap.
"""
import json
import re
import sys

SHA256 = re.compile(r"[0-9a-f]{64}")


def image_h(cfg):
    """The sat_game.h lines for a game.json "saturn" object's image_files, image_sha256 and fallback."""
    out, files, sha = [], cfg.get("image_files"), str(cfg.get("image_sha256", "")).lower()
    if files is not None:
        if not isinstance(files, list) or not files or any(
                not isinstance(f, list) or len(f) != 2 or not isinstance(f[0], str) or not f[0].strip("/") for f in files):
            sys.exit('game.json image_files: a list of ["disc path", "0xLOAD_ADDRESS"], the code image\'s files')
        if not SHA256.fullmatch(sha):
            sys.exit("game.json image_sha256: the code image's SHA-256, 64 hex digits (image_files needs it)")
        try:
            rows = [(json.dumps("/" + p.strip("/")), int(str(b), 0)) for p, b in files]
        except ValueError:
            sys.exit("game.json image_files: a load address is not a number")
        out += ["#define SAT_IMAGE_FILES {" + ", ".join(f"{{{p}, 0x{b:08x}u}}" for p, b in rows) + "}",
                f'#define SAT_IMAGE_SHA256 "{sha}"']
    elif "image_sha256" in cfg:
        sys.exit("game.json image_sha256 without image_files")
    fb = cfg.get("fallback")
    if fb is not None:
        if fb not in ("on", "off"):
            sys.exit(f'game.json fallback: {fb!r} is not "on" or "off"')
        out.append(f'#define SAT_FALLBACK_DEFAULT "{fb}"')
    return out
