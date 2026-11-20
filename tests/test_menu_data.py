"""game.json menu_choices and extra_buttons (port/tools/play_keys.py -> base/menu_data.c): checked, refused when malformed
or beside game_menu, and written to port_config.h as the C the base reads. Run: python tests/test_menu_data.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "port", "tools"))
import game_config  # noqa: E402
import play_keys  # noqa: E402

E = game_config.ConfigError
CHOICE = {"menu": "Game", "label": "&Count", "setting": "game.count", "choices": ["&4", "&8"], "values": ["4", "8"]}


class MenuData(unittest.TestCase):
    def test_absent_changes_nothing(self):
        lines = play_keys.defines(play_keys.config({}, E))
        self.assertIn("#define PORT_GAME_MENU 0", lines[2])
        self.assertFalse([l for l in lines if "MENU_DATA" in l or "MENU_CHOICES" in l or "EXTRA_BUTTONS" in l])

    def test_written(self):
        cfg = play_keys.config({"menu_choices": [dict(CHOICE, default="8")], "extra_buttons": {"actions": ["&Go"], "players": 2}}, E)
        text = "\n".join(play_keys.defines(cfg))
        self.assertIn("#define PORT_GAME_MENU 1", text)
        self.assertIn("#define PORT_MENU_DATA 1", text)
        self.assertIn('{"Game", "&Count", "game.count", 2, 1, {"&4", "&8"}, {"4", "8"}}', text)
        self.assertIn('#define PORT_EXTRA_BUTTONS "&Go"', text)
        self.assertIn("#define PORT_EXTRA_BUTTONS_PLAYERS 2", text)

    def test_refused(self):
        bad = [
            {"menu_choices": [dict(CHOICE, menu="Online")]},
            {"menu_choices": [dict(CHOICE, setting="fighters")]},
            {"menu_choices": [dict(CHOICE, values=["4", "4"])]},
            {"menu_choices": [dict(CHOICE, values=["4"])]},
            {"menu_choices": [dict(CHOICE, default="6")]},
            {"menu_choices": [CHOICE] * 9},
            {"extra_buttons": {"actions": []}},
            {"extra_buttons": {"actions": ["a"], "players": 9}},
            {"game_menu": True, "menu_choices": [CHOICE]},
        ]
        for raw in bad:
            with self.assertRaises(E, msg=raw):
                play_keys.config(raw, E)

    def test_commands_written(self):
        cfg = play_keys.config({"menu_commands": [
            {"menu": "Game", "label": "&Levels", "tag": 1, "list": "runtime"},
            {"menu": "Game", "label": "&Warp", "tag": 2, "list": ["Act 1 > Start", "Act 1 > Boss"]},
            {"menu": "Game", "label": "&Reset", "tag": 3}]}, E)
        text = "\n".join(play_keys.defines(cfg))
        self.assertIn("#define PORT_GAME_MENU 1", text)
        self.assertIn("#define PORT_MENU_DATA 1", text)
        self.assertIn('#define PORT_MENU_COMMANDS {"Game", "&Levels", 1, 2, 0, 0}, {"Game", "&Warp", 2, 1, 0, 2}, '
                      '{"Game", "&Reset", 3, 0, 2, 0}', text)
        self.assertIn('#define PORT_MENU_COMMAND_LABELS "Act 1 > Start", "Act 1 > Boss"', text)

    def test_commands_refused(self):
        one = {"menu": "Game", "label": "&Levels", "tag": 1, "list": "runtime"}
        bad = [
            {"menu_commands": [one, dict(one, label="&Other")]},  # the same tag twice
            {"menu_commands": [dict(one, list=["x"] * 436)]},  # more than the menu's 435 entries
            {"menu_commands": [dict(one, list=["x"] * 300), dict(one, tag=2, list=["y"] * 136)]},  # 436 in all
            {"game_menu": True, "menu_commands": [one]},
            {"menu_commands": [dict(one, tag=0)]},
            {"menu_commands": [dict(one, menu="Online")]},
            {"menu_commands": [dict(one, list=[])]},
            {"menu_commands": [dict(one, list="later")]},
        ]
        for raw in bad:
            with self.assertRaises(E, msg=str(raw)[:80]):
                play_keys.config(raw, E)
        play_keys.config({"menu_commands": [dict(one, list=["x"] * 435)]}, E)  # 435 is taken


if __name__ == "__main__":
    unittest.main()
