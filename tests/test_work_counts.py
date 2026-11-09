"""port/tools/work_counts.py's verdicts, and that a Direct3D call can't get past the counters.

The tool's run step is replaced, so this needs no game; docs/proofs/work_counts/plant.py plants faults in real ports.

Nearest existing: tests/test_port_kit.py - checks the kit names no game; it doesn't read these rules.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
import work_counts  # noqa: E402  pylint: disable=wrong-import-position


def result(draws=5, allocs=0):
    return {"fixed_clock": 1, "from": 10, "last": 20, "blanks": 10, "counts": {"draws": draws, "allocs": allocs},
            "peak": {"draws": 1, "allocs": allocs}}


def spec_of(tmp_path, base):
    scenes = {"menu": {"args": [], "from": 10, "to": 20, **{k: base[k] for k in ("blanks", "counts", "peak")}}}
    spec = {"args": [], "scenes": scenes, "ledger": [{"why": "first", "digest": work_counts.digest(scenes)}]}
    path = tmp_path / "work_counts.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path, spec


def faking(monkeypatch, *runs):
    runs = list(runs)
    monkeypatch.setattr(work_counts, "run_scene", lambda *a: runs.pop(0))


def check(path, spec):
    return work_counts.check(pathlib.Path("x.exe"), path, spec, ["menu"], 1, 1)


def test_same_counts_pass(tmp_path, monkeypatch):
    path, spec = spec_of(tmp_path, result())
    faking(monkeypatch, result())
    assert check(path, spec) == 0


def test_more_and_fewer_both_fail(tmp_path, monkeypatch, capsys):
    path, spec = spec_of(tmp_path, result())
    for now in (result(draws=6), result(draws=4), result(allocs=1)):
        faking(monkeypatch, now)
        assert check(path, spec) == work_counts.FAIL
    assert "allocs: 0 -> 1 (+1)" in capsys.readouterr().out


def test_a_hand_edited_baseline_fails(tmp_path, monkeypatch, capsys):
    path, spec = spec_of(tmp_path, result())
    spec["scenes"]["menu"]["counts"]["draws"] = 6
    faking(monkeypatch, result(draws=6))
    assert check(path, spec) == work_counts.FAIL
    assert "edited without accept" in capsys.readouterr().out


def test_a_run_that_cannot_be_compared_fails(tmp_path, monkeypatch):
    path, spec = spec_of(tmp_path, result())

    def broken(*_):
        raise RuntimeError("no result")
    monkeypatch.setattr(work_counts, "run_scene", broken)
    assert check(path, spec) == work_counts.CANNOT_RUN


def test_accept_needs_a_reason_and_repeating_counts(tmp_path, monkeypatch):
    path, spec = spec_of(tmp_path, result())
    exe = pathlib.Path("x.exe")
    faking(monkeypatch, result(draws=6), result(draws=6))
    assert work_counts.accept(exe, path, spec, ["menu"], 1, 1, "") == work_counts.FAIL
    faking(monkeypatch, result(draws=6), result(draws=7))
    assert work_counts.accept(exe, path, spec, ["menu"], 1, 1, "an honest long reason") == work_counts.FAIL
    assert spec["scenes"]["menu"]["counts"]["draws"] == 5


def test_accept_writes_the_reason_and_then_check_passes(tmp_path, monkeypatch):
    path, spec = spec_of(tmp_path, result())
    exe = pathlib.Path("x.exe")
    faking(monkeypatch, result(draws=6), result(draws=6))
    assert work_counts.accept(exe, path, spec, ["menu"], 1, 1, "the options screen draws a new row") == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["ledger"][-1]["why"] == "the options screen draws a new row"
    assert saved["ledger"][-1]["changes"]["menu"]["draws"] == [5, 6]
    faking(monkeypatch, result(draws=6))
    assert check(path, saved) == 0


def test_no_direct3d_call_gets_past_the_counters():
    """Everything that does work goes through present.h's counted macros, or the counts would miss it."""
    counted = ("Draw", "DrawIndexed", "DrawInstanced", "DrawIndexedInstanced", "Map", "UpdateSubresource", "CopyResource",
               "CopySubresourceRegion", "CreateTexture2D", "CreateBuffer")
    present = (ROOT / "port" / "base" / "present.h").read_text(encoding="utf-8")
    for name in counted:
        assert re.search(rf"#define ID3D11Device(Context)?_{name}\(", present), name
    for folder in ("base", "runtime", "pvr", "saturn"):
        for path in (ROOT / "port" / folder).rglob("*"):
            if path.suffix not in (".c", ".h", ".cpp") or path.name == "present.h":
                continue
            if path.name == "imgui_impl_dx11.cpp" and path.parent.parts[-2:] == ("third_party", "imgui"):
                # Dear ImGui's Direct3D backend (vendored) draws only the level editor's own UI. The editor runs with
                # the game paused, opened by Game > Level editor or the test option --level-edit-at, never in a play
                # frame, so these calls are not play work. Only the editor (level_edit*.cpp) may call the backend:
                callers = {c.name for c in (ROOT / "port").rglob("*.cpp") if "third_party" not in c.parts
                           and "ImGui_ImplDX11_" in c.read_text(encoding="utf-8", errors="replace")}
                assert callers and all(c.startswith("level_edit") for c in callers), callers
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "lpVtbl->" not in text, f"{path}: a direct Direct3D call"
            assert "<d3d11.h>" not in text, f"{path}: Direct3D without present.h's counted calls"
