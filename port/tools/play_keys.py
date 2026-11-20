"""game.json's player, keyboard, menu and recording keys (coop, pads, keys, key_ramp, game_menu, menu_checks,
session_ahead): checked by game_config.load, written to port_config.h.

    "coop": 2..4    the most players of a co-op session (base/coop_join.c); 0 or absent: none     -> PORT_COOP
    "pads": 1..8    the host controllers read (base/input_device.c); absent: 4                     -> PORT_PADS, only when not 4
    "keys": {..}    the game's keyboard and mouse layout (base/input_kbm.c)                       -> PORT_KEYS, only when set
    "key_ramp": 0..60   reads a held stick key takes to full deflection; absent: 0              -> PORT_KEY_RAMP
    "game_menu": true   the game defines game_menu_extra (base/console.h), its own menu-bar entries  -> PORT_GAME_MENU
    "menu_checks": [..] at most four of the game's own on/off settings on the Game menu (runtime/console_dc.c)
                    -> PORT_MENU_CHECKS, only when there are some
    "menu_choices": [..] at most eight of the game's own choices kept as game.NAME, each in the menu it names
                    (base/menu_data.c)                                                        -> PORT_MENU_CHOICES
    "extra_buttons": {"actions": [..], "players": 1..8, "menu": "Input"}  Input > Extra buttons for a game with no key
                    screen of its own (base/menu_data.c)                                      -> PORT_EXTRA_BUTTONS
    "menu_commands": [{"menu", "label", "tag": 1..65535, "list": "runtime" | [labels]}]  the game's own commands, each
                    picked as (tag, index) at its next tick (native_game.h pg_native_menu_take); a list is a submenu
                    ('>' nests, more than 16 split), "runtime" the one the game gives (pg_native_menu_list); at most
                    435 entries in all (base/menu_data.c, host_menu_tagged.cpp)  -> PORT_MENU_COMMANDS
    "session_ahead": "keep" (default) or "old", how a replay reads notes of sound mixed ahead (base/session.c)
                    -> PORT_SESSION_AHEAD 1, only for "old"

Kept out of game_config.py so that file stays under the 400-line cap (tests/test_sizes.py).

Nearest existing: game_config.py - these checks lived there, and with them it is over the 400-line cap; part_draw.py is
the same kind of split for one key.
"""
import re


def config(raw, error):
    """The keys from game.json's dict, checked; error is game_config.ConfigError."""
    coop = raw.get("coop", 0)
    if type(coop) is not int or coop not in (0, 2, 3, 4):
        raise error(f"game.json coop: {coop!r} (0: no co-op; 2..4: the most players a co-op session takes, "
                    "base/coop_join.c; the game wires them in)")
    pads = raw.get("pads", 4)  # players 5..8 reach the game through port_pad_player
    if type(pads) is not int or not 1 <= pads <= 8:
        raise error(f"game.json pads: {pads!r} (1..8, default 4: the host controllers read)")
    keys = raw.get("keys", {})  # base/input_kbm.c: the game's whole keyboard and mouse layout, {"W": "RT", "A": "LSLEFT"}
    if not isinstance(keys, dict) or not all(isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_]{1,23}", k) and isinstance(v, str)
                                             and re.fullmatch(r"[A-Za-z0-9]+(\+[A-Za-z0-9]+)*", v) for k, v in keys.items()):
        raise error(f"game.json keys: {keys!r} (an object of key or mouse names to targets joined by '+', as base/input_kbm.c says)")
    key_ramp = raw.get("key_ramp", 0)
    if not isinstance(key_ramp, int) or isinstance(key_ramp, bool) or not 0 <= key_ramp <= 60:
        raise error(f"game.json key_ramp: {key_ramp!r} (0..60: reads a stick key takes to reach full deflection)")
    game_menu = raw.get("game_menu", False)
    if not isinstance(game_menu, bool):
        raise error(f"game.json game_menu: {game_menu!r} (true: the game adds its own menu-bar entries, "
                    "game_menu_extra in base/console.h)")
    # menu_checks: {"label", "setting": "game.NAME" (what the game reads with port_setting_get_int), "needs": a file in the
    # data folder without which the entry is greyed (optional), "default": true (optional, on until chosen in a play run)}
    checks = raw.get("menu_checks", [])
    if (not isinstance(checks, list) or len(checks) > 4 or not all(
            isinstance(c, dict) and set(c) <= {"label", "setting", "needs", "default"} and isinstance(c.get("label"), str)
            and c["label"] and re.fullmatch(r"game\.[a-z][a-z0-9_-]*", str(c.get("setting", "")))
            and isinstance(c.get("needs", ""), str) and isinstance(c.get("default", False), bool) for c in checks)):
        raise error('game.json menu_checks: at most four {"label", "setting": "game.NAME", "needs": a file in the '
                    'data folder (optional), "default": true to be on until chosen (optional)}')
    choices, extra = menu_data(raw, error, game_menu)
    commands = menu_commands(raw, error, game_menu)
    ahead = raw.get("session_ahead", "keep")
    if ahead not in ("keep", "old"):
        raise error(f'game.json session_ahead: {ahead!r} ("keep": a replay keeps the notes of sound mixed ahead for '
                    'this blank or later at a save or note read; "old": drops them all, as kits before 11-07 did)')
    return {"coop": coop, "pads": pads, "keys": keys, "key_ramp": key_ramp, "game_menu": game_menu, "menu_checks": checks,
            "menu_choices": choices, "extra_buttons": extra, "menu_commands": commands, "session_ahead": ahead}


