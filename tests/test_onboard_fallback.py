"""The onboard tool's side of the Saturn kit's SH-2 interpreter fallback (tools/AutoOnboard/onboard_sat.py): the boot stage reads the
kit's "fallbacks:" and "fallback:" lines, keeps each address in decomp/state/fallback_seeds.txt, translates the seeds on
the next boot (entry-only lines added to the list overlay.py reads), the census takes them as catalog_overrides.entry and
GAPS.md lists them; --pointers off drops overlay.py's --pointers. Pure Python, no build.
Run: python -W error tests/test_onboard_fallback.py.
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools" / "AutoOnboard"), str(ROOT / "tools")]
import onboard_sat as O  # noqa: E402

LOG = """port: start
FALLBACK #1 (master): 06034322 has no translated function; the SH-2 interpreter runs it; from 0600a000 < 00000000 < 00000000 < 00000000
fallbacks: 2 address(es), 7 entries, 900 instructions interpreted, 0 fault(s)
fallback: 06034322 entries 5 instructions 800 from 0600a000 < 06006000 < 00000000 < 00000000
fallback: 0603415a entries 2 instructions 100 nested from 06034322 < 0600a000 < 00000000 < 00000000
"""


def main():
    n, rows = O.fallback_facts(LOG)
    assert n == 2 and [r[0] for r in rows] == ["06034322", "0603415a"], (n, rows)
    assert rows[0][1] == 5 and rows[0][4] == "0600a000<06006000" and rows[1][3], rows
    assert O.fallback_facts("exit: done (0) after 9 vertical blanks\n") == (None, []), "no report must read as not reported"
    with tempfile.TemporaryDirectory() as tmp:
        ws = pathlib.Path(tmp)
        (ws / "state").mkdir()
        bodies = ws / "state" / "function_bodies.txt"
        bodies.write_text("06006000 06006000 06006010\n0603415a 0603415a 06034170\n", encoding="utf-8")
        assert O.seeded_bodies(ws, bodies) == bodies, "no seeds: the list itself"
        assert O.add_seeds(ws, rows) == ["06034322", "0603415a"]
        assert O.add_seeds(ws, rows) == [], "a seed is kept once"
        assert O.read_seeds(ws) == ["06034322", "0603415a"]
        sb = O.seeded_bodies(ws, bodies)
        lines = [ln for ln in sb.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
        assert lines[-1] == "06034322" and "0603415a" not in lines[2:], lines  # only the seed the list lacks, entry-only
        (ws / "project.json").write_text(json.dumps({"catalog_overrides": {"entry": ["06006000"]}}), encoding="utf-8")
        assert O.add_entries(ws, O.read_seeds(ws)) == ["06034322", "0603415a"]
        assert json.loads((ws / "project.json").read_text())["catalog_overrides"]["entry"] == ["06006000", "0603415a", "06034322"]
    assert "--pointers" in O.boot_flags(True) and "--pointers" not in O.boot_flags(False)
    assert "--pointers" not in O.tx_args("i", "b", "o", O.boot_flags(False))
    assert O.tx_key.__defaults__ is not None
    # --overlays off (Dragon Force's launcher build): no overlay translated, and its own build folder
    ao = (ROOT / "tools" / "AutoOnboard" / "autoonboard.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--overlays"' in ao and 'for flag in ("fallback", "pointers", "overlays")' in ao,         "--overlays must be an option and reach the build box"
    plan = {"image": [{"path": "/0", "base": 0x06010000, "size": 0x4000}],
            "left_out": [{"path": "/GAME/DF.BIN", "base": 0x06010000, "size": 0x40}]}
    df = {"/GAME/DF.BIN": bytes.fromhex("4f22e400000b0009") * 8}
    assert O.boot_overlay_files(plan, df, True) == O.overlay_files(plan, df)
    assert O.boot_overlay_files(plan, df, False) == []
    assert [O.build_suffix(p, v) for p, v in ((True, True), (False, True), (False, False))] == ["", "_np", "_np_novl"]
    src = (ROOT / "tools" / "AutoOnboard" / "onboard_sat.py").read_text(encoding="utf-8")
    assert '"--fallback", "on" if fallback else "off"' in src, "the boot run must pass --fallback to the kit"
    print("test_onboard_fallback: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
