"""Writes data/export_names.json: the names of the xboxkrnl and xam exports by ordinal, for the logs and the stops of the Xbox 360
kit (the XEX holds ordinals only). Source: the export tables of the Xenia project (BSD licence, Copyright 2021 Ben Vanik), as
shipped in hedge-dev/XenonRecomp (MIT) at XenonUtils/xbox/xboxkrnl_table.inc and xam_table.inc; data/NOTICE-xenia.txt keeps the
notice. Run again only to refresh from a newer copy.

Nearest existing: none - the repository holds no export-name table.

  python export_names.py XENONRECOMP/XenonUtils/xbox
"""
import json
import pathlib
import re
import sys

# An export whose name the forbidden-parts guard (tests/test_x360_port_forbidden.py) flags is left out: the guard scans this table like
# any kit source and its list is not changed to suit data. Such an import logs as "xboxkrnl#N" with no name.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "tests"))
from test_x360_port_forbidden import RULES  # noqa: E402

ROW = re.compile(r"XE_EXPORT\(\s*(\w+)\s*,\s*0x([0-9A-Fa-f]+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)")


def main():
    src = pathlib.Path(sys.argv[1])
    out = {}
    for f in ("xboxkrnl_table.inc", "xam_table.inc"):
        for lib, ordinal, name, kind in ROW.findall((src / f).read_text(encoding="utf-8", errors="replace")):
            if not any(rx.search(name) for rx in RULES.values()):
                out.setdefault(lib, {})[str(int(ordinal, 16))] = [name, kind]
    dst = pathlib.Path(__file__).resolve().parent.parent / "data" / "export_names.json"
    dst.write_text(json.dumps(out, indent=0, sort_keys=True) + "\n")
    print({k: len(v) for k, v in out.items()}, "->", dst)


if __name__ == "__main__":
    main()