MENUS = ("Display", "Audio", "Input", "Game")


def menu_data(raw, error, game_menu):
    """game.json menu_choices and extra_buttons, checked (base/menu_data.c serves them as game_menu_extra)."""
    choices = raw.get("menu_choices", [])
    shape = ('game.json menu_choices: at most eight {"menu": Display|Audio|Input|Game, "label", "setting": "game.NAME", '
             '"choices": [2..16 labels], "values": [as many distinct words], "default": one of the values (optional, '
             'else the first)}')
    if not isinstance(choices, list) or len(choices) > 8:
        raise error(shape)
    for c in choices:
        if not (isinstance(c, dict) and set(c) <= {"menu", "label", "setting", "choices", "values", "default"}
                and c.get("menu") in MENUS and isinstance(c.get("label"), str) and c["label"]
                and re.fullmatch(r"game\.[a-z][a-z0-9_.-]*", str(c.get("setting", "")))
                and isinstance(c.get("choices"), list) and 2 <= len(c["choices"]) <= 16
                and all(isinstance(x, str) and x for x in c["choices"])
                and isinstance(c.get("values"), list) and len(c["values"]) == len(c["choices"])
                and all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,31}", x) for x in c["values"])
                and len(set(c["values"])) == len(c["values"]) and c.get("default", c["values"][0]) in c["values"]):
            raise error(shape + f": {c!r}")
    extra = raw.get("extra_buttons")
    if extra is not None and not (
            isinstance(extra, dict) and set(extra) <= {"actions", "players", "menu"} and extra.get("menu", "Input") in MENUS
            and isinstance(extra.get("actions"), list) and 1 <= len(extra["actions"]) <= 16
            and all(isinstance(x, str) and x for x in extra["actions"])
            and type(extra.get("players", 4)) is int and 1 <= extra.get("players", 4) <= 8):
        raise error('game.json extra_buttons: {"actions": [1..16 labels of the game\'s actions, in the order the game '
                    'numbers them from 1], "players": 1..8 (default 4), "menu": "Input" (default)}')
    if game_menu and (choices or extra):
        raise error("game.json: menu_choices and extra_buttons are the base's game_menu_extra (base/menu_data.c); a game "
                    'with "game_menu": true defines its own, so move its entries into the data or keep them all in C')
    return choices, extra


COMMANDS_MAX = 435  # host_menu_tagged.cpp's ids, (7960 - 1000) / 16


def menu_commands(raw, error, game_menu):
    """game.json menu_commands, checked: tags unique, at most COMMANDS_MAX entries in all, not beside game_menu."""
    commands = raw.get("menu_commands", [])
    shape = ('game.json menu_commands: [{"menu": Display|Audio|Input|Game, "label", "tag": 1..65535 (each its own), '
             '"list": "runtime" or [labels] (optional: without it a single command)}]')
    if not isinstance(commands, list):
        raise error(shape)
    for c in commands:
        lst = c.get("list") if isinstance(c, dict) else None
        if not (isinstance(c, dict) and set(c) <= {"menu", "label", "tag", "list"} and c.get("menu") in MENUS
                and isinstance(c.get("label"), str) and c["label"] and type(c.get("tag")) is int and 1 <= c["tag"] <= 65535
                and (lst is None or lst == "runtime"
                     or (isinstance(lst, list) and lst and all(isinstance(x, str) and x.strip(" >") for x in lst)))):
            raise error(shape + f": {c!r}")
    tags = [c["tag"] for c in commands]
    if len(set(tags)) != len(tags):
        raise error(f"game.json menu_commands: tags {tags} repeat one (each command's tag is its own)")
    entries = sum(len(c["list"]) if isinstance(c.get("list"), list) else 1 for c in commands)
    if entries > COMMANDS_MAX:
        raise error(f"game.json menu_commands: {entries} entries; the menu takes at most {COMMANDS_MAX} in all")
    if game_menu and commands:
        raise error("game.json: menu_commands are the base's game_menu_extra (base/menu_data.c); a game with \"game_menu\": "
                    "true defines its own entries in C, so give them a tag there (console.h PortMenuItem tag)")
    return commands


