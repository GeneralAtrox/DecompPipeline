"""Write a project's state/gate_scope.txt, the list the stamp scope "gate" reads (x360_scope, target.json
"stamp_scopes"): the functions TcGateScan.Seeds (the decision TcVectors takes) puts in the GATE class, as Ghidra's TcGateScan finds them on every
function of the project's Ghidra project (read-only). The first line binds the list to the scripts and the catalog it
was made from (x360_scope.gate_header); a change to either makes it stale, and a stale or missing list puts every
function in the scope. Run it again after a catalog or a scripts change. Needs the master Ghidra project unlocked
(no server, no prepare running).
Nearest existing: decode_audit.py / ghidra.export - they run other Ghidra scripts and write no scope list.
usage: py -3.12 targets/xbox360_ppc/tools/gate_scan.py --project DIR
"""
import argparse
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / "tools"), str(REPO / "targets" / "xbox360_ppc")]
from common import use_project  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True)
    a = ap.parse_args(argv)
    use_project(a.project)
    from core import ghidra, paths
    import x360_scope
    cfg = paths.config()
    out = paths.STATE / x360_scope.GATE_LIST
    tmp = out.with_suffix(".tmp")
    header = x360_scope.gate_header()  # before the run: the scripts it compiles and the catalog it reads
    ghidra._headless(cfg, ["-process", cfg["binary_path"].name, "-noanalysis", "-readOnly",
                           "-scriptPath", str(paths.sync_scripts()), "-postScript", "TcGateScan.java", str(tmp)],
                     "ghidra_gate_scan.log")
    if x360_scope.gate_header() != header:
        sys.exit("gate_scan: the scripts or the catalog changed during the scan; run it again")
    body = tmp.read_text(encoding="utf-8")
    tmp.write_text(header + "\n" + body, encoding="utf-8")
    os.replace(tmp, out)
    print(f"gate_scan: {body.splitlines()[0][2:]} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
