"""Overlay native and wrap functions (port/tools/gen_overlays.py "native" and "wrap", overlay_tx.py): the game's own C in
place of an overlay function, or around it (split from tests/test_overlay_slots.py at its 400-line cap; the built
check is test_overlay_slots.OverlaySlotsBuilt). Synthetic data. Run: python -W error tests/test_overlay_native.py
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "port" / "tools"), str(TOOL / "tests"), str(TOOL)]
import gen_overlays as go  # noqa: E402


class NativeFunctions(unittest.TestCase):
    """overlays.json "native": the game's own C in place of an overlay function (gen_overlays.py worker)."""

    def test_native_config(self):
        """{8 lower-case hex digits: a C name}; anything else stops the run."""
        import json
        game = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, game, ignore_errors=True)
        (game / "ws").mkdir()
        (game / "ws" / "project.json").write_text('{"game_ranges": [["8c600000", "8c601000"]]}', encoding="utf-8")

        def units(native):
            (game / "overlays.json").write_text(json.dumps({"overlays": [{"id": "a", "decomp": "ws", "native": native}]}),
                                                encoding="utf-8")
            return go.load_units(game)
        self.assertEqual(units({"8c600100": "Fx_Native"})[0]["native"], {"8c600100": "Fx_Native"})
        self.assertEqual(units(None)[0]["native"], {})
        for bad in ({"8C600100": "Fx"}, {"8c6001": "Fx"}, {"8c600100": "not a name"}, ["8c600100"]):
            with self.assertRaises(go.OverlayError):
                units(bad)

    def test_native_stub(self):
        """The function, under its registry prototype, calls the game's C with its arguments; a variadic one stops."""
        from test_sync import TMP  # noqa: F401  (sets DECOMP_PROJECT when run alone)
        from core import readable
        import overlay_tx
        self.assertEqual(overlay_tx.native_c(readable, "u32 Fx(u32 a0, u8 *p);", "Game_Fx"),
                         ["u32 Game_Fx(u32 a0, u8 *p);", "u32 Fx(u32 a0, u8 *p)", "{", "    return Game_Fx(a0, p);", "}"])
        self.assertEqual(overlay_tx.native_c(readable, "void Fx(void)", "Game_Fx")[3], "    Game_Fx();")
        with self.assertRaises(ValueError):
            overlay_tx.native_c(readable, "void Fx(u32 a0, ...)", "Game_Fx")
        self.assertEqual(overlay_tx.native_c(readable, "void Fx(u32 a0)", "Game_Fx", r0=True),
                         ["u32 Game_Fx(u32 a0);", "u32 Fx(u32 a0)", "{", "    return Game_Fx(a0);", "}"])
        self.assertEqual(overlay_tx.kept_stub(readable, "A", "void Fx(u32 a0);", "Game_Fx"),
                         ["u32 Game_Fx(u32 a0);", "u32 A__Fx(u32 a0)", "{", "    return Game_Fx(a0);", "}"])

    def test_wrap_config(self):
        """"wrap" has native's form; an address in both stops the run."""
        import json
        game = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, game, ignore_errors=True)
        (game / "ws").mkdir()
        (game / "ws" / "project.json").write_text('{"game_ranges": [["8c600000", "8c601000"]]}', encoding="utf-8")

        def units(**keys):
            (game / "overlays.json").write_text(json.dumps({"overlays": [{"id": "a", "decomp": "ws", **keys}]}),
                                                encoding="utf-8")
            return go.load_units(game)
        self.assertEqual(units(wrap={"8c600100": "Fx_Wrap"})[0]["wrap"], {"8c600100": "Fx_Wrap"})
        self.assertEqual(units()[0]["wrap"], {})
        for bad in ({"wrap": {"8c600100": "not a name"}}, {"wrap": ["8c600100"]},
                    {"wrap": {"8c600100": "W"}, "native": {"8c600100": "N"}}):
            with self.assertRaises(go.OverlayError):
                units(**bad)

    def test_wrap_renames_the_original(self):
        """Translator C's definition becomes <C name>_Original in its text (r0 kept where its header says so); a
        readable file compiles through a file that renames it; a function with neither stops the run."""
        import overlay_tx
        import tx_port
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        texts = {"8c600100": f"/* {tx_port.KEPT} */\nu32 A__Fx(void);\nu32 A__Fx(void)\n{{ return A__txj(); }}\n"}
        names = {"8c600100": "Fx", "8c600200": "Gx", "8c600300": "Hx"}
        files, kept = overlay_tx.wrap(d, {"8c600100": "Game_Fx", "8c600200": "Game_Gx"}, "A", names, ["8c600200"],
                                      ["8c600100"], texts, d / "src", ValueError)
        self.assertEqual(kept, {"8c600100"})
        self.assertNotIn("A__Fx(", texts["8c600100"])
        self.assertEqual(texts["8c600100"].count("Game_Fx_Original("), 2)
        self.assertEqual(files, {"8c600200": str(d / "wrap" / "8c600200.c")})
        self.assertIn("#define Gx Game_Gx_Original\n", (d / "wrap" / "8c600200.c").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            overlay_tx.wrap(d, {"8c600300": "Game_Hx"}, "A", names, [], [], texts, d / "src", ValueError)


if __name__ == "__main__":
    unittest.main()