def defines(cfg):
    """Their port_config.h lines; PORT_PADS only when not 4, so a 4-pad game's header is unchanged."""
    from game_config import c_string  # noqa: E402 (game_config imports this module first)
    lines = [f"#define PORT_COOP {int(cfg.get('coop', 0))} /* co-op: 0 none, else the most players (game.json coop) */"]
    if cfg.get("pads", 4) != 4:
        lines.append(f"#define PORT_PADS {int(cfg['pads'])} /* the players read, 1..8 (game.json pads; --pads overrides) */")
    if cfg.get("keys"):
        lines.append("#define PORT_KEYS " + c_string(",".join(f"{k}={v}" for k, v in cfg["keys"].items())) + " /* the game's keyboard and mouse layout (game.json keys) */")
    lines.append(f"#define PORT_KEY_RAMP {int(cfg.get('key_ramp', 0))} /* reads a stick key takes to full deflection (game.json key_ramp) */")
    data = bool(cfg.get("menu_choices") or cfg.get("extra_buttons") or cfg.get("menu_commands"))
    lines.append(f"#define PORT_GAME_MENU {int(bool(cfg.get('game_menu')) or data)} /* 1: game_menu_extra adds the game's menu-bar entries (game.json) */")
    if data:  # base/menu_data.c defines game_menu_extra from these
        lines.append("#define PORT_MENU_DATA 1 /* game.json menu_choices / extra_buttons / menu_commands: base/menu_data.c */")
    if cfg.get("menu_choices"):
        lines.append("#define PORT_MENU_CHOICES " + ", ".join("{%s, %s, %s, %d, %d, {%s}, {%s}}" % (
            c_string(c["menu"]), c_string(c["label"]), c_string(c["setting"]), len(c["values"]),
            c["values"].index(c.get("default", c["values"][0])), ", ".join(c_string(x) for x in c["choices"]),
            ", ".join(c_string(x) for x in c["values"])) for c in cfg["menu_choices"]))
    if cfg.get("extra_buttons"):
        e = cfg["extra_buttons"]
        lines.append("#define PORT_EXTRA_BUTTONS " + ", ".join(c_string(x) for x in e["actions"]))
        lines.append(f"#define PORT_EXTRA_BUTTONS_PLAYERS {int(e.get('players', 4))}")
        lines.append("#define PORT_EXTRA_BUTTONS_MENU " + c_string(e.get("menu", "Input")))
    if cfg.get("menu_commands"):  # {menu, label, tag, list (0 a command, 1 its labels, 2 the game's), first label, count}
        rows, labels = [], []
        for c in cfg["menu_commands"]:
            lst = c.get("list")
            kind, n = (1, len(lst)) if isinstance(lst, list) else (2, 0) if lst == "runtime" else (0, 0)
            rows.append("{%s, %s, %d, %d, %d, %d}" % (c_string(c["menu"]), c_string(c["label"]), c["tag"], kind, len(labels), n))
            labels += lst if kind == 1 else []
        lines.append("#define PORT_MENU_COMMANDS " + ", ".join(rows))
        if labels:
            lines.append("#define PORT_MENU_COMMAND_LABELS " + ", ".join(c_string(x) for x in labels))
    if cfg.get("menu_checks"):  # runtime/console_dc.c: Game > the game's own on/off settings (game.json menu_checks)
        lines.append("#define PORT_MENU_CHECKS " + ", ".join("{%s, %s, %s, %d}" % (
            c_string(c["label"]), c_string(c["setting"]), c_string(c.get("needs", "").replace(chr(92), "/")),
            int(c.get("default", False)))
            for c in cfg["menu_checks"]))
    if cfg.get("session_ahead") == "old":  # base/session.c ahead_mode's default
        lines.append("#define PORT_SESSION_AHEAD 1 /* a replay drops the notes of sound mixed ahead at a read (game.json session_ahead) */")
    return lines
